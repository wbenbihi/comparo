"""Typed capability requests and final operator-authority decisions."""

from __future__ import annotations

import re
from typing import final
from urllib.parse import urlsplit

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind

_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")
_TARGET_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,254}")
_EXTERNAL_TARGET_PATTERN = re.compile(
    r"https?://[A-Za-z0-9][A-Za-z0-9._~:/?#[\]@!$&'()*+,;=%-]{0,2040}"
)


class CapabilityCategory(DomainStrEnum):
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
        if int(self.version) < 1:
            raise ValueError("capability version must be positive")
        if str(int(self.version)) != self.version:
            raise ValueError("capability version must use canonical decimal notation")


@final
class CapabilityOperation(msgspec.Struct, frozen=True, kw_only=True):
    """A stable operation name within a capability contract."""

    value: str

    def __post_init__(self) -> None:
        """Require a deterministic machine-readable operation name."""
        if type(self.value) is not str or _TOKEN_PATTERN.fullmatch(self.value) is None:
            raise ValueError("capability operation must be a lowercase dotted token")


class CapabilityTargetKind(DomainStrEnum):
    """The kind of non-secret identifier to which a capability applies."""

    RESOURCE = "resource"
    DESTINATION = "destination"
    FILE_ROOT = "file-root"
    ENVIRONMENT_NAME = "environment-name"
    SECRET_NAME = "secret-name"  # noqa: S105
    PROVIDER = "provider"
    SCHEMA_SOURCE = "schema-source"
    ARTIFACT_STORE = "artifact-store"


_EXTERNAL_TARGET_KINDS = frozenset(
    {CapabilityTargetKind.DESTINATION, CapabilityTargetKind.SCHEMA_SOURCE}
)


def _is_external_target_identifier(identifier: object) -> bool:
    """Recognize a bounded HTTP(S) target without embedded user-info credentials."""
    if type(identifier) is not str or _EXTERNAL_TARGET_PATTERN.fullmatch(identifier) is None:
        return False
    try:
        parsed = urlsplit(identifier)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.username is None and not parsed.query and not parsed.fragment


@final
class NamedCapabilityTarget(msgspec.Struct, frozen=True, kw_only=True):
    """A safe exact external target or logical alias used during authorization."""

    kind: CapabilityTargetKind
    identifier: str

    def __post_init__(self) -> None:
        """Validate the target category and bounded non-payload identifier."""
        if type(self.kind) is not CapabilityTargetKind:
            raise TypeError("capability target kind must be a CapabilityTargetKind")
        if self.kind is CapabilityTargetKind.RESOURCE:
            raise ValueError("resource capability targets require ResourceIdentity")
        if self.kind in _EXTERNAL_TARGET_KINDS:
            if not (
                type(self.identifier) is str
                and (
                    _TARGET_IDENTIFIER_PATTERN.fullmatch(self.identifier) is not None
                    or _is_external_target_identifier(self.identifier)
                )
            ):
                raise ValueError(
                    "external capability target must be a logical alias or bounded HTTP(S) URL"
                )
        elif (
            type(self.identifier) is not str
            or _TARGET_IDENTIFIER_PATTERN.fullmatch(self.identifier) is None
        ):
            raise ValueError(
                "logical capability target identifier must be a bounded structural token"
            )


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


class CapabilityDenialReason(DomainStrEnum):
    """Stable reasons why effective authority refused a capability request."""

    NOT_ALLOWED = "not-allowed"
    EXPLICITLY_DENIED = "explicitly-denied"
    OUTSIDE_ALLOWED_TARGETS = "outside-allowed-targets"
    MISSING_REQUIRED_SAFEGUARD = "missing-required-safeguard"
    UNSUPPORTED = "unsupported"


@final
class ExecutionPolicyAuthority(msgspec.Struct, frozen=True, kw_only=True):
    """Exact policy definition whose fingerprint authorized a capability decision."""

    policy: DefinitionRef

    def __post_init__(self) -> None:
        """Require an exact fingerprinted ExecutionPolicy definition."""
        if type(self.policy) is not DefinitionRef:
            raise TypeError("capability authority policy must be a DefinitionRef")
        if self.policy.identity.kind is not ResourceKind.EXECUTION_POLICY:
            raise ValueError("capability authority must identify an ExecutionPolicy")


@final
class CapabilityGranted(msgspec.Struct, frozen=True, kw_only=True):
    """A positive final authority decision for one exact request."""

    request: CapabilityRequest
    authority: ExecutionPolicyAuthority

    def __post_init__(self) -> None:
        """Validate the request and immutable authority chain."""
        if type(self.request) is not CapabilityRequest:
            raise TypeError("capability grant requires a CapabilityRequest")
        if type(self.authority) is not ExecutionPolicyAuthority:
            raise TypeError("capability grant requires exact ExecutionPolicy authority")


@final
class CapabilityDenied(msgspec.Struct, frozen=True, kw_only=True):
    """A negative final authority decision distinct from runtime failure."""

    request: CapabilityRequest
    reason: CapabilityDenialReason
    authority: ExecutionPolicyAuthority

    def __post_init__(self) -> None:
        """Validate the request, typed reason, and immutable authority chain."""
        if type(self.request) is not CapabilityRequest:
            raise TypeError("capability denial requires a CapabilityRequest")
        if type(self.reason) is not CapabilityDenialReason:
            raise TypeError("capability denial reason must be a CapabilityDenialReason")
        if type(self.authority) is not ExecutionPolicyAuthority:
            raise TypeError("capability denial requires exact ExecutionPolicy authority")


type CapabilityDecision = CapabilityGranted | CapabilityDenied
