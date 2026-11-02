"""Revision-pinned raw, masked, and effective project inspection services."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
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
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.sensitivity import sensitive_source_spans


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
class AuthoredResourceInspectionRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One exact resource-document window tied to a compiled resource revision."""

    workspace: ProjectWorkspaceRequest
    coordinate: ResourceCoordinate
    limits: SourceInspectionLimits
    start_resource_line: int = 1
    mode: SourceViewMode = SourceViewMode.MASKED
    expected_sha256: str | None = None

    def __post_init__(self) -> None:
        """Require exact resource identity, disclosure, revision, and finite limits."""
        if (
            type(self.workspace) is not ProjectWorkspaceRequest
            or type(self.coordinate) is not ResourceCoordinate
            or type(self.limits) is not SourceInspectionLimits
        ):
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if type(self.start_resource_line) is not int or self.start_resource_line < 1:
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if type(self.mode) is not SourceViewMode:
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
        if self.expected_sha256 is not None and (
            len(self.expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.expected_sha256)
        ):
            raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)


@final
class AuthoredResourceWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One line-bounded authored YAML document belonging to an exact resource revision."""

    coordinate: ResourceCoordinate
    source: SourceCoordinate
    source_sha256: str
    source_byte_count: int
    mode: SourceViewMode
    resource_start_line: int
    resource_start_column: int
    resource_end_line: int
    resource_end_column: int
    start_resource_line: int
    returned_lines: int
    total_resource_lines: int
    content: str
    next_resource_line: int | None
    line_truncated: bool
    redaction_count: int
    contains_unmasked_values: bool
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:  # noqa: C901 - exact source-window coherence
        """Require coherent resource-relative and source-absolute coordinates."""
        if (
            type(self.coordinate) is not ResourceCoordinate
            or type(self.source) is not SourceCoordinate
        ):
            raise TypeError("authored resource window requires typed coordinates")
        if (
            type(self.source_sha256) is not str
            or len(self.source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.source_sha256)
        ):
            raise ValueError("authored resource source revision must be SHA-256")
        counts = (
            self.source_byte_count,
            self.returned_lines,
            self.total_resource_lines,
            self.redaction_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("authored resource counts must be non-negative integers")
        positions = (
            self.resource_start_line,
            self.resource_start_column,
            self.resource_end_line,
            self.resource_end_column,
            self.start_resource_line,
        )
        if any(type(value) is not int or value < 1 for value in positions):
            raise ValueError("authored resource positions must be positive integers")
        if (self.resource_end_line, self.resource_end_column) < (
            self.resource_start_line,
            self.resource_start_column,
        ):
            raise ValueError("authored resource source interval must be ordered")
        if self.start_resource_line > self.total_resource_lines:
            raise ValueError("authored resource cursor exceeds its document")
        if type(self.mode) is not SourceViewMode:
            raise TypeError("authored resource disclosure mode must be typed")
        if type(self.content) is not str:
            raise TypeError("authored resource content must be text")
        consumed = self.start_resource_line + self.returned_lines
        expected_next = (
            None if self.line_truncated or consumed > self.total_resource_lines else consumed
        )
        if self.next_resource_line != expected_next:
            raise ValueError("authored resource continuation contradicts its page")
        if type(self.line_truncated) is not bool or type(self.contains_unmasked_values) is not bool:
            raise TypeError("authored resource disclosure flags must be bool")
        if self.contains_unmasked_values != (self.mode is SourceViewMode.TRUSTED_EXACT):
            raise ValueError("authored resource exact-disclosure marker contradicts its mode")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("authored resource diagnostics must be immutable and typed")


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
    """Read one pinned revision and apply compiler-aligned source masking by default."""
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
    text: str | None = None
    with suppress(UnicodeDecodeError):
        text = content.decode("utf-8")
    if text is None:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_UNAVAILABLE)
    redactions = 0
    if request.mode is SourceViewMode.MASKED:
        if loaded.compilation.project is None:
            text, redactions = _mask_unparsed_source(text)
        else:
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


def inspect_authored_resource(
    request: AuthoredResourceInspectionRequest,
) -> AuthoredResourceWindow:
    """Read only the authored YAML document for one exact compiled resource revision."""
    if type(request) is not AuthoredResourceInspectionRequest:
        raise SourceInspectionError(SourceInspectionErrorCode.INVALID_REQUEST)
    loaded = compile_project_path(
        request.workspace.config,
        additional_sources=request.workspace.additional_sources,
        limits=request.workspace.load_limits,
    )
    project = loaded.compilation.project
    if project is None:
        raise SourceInspectionError(SourceInspectionErrorCode.PROJECT_INVALID)
    resource = next(
        (
            item
            for item in (project.project, *project.resources)
            if _coordinate(item) == request.coordinate
        ),
        None,
    )
    if resource is None:
        raise SourceInspectionError(SourceInspectionErrorCode.RESOURCE_NOT_FOUND)
    source = next(
        (item for item in loaded.catalog if item.source_id == resource.location.source_id),
        None,
    )
    if source is None:  # pragma: no cover - compiled-resource source closure invariant
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_NOT_FOUND)
    document = next(
        (
            item
            for item in loaded.documents
            if item.number == resource.location.document.value
            and item.root.span.start.source_id == resource.location.source_id
        ),
        None,
    )
    if document is None:  # pragma: no cover - compiled-resource document closure invariant
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_NOT_FOUND)
    content = _read_source(source, request.limits.maximum_source_bytes)
    digest = _digest(content)
    if source.sha256 is None or source.sha256 != digest:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_CHANGED)
    if request.expected_sha256 is not None and request.expected_sha256 != digest:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_CHANGED)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceInspectionError(SourceInspectionErrorCode.SOURCE_UNAVAILABLE) from error
    redactions = 0
    if request.mode is SourceViewMode.MASKED:
        text, redactions = _mask_source(text, loaded.documents, source.source_id.value)
    span = document.root.span
    lines = text.splitlines(keepends=True)
    resource_text = "".join(lines[span.start.line.value - 1 : span.end.line.value])
    window, returned, total, next_line, truncated = _line_window(
        resource_text,
        start_line=request.start_resource_line,
        maximum_lines=request.limits.maximum_lines,
        maximum_bytes=request.limits.maximum_output_bytes,
    )
    return AuthoredResourceWindow(
        coordinate=request.coordinate,
        source=_source_coordinate(resource),
        source_sha256=digest,
        source_byte_count=len(content),
        mode=request.mode,
        resource_start_line=span.start.line.value,
        resource_start_column=span.start.column.value,
        resource_end_line=span.end.line.value,
        resource_end_column=span.end.column.value,
        start_resource_line=request.start_resource_line,
        returned_lines=returned,
        total_resource_lines=total,
        content=window,
        next_resource_line=next_line,
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
    failure: SourceInspectionErrorCode | None = None
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
    except SourceInspectionError as error:
        failure = error.code
    except OSError:
        failure = SourceInspectionErrorCode.SOURCE_UNAVAILABLE
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
    raise SourceInspectionError(failure or SourceInspectionErrorCode.SOURCE_UNAVAILABLE)


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
            spans.extend(sensitive_source_spans(document.root))
    offsets = _line_offsets(text)
    masked = list(text)
    ranges = sorted(
        {
            *(_span_range(span, offsets, len(text)) for span in spans),
            *_yaml_comment_ranges(text),
        }
    )
    if not ranges:
        return text, 0
    for start, end in ranges:
        for index in range(start, end):
            if not masked[index].isspace():
                masked[index] = "*"
    return "".join(masked), len(ranges)


def _mask_unparsed_source(text: str) -> tuple[str, int]:
    """Fail closed when hostile or invalid YAML cannot provide trustworthy typed spans."""
    masked = "".join(character if character.isspace() else "*" for character in text)
    return masked, int(masked != text)


def _yaml_comment_ranges(text: str) -> tuple[tuple[int, int], ...]:  # noqa: C901
    """Locate unquoted YAML comments so masked views never disclose comment payloads."""
    ranges: list[tuple[int, int]] = []
    single_quoted = False
    double_quoted = False
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\n":
            single_quoted = False
            double_quoted = False
            escaped = False
        elif double_quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                double_quoted = False
        elif single_quoted:
            if character == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 1
                else:
                    single_quoted = False
        elif character == '"':
            double_quoted = True
        elif character == "'":
            single_quoted = True
        elif character == "#":
            end = text.find("\n", index)
            ranges.append((index + 1, len(text) if end < 0 else end))
            index = len(text) if end < 0 else end - 1
        index += 1
    return tuple(ranges)


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
    "AuthoredResourceInspectionRequest",
    "AuthoredResourceWindow",
    "EffectiveResourceInspectionRequest",
    "EffectiveResourceWindow",
    "SourceInspectionError",
    "SourceInspectionErrorCode",
    "SourceInspectionLimits",
    "SourceInspectionRequest",
    "SourceViewMode",
    "SourceWindow",
    "inspect_authored_resource",
    "inspect_effective_resource",
    "inspect_project_source",
)
