"""Secret-safe structured diagnostics with no arbitrary payload-bearing messages."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import final

import msgspec

from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.primitives import Count
from comparo.core.domain.provenance import SourceLocation

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+")
_FIELD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


@final
class DiagnosticCode(msgspec.Struct, frozen=True, kw_only=True):
    """A stable machine-readable code rendered to prose outside the domain."""

    value: str

    def __post_init__(self) -> None:
        """Require a namespaced code instead of free-form diagnostic prose."""
        if type(self.value) is not str or _CODE_PATTERN.fullmatch(self.value) is None:
            raise ValueError("diagnostic code must be a lowercase namespaced token")


class DiagnosticSeverity(StrEnum):
    """The stable severity levels understood by all reporting adapters."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DiagnosticStage(StrEnum):
    """The engine stage that produced a diagnostic without exposing input values."""

    CONFIGURATION = "configuration"
    COMPILATION = "compilation"
    PLANNING = "planning"
    RESOLUTION = "resolution"
    AUTHORIZATION = "authorization"
    TRANSPORT = "transport"
    CAPTURE = "capture"
    DECODING = "decoding"
    SELECTION = "selection"
    EVALUATION = "evaluation"
    GATING = "gating"
    ARTIFACT = "artifact"


@final
class FieldPathSegment(msgspec.Struct, frozen=True, kw_only=True):
    """A static schema field name, never a value selected from user payload data."""

    name: str

    def __post_init__(self) -> None:
        """Reject values that cannot be static schema field identifiers."""
        if type(self.name) is not str or _FIELD_PATTERN.fullmatch(self.name) is None:
            raise ValueError("diagnostic field segment must be a static field identifier")


@final
class IndexPathSegment(msgspec.Struct, frozen=True, kw_only=True):
    """A non-negative structural collection index."""

    index: Count

    def __post_init__(self) -> None:
        """Require a validated exact index."""
        if type(self.index) is not Count:
            raise TypeError("diagnostic index segment requires a Count")


type DiagnosticPathSegment = FieldPathSegment | IndexPathSegment


@final
class DiagnosticContext(msgspec.Struct, frozen=True, kw_only=True):
    """Closed, value-free metadata safe for logs and serialized reports."""

    resource: ResourceIdentity | None = None
    location: SourceLocation | None = None
    field_path: tuple[DiagnosticPathSegment, ...] = ()
    occurrences: Count | None = None

    def __post_init__(self) -> None:
        """Allow only typed identifiers, coordinates, paths, and counts."""
        if self.resource is not None and type(self.resource) is not ResourceIdentity:
            raise TypeError("diagnostic resource must be a ResourceIdentity")
        if self.location is not None and type(self.location) is not SourceLocation:
            raise TypeError("diagnostic location must be a SourceLocation")
        if type(self.field_path) is not tuple:
            raise TypeError("diagnostic field path must be an immutable tuple")
        if any(
            type(segment) not in {FieldPathSegment, IndexPathSegment} for segment in self.field_path
        ):
            raise TypeError("diagnostic field path contains an unsupported segment")
        if self.occurrences is not None and type(self.occurrences) is not Count:
            raise TypeError("diagnostic occurrences must be a Count")


@final
class Diagnostic(msgspec.Struct, frozen=True, kw_only=True):
    """A structured issue whose human-readable message is code-driven."""

    code: DiagnosticCode
    severity: DiagnosticSeverity
    stage: DiagnosticStage
    context: DiagnosticContext = DiagnosticContext()

    def __post_init__(self) -> None:
        """Reject untyped diagnostic components and arbitrary message payloads."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("diagnostic code must be a DiagnosticCode")
        if type(self.severity) is not DiagnosticSeverity:
            raise TypeError("diagnostic severity must be a DiagnosticSeverity")
        if type(self.stage) is not DiagnosticStage:
            raise TypeError("diagnostic stage must be a DiagnosticStage")
        if type(self.context) is not DiagnosticContext:
            raise TypeError("diagnostic context must be a DiagnosticContext")
