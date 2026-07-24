"""Typed SSE and streaming-JSON items with application-record coordinates."""

from __future__ import annotations

from itertools import pairwise
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import TextContent
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import _validate_view_budget_exhaustion
from comparo.core.domain.http.views import _view_budget_kind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value

type StreamJsonValue = JsonValue | SensitiveValue[JsonValue]


def _is_stream_text(value: object) -> TypeGuard[TextContent]:
    return type(value) is BodyText or (is_sensitive_value(value) and value.payload_type is BodyText)


def _is_stream_json(value: object) -> TypeGuard[StreamJsonValue]:
    return is_json_value(value) or (
        is_sensitive_value(value)
        and value.payload_type
        in {JsonNull, JsonBoolean, JsonInteger, JsonDecimal, JsonString, JsonArray, JsonObject}
    )


@final
class ByteRange(msgspec.Struct, frozen=True, kw_only=True):
    """A half-open byte range in the captured decoded entity."""

    start: ByteCount
    end_exclusive: ByteCount

    def __post_init__(self) -> None:
        """Require an ordered exact byte range."""
        if type(self.start) is not ByteCount or type(self.end_exclusive) is not ByteCount:
            raise TypeError("byte-range bounds must be ByteCount values")
        if self.end_exclusive.value < self.start.value:
            raise ValueError("byte-range end must not precede its start")


@final
class StreamItemLocation(msgspec.Struct, frozen=True, kw_only=True):
    """Stable connection, record, byte, line, and receive-time coordinates."""

    segment_index: Count
    item_index: Count
    category_index: Count
    byte_range: ByteRange
    line: PositiveCount | None
    received_offset: Duration

    def __post_init__(self) -> None:
        """Require exact typed coordinates without transport-chunk identity."""
        if any(
            type(index) is not Count
            for index in (self.segment_index, self.item_index, self.category_index)
        ):
            raise TypeError("stream segment, item, and category indexes must be Count values")
        if type(self.byte_range) is not ByteRange:
            raise TypeError("stream item byte range must be ByteRange")
        if self.line is not None and type(self.line) is not PositiveCount:
            raise TypeError("stream line must be PositiveCount when present")
        if type(self.received_offset) is not Duration:
            raise TypeError("stream received offset must be Duration")


class SseFieldName(DomainStrEnum):
    """EventSource fields whose raw occurrence provenance is retained."""

    DATA = "data"
    EVENT = "event"
    ID = "id"
    RETRY = "retry"


@final
class SseFieldOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """One recognized raw SSE field location before dispatch semantics."""

    name: SseFieldName
    byte_range: ByteRange
    line: PositiveCount

    def __post_init__(self) -> None:
        """Require typed field provenance while raw bytes remain storage-backed."""
        if type(self.name) is not SseFieldName:
            raise TypeError("SSE field name must be SseFieldName")
        if type(self.byte_range) is not ByteRange:
            raise TypeError("SSE field byte range must be ByteRange")
        if type(self.line) is not PositiveCount:
            raise TypeError("SSE field line must be PositiveCount")


@final
class SseJsonReady(msgspec.Struct, frozen=True, kw_only=True):
    """Strict JSON decoded explicitly from one dispatched event's text data."""

    decoder: DecoderSpec
    value: StreamJsonValue

    def __post_init__(self) -> None:
        """Require an explicit JSON decoder and exact immutable value."""
        if type(self.decoder) is not DecoderSpec or self.decoder.view is not ViewKind.JSON:
            raise ValueError("nested SSE data requires a JSON DecoderSpec")
        if not _is_stream_json(self.value):
            raise TypeError("nested SSE JSON must be JsonValue or SensitiveValue")


@final
class SseJsonDecodeError(msgspec.Struct, frozen=True, kw_only=True):
    """A nested JSON failure belonging only to its concrete outer event."""

    decoder: DecoderSpec
    code: DecodeErrorCode

    def __post_init__(self) -> None:
        """Require JSON decoder identity and a stable typed failure."""
        if type(self.decoder) is not DecoderSpec or self.decoder.view is not ViewKind.JSON:
            raise ValueError("nested SSE decode failure requires a JSON DecoderSpec")
        if type(self.code) is not DecodeErrorCode:
            raise TypeError("nested SSE decode error must be DecodeErrorCode")


@final
class SseJsonBudgetExceeded(msgspec.Struct, frozen=True, kw_only=True):
    """A nested event-data decode stopped at an independent JSON-view budget."""

    decoder: DecoderSpec
    exhaustion: BudgetExceeded

    def __post_init__(self) -> None:
        """Require JSON decoder identity and the exhausted view budget."""
        if type(self.decoder) is not DecoderSpec or self.decoder.view is not ViewKind.JSON:
            raise ValueError("nested SSE budget failure requires a JSON DecoderSpec")
        _validate_view_budget_exhaustion(self.exhaustion)
        if self.exhaustion.budget.resource not in {
            BudgetResource.DECODED_CHARACTERS,
            BudgetResource.JSON_DEPTH,
            BudgetResource.JSON_NODES,
            BudgetResource.JSON_TOKENS,
        }:
            raise ValueError("nested SSE JSON exhaustion requires a JSON-decoding resource")

    @property
    def budget(self) -> ViewBudgetKind:
        """Derive the local nested-view classification from canonical evidence."""
        return _view_budget_kind(self.exhaustion.budget.resource)


type SseNestedDataView = SseJsonReady | SseJsonDecodeError | SseJsonBudgetExceeded


def _validate_sse_event_fields(
    fields: tuple[SseFieldOccurrence, ...],
    location: StreamItemLocation,
) -> None:
    """Contain raw field provenance within one ordered, line-consistent event extent."""
    event_range = location.byte_range
    for field in fields:
        if field.byte_range.start == field.byte_range.end_exclusive:
            raise ValueError("SSE raw field byte ranges must be non-empty")
        if (
            field.byte_range.start.value < event_range.start.value
            or field.byte_range.end_exclusive.value > event_range.end_exclusive.value
        ):
            raise ValueError("SSE raw field byte range must fit its event location")
        if location.line is not None and field.line.value < location.line.value:
            raise ValueError("SSE raw field line cannot precede its event line")
    if any(
        current.byte_range.start.value < previous.byte_range.end_exclusive.value
        for previous, current in pairwise(fields)
    ):
        raise ValueError("SSE raw field ranges must be ordered and non-overlapping")
    if any(current.line.value <= previous.line.value for previous, current in pairwise(fields)):
        raise ValueError("SSE raw field lines must be strictly increasing")


@final
class SseEvent(msgspec.Struct, frozen=True, kw_only=True):
    """One complete dispatched EventSource event with joined text data."""

    data: TextContent
    event_type: TextContent
    last_event_id: TextContent
    fields: tuple[SseFieldOccurrence, ...]
    location: StreamItemLocation
    nested_data: SseNestedDataView | None = None

    def __post_init__(self) -> None:
        """Retain dispatch semantics, raw field locations, and optional explicit nested JSON."""
        for label, value in (
            ("data", self.data),
            ("event type", self.event_type),
            ("last event ID", self.last_event_id),
        ):
            if not _is_stream_text(value):
                raise TypeError(f"SSE {label} must be BodyText or SensitiveValue")
        if type(self.fields) is not tuple or any(
            type(field) is not SseFieldOccurrence for field in self.fields
        ):
            raise TypeError("SSE raw fields must be an immutable typed tuple")
        if type(self.location) is not StreamItemLocation:
            raise TypeError("SSE event location must be StreamItemLocation")
        _validate_sse_event_fields(self.fields, self.location)
        if self.nested_data is not None and type(self.nested_data) not in {
            SseJsonReady,
            SseJsonDecodeError,
            SseJsonBudgetExceeded,
        }:
            raise TypeError("SSE nested data must be a supported nested JSON outcome")


@final
class SseComment(msgspec.Struct, frozen=True, kw_only=True):
    """One heartbeat or application comment, distinct from dispatched events."""

    value: TextContent
    location: StreamItemLocation

    def __post_init__(self) -> None:
        """Require sensitivity-preserving text and stable coordinates."""
        if not _is_stream_text(self.value):
            raise TypeError("SSE comment must be BodyText or SensitiveValue")
        if type(self.location) is not StreamItemLocation:
            raise TypeError("SSE comment location must be StreamItemLocation")


@final
class SseRetryControl(msgspec.Struct, frozen=True, kw_only=True):
    """A parsed server retry hint, kept out of default event comparison."""

    retry_after: Duration | SensitiveValue[Duration]
    location: StreamItemLocation

    def __post_init__(self) -> None:
        """Require an exact duration and stable coordinates."""
        if type(self.retry_after) is not Duration and not is_sensitive_value(self.retry_after):
            raise TypeError("SSE retry hint must be Duration or SensitiveValue")
        if is_sensitive_value(self.retry_after) and self.retry_after.payload_type is not Duration:
            raise TypeError("sensitive SSE retry hint payload must be Duration")
        if type(self.location) is not StreamItemLocation:
            raise TypeError("SSE retry location must be StreamItemLocation")


@final
class SseIdStateTransition(msgspec.Struct, frozen=True, kw_only=True):
    """One EventSource last-event-ID state transition, including an empty-ID reset."""

    previous: TextContent
    current: TextContent
    location: StreamItemLocation

    def __post_init__(self) -> None:
        """Retain both states independently from dispatched event records."""
        if not _is_stream_text(self.previous) or not _is_stream_text(self.current):
            raise TypeError("SSE ID transition states must be BodyText or SensitiveValue")
        if type(self.location) is not StreamItemLocation:
            raise TypeError("SSE ID transition location must be StreamItemLocation")


@final
class SseUnknownField(msgspec.Struct, frozen=True, kw_only=True):
    """An unknown SSE field retained as a selectable diagnostic control."""

    name: TextContent
    value: TextContent
    location: StreamItemLocation

    def __post_init__(self) -> None:
        """Require exact text and coordinates without assigning protocol meaning."""
        for value in (self.name, self.value):
            if not _is_stream_text(value):
                raise TypeError("unknown SSE field name and value must be typed text")
        if type(self.location) is not StreamItemLocation:
            raise TypeError("unknown SSE field location must be StreamItemLocation")


type SseItem = SseEvent | SseComment | SseRetryControl | SseIdStateTransition | SseUnknownField


@final
class JsonStreamRecord(msgspec.Struct, frozen=True, kw_only=True):
    """One complete NDJSON line or JSON text-sequence record."""

    value: StreamJsonValue
    location: StreamItemLocation

    def __post_init__(self) -> None:
        """Require exact JSON and framing-aware coordinates."""
        if not _is_stream_json(self.value):
            raise TypeError("JSON stream record must be JsonValue or SensitiveValue")
        if type(self.location) is not StreamItemLocation:
            raise TypeError("JSON stream record location must be StreamItemLocation")


@final
class JsonStreamRecordError(msgspec.Struct, frozen=True, kw_only=True):
    """One malformed record that does not erase earlier complete records."""

    code: DecodeErrorCode
    location: StreamItemLocation

    def __post_init__(self) -> None:
        """Require a typed decode error and concrete record location."""
        if type(self.code) is not DecodeErrorCode:
            raise TypeError("JSON stream record error must be DecodeErrorCode")
        if type(self.location) is not StreamItemLocation:
            raise TypeError("JSON stream error location must be StreamItemLocation")


type JsonStreamItem = JsonStreamRecord | JsonStreamRecordError
type StreamItem = SseItem | JsonStreamItem


class StreamItemCategory(DomainStrEnum):
    """Independent stored count dimensions for stream item variants."""

    RECORD = "record"
    CONTROL = "control"
    ERROR = "error"


def stream_item_category(item: StreamItem) -> StreamItemCategory:
    """Classify one exact stream item variant into its aggregate count dimension."""
    if type(item) in {SseEvent, JsonStreamRecord}:
        return StreamItemCategory.RECORD
    if type(item) is JsonStreamRecordError:
        return StreamItemCategory.ERROR
    return StreamItemCategory.CONTROL


def _item_matches_view(item: StreamItem, view: ViewKind) -> bool:
    if type(item) in {JsonStreamRecord, JsonStreamRecordError}:
        return view in {ViewKind.NDJSON, ViewKind.JSON_SEQUENCE}
    return view is ViewKind.SSE


def _item_payload_sensitivity(item: StreamItem) -> tuple[bool, bool]:
    """Return whether an item contains sealed and public payload components."""
    payloads: tuple[object, ...]
    if type(item) is SseEvent:
        payloads = (item.data, item.event_type, item.last_event_id)
        nested = item.nested_data
        if type(nested) is SseJsonReady:
            payloads += (nested.value,)
    elif type(item) is SseComment:
        payloads = (item.value,)
    elif type(item) is SseRetryControl:
        payloads = (item.retry_after,)
    elif type(item) is SseUnknownField:
        payloads = (item.name, item.value)
    elif type(item) is SseIdStateTransition:
        payloads = (item.previous, item.current)
    elif type(item) is JsonStreamRecord:
        payloads = (item.value,)
    else:
        payloads = ()
    return (
        any(is_sensitive_value(value) for value in payloads),
        any(not is_sensitive_value(value) for value in payloads),
    )


def _validate_nested_budget_body(item: StreamItem, view: DecodedViewHandle) -> None:
    """Bind nested SSE exhaustion evidence to the exact enclosing response body."""
    if type(item) is not SseEvent or type(item.nested_data) is not SseJsonBudgetExceeded:
        return
    scope = item.nested_data.exhaustion.budget.scope
    if type(scope) is not BodyBudgetScope or scope.body_id != view.body_id:
        raise ValueError("nested SSE budget scope must identify its owning response body")


@final
class StreamItemEnvelope(msgspec.Struct, frozen=True, kw_only=True):
    """One stored item bound to its owning view and parent sensitivity classification."""

    view: DecodedViewHandle
    item: StreamItem

    def __post_init__(self) -> None:
        """Prevent framing drift and sensitivity loss at stream-store read boundaries."""
        if type(self.view) is not DecodedViewHandle:
            raise TypeError("stream item envelope view must be DecodedViewHandle")
        if type(self.item) not in {
            SseEvent,
            SseComment,
            SseRetryControl,
            SseIdStateTransition,
            SseUnknownField,
            JsonStreamRecord,
            JsonStreamRecordError,
        }:
            raise TypeError("stream item envelope item must be a supported StreamItem")
        if not _item_matches_view(self.item, self.view.decoder.view):
            raise ValueError("stream item framing must match its owning decoded view")
        _validate_nested_budget_body(self.item, self.view)
        has_sensitive, has_public = _item_payload_sensitivity(self.item)
        if self.view.sensitivity is Sensitivity.PUBLIC and has_sensitive:
            raise ValueError("items from a public stream view cannot contain sealed payloads")
        if self.view.sensitivity is Sensitivity.SENSITIVE and has_public:
            raise ValueError("items from a sensitive stream view must retain sealed payloads")
