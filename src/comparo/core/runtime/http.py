"""HTTP execution composition over request, authorization, transport, and storage ports."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import uuid5

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.scopes import AttemptScope
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import ExecutionOutcome
from comparo.core.runtime.models import ExecutionWork
from comparo.core.runtime.models import ObservedExecution
from comparo.core.runtime.ports import HttpExchangePreparer
from comparo.core.runtime.ports import HttpRedirectResolver
from comparo.core.runtime.ports import HttpRequestProvider
from comparo.core.runtime.ports import HttpTransport
from comparo.core.runtime.ports import NoRedirect
from comparo.core.runtime.ports import PreparedRedirect
from comparo.core.runtime.ports import RedirectFailure
from comparo.core.runtime.ports import RequestPreparationFailure
from comparo.core.runtime.ports import RetryWaiter
from comparo.core.runtime.ports import SelectionSnapshotProvider
from comparo.core.selection.snapshot import SelectionSnapshot

_UNAVAILABLE_TIMING = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)


class ObservationOnlySnapshotProvider:
    """Build selector snapshots for observations that require no stored body payloads."""

    async def snapshot(
        self,
        observation: EnvironmentExecutionObservation,
    ) -> SelectionSnapshot:
        """Return an exact observation-only snapshot."""
        return SelectionSnapshot(observation=observation)

    async def close(self) -> None:
        """Release no resources; observation-only snapshots own no storage handles."""


class NoRedirectResolver:
    """Disable redirect interpretation while retaining explicit response observations."""

    def resolve(
        self,
        request: AuthorizedExchangeRequest,
        response: ResponseObservation,
    ) -> NoRedirect:
        """Return the closed no-transition outcome for every terminal response."""
        if type(request) is not AuthorizedExchangeRequest:
            raise TypeError("redirect resolution requires AuthorizedExchangeRequest")
        if type(response) is not ResponseObservation:
            raise TypeError("redirect resolution requires ResponseObservation")
        return NoRedirect()


class ImmediateRetryWaiter:
    """Compute valid deterministic retry metadata without wall-clock delay for offline runs."""

    async def wait(
        self,
        policy: RetryPolicy,
        retry_index: Count,
        trigger: RetryTrigger,
        cancellation: RunCancellation,
    ) -> RetryMetadata:
        """Yield cooperatively once and return zero-jitter policy-derived timing facts."""
        if type(policy) is not RetryPolicy or type(retry_index) is not Count:
            raise TypeError("retry waiter requires a RetryPolicy and Count index")
        if retry_index.value < 1 or type(trigger) is not RetryTrigger:
            raise ValueError("retry waiter requires a positive index and RetryTrigger")
        if type(cancellation) is not RunCancellation:
            raise TypeError("retry waiter requires RunCancellation")
        await asyncio.sleep(0)
        if cancellation.cancelled:
            raise asyncio.CancelledError
        return RetryMetadata(
            trigger=trigger,
            backoff=Duration(nanoseconds=_backoff_nanoseconds(policy, retry_index.value)),
            jitter=Duration(nanoseconds=0),
        )


class HttpExecutionDriver:
    """Create canonical attempt observations around a narrow authorized transport port."""

    __slots__ = (
        "_cancellation",
        "_closed",
        "_exchange_preparer",
        "_maximum_attempts",
        "_redirect_resolver",
        "_request_provider",
        "_retry_waiter",
        "_snapshot_provider",
        "_transport",
    )

    def __init__(
        self,
        *,
        request_provider: HttpRequestProvider,
        exchange_preparer: HttpExchangePreparer,
        transport: HttpTransport,
        snapshot_provider: SelectionSnapshotProvider,
        retry_waiter: RetryWaiter,
        cancellation: RunCancellation,
        maximum_attempts: PositiveCount,
        redirect_resolver: HttpRedirectResolver | None = None,
    ) -> None:
        """Compose independent run-local services and the admitted attempt ceiling."""
        if type(cancellation) is not RunCancellation:
            raise TypeError("HTTP execution driver requires RunCancellation")
        if type(maximum_attempts) is not PositiveCount:
            raise TypeError("HTTP execution driver requires a PositiveCount attempt maximum")
        self._request_provider = request_provider
        self._exchange_preparer = exchange_preparer
        self._transport = transport
        self._snapshot_provider = snapshot_provider
        self._retry_waiter = retry_waiter
        self._cancellation = cancellation
        self._maximum_attempts = maximum_attempts
        self._redirect_resolver = redirect_resolver or NoRedirectResolver()
        if not hasattr(self._redirect_resolver, "resolve"):
            raise TypeError("HTTP execution driver requires HttpRedirectResolver")
        self._closed = False

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        """Resolve attempts until a terminal result, retry ceiling, or cancellation."""
        if type(work) is not ExecutionWork:
            raise TypeError("HTTP execution requires ExecutionWork")
        if self._closed:
            raise RuntimeError("HTTP execution driver is closed")
        attempts: list[HttpAttempt] = []
        retry: RetryMetadata | None = None
        for attempt_index in range(self._maximum_attempts.value):  # pragma: no branch
            if self._cancellation.cancelled:
                return CancelledExecution(source=_cancellation_source(self._cancellation))
            attempt_scope = _attempt_scope(work, attempt_index)
            prepared = await self._request_provider.prepare(work, attempt_scope)
            if type(prepared) is RequestPreparationFailure:
                return prepared.outcome
            request = prepared
            attempt = await self._execute_attempt(request, attempt_scope, retry)
            attempts.append(attempt)
            trigger = _retry_trigger(attempt)
            policy = request.policies.retries
            allowed_attempts = min(policy.max_attempts.value, self._maximum_attempts.value)
            if trigger is None or len(attempts) >= allowed_attempts:
                break
            retry = await self._retry_waiter.wait(
                policy,
                Count(value=len(attempts)),
                trigger,
                self._cancellation,
            )

        observation = EnvironmentExecutionObservation(
            execution_id=work.scope.execution_id,
            environment=work.environment,
            step_owner=None,
            attempts=tuple(attempts),
            timings=EnvironmentExecutionTimings(
                queue=_UNAVAILABLE_TIMING,
                elapsed=_UNAVAILABLE_TIMING,
            ),
        )
        snapshot = await self._snapshot_provider.snapshot(observation)
        return ObservedExecution(observation=observation, snapshot=snapshot)

    async def _execute_attempt(
        self,
        request: FinalizedWireRequest,
        attempt_scope: AttemptScope,
        retry: RetryMetadata | None,
    ) -> HttpAttempt:
        """Execute one initial request and its bounded, freshly authorized redirect chain."""
        occurrence = _exchange_occurrence(attempt_scope, 0)
        preparation = await self._exchange_preparer.prepare(request, occurrence)
        if type(preparation) is not AuthorizedExchangeRequest:
            return HttpAttempt(
                attempt_id=attempt_scope.attempt_id,
                retry=retry,
                exchanges=(),
                pre_send=preparation,
                timings=HttpAttemptTimings(elapsed=preparation.timings.elapsed),
            )
        authorized = preparation
        exchanges: list[HttpExchange] = []
        redirect_pre_send: HttpPreSendOutcome | None = None
        maximum_hops = request.policies.redirects.max_hops.value
        for exchange_index in range(maximum_hops + 1):  # pragma: no branch - positive range
            result = await self._transport.exchange(authorized)
            failure = result.failure
            redirect = None
            following: AuthorizedExchangeRequest | None = None
            if result.response is not None and failure is None and exchange_index < maximum_hops:
                resolution = self._redirect_resolver.resolve(authorized, result.response)
                if type(resolution) is RedirectFailure:
                    failure = resolution.failure
                elif type(resolution) is PreparedRedirect:
                    redirect = resolution.transition
                    next_occurrence = _exchange_occurrence(attempt_scope, exchange_index + 1)
                    next_preparation = await self._exchange_preparer.prepare(
                        resolution.request,
                        next_occurrence,
                    )
                    if type(next_preparation) is AuthorizedExchangeRequest:
                        following = next_preparation
                    else:
                        redirect_pre_send = next_preparation
            exchanges.append(
                HttpExchange(
                    exchange_id=authorized.occurrence.exchange_id,
                    request=authorized,
                    response=result.response,
                    failure=failure,
                    redirect=redirect,
                    timings=result.timings,
                )
            )
            if following is None:
                break
            authorized = following
        elapsed = (
            exchanges[0].timings.elapsed
            if len(exchanges) == 1 and redirect_pre_send is None
            else _UNAVAILABLE_TIMING
        )
        return HttpAttempt(
            attempt_id=attempt_scope.attempt_id,
            retry=retry,
            exchanges=tuple(exchanges),
            timings=HttpAttemptTimings(elapsed=elapsed),
            redirect_pre_send=redirect_pre_send,
        )

    async def close(self) -> None:
        """Attempt every owned cleanup exactly once, without leaking adapter exceptions."""
        if self._closed:
            return
        self._closed = True
        results = await asyncio.gather(
            self._request_provider.close(),
            self._exchange_preparer.close(),
            self._snapshot_provider.close(),
            self._transport.close(),
            return_exceptions=True,
        )
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("HTTP execution resource cleanup failed")


def _attempt_scope(work: ExecutionWork, attempt_index: int) -> AttemptScope:
    return AttemptScope(
        execution=work.scope,
        attempt_id=AttemptId(
            value=uuid5(
                work.scope.execution_id.value,
                f"attempt:{attempt_index}",
            )
        ),
    )


def _exchange_occurrence(
    attempt: AttemptScope,
    exchange_index: int,
) -> HttpRequestOccurrence:
    return HttpRequestOccurrence(
        attempt=attempt,
        exchange_id=ExchangeId(value=uuid5(attempt.attempt_id.value, f"exchange:{exchange_index}")),
    )


def _retry_trigger(attempt: HttpAttempt) -> RetryTrigger | None:
    failure_mapping = {
        FailurePhase.DNS: (RetryTrigger.DNS_FAILURE, RetryCondition.DNS_FAILURE),
        FailurePhase.CONNECT: (RetryTrigger.CONNECT_FAILURE, RetryCondition.CONNECT_FAILURE),
        FailurePhase.TLS: (RetryTrigger.TLS_FAILURE, RetryCondition.TLS_FAILURE),
        FailurePhase.RESPONSE_HEADERS_TIMEOUT: (
            RetryTrigger.RESPONSE_HEADERS_TIMEOUT,
            RetryCondition.RESPONSE_HEADERS_TIMEOUT,
        ),
        FailurePhase.IDLE_TIMEOUT: (RetryTrigger.IDLE_TIMEOUT, RetryCondition.IDLE_TIMEOUT),
    }
    request = (
        attempt.exchanges[0].request.request
        if attempt.exchanges
        else _pre_send_wire_request(cast(HttpPreSendOutcome, attempt.pre_send))
    )
    policy = request.policies.retries
    failure = None
    if type(attempt.pre_send) is HttpResolutionFailure:
        failure = attempt.pre_send.failure
    elif type(attempt.redirect_pre_send) is HttpResolutionFailure:
        failure = attempt.redirect_pre_send.failure
    elif attempt.exchanges:
        failure = attempt.exchanges[-1].failure
    if failure is not None:
        match = failure_mapping.get(failure.phase)
        if match is not None and failure.retryable and match[1] in policy.conditions:
            return match[0]
        return None
    response = attempt.exchanges[-1].response if attempt.exchanges else None
    if response is not None and response.status.value == 429:
        return RetryTrigger.STATUS_429 if RetryCondition.STATUS_429 in policy.conditions else None
    if response is not None and 500 <= response.status.value < 600:
        return RetryTrigger.STATUS_5XX if RetryCondition.STATUS_5XX in policy.conditions else None
    return None


def _backoff_nanoseconds(policy: RetryPolicy, retry_index: int) -> int:
    backoff = policy.backoff
    if type(backoff) is NoBackoff:
        return 0
    if type(backoff) is FixedBackoff:
        return backoff.delay.nanoseconds
    exponent = retry_index - 1
    scaled: int = (
        backoff.initial.nanoseconds
        * backoff.multiplier.numerator**exponent
        // backoff.multiplier.denominator.value**exponent
    )
    maximum: int = backoff.maximum.nanoseconds
    return min(maximum, scaled)


def _pre_send_wire_request(outcome: HttpPreSendOutcome) -> FinalizedWireRequest:
    if type(outcome) is HttpResolutionFailure:
        return outcome.request.request
    return outcome.request


def _cancellation_source(cancellation: RunCancellation) -> CancellationSource:
    source = cancellation.source
    return source if source is not None else CancellationSource.PARENT_SCOPE
