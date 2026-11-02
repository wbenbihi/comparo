"""Authenticated bounded structural projections of sanitized artifact bodies."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import NoReturn
from typing import cast
from typing import final

import msgspec

from comparo.application.artifact_references import ArtifactBodyReference
from comparo.application.artifacts import ArtifactBodyWindow
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import read_artifact_body_window

_SSE_LINES = re.compile(r"\r\n|\r|\n")


class StructuredBodyFormat(StrEnum):
    """Supported explicit or media-type-selected structural views."""

    AUTO = "auto"
    JSON = "json"
    SSE = "sse"


class StructuredJsonKind(StrEnum):
    """Closed JSON node vocabulary exposed to frontends."""

    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    STRING = "string"
    ARRAY = "array"
    OBJECT = "object"


class StructuredSseDataFormat(StrEnum):
    """Observed format of one dispatched SSE data buffer."""

    EMPTY = "empty"
    JSON = "json"
    TEXT = "text"


@final
class StructuredBodyLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned limits for decode amplification and structural projection."""

    maximum_document_bytes: int
    maximum_nodes: int
    maximum_depth: int
    maximum_preview_characters: int
    maximum_pointer_bytes: int
    maximum_sse_events: int
    maximum_sse_line_bytes: int

    def __post_init__(self) -> None:
        """Require every memory, work, depth, and presentation limit explicitly."""
        if any(
            type(value) is not int or value < 1
            for value in (
                self.maximum_document_bytes,
                self.maximum_nodes,
                self.maximum_depth,
                self.maximum_preview_characters,
                self.maximum_pointer_bytes,
                self.maximum_sse_events,
                self.maximum_sse_line_bytes,
            )
        ):
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)


@final
class StructuredBodyQuery(msgspec.Struct, frozen=True, kw_only=True):
    """One stable page request, optionally rooted in an SSE event's JSON data."""

    format: StructuredBodyFormat
    start: int
    maximum_items: int
    pointer: str = ""
    sse_event_index: int | None = None

    def __post_init__(self) -> None:
        """Require an unambiguous bounded page and a canonical JSON Pointer."""
        if type(self.format) is not StructuredBodyFormat:
            raise TypeError("structured body format must be typed")
        if type(self.start) is not int or self.start < 0:
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
        if type(self.maximum_items) is not int or self.maximum_items < 1:
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
        if type(self.pointer) is not str or not _valid_pointer(self.pointer):
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
        if self.sse_event_index is not None and (
            type(self.sse_event_index) is not int or self.sse_event_index < 0
        ):
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
        if self.format is StructuredBodyFormat.JSON and self.sse_event_index is not None:
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)


@final
class StructuredBodySource(msgspec.Struct, frozen=True, kw_only=True):
    """Authenticated capture metadata shared by every structural page."""

    reference: ArtifactBodyReference
    media_type: str | None
    source_state: str
    extent: str
    reason: str | None
    total_bytes: int
    redaction_count: int

    def __post_init__(self) -> None:
        """Require a typed available capture with truthful bounded metadata."""
        if type(self.reference) is not ArtifactBodyReference:
            raise TypeError("structured body source requires typed coordinates")
        if self.media_type is not None and type(self.media_type) is not str:
            raise TypeError("structured body source media type must be text or absent")
        if any(type(value) is not str or not value for value in (self.source_state, self.extent)):
            raise ValueError("structured body source labels must be non-empty text")
        if self.reason is not None and (type(self.reason) is not str or not self.reason):
            raise ValueError("structured body source reason must be text or absent")
        if type(self.total_bytes) is not int or self.total_bytes < 0:
            raise ValueError("structured body source total must be non-negative")
        if type(self.redaction_count) is not int or self.redaction_count < 0:
            raise ValueError("structured body source redaction count must be non-negative")


@final
class StructuredJsonNode(msgspec.Struct, frozen=True, kw_only=True):
    """One pre-order JSON node with a stable absolute RFC 6901 coordinate."""

    ordinal: int
    pointer: str
    parent_pointer: str | None
    name: str | None
    depth: int
    kind: StructuredJsonKind
    child_count: int
    preview: str
    preview_truncated: bool

    def __post_init__(self) -> None:
        """Require a coherent immutable node safe for serialization."""
        if any(type(value) is not int or value < 0 for value in (self.ordinal, self.depth)):
            raise ValueError("structured JSON node coordinates must be non-negative")
        if type(self.pointer) is not str or not _valid_pointer(self.pointer):
            raise ValueError("structured JSON node pointer must be canonical")
        if self.parent_pointer is not None and (
            type(self.parent_pointer) is not str or not _valid_pointer(self.parent_pointer)
        ):
            raise ValueError("structured JSON node parent must be canonical or absent")
        if self.name is not None and type(self.name) is not str:
            raise TypeError("structured JSON node name must be text or absent")
        if type(self.kind) is not StructuredJsonKind:
            raise TypeError("structured JSON node kind must be typed")
        if type(self.child_count) is not int or self.child_count < 0:
            raise ValueError("structured JSON child count must be non-negative")
        if type(self.preview) is not str or type(self.preview_truncated) is not bool:
            raise TypeError("structured JSON preview must be typed")


@final
class StructuredSseEvent(msgspec.Struct, frozen=True, kw_only=True):
    """One dispatched SSE event with bounded data and nested-JSON metadata."""

    ordinal: int
    event_type: str
    event_id: str
    retry_milliseconds: int | None
    data_line_count: int
    data_character_count: int
    data_format: StructuredSseDataFormat
    data_preview: str
    preview_truncated: bool
    json_node_count: int | None

    def __post_init__(self) -> None:
        """Require coherent event controls and bounded presentation data."""
        if any(
            type(value) is not int or value < 0
            for value in (self.ordinal, self.data_line_count, self.data_character_count)
        ):
            raise ValueError("structured SSE event counts must be non-negative")
        if any(type(value) is not str for value in (self.event_type, self.event_id)):
            raise TypeError("structured SSE event controls must be text")
        if self.retry_milliseconds is not None and (
            type(self.retry_milliseconds) is not int or self.retry_milliseconds < 0
        ):
            raise ValueError("structured SSE retry must be non-negative or absent")
        if type(self.data_format) is not StructuredSseDataFormat:
            raise TypeError("structured SSE data format must be typed")
        if type(self.data_preview) is not str or type(self.preview_truncated) is not bool:
            raise TypeError("structured SSE preview must be typed")
        if self.json_node_count is not None and (
            type(self.json_node_count) is not int or self.json_node_count < 1
        ):
            raise ValueError("structured SSE JSON node count must be positive or absent")
        if (self.data_format is StructuredSseDataFormat.JSON) != (self.json_node_count is not None):
            raise ValueError("structured SSE JSON metadata contradicts its data format")


@final
class StructuredJsonPage(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    tag=StructuredBodyFormat.JSON.value,
    tag_field="format",
):
    """One bounded pre-order page within a JSON document or SSE data buffer."""

    source: StructuredBodySource
    pointer: str
    sse_event_index: int | None
    start: int
    total_nodes: int
    nodes: tuple[StructuredJsonNode, ...]
    next_start: int | None

    @property
    def format(self) -> StructuredBodyFormat:
        """Return the serialized page discriminator."""
        return StructuredBodyFormat.JSON

    def __post_init__(self) -> None:
        """Require truthful JSON page coordinates and continuation."""
        if type(self.source) is not StructuredBodySource:
            raise TypeError("structured JSON page source must be typed")
        if type(self.pointer) is not str or not _valid_pointer(self.pointer):
            raise ValueError("structured JSON page pointer must be canonical")
        if self.sse_event_index is not None and (
            type(self.sse_event_index) is not int or self.sse_event_index < 0
        ):
            raise ValueError("structured JSON SSE event index must be non-negative or absent")
        _validate_page(
            start=self.start,
            total=self.total_nodes,
            items=self.nodes,
            next_start=self.next_start,
            expected=StructuredJsonNode,
        )


@final
class StructuredSsePage(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    tag=StructuredBodyFormat.SSE.value,
    tag_field="format",
):
    """One bounded page of dispatched events from an authenticated SSE capture."""

    source: StructuredBodySource
    start: int
    total_events: int
    events: tuple[StructuredSseEvent, ...]
    next_start: int | None
    trailing_incomplete_event: bool

    @property
    def format(self) -> StructuredBodyFormat:
        """Return the serialized page discriminator."""
        return StructuredBodyFormat.SSE

    def __post_init__(self) -> None:
        """Require truthful SSE page coordinates and continuation."""
        if type(self.source) is not StructuredBodySource:
            raise TypeError("structured SSE page source must be typed")
        _validate_page(
            start=self.start,
            total=self.total_events,
            items=self.events,
            next_start=self.next_start,
            expected=StructuredSseEvent,
        )
        if type(self.trailing_incomplete_event) is not bool:
            raise TypeError("structured SSE trailing state must be Boolean")


type StructuredBodyPage = StructuredJsonPage | StructuredSsePage


class _JsonObject(list[tuple[str, object]]):
    """Internal ordered JSON object distinct from an array."""


@final
class _SseRecord(msgspec.Struct, frozen=True, kw_only=True):
    ordinal: int
    event_type: str
    event_id: str
    retry_milliseconds: int | None
    data_lines: tuple[str, ...]

    @property
    def data(self) -> str:
        return "\n".join(self.data_lines)


async def read_artifact_structured_body_page(
    root: Path,
    bundle_id: str,
    reference: ArtifactBodyReference,
    read_limits: ArtifactReadLimits,
    limits: StructuredBodyLimits,
    query: StructuredBodyQuery,
) -> StructuredBodyPage:
    """Authenticate, cap, decode, and project one sanitized body without leaking failures."""
    if type(limits) is not StructuredBodyLimits:
        raise TypeError("structured body reading requires StructuredBodyLimits")
    if type(query) is not StructuredBodyQuery:
        raise TypeError("structured body reading requires StructuredBodyQuery")
    if len(query.pointer.encode("utf-8")) > limits.maximum_pointer_bytes:
        raise ArtifactOperationError(ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED)
    window = await read_artifact_body_window(
        root,
        bundle_id,
        reference,
        read_limits,
        offset=0,
        maximum_output_bytes=limits.maximum_document_bytes,
    )
    content = _complete_body(window)
    source = _source(window)
    selected = _select_format(query.format, window.media_type)
    if selected is StructuredBodyFormat.JSON:
        return _json_page(content, source, limits, query, sse_event_index=None)
    records, trailing = _parse_sse(content, limits)
    if query.sse_event_index is not None:
        record = _event(records, query.sse_event_index)
        return _json_page(
            record.data.encode("utf-8"),
            source,
            limits,
            query,
            sse_event_index=record.ordinal,
        )
    if query.pointer:
        raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
    return _sse_page(records, trailing, source, limits, query)


def _complete_body(window: ArtifactBodyWindow) -> bytes:
    if window.total_bytes is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_NOT_FOUND)
    if window.next_offset is not None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED)
    return window.content


def _source(window: ArtifactBodyWindow) -> StructuredBodySource:
    return StructuredBodySource(
        reference=window.reference,
        media_type=window.media_type,
        source_state=window.source_state,
        extent=window.extent,
        reason=window.reason,
        total_bytes=cast("int", window.total_bytes),
        redaction_count=window.redaction_count,
    )


def _select_format(
    requested: StructuredBodyFormat,
    media_type: str | None,
) -> StructuredBodyFormat:
    if requested is not StructuredBodyFormat.AUTO:
        return requested
    normalized = "" if media_type is None else media_type.partition(";")[0].strip().lower()
    if normalized == "text/event-stream":
        return StructuredBodyFormat.SSE
    if normalized == "application/json" or normalized.endswith("+json"):
        return StructuredBodyFormat.JSON
    raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_FORMAT_UNSUPPORTED)


def _json_page(
    content: bytes,
    source: StructuredBodySource,
    limits: StructuredBodyLimits,
    query: StructuredBodyQuery,
    *,
    sse_event_index: int | None,
) -> StructuredJsonPage:
    value = _decode_json(content)
    selected = _select_pointer(value, query.pointer)
    total, nodes = _project_nodes(selected, query.pointer, limits, query)
    consumed = query.start + len(nodes)
    return StructuredJsonPage(
        source=source,
        pointer=query.pointer,
        sse_event_index=sse_event_index,
        start=query.start,
        total_nodes=total,
        nodes=nodes,
        next_start=(consumed if consumed < total else None),
    )


def _decode_json(content: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> _JsonObject:
        names: set[str] = set()
        ordered = _JsonObject()
        for name, value in items:
            if name in names:
                raise ValueError("duplicate object member")
            names.add(name)
            ordered.append((name, value))
        return ordered

    def constant(_value: str) -> NoReturn:
        raise ValueError("non-finite JSON number")

    try:
        text = content.decode("utf-8-sig")
        return cast(
            "object",
            json.loads(
                text,
                parse_float=Decimal,
                parse_int=int,
                parse_constant=constant,
                object_pairs_hook=pairs,
            ),
        )
    except (UnicodeError, ValueError, RecursionError, json.JSONDecodeError):
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_DECODE_FAILED) from None


def _select_pointer(value: object, pointer: str) -> object:
    selected = value
    for token in _pointer_tokens(pointer):
        if type(selected) is _JsonObject:
            match = next((item for name, item in selected if name == token), None)
            if match is None and all(name != token for name, _item in selected):
                raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_SUBTREE_NOT_FOUND)
            selected = match
            continue
        if type(selected) is list and _array_index(token, len(selected)) is not None:
            selected = selected[cast("int", _array_index(token, len(selected)))]
            continue
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_SUBTREE_NOT_FOUND)
    return selected


def _project_nodes(
    root: object,
    pointer: str,
    limits: StructuredBodyLimits,
    query: StructuredBodyQuery,
) -> tuple[int, tuple[StructuredJsonNode, ...]]:
    stack: list[tuple[object, str, str | None, str | None, int]] = [(root, pointer, None, None, 0)]
    retained: list[StructuredJsonNode] = []
    ordinal = 0
    while stack:
        value, node_pointer, parent, name, depth = stack.pop()
        if ordinal >= limits.maximum_nodes or depth > limits.maximum_depth:
            raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_PROJECTION_LIMIT_EXCEEDED)
        kind, child_count, preview = _node_shape(value)
        bounded_preview, truncated = _preview(preview, limits.maximum_preview_characters)
        if query.start <= ordinal < query.start + query.maximum_items:
            retained.append(
                StructuredJsonNode(
                    ordinal=ordinal,
                    pointer=node_pointer,
                    parent_pointer=parent,
                    name=name,
                    depth=depth,
                    kind=kind,
                    child_count=child_count,
                    preview=bounded_preview,
                    preview_truncated=truncated,
                )
            )
        ordinal += 1
        stack.extend(reversed(_children(value, node_pointer, depth)))
    return ordinal, tuple(retained)


def _node_shape(value: object) -> tuple[StructuredJsonKind, int, str]:
    if value is None:
        return StructuredJsonKind.NULL, 0, "null"
    if type(value) is bool:
        return StructuredJsonKind.BOOLEAN, 0, "true" if value else "false"
    if type(value) is int:
        return StructuredJsonKind.INTEGER, 0, str(value)
    if type(value) is Decimal:
        return StructuredJsonKind.DECIMAL, 0, str(value)
    if type(value) is str:
        _require_unicode(value)
        return StructuredJsonKind.STRING, 0, value
    if type(value) is list:
        return StructuredJsonKind.ARRAY, len(value), f"array · {len(value):,} items"
    if type(value) is _JsonObject:
        return StructuredJsonKind.OBJECT, len(value), f"object · {len(value):,} members"
    raise ArtifactOperationError(  # pragma: no cover - json decoder has a closed vocabulary
        ArtifactOperationErrorCode.BODY_DECODE_FAILED
    )


def _children(
    value: object,
    parent_pointer: str,
    depth: int,
) -> tuple[tuple[object, str, str, str, int], ...]:
    if type(value) is list:
        return tuple(
            (item, _join_pointer(parent_pointer, str(index)), parent_pointer, str(index), depth + 1)
            for index, item in enumerate(value)
        )
    if type(value) is _JsonObject:
        children: list[tuple[object, str, str, str, int]] = []
        for name, item in value:
            _require_unicode(name)
            children.append(
                (item, _join_pointer(parent_pointer, name), parent_pointer, name, depth + 1)
            )
        return tuple(children)
    return ()


def _parse_sse(  # noqa: C901 - one explicit state machine implements SSE field semantics
    content: bytes,
    limits: StructuredBodyLimits,
) -> tuple[tuple[_SseRecord, ...], bool]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_DECODE_FAILED) from None
    lines = _SSE_LINES.split(text)
    data_lines: list[str] = []
    event_type = ""
    last_event_id = ""
    retry: int | None = None
    touched = False
    records: list[_SseRecord] = []
    for line in lines:
        if len(line.encode("utf-8")) > limits.maximum_sse_line_bytes:
            raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_PROJECTION_LIMIT_EXCEEDED)
        if not line:
            if data_lines:
                if len(records) >= limits.maximum_sse_events:
                    raise ArtifactOperationError(
                        ArtifactOperationErrorCode.BODY_PROJECTION_LIMIT_EXCEEDED
                    )
                records.append(
                    _SseRecord(
                        ordinal=len(records),
                        event_type=event_type or "message",
                        event_id=last_event_id,
                        retry_milliseconds=retry,
                        data_lines=tuple(data_lines),
                    )
                )
            data_lines = []
            event_type = ""
            retry = None
            touched = False
            continue
        touched = True
        if line.startswith(":"):
            continue
        field, separator, raw_value = line.partition(":")
        value = raw_value[1:] if separator and raw_value.startswith(" ") else raw_value
        if field == "data":
            data_lines.append(value)
        elif field == "event":
            event_type = value
        elif field == "id" and "\x00" not in value:
            last_event_id = value
        elif field == "retry" and value.isascii() and value.isdigit():
            retry = int(value)
    return tuple(records), bool(data_lines or event_type or retry is not None or touched)


def _sse_page(
    records: tuple[_SseRecord, ...],
    trailing: bool,
    source: StructuredBodySource,
    limits: StructuredBodyLimits,
    query: StructuredBodyQuery,
) -> StructuredSsePage:
    selected = records[query.start : query.start + query.maximum_items]
    events = tuple(_event_projection(record, limits) for record in selected)
    consumed = query.start + len(events)
    return StructuredSsePage(
        source=source,
        start=query.start,
        total_events=len(records),
        events=events,
        next_start=(consumed if consumed < len(records) else None),
        trailing_incomplete_event=trailing,
    )


def _event_projection(
    record: _SseRecord,
    limits: StructuredBodyLimits,
) -> StructuredSseEvent:
    data = record.data
    preview, truncated = _preview(data, limits.maximum_preview_characters)
    if not data:
        data_format = StructuredSseDataFormat.EMPTY
        node_count = None
    else:
        try:
            decoded = _decode_json(data.encode("utf-8"))
        except ArtifactOperationError:
            data_format = StructuredSseDataFormat.TEXT
            node_count = None
        else:
            data_format = StructuredSseDataFormat.JSON
            node_count = _count_nodes(decoded, limits)
    return StructuredSseEvent(
        ordinal=record.ordinal,
        event_type=record.event_type,
        event_id=record.event_id,
        retry_milliseconds=record.retry_milliseconds,
        data_line_count=len(record.data_lines),
        data_character_count=len(data),
        data_format=data_format,
        data_preview=preview,
        preview_truncated=truncated,
        json_node_count=node_count,
    )


def _count_nodes(value: object, limits: StructuredBodyLimits) -> int:
    stack: list[tuple[object, int]] = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        if count >= limits.maximum_nodes or depth > limits.maximum_depth:
            raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_PROJECTION_LIMIT_EXCEEDED)
        _node_shape(item)
        count += 1
        stack.extend((child[0], depth + 1) for child in _children(item, "", depth))
    return count


def _event(records: tuple[_SseRecord, ...], ordinal: int) -> _SseRecord:
    if ordinal >= len(records):
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_EVENT_NOT_FOUND)
    return records[ordinal]


def _preview(value: str, maximum: int) -> tuple[str, bool]:
    if len(value) <= maximum:
        return value, False
    if maximum == 1:
        return "…", True
    return value[: maximum - 1] + "…", True


def _require_unicode(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_DECODE_FAILED)


def _valid_pointer(value: str) -> bool:
    if value and not value.startswith("/"):
        return False
    index = 0
    while index < len(value):
        if value[index] == "~":
            if index + 1 >= len(value) or value[index + 1] not in {"0", "1"}:
                return False
            index += 1
        index += 1
    return True


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if not pointer:
        return ()
    return tuple(token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/"))


def _join_pointer(parent: str, token: str) -> str:
    escaped = token.replace("~", "~0").replace("/", "~1")
    return f"{parent}/{escaped}"


def _array_index(token: str, length: int) -> int | None:
    if not token.isascii() or not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        return None
    index = int(token)
    return index if index < length else None


def _validate_page(
    *,
    start: int,
    total: int,
    items: tuple[object, ...],
    next_start: int | None,
    expected: type[object],
) -> None:
    if type(start) is not int or start < 0 or type(total) is not int or total < 0:
        raise ValueError("structured body page coordinates must be non-negative")
    if type(items) is not tuple or any(type(item) is not expected for item in items):
        raise TypeError("structured body page items must be immutable and typed")
    if items and start >= total:
        raise ValueError("structured body page items exceed their collection")
    if start < total and start + len(items) > total:
        raise ValueError("structured body page items exceed their collection")
    consumed = start + len(items)
    if next_start != (consumed if consumed < total else None):
        raise ValueError("structured body page continuation contradicts its items")


__all__ = (
    "StructuredBodyFormat",
    "StructuredBodyLimits",
    "StructuredBodyPage",
    "StructuredBodyQuery",
    "StructuredBodySource",
    "StructuredJsonKind",
    "StructuredJsonNode",
    "StructuredJsonPage",
    "StructuredSseDataFormat",
    "StructuredSseEvent",
    "StructuredSsePage",
    "read_artifact_structured_body_page",
)
