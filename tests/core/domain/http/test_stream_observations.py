"""Tests for SSE and streaming-JSON items, storage indexes, and bounded windows."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import StreamItemLocation
from comparo.core.domain.http.stream_termination import FramingDecodeFailureEvidence
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.streams import TrailingIncompleteItem
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_timings


@pytest.mark.parametrize(
    ("completeness", "termination"),
    [
        (StreamCompleteness.FULL, StreamTerminationReason.REMOTE_EOF),
        (StreamCompleteness.WINDOW, StreamTerminationReason.EVENT_COUNT_WINDOW),
        (StreamCompleteness.WINDOW, StreamTerminationReason.ACCEPTED_MANUAL_WINDOW),
        (StreamCompleteness.INCOMPLETE, StreamTerminationReason.SAFETY_BYTE_LIMIT),
        (StreamCompleteness.INCOMPLETE, StreamTerminationReason.MANUAL_STOP),
    ],
)
def test_stream_extent_accepts_only_matching_termination_classes(
    completeness: StreamCompleteness,
    termination: StreamTerminationReason,
) -> None:
    assert (
        StreamExtent(completeness=completeness, termination=termination).termination is termination
    )


def test_safety_limits_and_unaccepted_manual_stops_cannot_be_green_windows() -> None:
    with pytest.raises(ValueError, match="does not match"):
        StreamExtent(
            completeness=StreamCompleteness.WINDOW,
            termination=StreamTerminationReason.SAFETY_COUNT_LIMIT,
        )
    with pytest.raises(ValueError, match="does not match"):
        StreamExtent(
            completeness=StreamCompleteness.WINDOW,
            termination=StreamTerminationReason.MANUAL_STOP,
        )


def test_stream_observation_supports_multiple_contiguous_connection_segments() -> None:
    observed = StreamObservation(
        records=records_handle(
            StreamKind.SSE,
            records=3,
            controls=1,
            connections=2,
        ),
        segments=(
            segment(
                index=0,
                first=0,
                count=2,
                first_item=0,
                item_count=3,
                first_control=0,
                controls=1,
                end_reason=SegmentEndReason.RECONNECT,
                byte_start=0,
                byte_end=4,
            ),
            segment(
                index=1,
                first=2,
                count=1,
                first_item=3,
                first_control=1,
                byte_start=4,
                byte_end=8,
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
    assert len(observed.segments) == 2
    assert observed.records.record_count.value == 3
    assert observed.stream_id.value == observed.records.owner.execution_id.value

    with pytest.raises(ValueError, match="record ranges"):
        StreamObservation(
            records=observed.records,
            segments=(
                segment(
                    index=0,
                    first=1,
                    count=3,
                    first_item=0,
                    item_count=4,
                    controls=1,
                ),
            ),
            captured_bytes=observed.captured_bytes,
            extent=observed.extent,
            trailing_incomplete=(),
            timings=observed.timings,
        )


def test_stream_segments_bind_unique_occurrences_and_exact_termination() -> None:
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    extent = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
        byte_start=0,
        byte_end=4,
    )
    second = segment(index=1, first=1, count=1, byte_start=4, byte_end=8)

    with pytest.raises(ValueError, match="reconnection"):
        StreamObservation(
            records=records,
            segments=(
                msgspec.structs.replace(first, end_reason=SegmentEndReason.REMOTE_EOF),
                second,
            ),
            captured_bytes=ByteCount(value=8),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )
    with pytest.raises(ValueError, match="unique connection attempt IDs"):
        StreamObservation(
            records=records,
            segments=(
                first,
                msgspec.structs.replace(
                    second,
                    attempt_id=first.attempt_id,
                    exchange_id=first.exchange_id,
                ),
            ),
            captured_bytes=ByteCount(value=8),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )
    with pytest.raises(ValueError, match="exact execution owner"):
        StreamObservation(
            records=records,
            segments=(
                first,
                msgspec.structs.replace(
                    second,
                    exchange_id=ExchangeId(value=UUID(int=99)),
                ),
            ),
            captured_bytes=ByteCount(value=8),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )
    with pytest.raises(ValueError, match="exact segment end reason"):
        StreamObservation(
            records=records_handle(StreamKind.SSE, records=1),
            segments=(segment(index=0, first=0, count=1, end_reason=SegmentEndReason.FAILURE),),
            captured_bytes=ByteCount(value=4),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )


def test_trailing_items_require_an_incomplete_stream() -> None:
    trailing = TrailingIncompleteItem(
        byte_range=ByteRange(start=ByteCount(value=2), end_exclusive=ByteCount(value=4)),
        segment_index=Count(value=0),
    )
    with pytest.raises(ValueError, match="requires incomplete"):
        StreamObservation(
            records=records_handle(StreamKind.NDJSON, records=1),
            segments=(segment(index=0, first=0, count=1, kind=StreamKind.NDJSON),),
            captured_bytes=ByteCount(value=4),
            extent=StreamExtent(
                completeness=StreamCompleteness.FULL,
                termination=StreamTerminationReason.REMOTE_EOF,
            ),
            trailing_incomplete=(trailing,),
            timings=stream_timings(),
        )

    incomplete = StreamObservation(
        records=records_handle(StreamKind.NDJSON, records=1),
        segments=(
            segment(
                index=0,
                first=0,
                count=1,
                kind=StreamKind.NDJSON,
                end_reason=SegmentEndReason.FAILURE,
            ),
        ),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.DECODE_FAILURE,
        ),
        trailing_incomplete=(trailing,),
        timings=stream_timings(),
        termination_evidence=FramingDecodeFailureEvidence(),
    )
    assert incomplete.trailing_incomplete == (trailing,)


def test_trailing_items_are_segment_bounded_nonempty_and_unique() -> None:
    records = records_handle(StreamKind.NDJSON, records=1)
    segments = (
        segment(
            index=0,
            first=0,
            count=1,
            kind=StreamKind.NDJSON,
            end_reason=SegmentEndReason.FAILURE,
        ),
    )
    extent = StreamExtent(
        completeness=StreamCompleteness.INCOMPLETE,
        termination=StreamTerminationReason.DECODE_FAILURE,
    )

    def observe(*items: TrailingIncompleteItem) -> StreamObservation:
        return StreamObservation(
            records=records,
            segments=segments,
            captured_bytes=ByteCount(value=4),
            extent=extent,
            trailing_incomplete=items,
            timings=stream_timings(),
            termination_evidence=FramingDecodeFailureEvidence(),
        )

    with pytest.raises(ValueError, match="declared connection segment"):
        observe(
            TrailingIncompleteItem(
                byte_range=ByteRange(start=ByteCount(value=2), end_exclusive=ByteCount(value=4)),
                segment_index=Count(value=1),
            )
        )
    with pytest.raises(ValueError, match="fit their connection segment"):
        observe(
            TrailingIncompleteItem(
                byte_range=ByteRange(start=ByteCount(value=3), end_exclusive=ByteCount(value=5)),
                segment_index=Count(value=0),
            )
        )
    with pytest.raises(ValueError, match="non-empty"):
        observe(
            TrailingIncompleteItem(
                byte_range=ByteRange(start=ByteCount(value=2), end_exclusive=ByteCount(value=2)),
                segment_index=Count(value=0),
            )
        )
    with pytest.raises(ValueError, match="must end at"):
        observe(
            TrailingIncompleteItem(
                byte_range=ByteRange(start=ByteCount(value=1), end_exclusive=ByteCount(value=3)),
                segment_index=Count(value=0),
            )
        )
    with pytest.raises(ValueError, match="at most one"):
        observe(
            TrailingIncompleteItem(
                byte_range=ByteRange(start=ByteCount(value=1), end_exclusive=ByteCount(value=3)),
                segment_index=Count(value=0),
            ),
            TrailingIncompleteItem(
                byte_range=ByteRange(start=ByteCount(value=2), end_exclusive=ByteCount(value=4)),
                segment_index=Count(value=0),
            ),
        )


def test_reconnect_retains_trailing_items_from_an_earlier_segment() -> None:
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
        byte_start=0,
        byte_end=4,
    )
    second = segment(index=1, first=1, count=1, byte_start=4, byte_end=8)
    trailing = TrailingIncompleteItem(
        byte_range=ByteRange(start=ByteCount(value=2), end_exclusive=ByteCount(value=4)),
        segment_index=Count(value=0),
    )
    observed = StreamObservation(
        records=records_handle(StreamKind.SSE, records=2, connections=2),
        segments=(first, second),
        captured_bytes=ByteCount(value=8),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(trailing,),
        timings=stream_timings(),
    )
    assert observed.trailing_incomplete == (trailing,)

    with pytest.raises(ValueError, match="fit their connection segment"):
        msgspec.structs.replace(
            observed,
            trailing_incomplete=(
                TrailingIncompleteItem(
                    byte_range=ByteRange(
                        start=ByteCount(value=4),
                        end_exclusive=ByteCount(value=6),
                    ),
                    segment_index=Count(value=0),
                ),
            ),
        )


def test_stream_locations_have_no_raw_transport_chunk_identity() -> None:
    annotations = StreamItemLocation.__annotations__
    assert "chunk" not in " ".join(annotations)
    assert set(annotations) == {
        "segment_index",
        "item_index",
        "category_index",
        "byte_range",
        "line",
        "received_offset",
    }


def test_trailing_items_must_follow_segment_order() -> None:
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
        byte_start=0,
        byte_end=4,
    )
    second = segment(
        index=1,
        first=1,
        count=1,
        end_reason=SegmentEndReason.FAILURE,
        byte_start=4,
        byte_end=8,
    )
    first_trailing = TrailingIncompleteItem(
        byte_range=ByteRange(start=ByteCount(value=3), end_exclusive=ByteCount(value=4)),
        segment_index=Count(value=0),
    )
    second_trailing = TrailingIncompleteItem(
        byte_range=ByteRange(start=ByteCount(value=7), end_exclusive=ByteCount(value=8)),
        segment_index=Count(value=1),
    )
    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        StreamObservation(
            records=records_handle(StreamKind.SSE, records=2, connections=2),
            segments=(first, second),
            captured_bytes=ByteCount(value=8),
            extent=StreamExtent(
                completeness=StreamCompleteness.INCOMPLETE,
                termination=StreamTerminationReason.DECODE_FAILURE,
            ),
            trailing_incomplete=(second_trailing, first_trailing),
            timings=stream_timings(),
        )
