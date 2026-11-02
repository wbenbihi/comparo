"""Cycle-free retry metadata and ordered HTTP attempt-history validation."""

from __future__ import annotations

from typing import Protocol
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.attempt_transitions import StreamReconnectTransition
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import HttpProxyDnsApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.retry_timing_validation import validate_retry_delay
from comparo.core.domain.http.transition_validation import HttpAttemptRequest
from comparo.core.domain.http.transition_validation import validate_retry_request
from comparo.core.domain.http.transition_validation import validate_stream_reconnect_request
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Duration


class RetryTrigger(DomainStrEnum):
    """Observable causes for a new attempt, distinct from redirect exchanges."""

    DNS_FAILURE = "dns-failure"
    CONNECT_FAILURE = "connect-failure"
    TLS_FAILURE = "tls-failure"
    RESPONSE_HEADERS_TIMEOUT = "response-headers-timeout"
    IDLE_TIMEOUT = "idle-timeout"
    STATUS_429 = "status-429"
    STATUS_5XX = "status-5xx"


@final
class RetryMetadata(msgspec.Struct, frozen=True, kw_only=True):
    """The policy decision and delay that preceded one retry attempt."""

    trigger: RetryTrigger
    backoff: Duration
    jitter: Duration

    def __post_init__(self) -> None:
        """Require explicit monotonic delay components and retry cause."""
        if type(self.trigger) is not RetryTrigger:
            raise TypeError("retry trigger must be RetryTrigger")
        if type(self.backoff) is not Duration or type(self.jitter) is not Duration:
            raise TypeError("retry backoff and jitter must be Duration values")


class RetryExchangeLike(Protocol):
    """Exchange facts needed to validate retry causes and reconnect predecessors."""

    @property
    def exchange_id(self) -> ExchangeId:
        """Return this exchange's exact occurrence identity."""
        ...

    @property
    def request(self) -> AuthorizedExchangeRequest:
        """Return the request authority consumed by this exchange."""
        ...

    @property
    def response(self) -> ResponseObservation | None:
        """Return the terminal response when response headers were observed."""
        ...

    @property
    def failure(self) -> TransportFailure | None:
        """Return the typed terminal transport failure when present."""
        ...


class RetryAttemptLike(Protocol):
    """Attempt facts required without importing the observation aggregates."""

    @property
    def attempt_id(self) -> AttemptId:
        """Return this attempt's exact occurrence identity."""
        ...

    @property
    def retry(self) -> RetryMetadata | None:
        """Return retry metadata for attempts after an HTTP retry transition."""
        ...

    @property
    def exchanges(self) -> tuple[RetryExchangeLike, ...]:
        """Return the ordered redirect exchange chain, if transport was entered."""
        ...

    @property
    def stream_reconnect(self) -> StreamReconnectTransition | None:
        """Return stream reconnect metadata when this is a reconnect attempt."""
        ...

    @property
    def pre_send(self) -> HttpPreSendOutcome | None:
        """Return the exact terminal pre-send outcome, if transport was not entered."""
        ...

    @property
    def redirect_pre_send(self) -> HttpPreSendOutcome | None:
        """Return a redirect target's terminal pre-send outcome when present."""
        ...


_TRIGGER_CONDITIONS = (
    (RetryTrigger.DNS_FAILURE, RetryCondition.DNS_FAILURE),
    (RetryTrigger.CONNECT_FAILURE, RetryCondition.CONNECT_FAILURE),
    (RetryTrigger.TLS_FAILURE, RetryCondition.TLS_FAILURE),
    (RetryTrigger.RESPONSE_HEADERS_TIMEOUT, RetryCondition.RESPONSE_HEADERS_TIMEOUT),
    (RetryTrigger.IDLE_TIMEOUT, RetryCondition.IDLE_TIMEOUT),
    (RetryTrigger.STATUS_429, RetryCondition.STATUS_429),
    (RetryTrigger.STATUS_5XX, RetryCondition.STATUS_5XX),
)


def _attempt_request(attempt: RetryAttemptLike) -> HttpAttemptRequest:
    """Return the exact first request or pre-send candidate owned by one attempt."""
    if attempt.exchanges:
        return attempt.exchanges[0].request
    pre_send = cast(HttpPreSendOutcome, attempt.pre_send)
    if type(pre_send) is HttpResolutionFailure:
        return pre_send.request
    return pre_send


def _observed_retry_trigger(attempt: RetryAttemptLike) -> RetryTrigger | None:
    failure_triggers = {
        FailurePhase.DNS: RetryTrigger.DNS_FAILURE,
        FailurePhase.CONNECT: RetryTrigger.CONNECT_FAILURE,
        FailurePhase.TLS: RetryTrigger.TLS_FAILURE,
        FailurePhase.RESPONSE_HEADERS_TIMEOUT: RetryTrigger.RESPONSE_HEADERS_TIMEOUT,
        FailurePhase.IDLE_TIMEOUT: RetryTrigger.IDLE_TIMEOUT,
    }
    if type(attempt.pre_send) is HttpResolutionFailure:
        return RetryTrigger.DNS_FAILURE
    if type(attempt.pre_send) in {
        HttpResolutionDenial,
        HttpAuthorizationDenial,
        HttpMutationApprovalDenial,
        HttpProxyDnsApprovalDenial,
    }:
        return None
    if type(attempt.redirect_pre_send) is HttpResolutionFailure:
        return RetryTrigger.DNS_FAILURE
    if type(attempt.redirect_pre_send) in {
        HttpResolutionDenial,
        HttpAuthorizationDenial,
        HttpMutationApprovalDenial,
        HttpProxyDnsApprovalDenial,
    }:
        return None
    exchange = attempt.exchanges[-1]
    if exchange.failure is not None and exchange.failure.phase in failure_triggers:
        return failure_triggers[exchange.failure.phase]
    if exchange.response is not None and exchange.response.status.value == 429:
        return RetryTrigger.STATUS_429
    if exchange.response is not None and 500 <= exchange.response.status.value < 600:
        return RetryTrigger.STATUS_5XX
    return None


def validate_retry_history(
    attempts: tuple[RetryAttemptLike, ...],
    views: tuple[DecodedViewHandle, ...],
) -> None:
    """Validate retry policy, cause, schedule, and request continuity across attempts."""
    policy = _attempt_request(attempts[0]).request.policies.retries
    if any(_attempt_request(attempt).request.policies.retries != policy for attempt in attempts):
        raise ValueError("all retry attempts must retain one effective retry policy")
    retry_group_baseline = _attempt_request(attempts[0])
    retry_group_attempt_count = 1
    retry_index = 0
    views_by_id = {view.view_id: view for view in views}
    for index, attempt in enumerate(attempts[1:], start=1):
        preceding_attempt = attempts[index - 1]
        preceding_exchange = (
            preceding_attempt.exchanges[-1] if preceding_attempt.exchanges else None
        )
        if attempt.stream_reconnect is not None:
            if (
                preceding_exchange is None
                or attempt.stream_reconnect.previous_attempt_id != preceding_attempt.attempt_id
                or attempt.stream_reconnect.previous_exchange_id != preceding_exchange.exchange_id
            ):
                raise ValueError(
                    "stream reconnect transition must bind the immediately preceding attempt"
                )
            validate_stream_reconnect_request(
                preceding_exchange.request,
                _attempt_request(attempt),
            )
            response = preceding_exchange.response
            stream_view = views_by_id.get(attempt.stream_reconnect.previous_stream_view_id)
            establishment_failure = preceding_exchange.failure
            if (
                response is None
                or not 200 <= response.status.value < 300
                or type(response.body) is not ResponseEntityBody
                or response.body.handle is None
                or stream_view is None
                or stream_view.source != response.body.handle
                or stream_view.decoder.view
                not in {ViewKind.SSE, ViewKind.NDJSON, ViewKind.JSON_SEQUENCE}
                or (
                    establishment_failure is not None
                    and establishment_failure.phase
                    not in {
                        FailurePhase.BODY_READ,
                        FailurePhase.IDLE_TIMEOUT,
                        FailurePhase.PROTOCOL,
                    }
                )
            ):
                raise ValueError(
                    "stream reconnect predecessor must prove an exact captured streaming view"
                )
            retry_group_baseline = _attempt_request(attempt)
            retry_group_attempt_count = 1
            retry_index = 0
            continue
        retry_group_attempt_count += 1
        if retry_group_attempt_count > policy.max_attempts.value:
            raise ValueError("observed attempts exceed the effective retry maximum")
        retry_index += 1
        retry = cast(RetryMetadata, attempt.retry)
        expected = _observed_retry_trigger(preceding_attempt)
        if retry.trigger is not expected:
            raise ValueError("retry trigger does not match the preceding attempt outcome")
        preceding_failure = (
            preceding_exchange.failure
            if preceding_exchange is not None
            else cast(HttpResolutionFailure, preceding_attempt.pre_send).failure
        )
        if preceding_failure is not None and not preceding_failure.retryable:
            raise ValueError("a non-retryable transport failure cannot produce a retry attempt")
        required_condition = next(
            condition for trigger, condition in _TRIGGER_CONDITIONS if trigger is retry.trigger
        )
        if required_condition not in policy.conditions:
            raise ValueError("retry trigger is not enabled by the effective retry policy")
        validate_retry_request(retry_group_baseline, _attempt_request(attempt))
        validate_retry_delay(policy.backoff, policy.jitter, retry_index, retry)
