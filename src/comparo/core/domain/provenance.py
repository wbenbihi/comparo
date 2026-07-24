"""Value-free provenance describing where effective domain decisions originated."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.primitives import PositiveCount


class ProvenanceKind(StrEnum):
    """Stable categories of configuration and runtime origin."""

    VERSIONED_DEFAULT = "versioned-default"
    PROJECT = "project"
    PROFILE = "profile"
    INVOCATION = "invocation"
    SAFETY_PROFILE = "safety-profile"
    EXECUTION_POLICY = "execution-policy"
    PROVIDER = "provider"
    GENERATED = "generated"
    MATRIX = "matrix"
    STEP_CAPTURE = "step-capture"


@final
class SourceLocation(msgspec.Struct, frozen=True, kw_only=True):
    """A one-based source location using a logical, non-secret source identifier."""

    source_id: str
    document: PositiveCount
    line: PositiveCount
    column: PositiveCount

    def __post_init__(self) -> None:
        """Validate direct construction of the location components."""
        if type(self.source_id) is not str or not self.source_id.strip():
            raise ValueError("source ID must be non-blank text")
        for component in (self.document, self.line, self.column):
            if type(component) is not PositiveCount:
                raise TypeError("source coordinates must be PositiveCount values")


@final
class ProvenanceEntry(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered origin contributing to an effective value or policy."""

    kind: ProvenanceKind
    resource: ResourceIdentity | None = None
    location: SourceLocation | None = None

    def __post_init__(self) -> None:
        """Require a typed origin and validate optional typed coordinates."""
        if type(self.kind) is not ProvenanceKind:
            raise TypeError("provenance kind must be a ProvenanceKind")
        if self.resource is not None and type(self.resource) is not ResourceIdentity:
            raise TypeError("provenance resource must be a ResourceIdentity")
        if self.location is not None and type(self.location) is not SourceLocation:
            raise TypeError("provenance location must be a SourceLocation")


@final
class ValueProvenance(msgspec.Struct, frozen=True, kw_only=True):
    """A non-empty ordered chain of origins with no resolved values."""

    entries: tuple[ProvenanceEntry, ...]

    def __post_init__(self) -> None:
        """Require immutable, typed, non-empty provenance."""
        if type(self.entries) is not tuple:
            raise TypeError("provenance entries must be an immutable tuple")
        if not self.entries:
            raise ValueError("value provenance must contain at least one entry")
        if any(type(entry) is not ProvenanceEntry for entry in self.entries):
            raise TypeError("provenance entries must be ProvenanceEntry values")
