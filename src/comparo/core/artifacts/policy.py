"""Typed capture, redaction, persistence, and caller-owned artifact limits."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.selectors.model import CompiledSelector


class ArtifactPersistMode(DomainStrEnum):
    """When a safe artifact bundle is written by a configured sink."""

    ALWAYS = "always"
    ON_GATE_FAILURE = "on-gate-failure"
    NEVER = "never"


class UnsafeSourceBehavior(DomainStrEnum):
    """Fail-closed response to a source that cannot be safely inspected."""

    OMIT = "omit"
    REJECT_ARTIFACT = "reject-artifact"


class RedactionAction(DomainStrEnum):
    """Initial shared-selector redaction actions."""

    MASK = "mask"
    REMOVE = "remove"
    OMIT_SOURCE = "omit-source"


@final
class ArtifactCapturePolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Which already-sanitized live sources may enter a RunArtifact."""

    requests: bool = True
    responses: bool = True
    bodies: bool = True
    evidence: bool = True

    def __post_init__(self) -> None:
        """Require explicit booleans rather than truthy configuration values."""
        if any(
            type(value) is not bool
            for value in (self.requests, self.responses, self.bodies, self.evidence)
        ):
            raise TypeError("artifact capture policy flags must be bool values")


@final
class ArtifactLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite effective artifact and report budgets supplied by composition."""

    maximum_blobs: PositiveCount
    maximum_blob_bytes: ByteCount
    maximum_total_blob_bytes: ByteCount
    maximum_manifest_bytes: ByteCount
    maximum_report_bytes: ByteCount

    def __post_init__(self) -> None:
        """Require typed finite dimensions without a hidden application maximum."""
        if type(self.maximum_blobs) is not PositiveCount:
            raise TypeError("artifact blob limit must be PositiveCount")
        for value in (
            self.maximum_blob_bytes,
            self.maximum_total_blob_bytes,
            self.maximum_manifest_bytes,
            self.maximum_report_bytes,
        ):
            if type(value) is not ByteCount:
                raise TypeError("artifact byte limits must be ByteCount values")
        if self.maximum_blob_bytes.value > self.maximum_total_blob_bytes.value:
            raise ValueError("per-blob artifact limit cannot exceed the aggregate blob limit")


@final
class ArtifactPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Effective persistence policy independent of external YAML representation."""

    persist: ArtifactPersistMode
    unsafe_source: UnsafeSourceBehavior
    capture: ArtifactCapturePolicy
    limits: ArtifactLimits

    def __post_init__(self) -> None:
        """Require every policy dimension explicitly."""
        if type(self.persist) is not ArtifactPersistMode:
            raise TypeError("artifact persistence mode must be typed")
        if type(self.unsafe_source) is not UnsafeSourceBehavior:
            raise TypeError("unsafe artifact source behavior must be typed")
        if type(self.capture) is not ArtifactCapturePolicy:
            raise TypeError("artifact capture policy must be typed")
        if type(self.limits) is not ArtifactLimits:
            raise TypeError("artifact limits must be typed")


@final
class RedactionRule(msgspec.Struct, frozen=True, kw_only=True):
    """One shared compiled selector paired with a persistence-only action."""

    rule_id: str
    selector: CompiledSelector
    action: RedactionAction

    def __post_init__(self) -> None:
        """Require stable rule identity, a shared selector, and closed action."""
        if type(self.rule_id) is not str or not self.rule_id or len(self.rule_id) > 63:
            raise ValueError("redaction rule ID must be bounded non-empty text")
        if type(self.selector) is not CompiledSelector:
            raise TypeError("redaction rule selector must be CompiledSelector")
        if type(self.action) is not RedactionAction:
            raise TypeError("redaction action must be typed")


@final
class RedactionPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Mandatory floor plus ordered application-specific shared-selector rules."""

    rules: tuple[RedactionRule, ...] = ()
    marker: str = "<redacted>"

    def __post_init__(self) -> None:
        """Require canonical unique rules and a visible bounded marker."""
        if type(self.rules) is not tuple or any(
            type(rule) is not RedactionRule for rule in self.rules
        ):
            raise TypeError("redaction rules must be immutable and typed")
        identities = tuple(rule.rule_id for rule in self.rules)
        if len(set(identities)) != len(identities):
            raise ValueError("redaction rule IDs must be unique")
        if type(self.marker) is not str or not self.marker or len(self.marker) > 64:
            raise ValueError("redaction marker must be bounded non-empty text")
