"""Adversarial stream reconnect and item-index ownership regressions."""

from __future__ import annotations

import msgspec
import pytest

from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.stream_items import JsonStreamRecord
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamRecordsHandle
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from tests.core.domain.http.stream_factories import location
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import redirect_stream_execution
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_execution
from tests.core.domain.http.stream_factories import stream_timings


def _full_extent() -> StreamExtent:
    return StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )


def _empty_stream_timings() -> StreamTimings:
    return StreamTimings(
        time_to_first_record=NotPerformed(reason=NotPerformedReason.EMPTY_STREAM),
        observation_window_elapsed=stream_timings().observation_window_elapsed,
    )


def _sealed_text(value: str) -> SensitiveValue[BodyText]:
    return SensitiveValue(
        BodyText(value=value),
        origins=(TaintOriginId(value="stream-security"),),
    )


def test_reconnects_are_distinct_attempts_and_cannot_bind_followed_redirects() -> None:
    owner = stream_execution(ViewKind.SSE)
    assert len(owner.attempts) == 2
    assert owner.attempts[0].attempt_id != owner.attempts[1].attempt_id
    assert owner.attempts[1].retry is None
    assert owner.attempts[1].stream_reconnect is not None
    assert owner.attempts[0].exchanges[0].request.request.policies.retries.max_attempts.value == 1

    redirect_owner = redirect_stream_execution(ViewKind.SSE)
    records = StreamRecordsHandle(
        owner=redirect_owner,
        kind=StreamKind.SSE,
        record_count=Count(value=1),
        control_count=Count(value=0),
        error_count=Count(value=0),
    )
    followed = msgspec.structs.replace(
        segment(index=0, first=0, count=1),
        attempt_id=redirect_owner.attempts[0].attempt_id,
        exchange_id=redirect_owner.attempts[0].exchanges[0].exchange_id,
        view=redirect_owner.views[0],
    )
    with pytest.raises(ValueError, match="terminal exchange"):
        StreamObservation(
            records=records,
            segments=(followed,),
            captured_bytes=ByteCount(value=4),
            extent=_full_extent(),
            trailing_incomplete=(),
            timings=stream_timings(),
        )


def test_segment_order_cannot_bypass_reconnect_transition_lineage() -> None:
    owner = stream_execution(ViewKind.SSE)
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
    )
    second = segment(index=1, first=1, count=1)
    reversed_first = msgspec.structs.replace(
        first,
        attempt_id=owner.attempts[1].attempt_id,
        exchange_id=owner.attempts[1].exchanges[0].exchange_id,
        view=owner.views[1],
    )
    reversed_second = msgspec.structs.replace(
        second,
        attempt_id=owner.attempts[0].attempt_id,
        exchange_id=owner.attempts[0].exchanges[0].exchange_id,
        view=owner.views[0],
        end_reason=SegmentEndReason.RECONNECT,
    )

    with pytest.raises(ValueError, match="ordered attempt path"):
        StreamObservation(
            records=records,
            segments=(reversed_first, reversed_second),
            captured_bytes=ByteCount(value=8),
            extent=_full_extent(),
            trailing_incomplete=(),
            timings=stream_timings(),
        )


def test_item_global_index_cannot_borrow_another_segment_interval() -> None:
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
    )
    second = segment(index=1, first=1, count=1)
    observed = StreamObservation(
        records=records,
        segments=(first, second),
        captured_bytes=ByteCount(value=8),
        extent=_full_extent(),
        trailing_incomplete=(),
        timings=stream_timings(),
    )
    event = SseEvent(
        data=_sealed_text("data"),
        event_type=_sealed_text("message"),
        last_event_id=_sealed_text("id"),
        fields=(),
        location=location(segment=1, item=0, category=1, start=4, end=8),
    )
    with pytest.raises(ValueError, match="exact segment item interval"):
        observed.validate_item_envelope(StreamItemEnvelope(view=second.view, item=event))


def test_record_cannot_borrow_error_capacity_when_record_count_is_zero() -> None:
    records = records_handle(StreamKind.NDJSON, records=0, errors=1)
    observed = StreamObservation(
        records=records,
        segments=(
            segment(
                index=0,
                first=0,
                count=0,
                errors=1,
                kind=StreamKind.NDJSON,
            ),
        ),
        captured_bytes=ByteCount(value=4),
        extent=_full_extent(),
        trailing_incomplete=(),
        timings=_empty_stream_timings(),
    )
    record = JsonStreamRecord(
        value=SensitiveValue(
            JsonInteger(value=1),
            origins=(TaintOriginId(value="stream-security"),),
        ),
        location=location(item=0, category=0),
    )
    with pytest.raises(ValueError, match="exact segment category interval"):
        observed.validate_item_envelope(
            StreamItemEnvelope(view=observed.segments[0].view, item=record)
        )
