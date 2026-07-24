"""Tests for response capture, transport metadata, and observation hierarchy semantics."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.retry_timing_validation import validate_retry_delay
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import followed_redirect
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response


def test_redirects_are_exchanges_and_retries_are_attempts() -> None:
    effective_request = request(attempts=2, redirect_hops=1)
    redirect = exchange(
        exchange_number=1,
        effective_request=effective_request,
        observed_response=response(status=302),
        redirect=followed_redirect(effective_request),
    )
    redirected = exchange(
        exchange_number=2,
        attempt_number=1,
        effective_request=effective_request,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    initial = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(redirect, redirected),
        timings=attempt_timings(),
    )
    retry_exchange = exchange(
        exchange_number=3,
        attempt_number=2,
        effective_request=effective_request,
        observed_response=response(status=200),
    )
    retried = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=RetryMetadata(
            trigger=RetryTrigger.CONNECT_FAILURE,
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        ),
        exchanges=(retry_exchange,),
        timings=attempt_timings(),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    execution = EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=1)),
        environment=effective_request.environment,
        step_owner=None,
        attempts=(initial, retried),
        timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
    )
    assert len(execution.attempts) == 2
    assert len(execution.attempts[0].exchanges) == 2

    with pytest.raises(ValueError, match="non-terminal exchange"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=3)),
            retry=None,
            exchanges=(redirected, retry_exchange),
            timings=attempt_timings(),
        )
    with pytest.raises(ValueError, match="only attempts after"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=2)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(retried,),
            timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
        )


def test_retry_delay_is_bound_to_fixed_exponential_and_jitter_policy() -> None:
    metadata = RetryMetadata(
        trigger=RetryTrigger.CONNECT_FAILURE,
        backoff=Duration(nanoseconds=5),
        jitter=Duration(nanoseconds=0),
    )
    zero = Ratio(numerator=0, denominator=PositiveCount(value=1))
    validate_retry_delay(FixedBackoff(delay=Duration(nanoseconds=5)), zero, 1, metadata)
    exponential = ExponentialBackoff(
        initial=Duration(nanoseconds=5),
        multiplier=Ratio(numerator=2, denominator=PositiveCount(value=1)),
        maximum=Duration(nanoseconds=8),
    )
    half = Ratio(numerator=1, denominator=PositiveCount(value=2))
    validate_retry_delay(
        exponential,
        half,
        2,
        msgspec.structs.replace(
            metadata,
            backoff=Duration(nanoseconds=8),
            jitter=Duration(nanoseconds=4),
        ),
    )
    with pytest.raises(ValueError, match="backoff"):
        validate_retry_delay(exponential, half, 2, metadata)
    with pytest.raises(ValueError, match="jitter"):
        validate_retry_delay(
            FixedBackoff(delay=Duration(nanoseconds=5)),
            half,
            1,
            msgspec.structs.replace(metadata, jitter=Duration(nanoseconds=3)),
        )


@pytest.mark.parametrize("status", [304, 305, 306])
def test_only_actual_redirect_statuses_can_be_followed(status: int) -> None:
    effective_request = request(redirect_hops=1)
    observed = response(
        status=status,
        body=(NoResponseBody(reason=NoBodyReason.STATUS_304) if status == 304 else None),
    )
    with pytest.raises(ValueError, match="redirect status"):
        exchange(
            exchange_number=status,
            effective_request=effective_request,
            observed_response=observed,
            redirect=followed_redirect(effective_request),
        )


def test_redirect_hops_and_policy_are_bound_to_each_attempt() -> None:
    one_hop = request(redirect_hops=1)
    first = exchange(
        exchange_number=60,
        effective_request=one_hop,
        observed_response=response(status=302),
        redirect=followed_redirect(one_hop),
    )
    second = exchange(
        exchange_number=61,
        effective_request=one_hop,
        observed_response=response(status=307),
        redirect=followed_redirect(one_hop),
    )
    terminal = exchange(
        exchange_number=62,
        effective_request=one_hop,
        observed_response=response(),
    )
    with pytest.raises(ValueError, match="redirect maximum"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=60)),
            retry=None,
            exchanges=(first, second, terminal),
            timings=attempt_timings(),
        )

    changed_policy_terminal = exchange(
        exchange_number=63,
        effective_request=request(redirect_hops=2),
        observed_response=response(),
    )
    with pytest.raises(ValueError, match="one redirect policy"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=61)),
            retry=None,
            exchanges=(first, changed_policy_terminal),
            timings=attempt_timings(),
        )


def test_retry_history_is_bound_to_observed_outcomes_and_effective_policy() -> None:
    effective_request = request(attempts=2)
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    timings = EnvironmentExecutionTimings(queue=metric, elapsed=metric)

    def attempt(
        number: int,
        observed_exchange: HttpExchange,
        trigger: RetryTrigger | None,
    ) -> HttpAttempt:
        retry = (
            RetryMetadata(
                trigger=trigger,
                backoff=Duration(nanoseconds=0),
                jitter=Duration(nanoseconds=0),
            )
            if trigger is not None
            else None
        )
        return HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=number)),
            retry=retry,
            exchanges=(observed_exchange,),
            timings=attempt_timings(),
        )

    connect_failure = exchange(
        exchange_number=1,
        effective_request=effective_request,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    success = exchange(
        exchange_number=2,
        effective_request=effective_request,
        observed_response=response(),
    )
    with pytest.raises(ValueError, match="preceding attempt outcome"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(
                attempt(1, connect_failure, None),
                attempt(2, success, RetryTrigger.TLS_FAILURE),
            ),
            timings=timings,
        )

    generic_headers_failure = exchange(
        exchange_number=6,
        attempt_number=1,
        effective_request=effective_request,
        observed_response=None,
        failure=failure(FailurePhase.RESPONSE_HEADERS),
    )
    with pytest.raises(ValueError, match="preceding attempt outcome"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(
                attempt(1, generic_headers_failure, None),
                attempt(2, success, RetryTrigger.RESPONSE_HEADERS_TIMEOUT),
            ),
            timings=timings,
        )

    server_error = exchange(
        exchange_number=3,
        attempt_number=1,
        effective_request=effective_request,
        observed_response=response(status=500),
    )
    with pytest.raises(ValueError, match="not enabled"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(
                attempt(1, server_error, None),
                attempt(2, success, RetryTrigger.STATUS_5XX),
            ),
            timings=timings,
        )

    not_retryable = exchange(
        exchange_number=4,
        attempt_number=1,
        effective_request=effective_request,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT, retryable=False),
    )
    with pytest.raises(ValueError, match="non-retryable"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(
                attempt(1, not_retryable, None),
                attempt(2, success, RetryTrigger.CONNECT_FAILURE),
            ),
            timings=timings,
        )

    second_failure = exchange(
        exchange_number=5,
        attempt_number=2,
        effective_request=effective_request,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    third_success = exchange(
        exchange_number=7,
        attempt_number=3,
        effective_request=effective_request,
        observed_response=response(),
    )
    with pytest.raises(ValueError, match="exceed"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(
                attempt(1, connect_failure, None),
                attempt(2, second_failure, RetryTrigger.CONNECT_FAILURE),
                attempt(3, third_success, RetryTrigger.CONNECT_FAILURE),
            ),
            timings=timings,
        )
