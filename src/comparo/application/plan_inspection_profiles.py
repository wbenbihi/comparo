"""Typed validation, gate, and profile attachment facts for Plan review."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_inspection_identity import is_sha256
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity

PROFILE_KINDS = frozenset(
    {
        "AssertionProfile",
        "DiffProfile",
        "GateProfile",
        "SafetyProfile",
        "RedactionProfile",
        "ArtifactProfile",
    }
)


class PlanProfileAttachmentInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Stable named or inline profile identity without presentation prose."""

    kind: str
    namespace: str | None
    resource_id: str | None
    fingerprint: str
    inline: bool
    owner: tuple[str, str, str] | None = None
    fragment: str | None = None

    def __post_init__(self) -> None:
        """Keep named and inline attachment identities structurally disjoint."""
        if self.kind not in PROFILE_KINDS:
            raise ValueError("profile attachment kind is unsupported")
        if type(self.inline) is not bool:
            raise TypeError("profile attachment inline marker must be a bool")
        if self.namespace is not None and (
            type(self.namespace) is not str or not self.namespace or len(self.namespace) > 255
        ):
            raise ValueError("profile attachment namespace must be bounded non-empty text")
        if self.resource_id is not None and (
            type(self.resource_id) is not str or not self.resource_id or len(self.resource_id) > 255
        ):
            raise ValueError("profile attachment resource ID must be bounded non-empty text")
        if self.owner is not None and (
            type(self.owner) is not tuple
            or len(self.owner) != 3
            or any(type(item) is not str or not item or len(item) > 255 for item in self.owner)
        ):
            raise ValueError("inline profile owner must be a complete bounded coordinate")
        if self.fragment is not None and (
            type(self.fragment) is not str or not self.fragment or len(self.fragment) > 255
        ):
            raise ValueError("inline profile fragment must be bounded non-empty text")
        named = (
            self.namespace is not None
            and self.resource_id is not None
            and self.owner is None
            and self.fragment is None
        )
        inline = (
            self.namespace is None
            and self.resource_id is None
            and self.owner is not None
            and self.fragment is not None
        )
        if self.inline and not inline:
            raise ValueError("inline profile attachment requires owner and fragment only")
        if not self.inline and not named:
            raise ValueError("named profile attachment requires namespace and resource ID only")
        if not is_sha256(self.fingerprint):
            raise ValueError("profile attachment fingerprint must be a SHA-256 hex digest")


@final
class PlanGateThresholdInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Exact allowed adverse-rule counts for one gate severity."""

    severity: str
    violated: int
    drifted: int
    unavailable: int
    incomparable: int
    incomplete: int
    errors: int

    def __post_init__(self) -> None:
        """Require one severity and exact non-negative allowances."""
        if self.severity not in {item.value for item in GateSeverity}:
            raise ValueError("gate threshold severity is unsupported")
        counts = (
            self.violated,
            self.drifted,
            self.unavailable,
            self.incomparable,
            self.incomplete,
            self.errors,
        )
        if any(type(item) is not int or item < 0 for item in counts):
            raise ValueError("gate threshold allowances must be non-negative integers")


@final
class PlanGateInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Exact compiled release-gate formula admitted by evaluation preflight."""

    profile: ResourceCoordinate
    severities: tuple[str, ...]
    environment_roles: tuple[str, ...]
    require_complete_run: bool
    require_assertion_coverage: bool
    require_diff_coverage: bool
    thresholds: tuple[PlanGateThresholdInspection, ...]

    def __post_init__(self) -> None:
        """Require one exact kind-correct formula with coherent severities."""
        if type(self.profile) is not ResourceCoordinate or self.profile.kind != "GateProfile":
            raise TypeError("compiled Plan gate must identify a GateProfile")
        for values in (self.severities, self.environment_roles):
            if (
                type(values) is not tuple
                or not values
                or any(type(item) is not str or not item for item in values)
            ):
                raise ValueError("compiled Plan gate selections must be non-empty text tuples")
            if len(values) != len(set(values)):
                raise ValueError("compiled Plan gate selections must be unique")
        if any(value not in {item.value for item in GateSeverity} for value in self.severities):
            raise ValueError("compiled Plan gate severity is unsupported")
        if any(
            value not in {item.value for item in EnvironmentRole}
            for value in self.environment_roles
        ):
            raise ValueError("compiled Plan gate Environment role is unsupported")
        flags = (
            self.require_complete_run,
            self.require_assertion_coverage,
            self.require_diff_coverage,
        )
        if any(type(item) is not bool for item in flags):
            raise TypeError("compiled Plan gate requirements must be booleans")
        if type(self.thresholds) is not tuple or any(
            type(item) is not PlanGateThresholdInspection for item in self.thresholds
        ):
            raise TypeError("compiled Plan gate thresholds must be typed")
        threshold_severities = tuple(item.severity for item in self.thresholds)
        if len(threshold_severities) != len(set(threshold_severities)) or set(
            threshold_severities
        ) != set(self.severities):
            raise ValueError("compiled Plan gate thresholds must match selected severities")


@final
class PlanValidationInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Exact effect-free validation compilation summary for this Plan."""

    assertion_rules: int
    diff_rules: int
    gate: PlanGateInspection | None

    def __post_init__(self) -> None:
        """Require exact non-negative rule counts and an optional typed gate."""
        if any(
            type(item) is not int or item < 0 for item in (self.assertion_rules, self.diff_rules)
        ):
            raise ValueError("compiled validation rule counts must be non-negative integers")
        if self.gate is not None and type(self.gate) is not PlanGateInspection:
            raise TypeError("compiled validation gate must be typed or absent")


@final
class PlanProfilesInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free profile attachments that define one subject's validation behavior."""

    assertions: tuple[PlanProfileAttachmentInspection, ...] = ()
    diffs: tuple[PlanProfileAttachmentInspection, ...] = ()
    gate: PlanProfileAttachmentInspection | None = None
    safety: tuple[PlanProfileAttachmentInspection, ...] = ()
    redaction: PlanProfileAttachmentInspection | None = None
    artifacts: PlanProfileAttachmentInspection | None = None

    def __post_init__(self) -> None:
        """Require immutable typed attachments with their exact profile kinds."""
        groups = (
            (self.assertions, "AssertionProfile"),
            (self.diffs, "DiffProfile"),
            (self.safety, "SafetyProfile"),
        )
        for values, kind in groups:
            if type(values) is not tuple or any(
                type(item) is not PlanProfileAttachmentInspection or item.kind != kind
                for item in values
            ):
                raise TypeError(f"plan {kind} attachments must be typed and kind-correct")
        optional = (
            (self.gate, "GateProfile"),
            (self.redaction, "RedactionProfile"),
            (self.artifacts, "ArtifactProfile"),
        )
        if any(
            item is not None
            and (type(item) is not PlanProfileAttachmentInspection or item.kind != kind)
            for item, kind in optional
        ):
            raise TypeError("plan optional profile attachments must be typed and kind-correct")


__all__ = (
    "PlanGateInspection",
    "PlanGateThresholdInspection",
    "PlanProfileAttachmentInspection",
    "PlanProfilesInspection",
    "PlanValidationInspection",
)
