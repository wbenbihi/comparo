"""Branch-complete tests for terminal stream truth validation."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.stream_attempts import StreamNonSegmentResponseOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.stream_terminal_validation import _failure_extent
from comparo.core.domain.http.stream_terminal_validation import _validate_resource_budget
from comparo.core.domain.http.stream_terminal_validation import validate_terminal_stream_extent
from comparo.core.domain.http.stream_termination import EventCountWindowEvidence
from comparo.core.domain.http.stream_termination import SafetyByteLimitEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from tests.core.domain.http.budget_factories import capture_exhaustion
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence as request_occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_denial
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_timings


def _full_extent() -> StreamExtent:
    return StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )


def test_non_segment_and_failed_exchanges_reject_competing_stop_evidence() -> None:
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    terminal_segment = segment(index=1, first=1, count=1)
    terminal_attempt = records.owner.attempts[-1]
    terminal_exchange = terminal_attempt.exchanges[-1]
    occurrence = StreamNonSegmentResponseOccurrence(
        attempt_id=terminal_attempt.attempt_id,
        exchange_id=terminal_exchange.exchange_id,
        response=cast(ResponseObservation, terminal_exchange.response),
        failure=terminal_exchange.failure,
    )
    evidence = EventCountWindowEvidence(limit=PositiveCount(value=2))
    with pytest.raises(ValueError, match="non-segment outcomes cannot carry"):
        validate_terminal_stream_extent(
            terminal_segment=terminal_segment,
            terminal_exchange=terminal_exchange,
            terminal_occurrence=occurrence,
            extent=_full_extent(),
            evidence=evidence,
            records=records,
            captured_bytes=ByteCount(value=8),
            timings=stream_timings(),
            terminal_has_trailing=False,
        )

    denied = resolution_denial(
        request(),
        request_occurrence=request_occurrence(attempt=1302, exchange=1302),
    )
    pre_send = StreamPreSendOccurrence(
        attempt_id=denied.occurrence.attempt_id,
        outcome=denied,
    )
    with pytest.raises(ValueError, match="pre-send outcomes cannot carry"):
        validate_terminal_stream_extent(
            terminal_segment=terminal_segment,
            terminal_exchange=terminal_exchange,
            terminal_occurrence=pre_send,
            extent=_full_extent(),
            evidence=evidence,
            records=records,
            captured_bytes=ByteCount(value=8),
            timings=stream_timings(),
            terminal_has_trailing=False,
        )

    interrupted = exchange(
        exchange_number=1300,
        effective_request=request(),
        observed_response=response(
            body=ResponseEntityBody(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
                handle=body_handle(size=4, extent=BodyCaptureExtent.PARTIAL),
                encoded_byte_count=None,
            )
        ),
        failure=failure(FailurePhase.BODY_READ),
    )
    with pytest.raises(ValueError, match="failures cannot be replaced"):
        validate_terminal_stream_extent(
            terminal_segment=terminal_segment,
            terminal_exchange=interrupted,
            terminal_occurrence=None,
            extent=_full_extent(),
            evidence=evidence,
            records=records,
            captured_bytes=ByteCount(value=8),
            timings=stream_timings(),
            terminal_has_trailing=False,
        )


def test_policy_failure_keeps_its_distinct_terminal_classification() -> None:
    assert _failure_extent(FailurePhase.POLICY) == (
        StreamTerminationReason.POLICY_BLOCKED,
        "failure",
    )


def test_resource_budget_failure_uses_only_canonical_capture_exhaustion() -> None:
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    clean = records.owner.attempts[-1].exchanges[-1]
    with pytest.raises(ValueError, match="canonical capture exhaustion"):
        _validate_resource_budget(clean, None)

    handle = body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
    exhausted = exchange(
        exchange_number=1301,
        effective_request=request(),
        observed_response=response(
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
        ),
        failure=failure(FailurePhase.RESOURCE_BUDGET),
    )
    _validate_resource_budget(exhausted, None)
    with pytest.raises(ValueError, match="must not be duplicated"):
        _validate_resource_budget(
            exhausted,
            SafetyByteLimitEvidence(limit=ByteCount(value=4)),
        )
