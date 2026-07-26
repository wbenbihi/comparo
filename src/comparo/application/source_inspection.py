"""Revision-pinned raw, masked, and effective project inspection services."""

from __future__ import annotations

import os
import stat
from enum import StrEnum
from typing import final

import msgspec

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.inspection import inspect_configuration_diagnostic
from comparo.application.project_outline import ProjectOutlineField
from comparo.application.project_outline import ProjectRelationship
from comparo.application.project_outline import outline_resource
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.configuration.loader import SourceCatalogEntry
from comparo.configuration.loader import compile_project_path
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan

_SENSITIVE_FIELD_PARTS = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "key",
        "password",
        "secret",
        "secrets",
        "token",
    }
)


class SourceViewMode(StrEnum):
    """Explicit source disclosure modes; masked is the safe default."""

    MASKED = "masked"
    TRUSTED_EXACT = "trusted-exact"


class SourceInspectionErrorCode(StrEnum):
    """Closed source and effective-inspection failure categories."""

    INVALID_REQUEST = "invalid-request"
    SOURCE_NOT_FOUND = "source-not-found"
    SOURCE_UNAVAILABLE = "source-unavailable"
    SOURCE_CHANGED = "source-changed"
    BUDGET_EXCEEDED = "budget-exceeded"
    RESOURCE_NOT_FOUND = "resource-not-found"
    PROJECT_INVALID = "project-invalid"


class SourceInspectionError(RuntimeError):
    """Value-free inspection failure that never retains paths or authored values."""

    __slots__ = ("code",)

    def __init__(self, code: SourceInspectionErrorCode) -> None:
        """Retain only one closed failure category."""
        if type(code) is not SourceInspectionErrorCode:
            raise TypeError("source inspection error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class SourceInspectionLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned read and returned-window ceilings."""

    maximum_source_bytes: int
    maximum_output_bytes: int
    maximum_lines: int

    def __post_init__(self) -> None:
        """Require explicit positive finite limits."""
        if any(
            type(value) is not int or value < 1
            for value in (
                self.maximum_source_bytes,
                self.maximum_output_bytes,
                self.maximum_lines,
            )
        ):
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)


@final
class SourceInspectionRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One logical source window tied to a discovered project revision."""

    workspace: ProjectWorkspaceRequest
    source_id: str
    limits: SourceInspectionLimits
    start_line: int = 1
    mode: SourceViewMode = SourceViewMode.MASKED
    expected_sha256: str | None = None

    def __post_init__(self) -> None:
        """Require logical coordinates and an explicit disclosure mode."""
        if type(self.workspace) is not ProjectWorkspaceRequest:
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if type(self.source_id) is not str or not self.source_id or len(self.source_id) > 1024:
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if type(self.limits) is not SourceInspectionLimits:
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if type(self.start_line) is not int or self.start_line < 1:
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if type(self.mode) is not SourceViewMode:
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if self.expected_sha256 is not None and (
            len(self.expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.expected_sha256)
        ):
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)


@final
class SourceWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded line window with explicit revision and disclosure metadata."""

    source_id: str
    sha256: str
    byte_count: int
    mode: SourceViewMode
    start_line: int
    returned_lines: int
    total_lines: int
    content: str
    next_line: int | None
    line_truncated: bool
    redaction_count: int
    contains_unmasked_values: bool
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Require coherent immutable continuation and disclosure state."""
        if type(self.mode) is not SourceViewMode:
            raise TypeError("source window mode must be typed")
        if (
            any(
                type(value) is not int or value < 0
                for value in (
                    self.byte_count,
                    self.returned_lines,
                    self.total_lines,
                    self.redaction_count,
                )
            )
            or type(self.start_line) is not int
            or self.start_line < 1
        ):
            raise ValueError("source window counts must be non-negative")
        if type(self.content) is not str:
            raise TypeError("source window content must be text")
        if self.next_line is not None and (
            type(self.next_line) is not int
            or self.next_line <= self.start_line
            or self.next_line > self.total_lines
        ):
            raise ValueError("source window continuation must name a remaining line")
        if type(self.line_truncated) is not bool or type(self.contains_unmasked_values) is not bool:
            raise TypeError("source window disclosure flags must be bool")
        if self.contains_unmasked_values != (self.mode is SourceViewMode.TRUSTED_EXACT):
            raise ValueError("source window exact-disclosure marker contradicts its mode")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("source window diagnostics must be immutable and typed")


@final
class EffectiveResourceInspectionRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One caller-bounded effective structural resource window."""

    workspace: ProjectWorkspaceRequest
    coordinate: ResourceCoordinate
    start_field: int
    maximum_fields: int
    start_relationship: int = 0
    maximum_relationships: int = 128

    def __post_init__(self) -> None:
        """Require exact resource revision and finite collection windows."""
        if (
            type(self.workspace) is not ProjectWorkspaceRequest
            or type(self.coordinate) is not ResourceCoordinate
        ):
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if any(
            type(value) is not int or value < 0
            for value in (self.start_field, self.start_relationship)
        ) or any(
            type(value) is not int or value < 1
            for value in (self.maximum_fields, self.maximum_relationships)
        ):
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)


@final
class EffectiveResourceWindow(msgspec.Struct, frozen=True, kw_only=True):
    """Value-safe compiled structure with bounded fields and dependency edges."""

    coordinate: ResourceCoordinate
    source: SourceCoordinate
    fields: tuple[ProjectOutlineField, ...]
    relationships: tuple[ProjectRelationship, ...]
    total_fields: int
    total_relationships: int
    next_field: int | None
    next_relationship: int | None


def inspect_project_source(request: SourceInspectionRequest) -> SourceWindow:
    """Read one pinned source revision and mask authored secret-bearing values by default."""
    if type(request) is not SourceInspectionRequest:
        raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
    loaded = compile_project_path(
        request.workspace.config,
        additional_sources=request.workspace.additional_sources,
        limits=request.workspace.load_limits,
    )
    source = next(
        (item for item in loaded.catalog if item.source_id.value == request.source_id),
        None,
    )
    if source is None:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_NOT_FOUND)
    content = _read_source(source, request.limits.maximum_source_bytes)
    digest = _digest(content)
    if source.sha256 is None or source.sha256 != digest:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_CHANGED)
    if request.expected_sha256 is not None and request.expected_sha256 != digest:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_CHANGED)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_UNAVAILABLE) from None
    redactions = 0
    if request.mode is SourceViewMode.MASKED:
        text, redactions = _mask_source(text, loaded.documents, request.source_id)
    window, returned, total, next_line, truncated = _line_window(
        text,
        start_line=request.start_line,
        maximum_lines=request.limits.maximum_lines,
        maximum_bytes=request.limits.maximum_output_bytes,
    )
    return SourceWindow(
        source_id=request.source_id,
        sha256=digest,
        byte_count=len(content),
        mode=request.mode,
        start_line=request.start_line,
        returned_lines=returned,
        total_lines=total,
        content=window,
        next_line=next_line,
        line_truncated=truncated,
        redaction_count=redactions,
        contains_unmasked_values=request.mode is SourceViewMode.TRUSTED_EXACT,
        diagnostics=tuple(
            inspect_configuration_diagnostic(item) for item in loaded.compilation.diagnostics
        ),
    )


def inspect_effective_resource(
    request: EffectiveResourceInspectionRequest,
) -> EffectiveResourceWindow:
    """Compile and window one exact value-safe effective resource revision."""
    if type(request) is not EffectiveResourceInspectionRequest:
        raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
    loaded = compile_project_path(
        request.workspace.config,
        additional_sources=request.workspace.additional_sources,
        limits=request.workspace.load_limits,
    )
    project = loaded.compilation.project
    if project is None:
        raise SourceInspectionError(SourceInspectionErrorCode.PROJECT_INVALID)
    resources = (project.project, *project.resources)
    resource = next(
        (item for item in resources if _coordinate(item) == request.coordinate),
        None,
    )
    if resource is None:
        raise SourceInspectionError(SourceInspectionErrorCode.RESOURCE_NOT_FOUND)
    outline = outline_resource(resource.definition.identity, resource.spec)
    fields = outline.fields[request.start_field : request.start_field + request.maximum_fields]
    relationships = outline.relationships[
        request.start_relationship : request.start_relationship + request.maximum_relationships
    ]
    return EffectiveResourceWindow(
        coordinate=request.coordinate,
        source=_source_coordinate(resource),
        fields=fields,
        relationships=relationships,
        total_fields=len(outline.fields),
        total_relationships=len(outline.relationships),
        next_field=(
            request.start_field + len(fields)
            if request.start_field + len(fields) < len(outline.fields)
            else None
        ),
        next_relationship=(
            request.start_relationship + len(relationships)
            if request.start_relationship + len(relationships) < len(outline.relationships)
            else None
        ),
    )


def _read_source(source: SourceCatalogEntry, maximum: int) -> bytes:
    if source.byte_count is None or source.byte_count > maximum:
        raise SourceInspectionError(SourceInspectionErrorCode.BUDGET_EXCEEDED)
    descriptor = -1
    try:
        descriptor = os.open(
            source.path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != source.byte_count:
            raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_CHANGED)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > maximum:
            raise SourceInspectionError(SourceInspectionErrorCode.BUDGET_EXCEEDED)
        return content
    except SourceInspectionError:
        raise
    except OSError:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_UNAVAILABLE) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _digest(content: bytes) -> str:
    from hashlib import sha256

    return sha256(content).hexdigest()


def _mask_source(
    text: str,
    documents: tuple[ParsedDocument, ...],
    source_id: str,
) -> tuple[str, int]:
    spans: list[SourceSpan] = []
    for document in documents:
        if document.root.span.start.source_id.value == source_id:
            _sensitive_spans(document.root, spans, inherited=False)
    if not spans:
        return text, 0
    offsets = _line_offsets(text)
    masked = list(text)
    ranges = sorted({_span_range(span, offsets, len(text)) for span in spans})
    for start, end in ranges:
        for index in range(start, end):
            if not masked[index].isspace():
                masked[index] = "*"
    return "".join(masked), len(ranges)


def _sensitive_spans(
    node: ConfigurationNode,
    spans: list[SourceSpan],
    *,
    inherited: bool,
) -> None:
    if inherited:
        spans.append(node.span)
        return
    if type(node) is MappingNode:
        for entry in node.entries:
            sensitive = _sensitive_name(entry.name)
            _sensitive_spans(entry.value, spans, inherited=sensitive)
        return
    if type(node) is SequenceNode:
        for item in node.items:
            _sensitive_spans(item, spans, inherited=False)
        return
    if type(node) is ScalarNode and type(node.value) is str and "${secrets." in node.value:
        spans.append(node.span)


def _sensitive_name(name: str) -> bool:
    normalized = "".join(character.casefold() for character in name if character.isalnum())
    return any(part in normalized for part in _SENSITIVE_FIELD_PARTS)


def _line_offsets(text: str) -> tuple[int, ...]:
    offsets = [0]
    for index, character in enumerate(text):
        if character == "\n":
            offsets.append(index + 1)
    offsets.append(len(text))
    return tuple(offsets)


def _span_range(span: SourceSpan, offsets: tuple[int, ...], maximum: int) -> tuple[int, int]:
    start_line = min(span.start.line.value - 1, len(offsets) - 1)
    end_line = min(span.end.line.value - 1, len(offsets) - 1)
    start = min(maximum, offsets[start_line] + span.start.column.value - 1)
    end = min(maximum, offsets[end_line] + span.end.column.value - 1)
    return start, max(start, end)


def _line_window(
    text: str,
    *,
    start_line: int,
    maximum_lines: int,
    maximum_bytes: int,
) -> tuple[str, int, int, int | None, bool]:
    lines = text.splitlines(keepends=True)
    if not lines and text == "":
        lines = []
    total = len(lines)
    if start_line > total and not (start_line == 1 and total == 0):
        raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
    selected = lines[start_line - 1 : start_line - 1 + maximum_lines]
    output: list[str] = []
    used = 0
    truncated = False
    for line in selected:
        encoded = line.encode("utf-8")
        if used + len(encoded) <= maximum_bytes:
            output.append(line)
            used += len(encoded)
            continue
        if not output:
            prefix = encoded[:maximum_bytes]
            while prefix:
                try:
                    output.append(prefix.decode("utf-8"))
                    break
                except UnicodeDecodeError:
                    prefix = prefix[:-1]
            truncated = True
        break
    returned = len(output)
    if truncated:
        return "".join(output), returned, total, None, True
    consumed_lines = 0 if truncated else returned
    next_index = start_line - 1 + consumed_lines
    next_line = next_index + 1 if next_index < total else None
    return "".join(output), returned, total, next_line, truncated


def _coordinate(resource: CompiledResource) -> ResourceCoordinate:
    identity = resource.definition.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=resource.definition.fingerprint.digest.hex_value,
    )


def _source_coordinate(resource: CompiledResource) -> SourceCoordinate:
    location = resource.location
    return SourceCoordinate(
        source=location.source_id.value,
        document=location.document.value,
        line=location.line.value,
        column=location.column.value,
    )


__all__ = (
    "EffectiveResourceInspectionRequest",
    "EffectiveResourceWindow",
    "SourceInspectionError",
    "SourceInspectionErrorCode",
    "SourceInspectionLimits",
    "SourceInspectionRequest",
    "SourceViewMode",
    "SourceWindow",
    "inspect_effective_resource",
    "inspect_project_source",
)
