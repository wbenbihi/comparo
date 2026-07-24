"""Cycle-free validation for stream-store item windows against segment intervals."""

from __future__ import annotations

from typing import Protocol

from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import StreamItemCategory
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.stream_items import stream_item_category
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.primitives import Count


class StreamItemRecordsLike(Protocol):
    """Aggregate count facts required for item-window validation."""

    @property
    def record_count(self) -> Count:
        """Return the aggregate record count."""
        ...

    @property
    def control_count(self) -> Count:
        """Return the aggregate control count."""
        ...

    @property
    def error_count(self) -> Count:
        """Return the aggregate error count."""
        ...


class StreamItemSegmentLike(Protocol):
    """Exact segment intervals and view needed by item validation."""

    @property
    def index(self) -> Count:
        """Return the contiguous segment index."""
        ...

    @property
    def view(self) -> DecodedViewHandle:
        """Return the exact decoded view for this segment."""
        ...

    @property
    def first_item_index(self) -> Count:
        """Return the first global item index."""
        ...

    @property
    def item_count(self) -> Count:
        """Return the global item interval length."""
        ...

    @property
    def first_record_index(self) -> Count:
        """Return the first record index."""
        ...

    @property
    def record_count(self) -> Count:
        """Return the record interval length."""
        ...

    @property
    def first_control_index(self) -> Count:
        """Return the first control index."""
        ...

    @property
    def control_count(self) -> Count:
        """Return the control interval length."""
        ...

    @property
    def first_error_index(self) -> Count:
        """Return the first error index."""
        ...

    @property
    def error_count(self) -> Count:
        """Return the error interval length."""
        ...

    @property
    def byte_range(self) -> ByteRange:
        """Return the captured-byte interval for this segment."""
        ...


class StreamItemObservationLike(Protocol):
    """Minimal stream aggregate boundary used by store-window validation."""

    @property
    def records(self) -> StreamItemRecordsLike:
        """Return aggregate category counts."""
        ...

    @property
    def segments(self) -> tuple[StreamItemSegmentLike, ...]:
        """Return exact physical segment intervals."""
        ...


def validate_stream_item_envelopes(
    observation: StreamItemObservationLike,
    envelopes: tuple[StreamItemEnvelope, ...],
) -> None:
    """Validate one immutable store window with unique exact item coordinates."""
    if type(envelopes) is not tuple or any(
        type(envelope) is not StreamItemEnvelope for envelope in envelopes
    ):
        raise TypeError("stream items must be an immutable StreamItemEnvelope tuple")
    item_indexes: list[Count] = []
    category_indexes: list[tuple[StreamItemCategory, Count]] = []
    for envelope in envelopes:
        _validate_item_envelope(observation, envelope)
        category = stream_item_category(envelope.item)
        item_indexes.append(envelope.item.location.item_index)
        category_indexes.append((category, envelope.item.location.category_index))
    if len(set(item_indexes)) != len(item_indexes):
        raise ValueError("stream item indexes must be unique within one store window")
    if len(set(category_indexes)) != len(category_indexes):
        raise ValueError("stream category indexes must be unique within one store window")


def _validate_item_envelope(
    observation: StreamItemObservationLike,
    envelope: StreamItemEnvelope,
) -> None:
    location = envelope.item.location
    if location.segment_index.value >= len(observation.segments):
        raise ValueError("stream item must name a declared connection segment")
    segment = observation.segments[location.segment_index.value]
    if envelope.view != segment.view:
        raise ValueError("stream item envelope must use its exact segment view")
    byte_range = segment.byte_range
    if (
        location.byte_range.start.value < byte_range.start.value
        or location.byte_range.end_exclusive.value > byte_range.end_exclusive.value
    ):
        raise ValueError("stream item byte range must fit its exact segment view extent")
    if not (
        segment.first_item_index.value
        <= location.item_index.value
        < segment.first_item_index.value + segment.item_count.value
    ):
        raise ValueError("stream item index must fit its exact segment item interval")
    start, count = _category_interval(segment, stream_item_category(envelope.item))
    if not (start.value <= location.category_index.value < start.value + count.value):
        raise ValueError("stream item category index must fit its exact segment category interval")


def _category_interval(
    segment: StreamItemSegmentLike,
    category: StreamItemCategory,
) -> tuple[Count, Count]:
    return {
        StreamItemCategory.RECORD: (segment.first_record_index, segment.record_count),
        StreamItemCategory.CONTROL: (segment.first_control_index, segment.control_count),
        StreamItemCategory.ERROR: (segment.first_error_index, segment.error_count),
    }[category]
