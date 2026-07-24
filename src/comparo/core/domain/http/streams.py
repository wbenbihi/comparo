"""Storage-backed stream windows, connection segments, and termination facts."""

from __future__ import annotations

from itertools import pairwise
from typing import final

import msgspec

from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.stream_attempts import StreamNonSegmentOccurrence
from comparo.core.domain.http.stream_count_validation import validate_stream_framing_counts
from comparo.core.domain.http.stream_item_validation import validate_stream_item_envelopes
from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseJsonBudgetExceeded
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.stream_owner_validation import validate_stream_owner
from comparo.core.domain.http.stream_terminal_validation import validate_terminal_stream_extent
from comparo.core.domain.http.stream_termination import WINDOW_TERMINATIONS
from comparo.core.domain.http.stream_termination import StreamTerminationEvidence
from comparo.core.domain.http.stream_termination import (
    StreamTerminationReason as StreamTerminationReason,
)
from comparo.core.domain.http.stream_termination import is_stream_termination_evidence
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import StreamId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import BodyBudgetScope


class StreamKind(DomainStrEnum):
    """Protocol-level record framings that never depend on transport chunks."""

    SSE = "sse"
    NDJSON = "ndjson"
    JSON_SEQUENCE = "json-sequence"


@final
class StreamRecordsHandle(msgspec.Struct, frozen=True, kw_only=True):
    """A bounded record/control index owned by the DecodedViewStore."""

    owner: EnvironmentExecutionObservation
    kind: StreamKind
    record_count: Count
    control_count: Count
    error_count: Count

    def __post_init__(self) -> None:
        """Bind counts to one exact, fully validated Environment execution."""
        if type(self.owner) is not EnvironmentExecutionObservation:
            raise TypeError("stream records owner must be EnvironmentExecutionObservation")
        if type(self.kind) is not StreamKind:
            raise TypeError("stream records kind must be StreamKind")
        if any(
            type(count) is not Count
            for count in (self.record_count, self.control_count, self.error_count)
        ):
            raise TypeError("stream record, control, and error counts must be Count values")
        validate_stream_framing_counts(
            ViewKind(self.kind.value),
            self.control_count,
            self.error_count,
        )


class SegmentEndReason(DomainStrEnum):
    """Why one physical stream connection segment ended."""

    REMOTE_EOF = "remote-eof"
    RECONNECT = "reconnect"
    WINDOW_CLOSED = "window-closed"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@final
class ConnectionSegment(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered physical exchange segment in a logical stream observation."""

    index: Count
    attempt_id: AttemptId
    exchange_id: ExchangeId
    view: DecodedViewHandle
    first_item_index: Count
    item_count: Count
    first_record_index: Count
    record_count: Count
    first_control_index: Count
    control_count: Count
    first_error_index: Count
    error_count: Count
    byte_range: ByteRange
    end_reason: SegmentEndReason

    def __post_init__(self) -> None:
        """Require exact identities, indexes, counts, and segment termination."""
        indexes = (
            self.index,
            self.first_item_index,
            self.first_record_index,
            self.first_control_index,
            self.first_error_index,
        )
        if any(type(index) is not Count for index in indexes):
            raise TypeError("stream segment indexes must be Count values")
        if type(self.attempt_id) is not AttemptId or type(self.exchange_id) is not ExchangeId:
            raise TypeError("stream segment occurrence IDs must be typed HTTP IDs")
        if type(self.view) is not DecodedViewHandle:
            raise TypeError("stream segment view must be DecodedViewHandle")
        counts = (self.item_count, self.record_count, self.control_count, self.error_count)
        if any(type(count) is not Count for count in counts):
            raise TypeError("stream segment category counts must be Count values")
        if self.item_count.value != (
            self.record_count.value + self.control_count.value + self.error_count.value
        ):
            raise ValueError("stream segment item count must equal its category counts")
        validate_stream_framing_counts(
            self.view.decoder.view,
            self.control_count,
            self.error_count,
        )
        if type(self.byte_range) is not ByteRange:
            raise TypeError("stream segment byte range must be ByteRange")
        if type(self.end_reason) is not SegmentEndReason:
            raise TypeError("stream segment end reason must be SegmentEndReason")


class StreamCompleteness(DomainStrEnum):
    """The semantic extent of a closed bounded stream observation."""

    FULL = "full"
    WINDOW = "window"
    INCOMPLETE = "incomplete"


@final
class StreamExtent(msgspec.Struct, frozen=True, kw_only=True):
    """A completeness classification tied to an exact termination reason."""

    completeness: StreamCompleteness
    termination: StreamTerminationReason

    def __post_init__(self) -> None:
        """Prevent safety limits and unaccepted manual stops from becoming green windows."""
        if type(self.completeness) is not StreamCompleteness:
            raise TypeError("stream completeness must be StreamCompleteness")
        if type(self.termination) is not StreamTerminationReason:
            raise TypeError("stream termination must be StreamTerminationReason")
        if self.completeness is StreamCompleteness.FULL:
            valid = self.termination is StreamTerminationReason.REMOTE_EOF
        elif self.completeness is StreamCompleteness.WINDOW:
            valid = self.termination in WINDOW_TERMINATIONS
        else:
            valid = self.termination not in WINDOW_TERMINATIONS | {
                StreamTerminationReason.REMOTE_EOF
            }
        if not valid:
            raise ValueError("stream completeness does not match its termination reason")


@final
class TrailingIncompleteItem(msgspec.Struct, frozen=True, kw_only=True):
    """A non-dispatched trailing item retained by location without raw inline bytes."""

    byte_range: ByteRange
    segment_index: Count

    def __post_init__(self) -> None:
        """Require storage-addressable byte and segment coordinates."""
        if type(self.byte_range) is not ByteRange or type(self.segment_index) is not Count:
            raise TypeError("trailing stream item requires typed byte and segment coordinates")


def _validate_segments(
    records: StreamRecordsHandle,
    segments: tuple[ConnectionSegment, ...],
    captured_bytes: ByteCount,
) -> None:
    if type(segments) is not tuple or not segments:
        raise ValueError("stream observation requires at least one connection segment")
    if any(type(segment) is not ConnectionSegment for segment in segments):
        raise TypeError("stream segments must be an immutable typed tuple")
    if tuple(segment.index.value for segment in segments) != tuple(range(len(segments))):
        raise ValueError("stream segment indexes must be contiguous from zero")
    _validate_segment_axis(
        segments,
        start="first_item_index",
        count="item_count",
        expected_total=(
            records.record_count.value + records.control_count.value + records.error_count.value
        ),
        label="item",
    )
    _validate_segment_axis(
        segments,
        start="first_record_index",
        count="record_count",
        expected_total=records.record_count.value,
        label="record",
    )
    _validate_segment_axis(
        segments,
        start="first_control_index",
        count="control_count",
        expected_total=records.control_count.value,
        label="control",
    )
    _validate_segment_axis(
        segments,
        start="first_error_index",
        count="error_count",
        expected_total=records.error_count.value,
        label="error",
    )
    _validate_segment_identity_and_bytes(segments, captured_bytes)


def _validate_segment_axis(
    segments: tuple[ConnectionSegment, ...],
    *,
    start: str,
    count: str,
    expected_total: int,
    label: str,
) -> None:
    """Validate one exact half-open global or per-category segment interval axis."""
    expected_starts: list[int] = []
    next_index = 0
    for segment in segments:
        expected_starts.append(next_index)
        next_index += getattr(segment, count).value
    actual_starts = tuple(getattr(segment, start).value for segment in segments)
    if actual_starts != tuple(expected_starts):
        raise ValueError(f"stream segment {label} ranges must be contiguous from zero")
    if next_index != expected_total:
        raise ValueError(f"stream segment {label} counts must equal the stored {label} count")


def _validate_segment_identity_and_bytes(
    segments: tuple[ConnectionSegment, ...],
    captured_bytes: ByteCount,
) -> None:
    """Bind every connection occurrence to one contiguous captured-byte interval."""
    if len({segment.attempt_id for segment in segments}) != len(segments):
        raise ValueError("stream connection segments must use unique connection attempt IDs")
    byte_ranges = tuple(segment.byte_range for segment in segments)
    if byte_ranges[0].start.value != 0 or any(
        current.start != previous.end_exclusive for previous, current in pairwise(byte_ranges)
    ):
        raise ValueError("stream segment byte ranges must be contiguous from zero")
    if byte_ranges[-1].end_exclusive.value != captured_bytes.value:
        raise ValueError("stream segment byte ranges must cover the captured byte extent")


def _validate_trailing_items(
    items: tuple[TrailingIncompleteItem, ...],
    segments: tuple[ConnectionSegment, ...],
) -> None:
    if type(items) is not tuple or any(type(item) is not TrailingIncompleteItem for item in items):
        raise TypeError("trailing stream items must be an immutable typed tuple")
    segment_ranges = {segment.index: segment.byte_range for segment in segments}
    if any(item.segment_index not in segment_ranges for item in items):
        raise ValueError("trailing stream items must name a declared connection segment")
    segment_indexes = tuple(item.segment_index for item in items)
    if len(set(segment_indexes)) != len(segment_indexes):
        raise ValueError("each connection segment may retain at most one trailing stream item")
    if any(
        item.byte_range.start.value < segment_ranges[item.segment_index].start.value
        or item.byte_range.end_exclusive.value
        > segment_ranges[item.segment_index].end_exclusive.value
        for item in items
    ):
        raise ValueError("trailing stream item byte ranges must fit their connection segment")
    ranges = tuple(item.byte_range for item in items)
    if any(item.start.value == item.end_exclusive.value for item in ranges):
        raise ValueError("trailing stream item byte ranges must be non-empty")
    if any(
        item.byte_range.end_exclusive != segment_ranges[item.segment_index].end_exclusive
        for item in items
    ):
        raise ValueError("a trailing stream item must end at its connection segment byte extent")
    if any(
        current.start.value < previous.end_exclusive.value for previous, current in pairwise(ranges)
    ):
        raise ValueError("trailing stream item byte ranges must be ordered and non-overlapping")


def _validate_segment_body_extents(segments: tuple[ConnectionSegment, ...]) -> None:
    """Bind each global segment interval length to its exact response body."""
    if any(
        segment.byte_range.end_exclusive.value - segment.byte_range.start.value
        != segment.view.source.byte_count.value
        for segment in segments
    ):
        raise ValueError("stream segment byte extent must equal its source response body")


def _validate_stream_timing(records: StreamRecordsHandle, timings: StreamTimings) -> None:
    """Bind time-to-first-record applicability to the exact stored record count."""
    if type(timings) is not StreamTimings:
        raise TypeError("stream timings must be StreamTimings")
    first_record = timings.time_to_first_record
    if records.record_count.value == 0:
        if (
            type(first_record) is not NotPerformed
            or first_record.reason is not NotPerformedReason.EMPTY_STREAM
        ):
            raise ValueError("an empty stream must explicitly omit time to first record")
    elif (
        type(first_record) is NotPerformed
        and first_record.reason is NotPerformedReason.EMPTY_STREAM
    ):
        raise ValueError("a non-empty stream cannot claim empty-stream timing")


def _validate_stream_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> None:
    """Require immutable typed stream diagnostics."""
    if type(diagnostics) is not tuple or any(
        type(diagnostic) is not Diagnostic for diagnostic in diagnostics
    ):
        raise TypeError("stream diagnostics must be an immutable typed tuple")


def _validate_nested_budget_attempts(
    observation: StreamObservation,
    envelopes: tuple[StreamItemEnvelope, ...],
) -> None:
    """Bind nested SSE budget evidence to the exact segment attempt scope."""
    exchanges = {
        (attempt.attempt_id, exchange.exchange_id): exchange
        for attempt in observation.records.owner.attempts
        for exchange in attempt.exchanges
    }
    for envelope in envelopes:
        item = envelope.item
        if type(item) is not SseEvent or type(item.nested_data) is not SseJsonBudgetExceeded:
            continue
        segment = observation.segments[item.location.segment_index.value]
        exchange = exchanges[(segment.attempt_id, segment.exchange_id)]
        scope = item.nested_data.exhaustion.budget.scope
        if (
            type(scope) is not BodyBudgetScope
            or scope.attempt != exchange.request.occurrence.attempt
        ):
            raise ValueError("nested SSE budget scope does not own its exchange attempt")


@final
class StreamObservation(msgspec.Struct, frozen=True, kw_only=True):
    """A closed storage-backed record window with segments, extent, timing, and diagnostics."""

    records: StreamRecordsHandle
    segments: tuple[ConnectionSegment, ...]
    captured_bytes: ByteCount
    extent: StreamExtent
    trailing_incomplete: tuple[TrailingIncompleteItem, ...]
    timings: StreamTimings
    diagnostics: tuple[Diagnostic, ...] = ()
    non_segment_attempts: tuple[StreamNonSegmentOccurrence, ...] = ()
    termination_evidence: StreamTerminationEvidence | None = None

    def __post_init__(self) -> None:
        """Require ordered contiguous segment indexes and exact retained stream facts."""
        if type(self.records) is not StreamRecordsHandle:
            raise TypeError("stream records must be StreamRecordsHandle")
        if type(self.captured_bytes) is not ByteCount:
            raise TypeError("stream captured byte extent must be ByteCount")
        _validate_segments(self.records, self.segments, self.captured_bytes)
        terminal_exchange, terminal_occurrence = validate_stream_owner(
            self.records,
            self.segments,
            self.non_segment_attempts,
        )
        _validate_segment_body_extents(self.segments)
        if type(self.extent) is not StreamExtent:
            raise TypeError("stream extent must be StreamExtent")
        _validate_trailing_items(self.trailing_incomplete, self.segments)
        terminal_has_trailing = any(
            item.segment_index == self.segments[-1].index for item in self.trailing_incomplete
        )
        if terminal_has_trailing and self.extent.completeness is not StreamCompleteness.INCOMPLETE:
            raise ValueError("a terminal trailing item requires incomplete stream extent")
        _validate_stream_timing(self.records, self.timings)
        if self.termination_evidence is not None and not is_stream_termination_evidence(
            self.termination_evidence
        ):
            raise TypeError("stream termination evidence must be one exact typed variant")
        validate_terminal_stream_extent(
            terminal_segment=self.segments[-1],
            terminal_exchange=terminal_exchange,
            terminal_occurrence=terminal_occurrence,
            extent=self.extent,
            evidence=self.termination_evidence,
            records=self.records,
            captured_bytes=self.captured_bytes,
            timings=self.timings,
            terminal_has_trailing=terminal_has_trailing,
        )
        _validate_stream_diagnostics(self.diagnostics)

    def validate_item_envelope(self, envelope: StreamItemEnvelope) -> None:
        """Validate one store-returned item against its exact segment view and byte extent."""
        self.validate_item_envelopes((envelope,))

    @property
    def stream_id(self) -> StreamId:
        """Derive the one logical-stream identity owned by this Environment execution."""
        return StreamId(value=self.records.owner.execution_id.value)

    def validate_item_envelopes(
        self,
        envelopes: tuple[StreamItemEnvelope, ...],
    ) -> None:
        """Validate one immutable store window with unique exact item coordinates."""
        validate_stream_item_envelopes(self, envelopes)
        _validate_nested_budget_attempts(self, envelopes)
