"""Tests for response capture, transport metadata, and observation hierarchy semantics."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import Duration
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import multipart_body
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import followed_redirect
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import view_handle


def test_execution_history_retains_logical_plan_and_policy_fingerprints() -> None:
    effective_request = request(redirect_hops=1)
    redirected = exchange(
        exchange_number=40,
        execution_number=40,
        attempt_number=40,
        effective_request=effective_request,
        observed_response=response(status=302),
        redirect=followed_redirect(effective_request),
    )
    terminal = exchange(
        exchange_number=41,
        execution_number=40,
        attempt_number=40,
        effective_request=effective_request,
        observed_response=response(),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)

    def observe(second: HttpExchange) -> EnvironmentExecutionObservation:
        return EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=40)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(
                HttpAttempt(
                    attempt_id=AttemptId(value=UUID(int=40)),
                    retry=None,
                    exchanges=(redirected, second),
                    timings=attempt_timings(),
                ),
            ),
            timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
        )

    changed_logical_request = msgspec.structs.replace(
        effective_request,
        logical_request_fingerprint=fingerprint("b"),
    )
    with pytest.raises(ValueError, match="logical-request fingerprint"):
        observe(
            exchange(
                exchange_number=42,
                execution_number=40,
                attempt_number=40,
                effective_request=changed_logical_request,
                observed_response=response(),
            )
        )

    intent = terminal.request.authorization.intent
    changed_plan_intent = msgspec.structs.replace(intent, plan_fingerprint=fingerprint("b"))
    changed_plan = msgspec.structs.replace(
        terminal,
        request=AuthorizedExchangeRequest(
            authorization=msgspec.structs.replace(
                terminal.request.authorization,
                intent=changed_plan_intent,
            )
        ),
    )
    with pytest.raises(ValueError, match="run-plan fingerprint"):
        observe(changed_plan)

    changed_policy_intent = msgspec.structs.replace(
        intent,
        effective_policy_fingerprint=fingerprint("b"),
    )
    with pytest.raises(ValueError, match="effective-policy fingerprint"):
        AuthorizedExchangeRequest(
            authorization=msgspec.structs.replace(
                terminal.request.authorization,
                intent=changed_policy_intent,
            )
        )


def test_exchange_attempt_and_execution_components_validate_direct_construction() -> None:
    effective_request = request(attempts=2)
    observed_response = response()
    timing = exchange_timings()
    with pytest.raises(TypeError, match="raw Location"):
        FollowedRedirect(
            raw_location=cast("HeaderText", "location"),
            resolved_url=effective_request.url,
        )
    with pytest.raises(TypeError, match="resolved Location"):
        FollowedRedirect(
            raw_location=HeaderText(value="/next"),
            resolved_url=cast("AbsoluteHttpUrl", "url"),
        )
    with pytest.raises(ValueError, match="response, a failure"):
        exchange(
            exchange_number=1,
            effective_request=effective_request,
            observed_response=None,
        )
    with pytest.raises(ValueError, match="redirect status"):
        exchange(
            exchange_number=2,
            effective_request=effective_request,
            observed_response=observed_response,
            redirect=followed_redirect(effective_request),
        )
    marker = followed_redirect(effective_request)
    with pytest.raises(ValueError, match="exactly one Location"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=80)),
            request=authorized_request(effective_request),
            response=response(status=302),
            failure=None,
            redirect=marker,
            timings=timing,
        )
    wrong_location = msgspec.structs.replace(
        response(status=302),
        headers=ResponseFields(
            fields=(
                ResponseField(
                    name=HeaderName(value="location"),
                    value=HeaderText(value="/different"),
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="exact observed Location"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=81)),
            request=authorized_request(effective_request),
            response=wrong_location,
            failure=None,
            redirect=marker,
            timings=timing,
        )
    with pytest.raises(ValueError, match="failed exchange"):
        exchange(
            exchange_number=3,
            effective_request=effective_request,
            observed_response=response(status=302),
            failure=failure(),
            redirect=followed_redirect(effective_request),
        )
    with pytest.raises(TypeError, match="exchange ID"):
        HttpExchange(
            exchange_id=cast("ExchangeId", UUID(int=1)),
            request=authorized_request(effective_request),
            response=observed_response,
            failure=None,
            redirect=None,
            timings=timing,
        )
    with pytest.raises(TypeError, match="exchange request"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=1)),
            request=cast("AuthorizedExchangeRequest", "request"),
            response=observed_response,
            failure=None,
            redirect=None,
            timings=timing,
        )
    with pytest.raises(TypeError, match="retry trigger"):
        RetryMetadata(
            trigger=cast("RetryTrigger", "status-5xx"),
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        )
    with pytest.raises(TypeError, match="backoff and jitter"):
        RetryMetadata(
            trigger=RetryTrigger.STATUS_5XX,
            backoff=cast("Duration", 0),
            jitter=Duration(nanoseconds=0),
        )

    valid_exchange = exchange(
        exchange_number=4,
        effective_request=effective_request,
        observed_response=observed_response,
    )
    with pytest.raises(TypeError, match="attempt ID"):
        HttpAttempt(
            attempt_id=cast("AttemptId", UUID(int=1)),
            retry=None,
            exchanges=(valid_exchange,),
            timings=attempt_timings(),
        )
    with pytest.raises(ValueError, match="either exchanges or one pre-send outcome"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=1)),
            retry=None,
            exchanges=(),
            timings=attempt_timings(),
        )
    with pytest.raises(ValueError, match="terminal exchange"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=1)),
            retry=None,
            exchanges=(
                exchange(
                    exchange_number=5,
                    effective_request=effective_request,
                    observed_response=response(status=302),
                    redirect=followed_redirect(effective_request),
                ),
            ),
            timings=attempt_timings(),
        )


def test_execution_rejects_wrong_ownership_environment_and_occurrence_identities() -> None:
    effective_request = request(attempts=2)
    observed_exchange = exchange(
        exchange_number=1,
        effective_request=effective_request,
        observed_response=response(),
    )
    attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(observed_exchange,),
        timings=attempt_timings(),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    timings = EnvironmentExecutionTimings(queue=metric, elapsed=metric)
    with pytest.raises(TypeError, match="observation ID"):
        EnvironmentExecutionObservation(
            execution_id=cast("EnvironmentExecutionId", UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(attempt,),
            timings=timings,
        )

    with pytest.raises(ValueError, match="own this exchange"):
        msgspec.structs.replace(
            observed_exchange,
            exchange_id=ExchangeId(value=UUID(int=2)),
        )
    with pytest.raises(ValueError, match="own this attempt"):
        msgspec.structs.replace(
            attempt,
            attempt_id=AttemptId(value=UUID(int=2)),
        )
    with pytest.raises(ValueError, match="own this execution"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=2)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(attempt,),
            timings=timings,
        )
    with pytest.raises(ValueError, match="Scenario step"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=StepExecutionId(value=UUID(int=2)),
            attempts=(attempt,),
            timings=timings,
        )


def test_redirect_cannot_span_full_execution_scopes_with_the_same_execution_id() -> None:
    """Reject replay across run/cell/trial scope even when flattened IDs match."""
    effective = request(redirect_hops=1)
    first = exchange(
        exchange_number=100,
        execution_number=100,
        attempt_number=100,
        effective_request=effective,
        observed_response=response(status=302),
        redirect=followed_redirect(effective),
    )
    second = exchange(
        exchange_number=101,
        execution_number=100,
        attempt_number=100,
        effective_request=effective,
        observed_response=response(),
    )
    occurrence = second.request.occurrence
    foreign_execution = msgspec.structs.replace(
        occurrence.attempt.execution,
        trial=msgspec.structs.replace(
            occurrence.attempt.execution.trial,
            trial_id=TrialId(value=UUID(int=999)),
        ),
    )
    foreign_occurrence = msgspec.structs.replace(
        occurrence,
        attempt=msgspec.structs.replace(occurrence.attempt, execution=foreign_execution),
    )
    foreign_intent = msgspec.structs.replace(
        second.request.authorization.intent,
        occurrence=foreign_occurrence,
    )
    foreign_second = msgspec.structs.replace(
        second,
        request=AuthorizedExchangeRequest(
            authorization=msgspec.structs.replace(
                second.request.authorization,
                intent=foreign_intent,
            )
        ),
    )
    with pytest.raises(ValueError, match="execution or attempt occurrence"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=100)),
            retry=None,
            exchanges=(first, foreign_second),
            timings=attempt_timings(),
        )

    retry_request = request(attempts=2)
    failed = exchange(
        exchange_number=200,
        execution_number=200,
        attempt_number=1,
        effective_request=retry_request,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    succeeded = exchange(
        exchange_number=201,
        execution_number=200,
        attempt_number=2,
        effective_request=retry_request,
        observed_response=response(),
    )
    succeeded_occurrence = succeeded.request.occurrence
    foreign_execution = msgspec.structs.replace(
        succeeded_occurrence.attempt.execution,
        trial=msgspec.structs.replace(
            succeeded_occurrence.attempt.execution.trial,
            trial_id=TrialId(value=UUID(int=998)),
        ),
    )
    foreign_intent = msgspec.structs.replace(
        succeeded.request.authorization.intent,
        occurrence=msgspec.structs.replace(
            succeeded_occurrence,
            attempt=msgspec.structs.replace(
                succeeded_occurrence.attempt,
                execution=foreign_execution,
            ),
        ),
    )
    succeeded = msgspec.structs.replace(
        succeeded,
        request=AuthorizedExchangeRequest(
            authorization=msgspec.structs.replace(
                succeeded.request.authorization,
                intent=foreign_intent,
            )
        ),
    )
    first_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(failed,),
        timings=attempt_timings(),
    )
    second_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=RetryMetadata(
            trigger=RetryTrigger.CONNECT_FAILURE,
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        ),
        exchanges=(succeeded,),
        timings=attempt_timings(),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    with pytest.raises(ValueError, match="span execution scopes"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=200)),
            environment=retry_request.environment,
            step_owner=None,
            attempts=(first_attempt, second_attempt),
            timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
        )


def test_execution_storage_lineage_rejects_duplicate_or_unbound_artifacts() -> None:
    effective = request()
    observed_exchange = exchange(
        exchange_number=700,
        execution_number=700,
        attempt_number=700,
        effective_request=effective,
        observed_response=response(),
    )
    assert observed_exchange.response is not None
    assert type(observed_exchange.response.body) is ResponseEntityBody
    assert observed_exchange.response.body.handle is not None
    attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=700)),
        retry=None,
        exchanges=(observed_exchange,),
        timings=attempt_timings(),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    timings = EnvironmentExecutionTimings(queue=metric, elapsed=metric)

    exact_view = msgspec.structs.replace(
        view_handle(),
        source=observed_exchange.response.body.handle,
    )
    valid = EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=700)),
        environment=effective.environment,
        step_owner=None,
        attempts=(attempt,),
        timings=timings,
        views=(exact_view,),
    )
    assert valid.views == (exact_view,)
    with pytest.raises(TypeError, match="execution views"):
        msgspec.structs.replace(
            valid,
            views=cast("tuple[DecodedViewHandle, ...]", [exact_view]),
        )
    with pytest.raises(ValueError, match="view IDs must be unique"):
        msgspec.structs.replace(valid, views=(exact_view, exact_view))
    with pytest.raises(ValueError, match="exact request or response body"):
        msgspec.structs.replace(valid, views=(view_handle(),))

    redirected_request = request(redirect_hops=1)
    first = exchange(
        exchange_number=701,
        execution_number=701,
        attempt_number=701,
        effective_request=redirected_request,
        observed_response=response(status=302),
        redirect=followed_redirect(redirected_request),
    )
    second = exchange(
        exchange_number=702,
        execution_number=701,
        attempt_number=701,
        effective_request=redirected_request,
        observed_response=response(),
    )
    assert first.response is not None
    assert second.response is not None
    assert type(first.response.body) is ResponseEntityBody
    assert type(second.response.body) is ResponseEntityBody
    assert first.response.body.handle is not None
    assert second.response.body.handle is not None
    duplicate_handle = msgspec.structs.replace(
        second.response.body.handle,
        body_id=first.response.body.handle.body_id,
    )
    second = msgspec.structs.replace(
        second,
        response=msgspec.structs.replace(
            second.response,
            body=msgspec.structs.replace(second.response.body, handle=duplicate_handle),
        ),
    )
    duplicate_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=701)),
        retry=None,
        exchanges=(first, second),
        timings=attempt_timings(),
    )
    with pytest.raises(ValueError, match="body IDs must be unique"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=701)),
            environment=redirected_request.environment,
            step_owner=None,
            attempts=(duplicate_attempt,),
            timings=timings,
        )


def test_execution_views_admit_exact_materialized_request_snapshots() -> None:
    effective = request()
    materialized, _ = multipart_body()
    materialized = msgspec.structs.replace(materialized, multipart=None)
    effective = msgspec.structs.replace(
        effective,
        body=BytesBody(value=materialized.source),
    )
    request_occurrence = occurrence(execution=800, attempt=800, exchange=800)
    observed_exchange = HttpExchange(
        exchange_id=request_occurrence.exchange_id,
        request=authorized_request(
            effective,
            request_occurrence=request_occurrence,
            body=materialized,
        ),
        response=response(),
        failure=None,
        redirect=None,
        timings=exchange_timings(),
    )
    attempt = HttpAttempt(
        attempt_id=request_occurrence.attempt_id,
        retry=None,
        exchanges=(observed_exchange,),
        timings=attempt_timings(),
    )
    request_view = view_handle(source=materialized.source, view_number=800)
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    timings = EnvironmentExecutionTimings(queue=metric, elapsed=metric)
    observed = EnvironmentExecutionObservation(
        execution_id=request_occurrence.execution_id,
        environment=effective.environment,
        step_owner=None,
        attempts=(attempt,),
        timings=timings,
        views=(request_view,),
    )
    assert observed.views == (request_view,)

    observed_response = response()
    assert type(observed_response.body) is ResponseEntityBody
    assert observed_response.body.handle is not None
    colliding_response = msgspec.structs.replace(
        observed_response,
        body=msgspec.structs.replace(
            observed_response.body,
            handle=msgspec.structs.replace(
                observed_response.body.handle,
                body_id=materialized.source.source_id,
            ),
        ),
    )
    colliding_exchange = msgspec.structs.replace(
        observed_exchange,
        response=colliding_response,
    )
    with pytest.raises(ValueError, match="request and response body identities"):
        msgspec.structs.replace(
            observed,
            attempts=(
                msgspec.structs.replace(
                    attempt,
                    exchanges=(colliding_exchange,),
                ),
            ),
            views=(),
        )

    foreign_materialized, _ = multipart_body()
    foreign_materialized = msgspec.structs.replace(foreign_materialized, multipart=None)
    with pytest.raises(ValueError, match="exact request or response body"):
        msgspec.structs.replace(
            observed,
            views=(view_handle(source=foreign_materialized.source, view_number=801),),
        )
    effective_request = request(attempts=2)
    with pytest.raises(ValueError, match="identify an Environment"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=definition(ResourceKind.REQUEST, "request"),
            step_owner=None,
            attempts=(attempt,),
            timings=timings,
        )
    with pytest.raises(TypeError, match="step owner"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=cast("StepExecutionId", UUID(int=2)),
            attempts=(attempt,),
            timings=timings,
        )
    with pytest.raises(ValueError, match="at least one HTTP attempt"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=StepExecutionId(value=UUID(int=2)),
            attempts=(),
            timings=timings,
        )

    other_request = request(attempts=2, environment_id="production")
    mismatched = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=None,
        exchanges=(
            exchange(
                exchange_number=2,
                effective_request=other_request,
                observed_response=response(),
            ),
        ),
        timings=attempt_timings(),
    )
    with pytest.raises(ValueError, match="observed Environment"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(mismatched,),
            timings=timings,
        )
