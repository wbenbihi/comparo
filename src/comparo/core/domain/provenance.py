"""Value-free provenance describing where effective domain decisions originated."""

from __future__ import annotations

import re
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import SourceId
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import scope_contains
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.value_references import namespace_reaches_runtime_scope
from comparo.core.domain.value_references import namespace_supports_runtime_scope
from comparo.core.domain.value_references import runtime_value_scope_kind

_PROVIDER_TOKEN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


class ProvenanceKind(DomainStrEnum):
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


class ResolutionStepOutcome(DomainStrEnum):
    """Value-free result categories for one ordered resolution attempt."""

    PRESENT = "present"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"
    ERROR = "error"
    BLOCKED = "blocked"


@final
class SourceLocation(msgspec.Struct, frozen=True, kw_only=True):
    """A one-based source location using a logical, non-secret source identifier."""

    source_id: SourceId
    document: PositiveCount
    line: PositiveCount
    column: PositiveCount

    def __post_init__(self) -> None:
        """Validate direct construction of the location components."""
        if type(self.source_id) is not SourceId:
            raise TypeError("source location requires a logical SourceId")
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
    """A non-empty ordered configuration-origin chain with no resolved values."""

    entries: tuple[ProvenanceEntry, ...]

    def __post_init__(self) -> None:
        """Require immutable, typed, non-empty provenance."""
        if type(self.entries) is not tuple:
            raise TypeError("provenance entries must be an immutable tuple")
        if not self.entries:
            raise ValueError("value provenance must contain at least one entry")
        if any(type(entry) is not ProvenanceEntry for entry in self.entries):
            raise TypeError("provenance entries must be ProvenanceEntry values")


@final
class ValueProviderIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """A stable, versioned identity for one built-in or future value provider."""

    namespace: str
    name: str
    version: str

    def __post_init__(self) -> None:
        """Reject ambiguous, dynamic, and unversioned provider identities."""
        for label, value in (("namespace", self.namespace), ("name", self.name)):
            if type(value) is not str or _PROVIDER_TOKEN.fullmatch(value) is None:
                raise ValueError(f"value provider {label} must be a lowercase namespaced token")
        if (
            type(self.version) is not str
            or not self.version.isascii()
            or not self.version.isdigit()
        ):
            raise ValueError("value provider version must be an ASCII decimal version")
        if int(self.version) < 1:
            raise ValueError("value provider version must be positive")
        if str(int(self.version)) != self.version:
            raise ValueError("value provider version must use canonical decimal notation")


@final
class ResolutionSource(msgspec.Struct, frozen=True, kw_only=True):
    """One logical, value-free source participating in resolution."""

    symbol: SymbolPath
    provider: ValueProviderIdentity
    configuration: ValueProvenance
    scope: RuntimeScope
    namespace: ValueNamespace | None = None

    def __post_init__(self) -> None:
        """Require exact immutable source identity and configuration provenance."""
        if type(self.symbol) is not SymbolPath:
            raise TypeError("resolution source symbol must be a SymbolPath")
        if type(self.provider) is not ValueProviderIdentity:
            raise TypeError("resolution source provider must be a ValueProviderIdentity")
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("resolution source configuration must be ValueProvenance")
        runtime_value_scope_kind(self.scope)
        if self.namespace is not None and type(self.namespace) is not ValueNamespace:
            raise TypeError("resolution source namespace must be a ValueNamespace")
        if self.namespace is not None and not namespace_supports_runtime_scope(
            self.namespace, self.scope
        ):
            raise ValueError("resolution source namespace is unavailable at its production scope")


@final
class ResolutionStep(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered source attempt and its coarse, value-free outcome."""

    source: ResolutionSource
    outcome: ResolutionStepOutcome

    def __post_init__(self) -> None:
        """Require exact source and result categories."""
        if type(self.source) is not ResolutionSource:
            raise TypeError("resolution step source must be a ResolutionSource")
        if type(self.outcome) is not ResolutionStepOutcome:
            raise TypeError("resolution step outcome must be a ResolutionStepOutcome")


def _validate_resolution_steps(
    steps: tuple[ResolutionStep, ...],
    *,
    provider: ValueProviderIdentity,
    scope: RuntimeScope,
) -> None:
    """Validate one immutable fallback chain against its provider and scope."""
    if type(steps) is not tuple:
        raise TypeError("resolution steps must be an immutable tuple")
    if not steps:
        raise ValueError("resolution provenance must contain at least one step")
    if any(type(step) is not ResolutionStep for step in steps):
        raise TypeError("resolution steps must be ResolutionStep values")
    if any(step.outcome is not ResolutionStepOutcome.UNAVAILABLE for step in steps[:-1]):
        raise ValueError("nonterminal resolution steps must be unavailable")
    for step in steps:
        if not scope_contains(step.source.scope, scope):
            raise ValueError("resolution source scope cannot supply the consumer scope")
    if steps[-1].source.provider != provider:
        raise ValueError("resolution provider must match the terminal source provider")


@final
class ResolutionProvenance(msgspec.Struct, frozen=True, kw_only=True):
    """The exact value-free identity, scope, provider, and attempts of one resolution."""

    symbol: SymbolPath
    provider: ValueProviderIdentity
    scope: RuntimeScope
    steps: tuple[ResolutionStep, ...]
    namespace: ValueNamespace | None = None

    def __post_init__(self) -> None:
        """Bind a logical value and ordered source chain to one exact runtime scope."""
        if type(self.symbol) is not SymbolPath:
            raise TypeError("resolution symbol must be a SymbolPath")
        if type(self.provider) is not ValueProviderIdentity:
            raise TypeError("resolution provider must be a ValueProviderIdentity")
        if self.namespace is not None and type(self.namespace) is not ValueNamespace:
            raise TypeError("resolution namespace must be a ValueNamespace")
        runtime_value_scope_kind(self.scope)
        if self.namespace is not None and not namespace_reaches_runtime_scope(
            self.namespace, self.scope
        ):
            raise ValueError("resolution namespace cannot reach the concrete consumer scope")
        _validate_resolution_steps(self.steps, provider=self.provider, scope=self.scope)

    @property
    def terminal_outcome(self) -> ResolutionStepOutcome:
        """Return the terminal attempt category without exposing any resolved value."""
        return self.steps[-1].outcome
