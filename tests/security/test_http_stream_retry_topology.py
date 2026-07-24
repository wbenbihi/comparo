"""Adversarial logical-stream traversal across reconnect and retry attempt edges."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest
from tests.core.domain.http.budget_factories import capture_exhaustion
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import authorization_denial
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_execution
from tests.core.domain.http.stream_factories import stream_timings
from tests.core.domain.http.stream_factories import view_handle

from comparo.core.domain.http.attempt_transitions import StreamReconnectTransition
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.stream_attempts import StreamConnectionFailureOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentResponseOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.streams import ConnectionSegment
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamRecordsHandle
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration


def _captured_response(*, status: int = 200) -> ResponseObservation:
    return response(
        status=status,
        body=ResponseEntityBody(
            capture=CompleteCapture(),
            handle=body_handle(size=4),
            encoded_byte_count=ByteCount(value=4),
        ),
    )


def _budget_exhausted_response() -> ResponseObservation:
    """Build a response whose canonical entity-byte exhaustion is its only budget truth."""
    handle = body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
    return response(
        body=ResponseEntityBody(
            capture=BudgetExceededCapture(
                exhaustion=capture_exhaustion(
                    handle=handle,
                    kind=CaptureBudgetKind.ENTITY_BYTES,
                    encoded_byte_count=ByteCount(value=4),
                )
            ),
            handle=handle,
            encoded_byte_count=ByteCount(value=4),
        )
    )


def _view(
    exchange_observation: HttpExchange,
    number: int,
    kind: ViewKind,
    *,
    completeness: ViewCompleteness = ViewCompleteness.FULL,
) -> DecodedViewHandle:
    observed = exchange_observation.response
    assert observed is not None
    assert type(observed.body) is ResponseEntityBody
    assert observed.body.handle is not None
    return view_handle(
        kind,
        completeness=completeness,
        source=observed.body.handle,
        view_number=number,
    )


def _execution(
    effective: EffectiveRequest,
    attempts: tuple[HttpAttempt, ...],
    views: tuple[DecodedViewHandle, ...],
) -> EnvironmentExecutionObservation:
    return EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=700)),
        environment=effective.environment,
        step_owner=None,
        attempts=attempts,
        timings=stream_execution(ViewKind.SSE).timings,
        views=views,
    )


def _records(owner: EnvironmentExecutionObservation, count: int) -> StreamRecordsHandle:
    return StreamRecordsHandle(
        owner=owner,
        kind=StreamKind.SSE,
        record_count=Count(value=count),
        control_count=Count(value=0),
        error_count=Count(value=0),
    )


def _connection_segment(
    owner: EnvironmentExecutionObservation,
    *,
    attempt_index: int,
    view_index: int,
    segment_index: int,
    end_reason: SegmentEndReason,
) -> ConnectionSegment:
    attempt = owner.attempts[attempt_index]
    return msgspec.structs.replace(
        segment(
            index=segment_index,
            first=segment_index,
            count=1,
            byte_start=segment_index * 4,
            byte_end=(segment_index + 1) * 4,
            end_reason=end_reason,
        ),
        attempt_id=attempt.attempt_id,
        exchange_id=attempt.exchanges[-1].exchange_id,
        view=owner.views[view_index],
    )


def _retry(trigger: RetryTrigger) -> RetryMetadata:
    return RetryMetadata(
        trigger=trigger,
        backoff=Duration(nanoseconds=0),
        jitter=Duration(nanoseconds=0),
    )


def _with_retry_condition(condition: RetryCondition) -> EffectiveRequest:
    effective = request(attempts=2)
    return msgspec.structs.replace(
        effective,
        policies=msgspec.structs.replace(
            effective.policies,
            retries=msgspec.structs.replace(
                effective.policies.retries,
                conditions=(condition,),
            ),
        ),
    )


def test_failed_redirected_reconnect_then_retry_preserves_logical_stream() -> None:
    initial = request(attempts=2, redirect_hops=1)
    redirected_url = msgspec.structs.replace(
        initial.url,
        path=UrlPath(segments=(DecodedPathSegment(value="stream"),)),
    )
    redirected = msgspec.structs.replace(initial, url=redirected_url)
    redirect = exchange(
        exchange_number=70,
        execution_number=700,
        attempt_number=70,
        effective_request=initial,
        observed_response=response(status=302),
        redirect=FollowedRedirect(
            raw_location=HeaderText(value="/stream"),
            resolved_url=redirected_url,
        ),
    )
    first_stream = exchange(
        exchange_number=71,
        execution_number=700,
        attempt_number=70,
        effective_request=redirected,
        observed_response=_captured_response(),
    )
    reconnect_failure = exchange(
        exchange_number=72,
        execution_number=700,
        attempt_number=71,
        effective_request=redirected,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    retried_stream = exchange(
        exchange_number=73,
        execution_number=700,
        attempt_number=72,
        effective_request=redirected,
        observed_response=_captured_response(),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=70)),
        retry=None,
        exchanges=(redirect, first_stream),
        timings=attempt_timings(),
    )
    reconnect = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=71)),
        retry=None,
        exchanges=(reconnect_failure,),
        timings=attempt_timings(),
        stream_reconnect=StreamReconnectTransition(
            previous_attempt_id=first.attempt_id,
            previous_exchange_id=first_stream.exchange_id,
            previous_stream_view_id=ViewId(value=UUID(int=701)),
        ),
    )
    retried = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=72)),
        retry=_retry(RetryTrigger.CONNECT_FAILURE),
        exchanges=(retried_stream,),
        timings=attempt_timings(),
    )
    owner = _execution(
        initial,
        (first, reconnect, retried),
        (
            _view(first_stream, 701, ViewKind.SSE),
            _view(retried_stream, 703, ViewKind.SSE),
        ),
    )
    observed_failure = cast(TransportFailure, reconnect_failure.failure)
    observation = StreamObservation(
        records=_records(owner, 2),
        segments=(
            _connection_segment(
                owner,
                attempt_index=0,
                view_index=0,
                segment_index=0,
                end_reason=SegmentEndReason.RECONNECT,
            ),
            _connection_segment(
                owner,
                attempt_index=2,
                view_index=1,
                segment_index=1,
                end_reason=SegmentEndReason.REMOTE_EOF,
            ),
        ),
        captured_bytes=ByteCount(value=8),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
        non_segment_attempts=(
            StreamConnectionFailureOccurrence(
                attempt_id=reconnect.attempt_id,
                exchange_id=reconnect_failure.exchange_id,
                failure=observed_failure,
            ),
        ),
    )
    assert tuple(segment.attempt_id for segment in observation.segments) == (
        first.attempt_id,
        retried.attempt_id,
    )


def test_failed_reconnect_cannot_be_relabelled_as_another_budget_free_reconnect() -> None:
    effective = request(attempts=1)
    first_stream = exchange(
        exchange_number=74,
        execution_number=700,
        attempt_number=74,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    failed_reconnect = exchange(
        exchange_number=75,
        execution_number=700,
        attempt_number=75,
        effective_request=effective,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    relabelled_success = exchange(
        exchange_number=76,
        execution_number=700,
        attempt_number=76,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=74)),
        retry=None,
        exchanges=(first_stream,),
        timings=attempt_timings(),
    )
    failed = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=75)),
        retry=None,
        exchanges=(failed_reconnect,),
        timings=attempt_timings(),
        stream_reconnect=StreamReconnectTransition(
            previous_attempt_id=first.attempt_id,
            previous_exchange_id=first_stream.exchange_id,
            previous_stream_view_id=ViewId(value=UUID(int=741)),
        ),
    )
    relabelled = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=76)),
        retry=None,
        exchanges=(relabelled_success,),
        timings=attempt_timings(),
        stream_reconnect=StreamReconnectTransition(
            previous_attempt_id=failed.attempt_id,
            previous_exchange_id=failed_reconnect.exchange_id,
            previous_stream_view_id=ViewId(value=UUID(int=741)),
        ),
    )
    with pytest.raises(ValueError, match="exact captured streaming view"):
        _execution(
            effective,
            (first, failed, relabelled),
            (_view(first_stream, 741, ViewKind.SSE),),
        )


def test_non_stream_503_response_between_stream_connections_is_explicit() -> None:
    effective = _with_retry_condition(RetryCondition.STATUS_5XX)
    first_stream = exchange(
        exchange_number=80,
        execution_number=700,
        attempt_number=80,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    unavailable = exchange(
        exchange_number=81,
        execution_number=700,
        attempt_number=81,
        effective_request=effective,
        observed_response=_captured_response(status=503),
    )
    retried_stream = exchange(
        exchange_number=82,
        execution_number=700,
        attempt_number=82,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=80)),
        retry=None,
        exchanges=(first_stream,),
        timings=attempt_timings(),
    )
    reconnect = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=81)),
        retry=None,
        exchanges=(unavailable,),
        timings=attempt_timings(),
        stream_reconnect=StreamReconnectTransition(
            previous_attempt_id=first.attempt_id,
            previous_exchange_id=first_stream.exchange_id,
            previous_stream_view_id=ViewId(value=UUID(int=801)),
        ),
    )
    retried = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=82)),
        retry=_retry(RetryTrigger.STATUS_5XX),
        exchanges=(retried_stream,),
        timings=attempt_timings(),
    )
    unavailable_response = cast(ResponseObservation, unavailable.response)
    owner = _execution(
        effective,
        (first, reconnect, retried),
        (
            _view(first_stream, 801, ViewKind.SSE),
            _view(unavailable, 802, ViewKind.JSON),
            _view(retried_stream, 803, ViewKind.SSE),
        ),
    )
    observation = StreamObservation(
        records=_records(owner, 2),
        segments=(
            _connection_segment(
                owner,
                attempt_index=0,
                view_index=0,
                segment_index=0,
                end_reason=SegmentEndReason.RECONNECT,
            ),
            _connection_segment(
                owner,
                attempt_index=2,
                view_index=2,
                segment_index=1,
                end_reason=SegmentEndReason.REMOTE_EOF,
            ),
        ),
        captured_bytes=ByteCount(value=8),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
        non_segment_attempts=(
            StreamNonSegmentResponseOccurrence(
                attempt_id=reconnect.attempt_id,
                exchange_id=unavailable.exchange_id,
                response=unavailable_response,
                failure=None,
            ),
        ),
    )
    assert len(observation.non_segment_attempts) == 1


def test_sse_500_segment_uses_remote_eof_before_status_retry() -> None:
    effective = _with_retry_condition(RetryCondition.STATUS_5XX)
    server_error = exchange(
        exchange_number=90,
        execution_number=700,
        attempt_number=90,
        effective_request=effective,
        observed_response=_captured_response(status=500),
    )
    success = exchange(
        exchange_number=91,
        execution_number=700,
        attempt_number=91,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=90)),
        retry=None,
        exchanges=(server_error,),
        timings=attempt_timings(),
    )
    retried = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=91)),
        retry=_retry(RetryTrigger.STATUS_5XX),
        exchanges=(success,),
        timings=attempt_timings(),
    )
    owner = _execution(
        effective,
        (first, retried),
        (
            _view(server_error, 901, ViewKind.SSE),
            _view(success, 902, ViewKind.SSE),
        ),
    )
    first_segment = _connection_segment(
        owner,
        attempt_index=0,
        view_index=0,
        segment_index=0,
        end_reason=SegmentEndReason.REMOTE_EOF,
    )
    extent = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )
    with pytest.raises(ValueError, match="every stream owner attempt"):
        StreamObservation(
            records=_records(owner, 1),
            segments=(first_segment,),
            captured_bytes=ByteCount(value=4),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    complete = StreamObservation(
        records=_records(owner, 2),
        segments=(
            first_segment,
            _connection_segment(
                owner,
                attempt_index=1,
                view_index=1,
                segment_index=1,
                end_reason=SegmentEndReason.REMOTE_EOF,
            ),
        ),
        captured_bytes=ByteCount(value=8),
        extent=extent,
        trailing_incomplete=(),
        timings=stream_timings(),
    )
    assert complete.segments[0].end_reason is SegmentEndReason.REMOTE_EOF


def test_captured_idle_failure_segment_uses_failure_before_retry() -> None:
    effective = _with_retry_condition(RetryCondition.IDLE_TIMEOUT)
    interrupted = exchange(
        exchange_number=95,
        execution_number=700,
        attempt_number=95,
        effective_request=effective,
        observed_response=response(
            body=ResponseEntityBody(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.IDLE_TIMEOUT),
                handle=body_handle(size=4, extent=BodyCaptureExtent.PARTIAL),
                encoded_byte_count=ByteCount(value=4),
            ),
        ),
        failure=failure(FailurePhase.IDLE_TIMEOUT),
    )
    success = exchange(
        exchange_number=96,
        execution_number=700,
        attempt_number=96,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=95)),
        retry=None,
        exchanges=(interrupted,),
        timings=attempt_timings(),
    )
    retried = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=96)),
        retry=_retry(RetryTrigger.IDLE_TIMEOUT),
        exchanges=(success,),
        timings=attempt_timings(),
    )
    owner = _execution(
        effective,
        (first, retried),
        (
            _view(
                interrupted,
                951,
                ViewKind.SSE,
                completeness=ViewCompleteness.PREFIX,
            ),
            _view(success, 961, ViewKind.SSE),
        ),
    )
    observation = StreamObservation(
        records=_records(owner, 2),
        segments=(
            _connection_segment(
                owner,
                attempt_index=0,
                view_index=0,
                segment_index=0,
                end_reason=SegmentEndReason.FAILURE,
            ),
            _connection_segment(
                owner,
                attempt_index=1,
                view_index=1,
                segment_index=1,
                end_reason=SegmentEndReason.REMOTE_EOF,
            ),
        ),
        captured_bytes=ByteCount(value=8),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
    )
    assert observation.segments[0].end_reason is SegmentEndReason.FAILURE


@pytest.mark.parametrize(
    ("terminal_response", "terminal_failure", "termination"),
    [
        pytest.param(
            _captured_response(status=503),
            None,
            StreamTerminationReason.RECONNECT_EXHAUSTED,
            id="status-exhaustion",
        ),
        pytest.param(
            _captured_response(),
            None,
            StreamTerminationReason.DECODE_FAILURE,
            id="successful-non-stream-response",
        ),
        pytest.param(
            response(
                body=ResponseEntityBody(
                    capture=BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
                    handle=None,
                    encoded_byte_count=None,
                ),
            ),
            failure(FailurePhase.BODY_READ),
            StreamTerminationReason.TRANSPORT_FAILURE,
            id="body-read-failure",
        ),
        pytest.param(
            response(
                body=ResponseEntityBody(
                    capture=ContentDecodingFailedCapture(),
                    handle=None,
                    encoded_byte_count=ByteCount(value=4),
                ),
            ),
            failure(FailurePhase.CONTENT_DECODING),
            StreamTerminationReason.DECODE_FAILURE,
            id="content-decoding-failure",
        ),
        pytest.param(
            _budget_exhausted_response(),
            failure(FailurePhase.RESOURCE_BUDGET),
            StreamTerminationReason.RESOURCE_BUDGET,
            id="body-budget",
        ),
        pytest.param(
            response(
                body=ResponseEntityBody(
                    capture=StorageFailedCapture(),
                    handle=None,
                    encoded_byte_count=ByteCount(value=4),
                ),
            ),
            failure(FailurePhase.STORAGE),
            StreamTerminationReason.STORAGE_FAILURE,
            id="storage-failure",
        ),
    ],
)
def test_terminal_non_segment_responses_preserve_outcome_taxonomy(
    terminal_response: ResponseObservation,
    terminal_failure: TransportFailure | None,
    termination: StreamTerminationReason,
) -> None:
    effective = request()
    first_stream = exchange(
        exchange_number=97,
        execution_number=700,
        attempt_number=97,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    terminal = exchange(
        exchange_number=98,
        execution_number=700,
        attempt_number=98,
        effective_request=effective,
        observed_response=terminal_response,
        failure=terminal_failure,
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=97)),
        retry=None,
        exchanges=(first_stream,),
        timings=attempt_timings(),
    )
    terminal_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=98)),
        retry=None,
        exchanges=(terminal,),
        timings=attempt_timings(),
        stream_reconnect=StreamReconnectTransition(
            previous_attempt_id=first.attempt_id,
            previous_exchange_id=first_stream.exchange_id,
            previous_stream_view_id=ViewId(value=UUID(int=971)),
        ),
    )
    owner = _execution(
        effective,
        (first, terminal_attempt),
        (_view(first_stream, 971, ViewKind.SSE),),
    )
    occurrence = StreamNonSegmentResponseOccurrence(
        attempt_id=terminal_attempt.attempt_id,
        exchange_id=terminal.exchange_id,
        response=cast(ResponseObservation, terminal.response),
        failure=terminal.failure,
    )
    observation = StreamObservation(
        records=_records(owner, 1),
        segments=(
            _connection_segment(
                owner,
                attempt_index=0,
                view_index=0,
                segment_index=0,
                end_reason=SegmentEndReason.RECONNECT,
            ),
        ),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=termination,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
        non_segment_attempts=(occurrence,),
        termination_evidence=None,
    )
    assert observation.extent.termination is termination
    wrong_termination = (
        StreamTerminationReason.DECODE_FAILURE
        if termination is StreamTerminationReason.RECONNECT_EXHAUSTED
        else StreamTerminationReason.RECONNECT_EXHAUSTED
    )
    with pytest.raises(ValueError, match="exact logical extent"):
        msgspec.structs.replace(
            observation,
            extent=StreamExtent(
                completeness=StreamCompleteness.INCOMPLETE,
                termination=wrong_termination,
            ),
        )


@pytest.mark.parametrize(
    ("phase", "termination"),
    [
        (FailurePhase.CONNECT, StreamTerminationReason.RECONNECT_EXHAUSTED),
        (FailurePhase.CANCELLATION, StreamTerminationReason.CANCELLED),
        (FailurePhase.POLICY, StreamTerminationReason.POLICY_BLOCKED),
    ],
)
def test_terminal_non_segment_failures_determine_stream_extent(
    phase: FailurePhase,
    termination: StreamTerminationReason,
) -> None:
    effective = request()
    first_stream = exchange(
        exchange_number=100,
        execution_number=700,
        attempt_number=100,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=100)),
        retry=None,
        exchanges=(first_stream,),
        timings=attempt_timings(),
    )
    reconnect = StreamReconnectTransition(
        previous_attempt_id=first.attempt_id,
        previous_exchange_id=first_stream.exchange_id,
        previous_stream_view_id=ViewId(value=UUID(int=1001)),
    )
    occurrence_fact: StreamPreSendOccurrence | StreamConnectionFailureOccurrence
    if phase is FailurePhase.POLICY:
        denied = authorization_denial(
            effective,
            request_occurrence=occurrence(execution=700, attempt=101, exchange=101),
        )
        failed_attempt = HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=101)),
            retry=None,
            exchanges=(),
            timings=attempt_timings(),
            stream_reconnect=reconnect,
            pre_send=denied,
        )
        occurrence_fact = StreamPreSendOccurrence(
            attempt_id=failed_attempt.attempt_id,
            outcome=denied,
        )
    else:
        failed_exchange = exchange(
            exchange_number=101,
            execution_number=700,
            attempt_number=101,
            effective_request=effective,
            observed_response=None,
            failure=failure(phase),
        )
        failed_attempt = HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=101)),
            retry=None,
            exchanges=(failed_exchange,),
            timings=attempt_timings(),
            stream_reconnect=reconnect,
        )
        occurrence_fact = StreamConnectionFailureOccurrence(
            attempt_id=failed_attempt.attempt_id,
            exchange_id=failed_exchange.exchange_id,
            failure=cast(TransportFailure, failed_exchange.failure),
        )
    owner = _execution(
        effective,
        (first, failed_attempt),
        (_view(first_stream, 1001, ViewKind.SSE),),
    )
    observation = StreamObservation(
        records=_records(owner, 1),
        segments=(
            _connection_segment(
                owner,
                attempt_index=0,
                view_index=0,
                segment_index=0,
                end_reason=SegmentEndReason.RECONNECT,
            ),
        ),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=termination,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
        non_segment_attempts=(occurrence_fact,),
    )
    assert observation.non_segment_attempts == (occurrence_fact,)
    if phase is FailurePhase.CONNECT:
        with pytest.raises(ValueError, match="exact logical extent"):
            msgspec.structs.replace(
                observation,
                extent=StreamExtent(
                    completeness=StreamCompleteness.FULL,
                    termination=StreamTerminationReason.REMOTE_EOF,
                ),
            )


def test_partial_response_cancellation_remains_cancelled_not_reconnect_exhausted() -> None:
    effective = request()
    first_stream = exchange(
        exchange_number=110,
        execution_number=700,
        attempt_number=110,
        effective_request=effective,
        observed_response=_captured_response(),
    )
    cancelled = exchange(
        exchange_number=111,
        execution_number=700,
        attempt_number=111,
        effective_request=effective,
        observed_response=response(
            body=ResponseEntityBody(
                capture=CancelledCapture(),
                handle=body_handle(size=4, extent=BodyCaptureExtent.PARTIAL),
                encoded_byte_count=ByteCount(value=4),
            ),
        ),
        failure=failure(FailurePhase.CANCELLATION),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=110)),
        retry=None,
        exchanges=(first_stream,),
        timings=attempt_timings(),
    )
    cancelled_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=111)),
        retry=None,
        exchanges=(cancelled,),
        timings=attempt_timings(),
        stream_reconnect=StreamReconnectTransition(
            previous_attempt_id=first.attempt_id,
            previous_exchange_id=first_stream.exchange_id,
            previous_stream_view_id=ViewId(value=UUID(int=1101)),
        ),
    )
    cancelled_response = cast(ResponseObservation, cancelled.response)
    cancelled_failure = cast(TransportFailure, cancelled.failure)
    owner = _execution(
        effective,
        (first, cancelled_attempt),
        (
            _view(first_stream, 1101, ViewKind.SSE),
            _view(
                cancelled,
                1102,
                ViewKind.TEXT,
                completeness=ViewCompleteness.PREFIX,
            ),
        ),
    )
    observation = StreamObservation(
        records=_records(owner, 1),
        segments=(
            _connection_segment(
                owner,
                attempt_index=0,
                view_index=0,
                segment_index=0,
                end_reason=SegmentEndReason.RECONNECT,
            ),
        ),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.CANCELLED,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
        non_segment_attempts=(
            StreamNonSegmentResponseOccurrence(
                attempt_id=cancelled_attempt.attempt_id,
                exchange_id=cancelled.exchange_id,
                response=cancelled_response,
                failure=cancelled_failure,
            ),
        ),
    )
    assert observation.extent.termination is StreamTerminationReason.CANCELLED
