"""Bounded post-capture decoding for storage-backed HTTP record streams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import NoReturn
from typing import cast
from typing import final
from uuid import uuid5

import msgspec

from comparo.adapters.http.storage import StoredBodyStream
from comparo.adapters.http.storage import TransientBodyStore
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ParsedMediaType
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_attempts import StreamConnectionFailureOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentResponseOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import JsonStreamRecord
from comparo.core.domain.http.stream_items import JsonStreamRecordError
from comparo.core.domain.http.stream_items import SseComment
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseFieldName
from comparo.core.domain.http.stream_items import SseFieldOccurrence
from comparo.core.domain.http.stream_items import SseIdStateTransition
from comparo.core.domain.http.stream_items import SseJsonDecodeError
from comparo.core.domain.http.stream_items import SseJsonReady
from comparo.core.domain.http.stream_items import SseNestedDataView
from comparo.core.domain.http.stream_items import SseRetryControl
from comparo.core.domain.http.stream_items import SseUnknownField
from comparo.core.domain.http.stream_items import StreamItem
from comparo.core.domain.http.stream_items import StreamItemCategory
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.stream_items import StreamItemLocation
from comparo.core.domain.http.stream_termination import FramingDecodeFailureEvidence
from comparo.core.domain.http.stream_termination import SafetyCountLimitEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationReason
from comparo.core.domain.http.streams import ConnectionSegment
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamRecordsHandle
from comparo.core.domain.http.streams import TrailingIncompleteItem
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderIdentity
from comparo.core.domain.http.views import DecoderRegistryIdentity
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewStorageToken
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot

_VIEW_LOCATOR_ORIGIN = TaintOriginId(value="runtime/stream-view-locator")
_DIGEST_ACCESS = SensitiveValueAccess(purpose="http.stream-decoder.fingerprint")
_UNAVAILABLE_RECEIPT = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
_REGISTRY = DecoderRegistryIdentity(value="comparo/http-media@1")
_JSON_DECODER = DecoderSpec(
    identity=DecoderIdentity(value="comparo/json@1"),
    view=ViewKind.JSON,
)


@final
class StreamDecodingLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-supplied record and framing limits with no application-level maxima."""

    maximum_records: PositiveCount
    maximum_controls: PositiveCount
    maximum_errors: PositiveCount
    maximum_item_bytes: ByteCount

    def __post_init__(self) -> None:
        """Require finite positive limits for every retained stream dimension."""
        if any(
            type(value) is not PositiveCount
            for value in (self.maximum_records, self.maximum_controls, self.maximum_errors)
        ):
            raise TypeError("stream item limits must be PositiveCount values")
        if type(self.maximum_item_bytes) is not ByteCount:
            raise TypeError("stream item byte limit must be ByteCount")
        if self.maximum_item_bytes.value == 0:
            raise ValueError("stream item byte limit must be positive")


@final
class SseDataJsonPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Explicitly decode SSE data as strict JSON, optionally only for named event types."""

    event_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require immutable, unique, non-empty event type names."""
        if type(self.event_types) is not tuple or any(
            type(value) is not str or not value for value in self.event_types
        ):
            raise TypeError("SSE JSON event types must be an immutable tuple of non-empty text")
        if len(set(self.event_types)) != len(self.event_types):
            raise ValueError("SSE JSON event types must be unique")

    def applies_to(self, event_type: str) -> bool:
        """Return whether nested JSON decoding was explicitly enabled for this event."""
        return not self.event_types or event_type in self.event_types


class StoredStreamSnapshotProvider:
    """Project an automatically recognized terminal stream through one body-store port.

    The provider reads incrementally from the transient store, never reopens the network, and
    enriches only the immutable observation returned in its snapshot. Store lifetime remains with
    the composition root so request materialization, transport capture, and selection may share it.
    """

    __slots__ = ("_limits", "_sse_json", "_store")

    def __init__(
        self,
        *,
        store: TransientBodyStore,
        limits: StreamDecodingLimits,
        sse_json: SseDataJsonPolicy | None = None,
    ) -> None:
        """Compose one run-local store with explicit stream decoding limits."""
        if type(store) is not TransientBodyStore:
            raise TypeError("stream snapshot provider requires TransientBodyStore")
        if type(limits) is not StreamDecodingLimits:
            raise TypeError("stream snapshot provider requires StreamDecodingLimits")
        if sse_json is not None and type(sse_json) is not SseDataJsonPolicy:
            raise TypeError("SSE nested JSON policy must be SseDataJsonPolicy")
        self._store = store
        self._limits = limits
        self._sse_json = sse_json

    async def snapshot(
        self,
        observation: EnvironmentExecutionObservation,
    ) -> SelectionSnapshot:
        """Decode one terminal auto-recognized stream into an I/O-free closed snapshot."""
        if type(observation) is not EnvironmentExecutionObservation:
            raise TypeError("stream snapshot requires EnvironmentExecutionObservation")
        target = _terminal_stream_target(observation)
        if target is None:
            return SelectionSnapshot(observation=observation)
        attempt, exchange, handle, kind = target
        decoder = _decoder(kind)
        view = _view_handle(handle, decoder, complete=False)
        parsed = await _parse_stream(
            self._store.open_response(handle),
            view=view,
            kind=kind,
            limits=self._limits,
            capture_complete=_capture_complete(exchange),
            sse_json=self._sse_json,
        )
        complete = _capture_complete(exchange) and parsed.stop is None and not parsed.decode_failed
        view = _view_handle(handle, decoder, complete=complete)
        envelopes = tuple(
            StreamItemEnvelope(view=view, item=envelope.item) for envelope in parsed.envelopes
        )
        enriched = msgspec.structs.replace(observation, views=(*observation.views, view))
        evidence = _termination_evidence(exchange, parsed)
        extent = _stream_extent(exchange, evidence)
        segment = ConnectionSegment(
            index=Count(value=0),
            attempt_id=attempt.attempt_id,
            exchange_id=exchange.exchange_id,
            view=view,
            first_item_index=Count(value=0),
            item_count=Count(value=len(envelopes)),
            first_record_index=Count(value=0),
            record_count=Count(value=parsed.records),
            first_control_index=Count(value=0),
            control_count=Count(value=parsed.controls),
            first_error_index=Count(value=0),
            error_count=Count(value=parsed.errors),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=handle.byte_count),
            end_reason=_segment_end(exchange, evidence),
        )
        stream = StreamObservation(
            records=StreamRecordsHandle(
                owner=enriched,
                kind=kind,
                record_count=Count(value=parsed.records),
                control_count=Count(value=parsed.controls),
                error_count=Count(value=parsed.errors),
            ),
            segments=(segment,),
            captured_bytes=handle.byte_count,
            extent=extent,
            trailing_incomplete=_trailing(parsed, handle),
            timings=StreamTimings(
                time_to_first_record=(
                    _UNAVAILABLE_RECEIPT
                    if parsed.records
                    else NotPerformed(reason=NotPerformedReason.EMPTY_STREAM)
                ),
                observation_window_elapsed=exchange.timings.elapsed,
            ),
            non_segment_attempts=_non_segment_attempts(enriched, attempt),
            termination_evidence=evidence,
        )
        resolved = ResolvedBodyView(
            handle=view,
            requested_view=BodyViewKind.AUTO,
            value=_view_payload(parsed.record_values, handle),
            payload_fingerprint=view.fingerprint,
        )
        return SelectionSnapshot(
            observation=enriched,
            body_views=(resolved,),
            stream=stream,
            stream_items=envelopes,
        )

    async def close(self) -> None:
        """Release no resources because the composition root owns the shared body store."""


@dataclass(frozen=True, slots=True)
class _Line:
    value: bytes
    start: int
    end: int
    number: int
    terminated: bool


@dataclass(frozen=True, slots=True)
class _SequenceFrame:
    value: bytes
    start: int
    end: int
    number: int


class _FramingLimitError(Exception):
    __slots__ = ("start",)

    def __init__(self, start: int) -> None:
        self.start = start
        super().__init__("stream framing item exceeded its configured byte limit")


class _StopKind(str):
    RECORD_LIMIT = "record-limit"
    FRAMING_LIMIT = "framing-limit"


@dataclass(frozen=True, slots=True)
class _Stop:
    kind: str
    trailing_start: int


@dataclass(slots=True, repr=False)
class _ParsedStream:
    envelopes: list[StreamItemEnvelope]
    record_values: list[JsonValue]
    records: int = 0
    controls: int = 0
    errors: int = 0
    stop: _Stop | None = None
    decode_failed: bool = False


@dataclass(slots=True, repr=False)
class _ItemBuilder:
    view: DecodedViewHandle
    limits: StreamDecodingLimits
    parsed: _ParsedStream

    def location(
        self,
        *,
        category: StreamItemCategory,
        start: int,
        end: int,
        line: int | None,
    ) -> StreamItemLocation:
        """Create a domain location using the next exact global/category indexes."""
        category_index = {
            StreamItemCategory.RECORD: self.parsed.records,
            StreamItemCategory.CONTROL: self.parsed.controls,
            StreamItemCategory.ERROR: self.parsed.errors,
        }[category]
        return StreamItemLocation(
            segment_index=Count(value=0),
            item_index=Count(value=len(self.parsed.envelopes)),
            category_index=Count(value=category_index),
            byte_range=ByteRange(
                start=ByteCount(value=start),
                end_exclusive=ByteCount(value=end),
            ),
            line=PositiveCount(value=line) if line is not None else None,
            received_offset=_UNAVAILABLE_RECEIPT,
        )

    def append(self, item: StreamItem) -> _Stop | None:
        """Append one item or return the exact configured count stop before retaining it."""
        category = _category(item)
        current, maximum = {
            StreamItemCategory.RECORD: (self.parsed.records, self.limits.maximum_records.value),
            StreamItemCategory.CONTROL: (
                self.parsed.controls,
                self.limits.maximum_controls.value,
            ),
            StreamItemCategory.ERROR: (self.parsed.errors, self.limits.maximum_errors.value),
        }[category]
        if current >= maximum:
            return _Stop(
                kind=(
                    _StopKind.RECORD_LIMIT
                    if category is StreamItemCategory.RECORD
                    else _StopKind.FRAMING_LIMIT
                ),
                trailing_start=item.location.byte_range.start.value,
            )
        self.parsed.envelopes.append(StreamItemEnvelope(view=self.view, item=item))
        if category is StreamItemCategory.RECORD:
            self.parsed.records += 1
            self.parsed.record_values.append(
                _record_value(cast("SseEvent | JsonStreamRecord", item))
            )
        elif category is StreamItemCategory.CONTROL:
            self.parsed.controls += 1
        else:
            self.parsed.errors += 1
            self.parsed.decode_failed = True
        return None


async def _parse_stream(
    source: StoredBodyStream,
    *,
    view: DecodedViewHandle,
    kind: StreamKind,
    limits: StreamDecodingLimits,
    capture_complete: bool,
    sse_json: SseDataJsonPolicy | None,
) -> _ParsedStream:
    parsed = _ParsedStream(envelopes=[], record_values=[])
    builder = _ItemBuilder(view=view, limits=limits, parsed=parsed)
    try:
        if kind is StreamKind.SSE:
            await _parse_sse(
                source,
                builder=builder,
                capture_complete=capture_complete,
                sse_json=sse_json,
            )
        elif kind is StreamKind.NDJSON:
            await _parse_ndjson(source, builder=builder, capture_complete=capture_complete)
        else:
            await _parse_json_sequence(source, builder=builder)
    except _FramingLimitError as error:
        parsed.stop = _Stop(kind=_StopKind.FRAMING_LIMIT, trailing_start=error.start)
        parsed.decode_failed = True
    return parsed


async def _parse_ndjson(
    source: StoredBodyStream,
    *,
    builder: _ItemBuilder,
    capture_complete: bool,
) -> None:
    async for line in _iter_lines(source, builder.limits.maximum_item_bytes.value):
        if not line.value.strip():
            continue
        if not line.terminated and not capture_complete:
            builder.parsed.stop = _Stop(
                kind=_StopKind.FRAMING_LIMIT,
                trailing_start=line.start,
            )
            builder.parsed.decode_failed = True
            return
        value, error = _decode_json(line.value)
        if error is None:
            value = cast("JsonValue", value)
            item: StreamItem = JsonStreamRecord(
                value=_json_content(value, builder.view),
                location=builder.location(
                    category=StreamItemCategory.RECORD,
                    start=line.start,
                    end=line.end,
                    line=line.number,
                ),
            )
        else:
            item = JsonStreamRecordError(
                code=error,
                location=builder.location(
                    category=StreamItemCategory.ERROR,
                    start=line.start,
                    end=line.end,
                    line=line.number,
                ),
            )
        stop = builder.append(item)
        if stop is not None:
            builder.parsed.stop = stop
            return


async def _parse_json_sequence(source: StoredBodyStream, *, builder: _ItemBuilder) -> None:
    async for frame in _iter_sequence_frames(source, builder.limits.maximum_item_bytes.value):
        value, error = _decode_json(frame.value.rstrip(b"\r\n"))
        if error is None:
            value = cast("JsonValue", value)
            item: StreamItem = JsonStreamRecord(
                value=_json_content(value, builder.view),
                location=builder.location(
                    category=StreamItemCategory.RECORD,
                    start=frame.start,
                    end=frame.end,
                    line=None,
                ),
            )
        else:
            item = JsonStreamRecordError(
                code=error,
                location=builder.location(
                    category=StreamItemCategory.ERROR,
                    start=frame.start,
                    end=frame.end,
                    line=None,
                ),
            )
        stop = builder.append(item)
        if stop is not None:
            builder.parsed.stop = stop
            return


async def _parse_sse(  # noqa: C901 - protocol dispatch is clearest as one state machine
    source: StoredBodyStream,
    *,
    builder: _ItemBuilder,
    capture_complete: bool,
    sse_json: SseDataJsonPolicy | None,
) -> None:
    data: list[str] = []
    event_type = "message"
    last_event_id = ""
    fields: list[SseFieldOccurrence] = []
    event_start: int | None = None
    first = True
    async for line in _iter_lines(source, builder.limits.maximum_item_bytes.value):
        raw = line.value
        if first:
            first = False
            raw = raw.removeprefix(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            builder.parsed.stop = _Stop(
                kind=_StopKind.FRAMING_LIMIT,
                trailing_start=event_start if event_start is not None else line.start,
            )
            builder.parsed.decode_failed = True
            return
        if not line.terminated and not capture_complete:
            builder.parsed.stop = _Stop(
                kind=_StopKind.FRAMING_LIMIT,
                trailing_start=event_start if event_start is not None else line.start,
            )
            builder.parsed.decode_failed = True
            return
        if text == "":
            if data:
                location = builder.location(
                    category=StreamItemCategory.RECORD,
                    start=event_start if event_start is not None else line.start,
                    end=line.end,
                    line=(fields[0].line.value if fields else line.number),
                )
                joined = "\n".join(data)
                nested = _nested_sse_json(joined, event_type, builder.view, sse_json)
                stop = builder.append(
                    SseEvent(
                        data=_text_content(joined, builder.view),
                        event_type=_text_content(event_type, builder.view),
                        last_event_id=_text_content(last_event_id, builder.view),
                        fields=tuple(fields),
                        location=location,
                        nested_data=nested,
                    )
                )
                if stop is not None:
                    builder.parsed.stop = stop
                    return
            data = []
            event_type = "message"
            fields = []
            event_start = None
            continue
        event_start = line.start if event_start is None else event_start
        if line.end - event_start > builder.limits.maximum_item_bytes.value:
            builder.parsed.stop = _Stop(
                kind=_StopKind.FRAMING_LIMIT,
                trailing_start=event_start,
            )
            builder.parsed.decode_failed = True
            return
        if text.startswith(":"):
            stop = builder.append(
                SseComment(
                    value=_text_content(text[1:].removeprefix(" "), builder.view),
                    location=builder.location(
                        category=StreamItemCategory.CONTROL,
                        start=line.start,
                        end=line.end,
                        line=line.number,
                    ),
                )
            )
            if stop is not None:
                builder.parsed.stop = stop
                return
            continue
        name, separator, value = text.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        field_name = _sse_field_name(name)
        if field_name is not None:
            fields.append(
                SseFieldOccurrence(
                    name=field_name,
                    byte_range=ByteRange(
                        start=ByteCount(value=line.start),
                        end_exclusive=ByteCount(value=line.end),
                    ),
                    line=PositiveCount(value=line.number),
                )
            )
        if name == "data":
            data.append(value)
        elif name == "event":
            event_type = value or "message"
        elif name == "id" and "\x00" not in value:
            if value != last_event_id:
                previous = last_event_id
                last_event_id = value
                stop = builder.append(
                    SseIdStateTransition(
                        previous=_text_content(previous, builder.view),
                        current=_text_content(last_event_id, builder.view),
                        location=builder.location(
                            category=StreamItemCategory.CONTROL,
                            start=line.start,
                            end=line.end,
                            line=line.number,
                        ),
                    )
                )
                if stop is not None:
                    builder.parsed.stop = stop
                    return
        elif name == "retry" and value.isascii() and value.isdecimal():
            stop = builder.append(
                SseRetryControl(
                    retry_after=_duration_content(int(value) * 1_000_000, builder.view),
                    location=builder.location(
                        category=StreamItemCategory.CONTROL,
                        start=line.start,
                        end=line.end,
                        line=line.number,
                    ),
                )
            )
            if stop is not None:
                builder.parsed.stop = stop
                return
        elif name not in {"data", "event", "id"}:
            stop = builder.append(
                SseUnknownField(
                    name=_text_content(name, builder.view),
                    value=_text_content(value, builder.view),
                    location=builder.location(
                        category=StreamItemCategory.CONTROL,
                        start=line.start,
                        end=line.end,
                        line=line.number,
                    ),
                )
            )
            if stop is not None:
                builder.parsed.stop = stop
                return
    if event_start is not None:
        builder.parsed.stop = _Stop(
            kind=_StopKind.FRAMING_LIMIT,
            trailing_start=event_start,
        )
        builder.parsed.decode_failed = True


async def _iter_lines(source: StoredBodyStream, maximum: int) -> AsyncIterator[_Line]:
    buffer = bytearray()
    base = 0
    number = 1
    async for chunk in source:
        buffer.extend(chunk)
        while True:
            terminator = _line_terminator(buffer, eof=False)
            if terminator is None:
                if len(buffer) > maximum:
                    raise _FramingLimitError(base)
                break
            index, width = terminator
            if index > maximum:
                raise _FramingLimitError(base)
            end = base + index + width
            yield _Line(
                value=bytes(buffer[:index]),
                start=base,
                end=end,
                number=number,
                terminated=True,
            )
            del buffer[: index + width]
            base = end
            number += 1
    while buffer:
        terminator = _line_terminator(buffer, eof=True)
        if terminator is None:
            yield _Line(
                value=bytes(buffer),
                start=base,
                end=base + len(buffer),
                number=number,
                terminated=False,
            )
            return
        index, width = terminator
        end = base + index + width
        yield _Line(
            value=bytes(buffer[:index]),
            start=base,
            end=end,
            number=number,
            terminated=True,
        )
        del buffer[: index + width]
        base = end
        number += 1


def _line_terminator(buffer: bytearray, *, eof: bool) -> tuple[int, int] | None:
    for index, value in enumerate(buffer):
        if value == 0x0A:
            return index, 1
        if value == 0x0D:
            if index + 1 == len(buffer) and not eof:
                return None
            width = 2 if index + 1 < len(buffer) and buffer[index + 1] == 0x0A else 1
            return index, width
    return None


async def _iter_sequence_frames(  # noqa: C901 - incremental framing keeps state explicit
    source: StoredBodyStream,
    maximum: int,
) -> AsyncIterator[_SequenceFrame]:
    buffer = bytearray()
    base = 0
    current_start: int | None = None
    number = 1
    async for chunk in source:
        buffer.extend(chunk)
        while True:
            marker = buffer.find(0x1E)
            if current_start is None:
                if marker < 0:
                    if len(buffer) > maximum:
                        raise _FramingLimitError(base)
                    break
                if marker:
                    prefix = bytes(buffer[:marker])
                    if prefix.strip():
                        yield _SequenceFrame(
                            value=b"",
                            start=base,
                            end=base + marker,
                            number=number,
                        )
                        number += 1
                    del buffer[:marker]
                    base += marker
                current_start = base
                del buffer[:1]
                base += 1
                continue
            if marker < 0:
                if len(buffer) > maximum:
                    raise _FramingLimitError(current_start)
                break
            end = base + marker
            yield _SequenceFrame(
                value=bytes(buffer[:marker]),
                start=current_start,
                end=end,
                number=number,
            )
            number += 1
            del buffer[: marker + 1]
            base = end + 1
            current_start = end
    if current_start is None:
        if buffer.strip():
            yield _SequenceFrame(value=b"", start=base, end=base + len(buffer), number=number)
        return
    yield _SequenceFrame(
        value=bytes(buffer),
        start=current_start,
        end=base + len(buffer),
        number=number,
    )


@dataclass(frozen=True, slots=True)
class _JsonObjectPairs:
    pairs: tuple[tuple[str, object], ...]


class _DuplicateMemberError(ValueError):
    pass


class _NonFiniteNumberError(ValueError):
    pass


def _object_pairs(pairs: list[tuple[str, object]]) -> _JsonObjectPairs:
    return _JsonObjectPairs(pairs=tuple(pairs))


def _reject_constant(_value: str) -> NoReturn:
    raise _NonFiniteNumberError


def _decode_json(raw: bytes) -> tuple[JsonValue | None, DecodeErrorCode | None]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, DecodeErrorCode.INVALID_TEXT
    try:
        decoded = cast(
            "object",
            json.loads(
                text,
                parse_float=Decimal,
                parse_int=int,
                parse_constant=_reject_constant,
                object_pairs_hook=_object_pairs,
            ),
        )
        return _json_value(decoded), None
    except _DuplicateMemberError:
        return None, DecodeErrorCode.DUPLICATE_MEMBER
    except _NonFiniteNumberError:
        return None, DecodeErrorCode.NON_FINITE_NUMBER
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        return None, DecodeErrorCode.INVALID_SYNTAX


def _json_value(value: object) -> JsonValue:
    if value is None:
        return JsonNull()
    if type(value) is bool:
        return JsonBoolean(value=value)
    if type(value) is int:
        return JsonInteger(value=value)
    if type(value) is Decimal:
        if value == value.to_integral_value():
            return JsonInteger(value=int(value))
        return JsonDecimal(value=value.normalize())
    if type(value) is str:
        return JsonString(value=value)
    if type(value) is list:
        return JsonArray(items=tuple(_json_value(item) for item in value))
    if type(value) is _JsonObjectPairs:
        names = tuple(name for name, _item in value.pairs)
        if len(set(names)) != len(names):
            raise _DuplicateMemberError
        return JsonObject(
            members=tuple(
                JsonObjectMember(name=name, value=_json_value(item)) for name, item in value.pairs
            )
        )
    raise TypeError("JSON decoder returned an unsupported value")


def _terminal_stream_target(
    observation: EnvironmentExecutionObservation,
) -> tuple[HttpAttempt, HttpExchange, BodyHandle, StreamKind] | None:
    if not observation.attempts:
        return None
    attempt = observation.attempts[-1]
    if not attempt.exchanges:
        return None
    exchange = attempt.exchanges[-1]
    response = exchange.response
    if response is None or type(response.body) is not ResponseEntityBody:
        return None
    handle = response.body.handle
    if handle is None:
        return None
    kind = _stream_kind(handle)
    return None if kind is None else (attempt, exchange, handle, kind)


def _stream_kind(handle: BodyHandle) -> StreamKind | None:
    media = handle.media
    if type(media) is not ParsedMediaType:
        return None
    value = media.value
    if (value.type, value.subtype) == ("text", "event-stream"):
        return StreamKind.SSE
    if (value.type, value.subtype) in {
        ("application", "x-ndjson"),
        ("application", "ndjson"),
    }:
        return StreamKind.NDJSON
    if (value.type, value.subtype) == ("application", "json-seq"):
        return StreamKind.JSON_SEQUENCE
    return None


def _decoder(kind: StreamKind) -> DecoderSpec:
    view = ViewKind(kind.value)
    return DecoderSpec(
        identity=DecoderIdentity(value=f"comparo/{view.value}@1"),
        view=view,
    )


def _view_handle(
    source: BodyHandle,
    decoder: DecoderSpec,
    *,
    complete: bool,
) -> DecodedViewHandle:
    view_id = ViewId(value=uuid5(source.body_id.value, f"stream:{decoder.identity.value}"))
    digest = source.digest
    if is_sensitive_value(digest):
        sensitive_digest = cast(SensitiveValue[Sha256Digest], digest)
        revealed = sensitive_digest.reveal(access=_DIGEST_ACCESS)
        fingerprint: Fingerprint | SensitiveValue[Fingerprint] = SensitiveValue(
            Fingerprint(algorithm="sha256", digest=revealed),
            origins=sensitive_digest.origins,
        )
    else:
        fingerprint = Fingerprint(algorithm="sha256", digest=cast(Sha256Digest, digest))
    auto = AutoViewSelection(registry=_REGISTRY, decoder=decoder)
    return DecodedViewHandle(
        view_id=view_id,
        source=source,
        locator=SensitiveValue(
            ViewStorageToken(value=view_id.value.hex),
            origins=(_VIEW_LOCATOR_ORIGIN,),
        ),
        decoder=decoder,
        fingerprint=fingerprint,
        sensitivity=source.sensitivity,
        completeness=ViewCompleteness.FULL if complete else ViewCompleteness.PREFIX,
        auto_selection=auto,
    )


def _capture_complete(exchange: HttpExchange) -> bool:
    response = exchange.response
    return (
        response is not None
        and type(response.body) is ResponseEntityBody
        and type(response.body.capture) is CompleteCapture
        and response.body.handle is not None
        and response.body.handle.extent is BodyCaptureExtent.COMPLETE
    )


def _termination_evidence(
    exchange: HttpExchange,
    parsed: _ParsedStream,
) -> StreamTerminationEvidence | None:
    if exchange.failure is not None:
        return None
    if parsed.stop is not None and parsed.stop.kind == _StopKind.RECORD_LIMIT:
        return SafetyCountLimitEvidence(limit=PositiveCount(value=parsed.records))
    if parsed.stop is not None or parsed.decode_failed or not _capture_complete(exchange):
        return FramingDecodeFailureEvidence()
    return None


def _stream_extent(
    exchange: HttpExchange,
    evidence: StreamTerminationEvidence | None,
) -> StreamExtent:
    if exchange.failure is not None:
        termination = {
            FailurePhase.CANCELLATION: StreamTerminationReason.CANCELLED,
            FailurePhase.CONTENT_DECODING: StreamTerminationReason.DECODE_FAILURE,
            FailurePhase.RESOURCE_BUDGET: StreamTerminationReason.RESOURCE_BUDGET,
            FailurePhase.STORAGE: StreamTerminationReason.STORAGE_FAILURE,
            FailurePhase.POLICY: StreamTerminationReason.POLICY_BLOCKED,
        }.get(exchange.failure.phase, StreamTerminationReason.TRANSPORT_FAILURE)
        return StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=termination,
        )
    if type(evidence) is SafetyCountLimitEvidence:
        return StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.SAFETY_COUNT_LIMIT,
        )
    if type(evidence) is FramingDecodeFailureEvidence:
        return StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.DECODE_FAILURE,
        )
    return StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )


def _segment_end(
    exchange: HttpExchange,
    evidence: StreamTerminationEvidence | None,
) -> SegmentEndReason:
    if exchange.failure is not None:
        return (
            SegmentEndReason.CANCELLED
            if exchange.failure.phase is FailurePhase.CANCELLATION
            else SegmentEndReason.FAILURE
        )
    return SegmentEndReason.REMOTE_EOF if evidence is None else SegmentEndReason.FAILURE


def _trailing(
    parsed: _ParsedStream,
    handle: BodyHandle,
) -> tuple[TrailingIncompleteItem, ...]:
    if parsed.stop is None or parsed.stop.trailing_start >= handle.byte_count.value:
        return ()
    return (
        TrailingIncompleteItem(
            byte_range=ByteRange(
                start=ByteCount(value=parsed.stop.trailing_start),
                end_exclusive=handle.byte_count,
            ),
            segment_index=Count(value=0),
        ),
    )


def _non_segment_attempts(
    observation: EnvironmentExecutionObservation,
    segment_attempt: HttpAttempt,
) -> tuple[StreamNonSegmentOccurrence, ...]:
    occurrences: list[StreamNonSegmentOccurrence] = []
    for attempt in observation.attempts:
        if attempt.attempt_id == segment_attempt.attempt_id:
            continue
        if attempt.pre_send is not None:
            occurrences.append(
                StreamPreSendOccurrence(attempt_id=attempt.attempt_id, outcome=attempt.pre_send)
            )
            continue
        terminal = attempt.exchanges[-1]
        if terminal.response is None:
            failure = cast("TransportFailure", terminal.failure)
            occurrences.append(
                StreamConnectionFailureOccurrence(
                    attempt_id=attempt.attempt_id,
                    exchange_id=terminal.exchange_id,
                    failure=failure,
                )
            )
        else:
            occurrences.append(
                StreamNonSegmentResponseOccurrence(
                    attempt_id=attempt.attempt_id,
                    exchange_id=terminal.exchange_id,
                    response=terminal.response,
                    failure=terminal.failure,
                )
            )
    return tuple(occurrences)


def _sse_field_name(name: str) -> SseFieldName | None:
    try:
        return SseFieldName(name)
    except ValueError:
        return None


def _nested_sse_json(
    data: str,
    event_type: str,
    view: DecodedViewHandle,
    policy: SseDataJsonPolicy | None,
) -> SseNestedDataView | None:
    if policy is None or not policy.applies_to(event_type):
        return None
    value, error = _decode_json(data.encode("utf-8"))
    if error is not None:
        return SseJsonDecodeError(decoder=_JSON_DECODER, code=error)
    return SseJsonReady(
        decoder=_JSON_DECODER,
        value=_json_content(cast("JsonValue", value), view),
    )


def _text_content(
    value: str,
    view: DecodedViewHandle,
) -> BodyText | SensitiveValue[BodyText]:
    payload = BodyText(value=value)
    origins = _origins(view.source)
    return SensitiveValue(payload, origins=origins) if origins else payload


def _duration_content(
    nanoseconds: int,
    view: DecodedViewHandle,
) -> Duration | SensitiveValue[Duration]:
    payload = Duration(nanoseconds=nanoseconds)
    origins = _origins(view.source)
    return SensitiveValue(payload, origins=origins) if origins else payload


def _json_content(
    value: JsonValue,
    view: DecodedViewHandle,
) -> JsonValue | SensitiveValue[JsonValue]:
    origins = _origins(view.source)
    return SensitiveValue(value, origins=origins) if origins else value


def _view_payload(
    values: list[JsonValue],
    handle: BodyHandle,
) -> JsonArray | SensitiveValue[JsonArray]:
    payload = JsonArray(items=tuple(values))
    origins = _origins(handle)
    return SensitiveValue(payload, origins=origins) if origins else payload


def _origins(
    handle: BodyHandle | RepeatableByteSource,
) -> tuple[TaintOriginId, ...]:
    digest = handle.digest
    return digest.origins if is_sensitive_value(digest) else ()


def _category(item: StreamItem) -> StreamItemCategory:
    if type(item) in {SseEvent, JsonStreamRecord}:
        return StreamItemCategory.RECORD
    if type(item) is JsonStreamRecordError:
        return StreamItemCategory.ERROR
    return StreamItemCategory.CONTROL


def _record_value(item: SseEvent | JsonStreamRecord) -> JsonValue:
    if type(item) is JsonStreamRecord:
        value = item.value
        if is_sensitive_value(value):
            sensitive = cast("SensitiveValue[JsonValue]", value)
            return sensitive.reveal(access=_DIGEST_ACCESS)
        return cast("JsonValue", value)
    event = item
    return JsonObject(
        members=(
            JsonObjectMember(name="data", value=JsonString(value=_plain_text(event.data))),
            JsonObjectMember(
                name="event",
                value=JsonString(value=_plain_text(event.event_type)),
            ),
            JsonObjectMember(
                name="lastEventId",
                value=JsonString(value=_plain_text(event.last_event_id)),
            ),
        )
    )


def _plain_text(value: BodyText | SensitiveValue[BodyText]) -> str:
    if is_sensitive_value(value):
        return cast("SensitiveValue[BodyText]", value).reveal(access=_DIGEST_ACCESS).value
    return cast("BodyText", value).value


__all__ = (
    "SseDataJsonPolicy",
    "StoredStreamSnapshotProvider",
    "StreamDecodingLimits",
)
