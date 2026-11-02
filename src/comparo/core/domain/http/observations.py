"""Immutable HTTP exchange, retry-attempt, and Environment execution observations."""

from __future__ import annotations

from itertools import pairwise
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.http.attempt_transitions import StreamReconnectTransition
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.exchange_validation import validate_exchange_semantics
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HeaderValue
from comparo.core.domain.http.fields import is_header_value
from comparo.core.domain.http.lifecycle_validation import validate_attempt_timing_envelope
from comparo.core.domain.http.lifecycle_validation import validate_execution_timing_envelope
from comparo.core.domain.http.observation_lineage_validation import validate_execution_occurrences
from comparo.core.domain.http.observation_lineage_validation import (
    validate_execution_storage_lineage,
)
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import is_http_pre_send_outcome
from comparo.core.domain.http.redirect_validation import validate_sensitive_redirect_resolution
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.retry_observation_validation import RetryMetadata as RetryMetadata
from comparo.core.domain.http.retry_observation_validation import RetryTrigger as RetryTrigger
from comparo.core.domain.http.retry_observation_validation import validate_retry_history
from comparo.core.domain.http.timing_validation import validate_exchange_timing_truth
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.transition_validation import http_pre_send_request
from comparo.core.domain.http.transition_validation import validate_redirect_pre_send_transition
from comparo.core.domain.http.transition_validation import validate_redirect_transition
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.primitives import Duration
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import is_sensitive_value


@final
class FollowedRedirect(msgspec.Struct, frozen=True, kw_only=True):
    """An explicit transition to the next exchange within the same attempt."""

    raw_location: HeaderValue
    resolved_url: AbsoluteHttpUrl

    def __post_init__(self) -> None:
        """Retain the exact observed field text and its structured resolution result."""
        if not is_header_value(self.raw_location):
            raise TypeError("redirect raw Location must be HeaderText or SensitiveValue")
        if (
            is_sensitive_value(self.raw_location)
            and self.raw_location.payload_type is not HeaderText
        ):
            raise TypeError("sensitive redirect Location payload must be HeaderText")
        if type(self.resolved_url) is not AbsoluteHttpUrl:
            raise TypeError("redirect resolved Location must be AbsoluteHttpUrl")
        if is_sensitive_value(self.raw_location):
            validate_sensitive_redirect_resolution(self.raw_location.origins, self.resolved_url)
        elif self.resolved_url.sensitivity is Sensitivity.SENSITIVE:
            raise ValueError("a public redirect Location cannot resolve to sensitive URL values")


_PRE_RESPONSE_FAILURES = frozenset(
    {
        FailurePhase.DNS,
        FailurePhase.CONNECT,
        FailurePhase.PROXY,
        FailurePhase.TLS,
        FailurePhase.REQUEST_WRITE,
        FailurePhase.RESPONSE_HEADERS,
        FailurePhase.RESPONSE_HEADERS_TIMEOUT,
        FailurePhase.POLICY,
    }
)

_POST_RESPONSE_FAILURES = frozenset(
    {
        FailurePhase.BODY_READ,
        FailurePhase.IDLE_TIMEOUT,
        FailurePhase.CONTENT_DECODING,
        FailurePhase.RESOURCE_BUDGET,
        FailurePhase.STORAGE,
    }
)

_FOLLOWABLE_REDIRECT_STATUSES = frozenset({300, 301, 302, 303, 307, 308})


def _validate_exchange_outcome(
    response: ResponseObservation | None,
    failure: TransportFailure | None,
    redirect: FollowedRedirect | None,
) -> None:
    if response is not None and type(response) is not ResponseObservation:
        raise TypeError("exchange response must be ResponseObservation when present")
    if failure is not None and type(failure) is not TransportFailure:
        raise TypeError("exchange failure must be TransportFailure when present")
    if response is None and failure is None:
        raise ValueError("an exchange requires a response, a failure, or both")
    if failure is not None and failure.phase in {FailurePhase.DNS, FailurePhase.POLICY}:
        raise ValueError("DNS failures and policy denials are pre-send outcomes, not exchanges")
    if response is None and failure is not None and failure.phase in _POST_RESPONSE_FAILURES:
        raise ValueError("a post-response failure requires its exact terminal response and capture")
    if response is not None and failure is not None and failure.phase in _PRE_RESPONSE_FAILURES:
        raise ValueError("a pre-response failure cannot coexist with a terminal response")
    if redirect is not None:
        _validate_followed_redirect(response, failure, redirect)


def _validate_followed_redirect(
    response: ResponseObservation | None,
    failure: TransportFailure | None,
    redirect: FollowedRedirect,
) -> None:
    """Bind an onward transition to one exact observed redirect response."""
    if type(redirect) is not FollowedRedirect:
        raise TypeError("exchange redirect must be FollowedRedirect when present")
    if response is None or response.status.value not in _FOLLOWABLE_REDIRECT_STATUSES:
        raise ValueError("only an observed redirect status may be followed as a redirect")
    if failure is not None:
        raise ValueError("a failed exchange cannot also transition to a redirect")
    locations = tuple(field for field in response.headers.fields if field.name.value == "location")
    if len(locations) != 1:
        raise ValueError("a followed redirect requires exactly one Location field")
    if locations[0].value != redirect.raw_location:
        raise ValueError("followed redirect must retain the exact observed Location field")


def _validate_attempt_redirects(
    exchanges: tuple[HttpExchange, ...],
    redirect_pre_send: HttpPreSendOutcome | None,
) -> None:
    """Validate ordered redirect markers, limits, policies, and request transitions."""
    if any(exchange.redirect is None for exchange in exchanges[:-1]):
        raise ValueError("every non-terminal exchange in an attempt must follow a redirect")
    if (exchanges[-1].redirect is not None) is (redirect_pre_send is None):
        raise ValueError(
            "the terminal exchange may point onward only to a retained redirect pre-send outcome"
        )
    redirect_policy = exchanges[0].request.request.policies.redirects
    if any(
        exchange.request.request.policies.redirects != redirect_policy for exchange in exchanges
    ):
        raise ValueError("all exchanges in an attempt must retain one redirect policy")
    followed_count = sum(exchange.redirect is not None for exchange in exchanges)
    if followed_count > redirect_policy.max_hops.value:
        raise ValueError("followed redirects exceed the effective redirect maximum")
    for previous, following in pairwise(exchanges):
        redirect = cast(FollowedRedirect, previous.redirect)
        response = cast(ResponseObservation, previous.response)
        validate_redirect_transition(
            previous.request,
            response.status,
            redirect.resolved_url,
            following.request,
        )
    if redirect_pre_send is not None:
        previous = exchanges[-1]
        redirect = cast(FollowedRedirect, previous.redirect)
        response = cast(ResponseObservation, previous.response)
        validate_redirect_pre_send_transition(
            previous.request,
            response.status,
            redirect.resolved_url,
            http_pre_send_request(redirect_pre_send),
        )


@final
class HttpExchange(msgspec.Struct, frozen=True, kw_only=True):
    """One request and its response or typed partial failure."""

    exchange_id: ExchangeId
    request: AuthorizedExchangeRequest
    response: ResponseObservation | None
    failure: TransportFailure | None
    redirect: FollowedRedirect | None
    timings: HttpExchangeTimings

    def __post_init__(self) -> None:
        """Preserve partial response facts and reject outcome-less exchanges."""
        if type(self.exchange_id) is not ExchangeId:
            raise TypeError("exchange ID must be ExchangeId")
        if type(self.request) is not AuthorizedExchangeRequest:
            raise TypeError("exchange request must be AuthorizedExchangeRequest")
        _validate_exchange_outcome(self.response, self.failure, self.redirect)
        validate_exchange_semantics(self.request, self.response, self.failure)
        if type(self.timings) is not HttpExchangeTimings:
            raise TypeError("exchange timings must be HttpExchangeTimings")
        validate_exchange_timing_truth(self.request, self.response, self.failure, self.timings)
        if self.request.occurrence.exchange_id != self.exchange_id:
            raise ValueError("exchange request authorization does not own this exchange")
        if self.response is not None and type(self.response.body) is ResponseEntityBody:
            capture = self.response.body.capture
            if type(capture) is BudgetExceededCapture:
                scope = capture.exhaustion.budget.scope
                if (
                    type(scope) is not BodyBudgetScope
                    or scope.attempt != self.request.occurrence.attempt
                ):
                    raise ValueError("capture budget scope does not own this exchange attempt")


@final
class HttpAttempt(msgspec.Struct, frozen=True, kw_only=True):
    """One initial or retry attempt containing an ordered redirect exchange chain."""

    attempt_id: AttemptId
    retry: RetryMetadata | None
    exchanges: tuple[HttpExchange, ...]
    timings: HttpAttemptTimings
    stream_reconnect: StreamReconnectTransition | None = None
    pre_send: HttpPreSendOutcome | None = None
    redirect_pre_send: HttpPreSendOutcome | None = None

    def __post_init__(self) -> None:
        """Require every non-terminal exchange to be an explicit redirect."""
        _validate_attempt_shape(self)
        _validate_attempt_timing_and_ownership(self)


def _validate_attempt_shape(attempt: HttpAttempt) -> None:
    """Validate one attempt's identity, transition marker, and exclusive outcome shape."""
    if type(attempt.attempt_id) is not AttemptId:
        raise TypeError("attempt ID must be AttemptId")
    if attempt.retry is not None and type(attempt.retry) is not RetryMetadata:
        raise TypeError("attempt retry metadata must be RetryMetadata when present")
    if (
        attempt.stream_reconnect is not None
        and type(attempt.stream_reconnect) is not StreamReconnectTransition
    ):
        raise TypeError("attempt stream reconnect must be StreamReconnectTransition when present")
    if attempt.retry is not None and attempt.stream_reconnect is not None:
        raise ValueError("an attempt cannot be both an HTTP retry and a stream reconnect")
    if type(attempt.exchanges) is not tuple:
        raise TypeError("attempt exchanges must be an immutable tuple")
    if any(type(exchange) is not HttpExchange for exchange in attempt.exchanges):
        raise TypeError("attempt exchanges must be an immutable tuple of HttpExchange values")
    _validate_attempt_pre_send_shape(attempt)
    if attempt.exchanges:
        _validate_attempt_redirects(attempt.exchanges, attempt.redirect_pre_send)


def _validate_attempt_pre_send_shape(attempt: HttpAttempt) -> None:
    """Validate mutually exclusive initial and redirect-terminal pre-send outcomes."""
    if attempt.pre_send is not None and not is_http_pre_send_outcome(attempt.pre_send):
        raise TypeError("attempt pre-send outcome must be an exact typed variant")
    if attempt.redirect_pre_send is not None and not is_http_pre_send_outcome(
        attempt.redirect_pre_send
    ):
        raise TypeError("attempt redirect pre-send outcome must be an exact typed variant")
    if attempt.pre_send is not None and attempt.redirect_pre_send is not None:
        raise ValueError("an attempt cannot have initial and redirect pre-send outcomes")
    if attempt.redirect_pre_send is not None and not attempt.exchanges:
        raise ValueError("a redirect pre-send outcome requires a preceding exchange")
    if bool(attempt.exchanges) is (attempt.pre_send is not None):
        raise ValueError("an HTTP attempt requires either exchanges or one pre-send outcome")


def _validate_attempt_timing_and_ownership(attempt: HttpAttempt) -> None:
    """Bind attempt elapsed and IDs to its exact exchange or pre-send operation."""
    if type(attempt.timings) is not HttpAttemptTimings:
        raise TypeError("attempt timings must be HttpAttemptTimings")
    child_timings = tuple(exchange.timings for exchange in attempt.exchanges)
    if attempt.redirect_pre_send is not None:
        child_timings += (attempt.redirect_pre_send.timings,)
    if not child_timings:
        child_timings = (cast(HttpPreSendOutcome, attempt.pre_send).timings,)
    validate_attempt_timing_envelope(attempt.timings, child_timings)
    if any(
        exchange.request.occurrence.attempt_id != attempt.attempt_id
        for exchange in attempt.exchanges
    ):
        raise ValueError("exchange request authorization does not own this attempt")
    if (
        attempt.pre_send is not None
        and attempt.pre_send.occurrence.attempt_id != attempt.attempt_id
    ):
        raise ValueError("pre-send request authority does not own this attempt")
    if (
        attempt.redirect_pre_send is not None
        and attempt.redirect_pre_send.occurrence.attempt_id != attempt.attempt_id
    ):  # pragma: no cover - redirect transition identity validation is stricter
        raise ValueError("redirect pre-send request authority does not own this attempt")


@final
class EnvironmentExecutionObservation(msgspec.Struct, frozen=True, kw_only=True):
    """One Environment's ordered retry history, optionally owned by a Scenario step."""

    execution_id: EnvironmentExecutionId
    environment: DefinitionRef
    step_owner: StepExecutionId | None
    attempts: tuple[HttpAttempt, ...]
    timings: EnvironmentExecutionTimings
    views: tuple[DecodedViewHandle, ...] = ()

    def __post_init__(self) -> None:
        """Keep retry, redirect, Environment, and occurrence identities unambiguous."""
        if type(self.execution_id) is not EnvironmentExecutionId:
            raise TypeError("execution observation ID must be EnvironmentExecutionId")
        if type(self.environment) is not DefinitionRef:
            raise TypeError("execution Environment must be DefinitionRef")
        if self.environment.identity.kind is not ResourceKind.ENVIRONMENT:
            raise ValueError("execution Environment must identify an Environment resource")
        if self.step_owner is not None and type(self.step_owner) is not StepExecutionId:
            raise TypeError("execution step owner must be StepExecutionId when present")
        if type(self.attempts) is not tuple or not self.attempts:
            raise ValueError("an Environment execution requires at least one HTTP attempt")
        if any(type(attempt) is not HttpAttempt for attempt in self.attempts):
            raise TypeError("execution attempts must be an immutable tuple of HttpAttempt values")
        if (
            self.attempts[0].retry is not None
            or self.attempts[0].stream_reconnect is not None
            or any(
                attempt.retry is None and attempt.stream_reconnect is None
                for attempt in self.attempts[1:]
            )
        ):
            raise ValueError("only attempts after the initial attempt require retry metadata")
        if type(self.views) is not tuple or any(
            type(view) is not DecodedViewHandle for view in self.views
        ):
            raise TypeError("execution views must be an immutable tuple of DecodedViewHandle")
        validate_execution_occurrences(
            self.execution_id,
            self.environment,
            self.step_owner,
            self.attempts,
        )
        validate_retry_history(self.attempts, self.views)
        if type(self.timings) is not EnvironmentExecutionTimings:
            raise TypeError("execution timings must be EnvironmentExecutionTimings")
        retry_delays = tuple(
            Duration(
                nanoseconds=attempt.retry.backoff.nanoseconds + attempt.retry.jitter.nanoseconds
            )
            for attempt in self.attempts[1:]
            if attempt.retry is not None
        )
        validate_execution_timing_envelope(
            self.timings,
            tuple(attempt.timings for attempt in self.attempts),
            retry_delays,
        )
        validate_execution_storage_lineage(self.attempts, self.views)
