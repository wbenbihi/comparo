"""Typed capability requests and final operator-authority decisions."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import final

import msgspec

from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.provenance import ProvenanceEntry

_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


class CapabilityCategory(StrEnum):
    """Security-relevant effect categories controlled by ExecutionPolicy."""

    HTTP = "http"
    AMBIENT_ENVIRONMENT = "ambient-environment"
    FILE_READ = "file-read"
    SECRET_PROVIDER = "secret-provider"  # noqa: S105
    SECRET_EGRESS = "secret-egress"  # noqa: S105
    REMOTE_SCHEMA = "remote-schema"
    EXTERNAL_ANALYSIS = "external-analysis"
    RAW_PERSISTENCE = "raw-persistence"
    SANITIZED_PERSISTENCE = "sanitized-persistence"


@final
class CapabilityId(msgspec.Struct, frozen=True, kw_only=True):
    """A namespaced and versioned identity for a built-in or future capability."""

    namespace: str
    name: str
    version: str

    def __post_init__(self) -> None:
        """Reject ambiguous or unversioned capability identities."""
        for label, value in (("namespace", self.namespace), ("name", self.name)):
            if type(value) is not str or _TOKEN_PATTERN.fullmatch(value) is None:
                raise ValueError(f"capability {label} must be a lowercase dotted token")
        if (
            type(self.version) is not str
            or not self.version.isascii()
            or not self.version.isdigit()
        ):
            raise ValueError("capability version must be an ASCII decimal version")


@final
class CapabilityOperation(msgspec.Struct, frozen=True, kw_only=True):
    """A stable operation name within a capability contract."""

    value: str

    def __post_init__(self) -> None:
        """Require a deterministic machine-readable operation name."""
        if type(self.value) is not str or _TOKEN_PATTERN.fullmatch(self.value) is None:
            raise ValueError("capability operation must be a lowercase dotted token")


class CapabilityTargetKind(StrEnum):
    """The kind of non-secret identifier to which a capability applies."""

    RESOURCE = "resource"
    DESTINATION = "destination"
    FILE_ROOT = "file-root"
    ENVIRONMENT_NAME = "environment-name"
    SECRET_NAME = "secret-name"  # noqa: S105
    PROVIDER = "provider"
    SCHEMA_SOURCE = "schema-source"
    ARTIFACT_STORE = "artifact-store"


@final
class NamedCapabilityTarget(msgspec.Struct, frozen=True, kw_only=True):
    """A logical, non-payload target identifier used during authorization."""

    kind: CapabilityTargetKind
    identifier: str

    def __post_init__(self) -> None:
        """Validate the target category and non-blank logical identifier."""
        if type(self.kind) is not CapabilityTargetKind:
            raise TypeError("capability target kind must be a CapabilityTargetKind")
        if type(self.identifier) is not str or not self.identifier.strip():
            raise ValueError("capability target identifier must be non-blank text")


type CapabilityTarget = ResourceIdentity | NamedCapabilityTarget


@final
class CapabilityRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One concrete effect request presented to operator-owned authority."""

    category: CapabilityCategory
    capability_id: CapabilityId
    operation: CapabilityOperation
    target: CapabilityTarget | None = None

    def __post_init__(self) -> None:
        """Require only typed capability and target components."""
        if type(self.category) is not CapabilityCategory:
            raise TypeError("capability category must be a CapabilityCategory")
        if type(self.capability_id) is not CapabilityId:
            raise TypeError("capability ID must be a CapabilityId")
        if type(self.operation) is not CapabilityOperation:
            raise TypeError("capability operation must be a CapabilityOperation")
        if self.target is not None and type(self.target) not in {
            ResourceIdentity,
            NamedCapabilityTarget,
        }:
            raise TypeError("capability target must be a supported typed target")


class CapabilityDenialReason(StrEnum):
    """Stable reasons why effective authority refused a capability request."""

    NOT_ALLOWED = "not-allowed"
    EXPLICITLY_DENIED = "explicitly-denied"
    OUTSIDE_ALLOWED_TARGETS = "outside-allowed-targets"
    MISSING_REQUIRED_SAFEGUARD = "missing-required-safeguard"
    UNSUPPORTED = "unsupported"


def _validate_authority_chain(authority: tuple[ProvenanceEntry, ...]) -> None:
    """Require immutable, non-empty, value-free policy provenance."""
    if type(authority) is not tuple:
        raise TypeError("capability authority must be an immutable tuple")
    if not authority:
        raise ValueError("capability authority must not be empty")
    if any(type(entry) is not ProvenanceEntry for entry in authority):
        raise TypeError("capability authority must contain ProvenanceEntry values")


@final
class CapabilityGranted(msgspec.Struct, frozen=True, kw_only=True):
    """A positive final authority decision for one exact request."""

    request: CapabilityRequest
    authority: tuple[ProvenanceEntry, ...]

    def __post_init__(self) -> None:
        """Validate the request and immutable authority chain."""
        if type(self.request) is not CapabilityRequest:
            raise TypeError("capability grant requires a CapabilityRequest")
        _validate_authority_chain(self.authority)


@final
class CapabilityDenied(msgspec.Struct, frozen=True, kw_only=True):
    """A negative final authority decision distinct from runtime failure."""

    request: CapabilityRequest
    reason: CapabilityDenialReason
    authority: tuple[ProvenanceEntry, ...]

    def __post_init__(self) -> None:
        """Validate the request, typed reason, and immutable authority chain."""
        if type(self.request) is not CapabilityRequest:
            raise TypeError("capability denial requires a CapabilityRequest")
        if type(self.reason) is not CapabilityDenialReason:
            raise TypeError("capability denial reason must be a CapabilityDenialReason")
        _validate_authority_chain(self.authority)


type CapabilityDecision = CapabilityGranted | CapabilityDenied
