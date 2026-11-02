"""HTTP execution-driver retries, pre-send outcomes, and cleanup edge cases."""

from __future__ import annotations

import asyncio
from typing import override

import msgspec
import pytest

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import AttemptScope
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import CancelledExecution
from comparo.core.runtime import ExecutionWork
from comparo.core.runtime import HttpExecutionDriver
from comparo.core.runtime import HttpTransportResult
from comparo.core.runtime import ImmediateRetryWaiter
from comparo.core.runtime import ObservationOnlySnapshotProvider
from comparo.core.runtime import RequestPreparationFailure
from comparo.core.runtime import UnavailableExecution
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime.http import _backoff_nanoseconds
from comparo.core.runtime.http import _cancellation_source
from comparo.core.runtime.http import _pre_send_wire_request
from comparo.core.runtime.http import _retry_trigger
from comparo.core.runtime.ports import HttpExchangePreparer
from comparo.core.runtime.ports import HttpRequestProvider
from comparo.core.runtime.scheduling import _execution_failed
from tests.core.domain.http.factories import policies
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import authorization_denial
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_denial
from tests.core.domain.http.observation_factories import resolution_failure
from tests.core.domain.http.observation_factories import response
from tests.core.runtime.conftest import AllowingExchangePreparer
from tests.core.runtime.conftest import StaticRequestProvider


def _retry_policy(*conditions: RetryCondition) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=PositiveCount(value=3),
        conditions=conditions,
        backoff=FixedBackoff(delay=Duration(nanoseconds=7)),
        jitter=Ratio(numerator=0, denominator=PositiveCount(value=1)),
    )


def _effective(*conditions: RetryCondition) -> EffectiveRequest:
    value = request(attempts=1)
    return msgspec.structs.replace(
        value,
        policies=msgspec.structs.replace(
            value.policies,
            retries=_retry_policy(*conditions),
        ),
    )


def _attempt_for(
    *,
    status: int | None = None,
    phase: FailurePhase | None = None,
    retryable: bool = True,
    conditions: tuple[RetryCondition, ...],
) -> HttpAttempt:
    effective = _effective(*conditions)
    observed: ResponseObservation | None
    if phase is FailurePhase.IDLE_TIMEOUT:
        observed = response(
            body=ResponseEntityBody(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.IDLE_TIMEOUT),
                handle=None,
                encoded_byte_count=ByteCount(value=0),
            )
        )
    else:
        observed = response(status=status) if status is not None else None
    item = exchange(
        exchange_number=1,
        effective_request=effective,
        observed_response=observed,
        failure=failure(phase, retryable=retryable) if phase is not None else None,
    )
    return HttpAttempt(
        attempt_id=item.request.occurrence.attempt_id,
        retry=None,
        exchanges=(item,),
        timings=attempt_timings(),
    )


@pytest.mark.parametrize(
    ("phase", "condition", "trigger"),
    [
        (FailurePhase.CONNECT, RetryCondition.CONNECT_FAILURE, RetryTrigger.CONNECT_FAILURE),
        (FailurePhase.TLS, RetryCondition.TLS_FAILURE, RetryTrigger.TLS_FAILURE),
        (
            FailurePhase.RESPONSE_HEADERS_TIMEOUT,
            RetryCondition.RESPONSE_HEADERS_TIMEOUT,
            RetryTrigger.RESPONSE_HEADERS_TIMEOUT,
        ),
        (FailurePhase.IDLE_TIMEOUT, RetryCondition.IDLE_TIMEOUT, RetryTrigger.IDLE_TIMEOUT),
    ],
)
def test_retry_trigger_maps_each_supported_transport_phase(
    phase: FailurePhase,
    condition: RetryCondition,
    trigger: RetryTrigger,
) -> None:
    assert _retry_trigger(_attempt_for(phase=phase, conditions=(condition,))) is trigger
    assert (
        _retry_trigger(_attempt_for(phase=phase, conditions=(RetryCondition.STATUS_429,))) is None
    )
    assert (
        _retry_trigger(_attempt_for(phase=phase, retryable=False, conditions=(condition,))) is None
    )


def test_retry_trigger_handles_statuses_unmapped_failures_and_pre_send_outcomes() -> None:
    assert _retry_trigger(_attempt_for(status=429, conditions=(RetryCondition.STATUS_429,))) is (
        RetryTrigger.STATUS_429
    )
    assert (
        _retry_trigger(_attempt_for(status=429, conditions=(RetryCondition.CONNECT_FAILURE,)))
        is None
    )
    assert _retry_trigger(_attempt_for(status=503, conditions=(RetryCondition.STATUS_5XX,))) is (
        RetryTrigger.STATUS_5XX
    )
    assert (
        _retry_trigger(_attempt_for(status=503, conditions=(RetryCondition.CONNECT_FAILURE,)))
        is None
    )
    assert (
        _retry_trigger(_attempt_for(status=200, conditions=(RetryCondition.CONNECT_FAILURE,)))
        is None
    )
    assert (
        _retry_trigger(
            _attempt_for(
                phase=FailurePhase.REQUEST_WRITE,
                conditions=(RetryCondition.CONNECT_FAILURE,),
            )
        )
        is None
    )

    effective = _effective(RetryCondition.DNS_FAILURE)
    request_occurrence = occurrence(environment=effective.environment.identity)
    failed = resolution_failure(effective, request_occurrence=request_occurrence)
    attempt = HttpAttempt(
        attempt_id=request_occurrence.attempt_id,
        retry=None,
        exchanges=(),
        pre_send=failed,
        timings=attempt_timings(),
    )
    assert _retry_trigger(attempt) is RetryTrigger.DNS_FAILURE
    assert _pre_send_wire_request(failed) == failed.request.request

    denied = resolution_denial(effective, request_occurrence=request_occurrence)
    denied_attempt = msgspec.structs.replace(attempt, pre_send=denied)
    assert _retry_trigger(denied_attempt) is None
    assert _pre_send_wire_request(denied) == denied.request


def test_retry_backoff_and_waiter_validation_are_explicit() -> None:
    no_backoff = policies().retries
    assert _backoff_nanoseconds(no_backoff, 1) == 0
    fixed = msgspec.structs.replace(
        no_backoff,
        backoff=FixedBackoff(delay=Duration(nanoseconds=9)),
    )
    assert _backoff_nanoseconds(fixed, 2) == 9
    exponential = msgspec.structs.replace(
        no_backoff,
        backoff=ExponentialBackoff(
            initial=Duration(nanoseconds=3),
            multiplier=Ratio(numerator=2, denominator=PositiveCount(value=1)),
            maximum=Duration(nanoseconds=10),
        ),
    )
    assert _backoff_nanoseconds(exponential, 2) == 6
    assert _backoff_nanoseconds(exponential, 3) == 10

    waiter = ImmediateRetryWaiter()
    cancellation = RunCancellation()
    with pytest.raises(TypeError, match="RetryPolicy and Count"):
        asyncio.run(
            waiter.wait(object(), Count(value=1), RetryTrigger.STATUS_429, cancellation)  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="RetryPolicy and Count"):
        asyncio.run(
            waiter.wait(no_backoff, object(), RetryTrigger.STATUS_429, cancellation)  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="positive index"):
        asyncio.run(waiter.wait(no_backoff, Count(value=0), RetryTrigger.STATUS_429, cancellation))
    with pytest.raises(ValueError, match="positive index"):
        asyncio.run(
            waiter.wait(no_backoff, Count(value=1), object(), cancellation)  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="RunCancellation"):
        asyncio.run(
            waiter.wait(
                no_backoff,
                Count(value=1),
                RetryTrigger.STATUS_429,
                object(),  # type: ignore[arg-type]
            )
        )
    cancellation.cancel(CancellationSource.USER)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(waiter.wait(no_backoff, Count(value=1), RetryTrigger.STATUS_429, cancellation))


class FailureRequestProvider:
    """Return one typed preparation failure without reaching pre-send work."""

    async def prepare(
        self,
        work: ExecutionWork,
        attempt: AttemptScope,
    ) -> RequestPreparationFailure:
        del work, attempt
        return RequestPreparationFailure(
            outcome=UnavailableExecution(
                stage=DiagnosticStage.RESOLUTION,
                code=DiagnosticCode(value="runtime.test.preparation"),
            )
        )

    async def close(self) -> None:
        """Release no resources."""


class PreSendPreparer:
    """Return a configured pre-send terminal outcome."""

    def __init__(self, *, denial: bool = False) -> None:
        self.denial = denial

    async def prepare(
        self,
        request_value: FinalizedWireRequest,
        request_occurrence: HttpRequestOccurrence,
    ) -> HttpResolutionFailure | HttpAuthorizationDenial:
        effective = request(attempts=2)
        effective = msgspec.structs.replace(effective, environment=request_value.environment)
        if self.denial:
            return authorization_denial(
                effective,
                request_occurrence=request_occurrence,
            )
        return resolution_failure(effective, request_occurrence=request_occurrence)

    async def close(self) -> None:
        """Release no resources."""


class NeverTransport:
    """Fail if a pre-send-only test accidentally enters transport."""

    async def exchange(self, request_value: AuthorizedExchangeRequest) -> HttpTransportResult:
        del request_value
        raise AssertionError("transport must not be called")

    async def close(self) -> None:
        """Release no resources."""


def _driver(
    prepared: PreparedRun,
    cancellation: RunCancellation,
    *,
    request_provider: HttpRequestProvider | None = None,
    exchange_preparer: HttpExchangePreparer | None = None,
) -> HttpExecutionDriver:
    return HttpExecutionDriver(
        request_provider=request_provider or StaticRequestProvider(attempts=2),
        exchange_preparer=exchange_preparer or PreSendPreparer(denial=True),
        transport=NeverTransport(),
        snapshot_provider=ObservationOnlySnapshotProvider(),
        retry_waiter=ImmediateRetryWaiter(),
        cancellation=cancellation,
        maximum_attempts=prepared.effective.maximum_attempts,
    )


def test_driver_returns_preparation_failure_pre_send_observation_and_cancellation(
    prepared_comparison_run: PreparedRun,
) -> None:
    work = next(iter_execution_work(prepared_comparison_run))
    cancellation = RunCancellation()
    failed_driver = _driver(
        prepared_comparison_run,
        cancellation,
        request_provider=FailureRequestProvider(),
    )
    failed = asyncio.run(failed_driver.execute(work))
    assert type(failed) is UnavailableExecution
    asyncio.run(failed_driver.close())

    denied_driver = _driver(prepared_comparison_run, RunCancellation())
    denied = asyncio.run(denied_driver.execute(work))
    assert denied.observation.attempts[0].pre_send is not None  # type: ignore[union-attr]
    assert _execution_failed(denied)
    asyncio.run(denied_driver.close())

    cancelled = RunCancellation()
    cancelled.cancel(CancellationSource.DEADLINE)
    cancelled_driver = _driver(prepared_comparison_run, cancelled)
    outcome = asyncio.run(cancelled_driver.execute(work))
    assert outcome == CancelledExecution(source=CancellationSource.DEADLINE)
    asyncio.run(cancelled_driver.close())
    assert _cancellation_source(RunCancellation()) is CancellationSource.PARENT_SCOPE


def test_driver_validates_calls_and_closes_all_resources_once(
    prepared_comparison_run: PreparedRun,
) -> None:
    cancellation = RunCancellation()
    with pytest.raises(TypeError, match="RunCancellation"):
        HttpExecutionDriver(
            request_provider=StaticRequestProvider(),
            exchange_preparer=PreSendPreparer(),
            transport=NeverTransport(),
            snapshot_provider=ObservationOnlySnapshotProvider(),
            retry_waiter=ImmediateRetryWaiter(),
            cancellation=object(),  # type: ignore[arg-type]
            maximum_attempts=PositiveCount(value=1),
        )
    with pytest.raises(TypeError, match="PositiveCount"):
        HttpExecutionDriver(
            request_provider=StaticRequestProvider(),
            exchange_preparer=PreSendPreparer(),
            transport=NeverTransport(),
            snapshot_provider=ObservationOnlySnapshotProvider(),
            retry_waiter=ImmediateRetryWaiter(),
            cancellation=cancellation,
            maximum_attempts=object(),  # type: ignore[arg-type]
        )

    driver = _driver(prepared_comparison_run, cancellation)
    with pytest.raises(TypeError, match="ExecutionWork"):
        asyncio.run(driver.execute(object()))  # type: ignore[arg-type]
    asyncio.run(driver.close())
    asyncio.run(driver.close())
    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(driver.execute(next(iter_execution_work(prepared_comparison_run))))


class FailingCloseProvider(StaticRequestProvider):
    """Raise from close after recording that cleanup ran."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    @override
    async def close(self) -> None:
        self.closed = True
        raise RuntimeError("payload-like cleanup detail")


class TrackingPreparer(AllowingExchangePreparer):
    """Record cleanup independently of peer failures."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    @override
    async def close(self) -> None:
        self.closed = True


class TrackingSnapshotProvider(ObservationOnlySnapshotProvider):
    """Record snapshot-provider cleanup independently."""

    def __init__(self) -> None:
        self.closed = False

    @override
    async def close(self) -> None:
        self.closed = True


class TrackingTransport(NeverTransport):
    """Record transport cleanup independently."""

    def __init__(self) -> None:
        self.closed = False

    @override
    async def close(self) -> None:
        self.closed = True


def test_driver_cleanup_attempts_every_owned_resource_before_safe_failure() -> None:
    provider = FailingCloseProvider()
    preparer = TrackingPreparer()
    snapshots = TrackingSnapshotProvider()
    transport = TrackingTransport()
    driver = HttpExecutionDriver(
        request_provider=provider,
        exchange_preparer=preparer,
        transport=transport,
        snapshot_provider=snapshots,
        retry_waiter=ImmediateRetryWaiter(),
        cancellation=RunCancellation(),
        maximum_attempts=PositiveCount(value=1),
    )
    with pytest.raises(RuntimeError, match="resource cleanup failed") as captured:
        asyncio.run(driver.close())
    assert "payload-like" not in str(captured.value)
    assert provider.closed
    assert preparer.closed
    assert snapshots.closed
    assert transport.closed
    asyncio.run(driver.close())
