"""Stable definition identities and distinct runtime occurrence identifiers."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import final
from uuid import UUID

import msgspec

from comparo.core.domain.primitives import Fingerprint

_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class ResourceKind(StrEnum):
    """The accepted authored resource kinds across project and operator authorities."""

    PROJECT = "Project"
    ENVIRONMENT = "Environment"
    REQUEST = "Request"
    SCENARIO = "Scenario"
    MATRIX = "Matrix"
    EXECUTION_PROFILE = "ExecutionProfile"
    SCHEMA = "Schema"
    ASSERTION_PROFILE = "AssertionProfile"
    DIFF_PROFILE = "DiffProfile"
    GATE_PROFILE = "GateProfile"
    SAFETY_PROFILE = "SafetyProfile"
    REDACTION_PROFILE = "RedactionProfile"
    ARTIFACT_PROFILE = "ArtifactProfile"
    VALUE = "Value"
    EXECUTION_POLICY = "ExecutionPolicy"


class _SlugIdentifier(msgspec.Struct, frozen=True, kw_only=True):
    """Private implementation base for distinct slug identifier types."""

    value: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or len(self.value) > 63
            or _SLUG_PATTERN.fullmatch(self.value) is None
        ):
            raise ValueError(
                "identifier must be a 1-63 character lowercase alphanumeric slug with "
                "single hyphen-separated segments"
            )


@final
class NamespaceId(_SlugIdentifier, frozen=True, kw_only=True):
    """A project or imported-package namespace identifier."""


@final
class ResourceId(_SlugIdentifier, frozen=True, kw_only=True):
    """A stable identifier within one resource kind."""


@final
class RuleId(_SlugIdentifier, frozen=True, kw_only=True):
    """A rule identifier within its defining profile."""


@final
class EffectiveRuleId(_SlugIdentifier, frozen=True, kw_only=True):
    """A rule identifier after profile composition and aliasing."""


@final
class MatrixCaseId(_SlugIdentifier, frozen=True, kw_only=True):
    """A stable identity for a compiled Matrix case."""


@final
class MatrixBindingId(_SlugIdentifier, frozen=True, kw_only=True):
    """A local alias that disambiguates repeated Matrix attachments."""


@final
class ArtifactId(_SlugIdentifier, frozen=True, kw_only=True):
    """A stable artifact identity within one run."""


@final
class ResourceIdentity(msgspec.Struct, frozen=True, kw_only=True):
    """The canonical namespace, kind, and stable ID of an authored resource."""

    namespace: NamespaceId
    kind: ResourceKind
    resource_id: ResourceId

    def __post_init__(self) -> None:
        """Prevent unvalidated values from bypassing static typing."""
        if type(self.namespace) is not NamespaceId:
            raise TypeError("resource namespace must be a NamespaceId")
        if type(self.kind) is not ResourceKind:
            raise TypeError("resource kind must be a ResourceKind")
        if type(self.resource_id) is not ResourceId:
            raise TypeError("resource ID must be a ResourceId")


@final
class DefinitionRef(msgspec.Struct, frozen=True, kw_only=True):
    """A compiled resource identity paired with its effective content fingerprint."""

    identity: ResourceIdentity
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        """Require both components to be canonical domain values."""
        if type(self.identity) is not ResourceIdentity:
            raise TypeError("definition identity must be a ResourceIdentity")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("definition fingerprint must be a Fingerprint")


@final
class InlineDefinitionOrigin(msgspec.Struct, frozen=True, kw_only=True):
    """A diagnostic origin for an anonymous inline definition, never a reference target."""

    owner: ResourceIdentity
    fragment: str

    def __post_init__(self) -> None:
        """Validate an explicit non-empty diagnostic fragment."""
        if type(self.owner) is not ResourceIdentity:
            raise TypeError("inline owner must be a ResourceIdentity")
        if type(self.fragment) is not str or not self.fragment or not self.fragment.startswith("#"):
            raise ValueError("inline fragment must be a non-empty value beginning with '#'")


class _UuidIdentifier(msgspec.Struct, frozen=True, kw_only=True):
    """Private implementation base for type-distinct UUID occurrence IDs."""

    value: UUID

    def __post_init__(self) -> None:
        if type(self.value) is not UUID:
            raise TypeError("occurrence identifier must be a UUID")


@final
class RunId(_UuidIdentifier, frozen=True, kw_only=True):
    """A single run occurrence."""


@final
class InvocationId(_UuidIdentifier, frozen=True, kw_only=True):
    """One exact CLI, TUI, or CI invocation supplying runtime overrides."""


@final
class CellId(_UuidIdentifier, frozen=True, kw_only=True):
    """A compiled plan-cell occurrence."""


@final
class TrialId(_UuidIdentifier, frozen=True, kw_only=True):
    """A repeated sample occurrence within a cell."""


@final
class EnvironmentExecutionId(_UuidIdentifier, frozen=True, kw_only=True):
    """One cell/trial execution in one Environment."""


@final
class StepExecutionId(_UuidIdentifier, frozen=True, kw_only=True):
    """One future Scenario step execution occurrence."""


@final
class AttemptId(_UuidIdentifier, frozen=True, kw_only=True):
    """One concrete HTTP attempt occurrence."""


@final
class ExchangeId(_UuidIdentifier, frozen=True, kw_only=True):
    """One concrete HTTP exchange, including redirects within an attempt."""


@final
class ComparisonId(_UuidIdentifier, frozen=True, kw_only=True):
    """One directed binary comparison occurrence."""


@final
class RuleApplicationId(_UuidIdentifier, frozen=True, kw_only=True):
    """One effective rule application in an execution or comparison scope."""


@final
class FactId(_UuidIdentifier, frozen=True, kw_only=True):
    """One assertion, diff, runtime, or analysis fact occurrence."""


@final
class BodyId(_UuidIdentifier, frozen=True, kw_only=True):
    """One captured body occurrence."""


@final
class ViewId(_UuidIdentifier, frozen=True, kw_only=True):
    """One decoded body-view occurrence."""


@final
class AnalysisId(_UuidIdentifier, frozen=True, kw_only=True):
    """One authorized external-analysis occurrence."""
