"""Branch-complete validation tests for typed non-segment stream attempt paths."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.stream_attempts import StreamConnectionFailureOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentResponseOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.stream_owner_validation import _resolve_segment_exchange
from comparo.core.domain.http.stream_owner_validation import _validate_capture_binding
from comparo.core.domain.http.stream_owner_validation import _validate_non_segment_occurrence
from comparo.core.domain.http.stream_owner_validation import _validate_non_segment_outgoing
from comparo.core.domain.http.stream_owner_validation import _validate_outgoing_segment_end
from comparo.core.domain.http.stream_owner_validation import _validate_projected_order
from comparo.core.domain.http.stream_owner_validation import validate_stream_owner
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import Duration
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_denial
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_execution


def _response_occurrence(
    attempt: HttpAttempt,
) -> StreamNonSegmentResponseOccurrence:
    terminal = attempt.exchanges[-1]
    return StreamNonSegmentResponseOccurrence(
        attempt_id=attempt.attempt_id,
        exchange_id=terminal.exchange_id,
        response=cast(ResponseObservation, terminal.response),
        failure=terminal.failure,
    )


def _failed_attempt() -> tuple[HttpAttempt, StreamConnectionFailureOccurrence]:
    failed_exchange = exchange(
        exchange_number=300,
        effective_request=request(),
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=300)),
        retry=None,
        exchanges=(failed_exchange,),
        timings=attempt_timings(),
    )
    return attempt, StreamConnectionFailureOccurrence(
        attempt_id=attempt.attempt_id,
        exchange_id=failed_exchange.exchange_id,
        failure=cast(TransportFailure, failed_exchange.failure),
    )


def test_non_segment_occurrence_constructors_reject_untyped_fields() -> None:
    _, failed = _failed_attempt()
    with pytest.raises(TypeError, match="typed HTTP occurrence IDs"):
        msgspec.structs.replace(failed, attempt_id=cast("AttemptId", "attempt"))
    with pytest.raises(TypeError, match="requires TransportFailure"):
        msgspec.structs.replace(failed, failure=cast("TransportFailure", "failure"))

    response_occurrence = _response_occurrence(stream_execution(ViewKind.SSE).attempts[0])
    with pytest.raises(TypeError, match="typed HTTP occurrence IDs"):
        msgspec.structs.replace(
            response_occurrence,
            exchange_id=cast("ExchangeId", "exchange"),
        )
    with pytest.raises(TypeError, match="requires ResponseObservation"):
        msgspec.structs.replace(
            response_occurrence,
            response=cast("ResponseObservation", "response"),
        )
    with pytest.raises(TypeError, match="failure must be TransportFailure"):
        msgspec.structs.replace(
            response_occurrence,
            failure=cast("TransportFailure", "failure"),
        )

    denied = resolution_denial(
        request(),
        request_occurrence=occurrence(attempt=302, exchange=302),
    )
    pre_send = StreamPreSendOccurrence(
        attempt_id=denied.occurrence.attempt_id,
        outcome=denied,
    )
    with pytest.raises(TypeError, match="requires AttemptId"):
        msgspec.structs.replace(pre_send, attempt_id=cast("AttemptId", "attempt"))
    with pytest.raises(TypeError, match="exact pre-send outcome"):
        msgspec.structs.replace(pre_send, outcome=cast("HttpPreSendOutcome", object()))
    with pytest.raises(ValueError, match="does not own its attempt"):
        msgspec.structs.replace(
            pre_send,
            attempt_id=AttemptId(value=UUID(int=303)),
        )


def test_segment_view_must_source_its_exact_exchange_body() -> None:
    owner = stream_execution(ViewKind.SSE)
    attempt = owner.attempts[0]
    observed_exchange = attempt.exchanges[-1]
    wrong_view = owner.views[1]
    wrong_segment = msgspec.structs.replace(
        segment(index=0, first=0, count=1),
        attempt_id=attempt.attempt_id,
        exchange_id=observed_exchange.exchange_id,
        view=wrong_view,
    )
    with pytest.raises(ValueError, match="exact exchange response body"):
        _validate_capture_binding(
            wrong_segment,
            observed_exchange,
            {wrong_view.view_id: wrong_view},
            ViewKind.SSE,
        )


def test_owner_path_rejects_untyped_overlap_duplicate_unknown_and_misordered_occurrences() -> None:
    owner = stream_execution(ViewKind.SSE)
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    first_segment = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
    )
    second_segment = segment(index=1, first=1, count=1)
    first_occurrence = _response_occurrence(owner.attempts[0])
    second_occurrence = _response_occurrence(owner.attempts[1])

    with pytest.raises(TypeError, match="immutable typed tuple"):
        validate_stream_owner(
            records,
            (first_segment, second_segment),
            cast("tuple[StreamNonSegmentOccurrence, ...]", [first_occurrence]),
        )
    with pytest.raises(TypeError, match="immutable typed tuple"):
        validate_stream_owner(
            records,
            (first_segment, second_segment),
            cast("tuple[StreamNonSegmentOccurrence, ...]", ("occurrence",)),
        )
    with pytest.raises(ValueError, match="both captured and non-segment"):
        validate_stream_owner(
            records,
            (first_segment, second_segment),
            (first_occurrence,),
        )
    with pytest.raises(ValueError, match="attempt IDs must be unique"):
        validate_stream_owner(records, (), (first_occurrence, first_occurrence))
    with pytest.raises(ValueError, match="exchange IDs must be unique"):
        validate_stream_owner(
            records,
            (),
            (
                first_occurrence,
                msgspec.structs.replace(
                    second_occurrence,
                    exchange_id=first_occurrence.exchange_id,
                ),
            ),
        )
    with pytest.raises(ValueError, match="exact execution owner"):
        validate_stream_owner(
            records,
            (),
            (
                msgspec.structs.replace(
                    first_occurrence,
                    attempt_id=AttemptId(value=UUID(int=999)),
                ),
            ),
        )

    with pytest.raises(ValueError, match="ordered attempt path"):
        _validate_projected_order(
            (),
            (),
            (first_occurrence.attempt_id, second_occurrence.attempt_id),
            (second_occurrence, first_occurrence),
        )


def test_segment_resolution_and_outgoing_retry_end_are_exact() -> None:
    first_segment = segment(index=0, first=0, count=1)
    with pytest.raises(ValueError, match="exact execution owner"):
        _resolve_segment_exchange({}, {}, first_segment)

    owner = stream_execution(ViewKind.SSE)
    reconnect_transition = owner.attempts[1].stream_reconnect
    assert reconnect_transition is not None
    ordinary_retry = msgspec.structs.replace(
        owner.attempts[1],
        retry=RetryMetadata(
            trigger=RetryTrigger.STATUS_5XX,
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        ),
        stream_reconnect=None,
    )
    with pytest.raises(ValueError, match="outgoing retry outcome"):
        _validate_outgoing_segment_end(
            msgspec.structs.replace(
                first_segment,
                end_reason=SegmentEndReason.RECONNECT,
            ),
            owner.attempts[0],
            ordinary_retry,
        )
    with pytest.raises(ValueError, match="predecessor segment's exact view"):
        _validate_outgoing_segment_end(
            msgspec.structs.replace(
                first_segment,
                attempt_id=owner.attempts[0].attempt_id,
                exchange_id=owner.attempts[0].exchanges[-1].exchange_id,
                view=owner.views[0],
                end_reason=SegmentEndReason.RECONNECT,
            ),
            owner.attempts[0],
            msgspec.structs.replace(
                owner.attempts[1],
                stream_reconnect=msgspec.structs.replace(
                    reconnect_transition,
                    previous_stream_view_id=ViewId(value=UUID(int=999)),
                ),
            ),
        )
    with pytest.raises(ValueError, match="captured stream segment"):
        _validate_non_segment_outgoing(owner.attempts[1])


def test_non_segment_occurrences_bind_exact_terminal_outcomes_and_cannot_hide_streams() -> None:
    owner = stream_execution(ViewKind.SSE)
    attempt = owner.attempts[0]
    exact_response = _response_occurrence(attempt)
    views = {view.view_id: view for view in owner.views}
    with pytest.raises(ValueError, match="terminal exchange"):
        _validate_non_segment_occurrence(
            attempt,
            msgspec.structs.replace(
                exact_response,
                exchange_id=ExchangeId(value=UUID(int=999)),
            ),
            views,
            ViewKind.SSE,
        )

    _, failed = _failed_attempt()
    with pytest.raises(ValueError, match="must have no HTTP response"):
        _validate_non_segment_occurrence(
            attempt,
            msgspec.structs.replace(
                failed,
                attempt_id=attempt.attempt_id,
                exchange_id=attempt.exchanges[-1].exchange_id,
            ),
            views,
            ViewKind.SSE,
        )
    failed_attempt, exact_failure = _failed_attempt()
    with pytest.raises(ValueError, match="exact transport failure"):
        _validate_non_segment_occurrence(
            failed_attempt,
            msgspec.structs.replace(
                exact_failure,
                failure=failure(FailurePhase.TLS),
            ),
            {},
            ViewKind.SSE,
        )
    with pytest.raises(ValueError, match="exact terminal outcome"):
        _validate_non_segment_occurrence(
            attempt,
            msgspec.structs.replace(exact_response, response=response(status=201)),
            views,
            ViewKind.SSE,
        )
    with pytest.raises(ValueError, match="represented by a segment"):
        _validate_non_segment_occurrence(
            attempt,
            exact_response,
            views,
            ViewKind.SSE,
        )

    no_body_exchange = exchange(
        exchange_number=301,
        effective_request=request(),
        observed_response=response(
            status=204,
            body=NoResponseBody(reason=NoBodyReason.STATUS_204),
        ),
    )
    no_body_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=301)),
        retry=None,
        exchanges=(no_body_exchange,),
        timings=attempt_timings(),
    )
    _validate_non_segment_occurrence(
        no_body_attempt,
        _response_occurrence(no_body_attempt),
        {},
        ViewKind.SSE,
    )


def test_pre_send_attempt_projection_rejects_exchange_occurrences_and_outcome_drift() -> None:
    denied = resolution_denial(
        request(),
        request_occurrence=occurrence(attempt=304, exchange=304),
    )
    attempt = HttpAttempt(
        attempt_id=denied.occurrence.attempt_id,
        retry=None,
        exchanges=(),
        timings=attempt_timings(),
        pre_send=denied,
    )
    exchange_occurrence = _response_occurrence(stream_execution(ViewKind.SSE).attempts[0])
    with pytest.raises(ValueError, match="requires a terminal exchange"):
        _validate_non_segment_occurrence(
            attempt,
            exchange_occurrence,
            {},
            ViewKind.SSE,
        )

    changed_denial = msgspec.structs.replace(
        denied,
        code=msgspec.structs.replace(denied.code, value="http.other-denial"),
    )
    changed_occurrence = StreamPreSendOccurrence(
        attempt_id=attempt.attempt_id,
        outcome=changed_denial,
    )
    with pytest.raises(ValueError, match="exact attempt outcome"):
        _validate_non_segment_occurrence(
            attempt,
            changed_occurrence,
            {},
            ViewKind.SSE,
        )
