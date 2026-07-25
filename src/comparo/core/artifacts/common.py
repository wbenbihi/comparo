"""Shared safe scalar, identity, capture, and failure artifact contracts."""

from __future__ import annotations

from fractions import Fraction
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum

ARTIFACT_API_VERSION = "comparo.artifact/v1alpha1"
SANITIZER_VERSION = "comparo.sanitizer/v1alpha1"
CREDENTIAL_CATALOG_VERSION = "comparo.credentials/v1alpha1"


def require_text(value: object, *, label: str, maximum: int = 512) -> None:
    """Require exact bounded non-empty text."""
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{label} must be bounded non-empty text")


def require_count(value: object, *, label: str) -> None:
    """Require an exact non-negative integer and reject bool."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


class ArtifactCompletionKind(DomainStrEnum):
    """Terminal run lifecycle states retained independently from the gate."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    INTERNAL_ERROR = "internal-error"


class ArtifactGateVerdict(DomainStrEnum):
    """Persistable gate verdicts that never collapse absence into success."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_REQUESTED = "not-requested"


class ArtifactExecutionOutcome(DomainStrEnum):
    """Terminal Environment execution states."""

    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ArtifactComparisonOutcome(DomainStrEnum):
    """Terminal directed comparison states."""

    EVALUATED = "evaluated"
    INCOMPLETE = "incomplete"


class ArtifactFactEngine(DomainStrEnum):
    """Pure engines from which artifact facts originate."""

    ASSERTION = "assertion"
    DIFF = "diff"


class ArtifactFactOutcome(DomainStrEnum):
    """Closed reporter-facing fact outcomes across both pure engines."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    EQUIVALENT = "equivalent"
    DRIFTED = "drifted"
    INCOMPLETE = "incomplete"
    ERROR = "error"
    NOT_EVALUATED = "not-evaluated"


class ArtifactSeverity(DomainStrEnum):
    """Normalized severity retained without importing live gate types."""

    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class ArtifactCaptureExtent(DomainStrEnum):
    """Truthful persistence extent of one authored or observed source."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    OMITTED = "omitted"


class ArtifactSourceCaptureState(DomainStrEnum):
    """Truth about the transient source before artifact policy is applied."""

    COMPLETE = "complete"
    REMOTE_TRUNCATED = "remote-truncated"
    BUDGET_EXCEEDED = "budget-exceeded"
    STORAGE_FAILED = "storage-failed"
    CANCELLED = "cancelled"
    READ_FAILED = "read-failed"
    IDLE_TIMEOUT = "idle-timeout"
    CONTENT_DECODING_FAILED = "content-decoding-failed"
    POLICY_OMITTED = "policy-omitted"
    EXPLICITLY_TRUNCATED = "explicitly-truncated"


class ArtifactOmissionReason(DomainStrEnum):
    """Value-free reasons sanitized capture could not be persisted completely."""

    POLICY = "policy"
    SOURCE_UNAVAILABLE = "source-unavailable"
    SOURCE_INCOMPLETE = "source-incomplete"
    REDACTION_FAILED = "redaction-failed"
    DECODE_FAILED = "decode-failed"
    BUDGET_EXCEEDED = "budget-exceeded"
    STORAGE_FAILED = "storage-failed"
    CANCELLED = "cancelled"


class ArtifactAmountUnit(DomainStrEnum):
    """Exact units retained for resource-budget accounting."""

    COUNT = "count"
    BYTES = "bytes"
    NANOSECONDS = "nanoseconds"
    RATIO = "ratio"


@final
class ArtifactDefinition(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Safe canonical identity and fingerprint of one authored definition."""

    namespace: str
    kind: str
    resource_id: str
    fingerprint: str

    def __post_init__(self) -> None:
        """Require bounded structural definition coordinates."""
        for value, label, maximum in (
            (self.namespace, "definition namespace", 63),
            (self.kind, "definition kind", 63),
            (self.resource_id, "definition resource ID", 255),
            (self.fingerprint, "definition fingerprint", 128),
        ):
            require_text(value, label=label, maximum=maximum)


@final
class ArtifactMetadata(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Version and occurrence identity fixed before rendering or persistence."""

    artifact_id: str
    run_id: str
    invocation_id: str
    created_unix_nanoseconds: int
    tool_name: str
    tool_version: str
    configuration_api_version: str
    sanitizer_version: str = SANITIZER_VERSION
    credential_catalog_version: str = CREDENTIAL_CATALOG_VERSION

    def __post_init__(self) -> None:
        """Require deterministic, bounded metadata without ambient derivation."""
        for value, label, maximum in (
            (self.artifact_id, "artifact ID", 63),
            (self.run_id, "run ID", 64),
            (self.invocation_id, "invocation ID", 64),
            (self.tool_name, "tool name", 63),
            (self.tool_version, "tool version", 63),
            (self.configuration_api_version, "configuration API version", 127),
            (self.sanitizer_version, "sanitizer version", 127),
            (self.credential_catalog_version, "credential catalog version", 127),
        ):
            require_text(value, label=label, maximum=maximum)
        if type(self.created_unix_nanoseconds) is not int:
            raise TypeError("artifact creation instant must be integer nanoseconds")


@final
class ArtifactProvenance(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Safe reproducibility identities retained independently of live configuration."""

    project: ArtifactDefinition
    execution_policy: ArtifactDefinition
    artifact_profile: ArtifactDefinition | None
    redaction_profile: ArtifactDefinition | None
    configuration_fingerprint: str
    plan_fingerprint: str
    ordering: str

    def __post_init__(self) -> None:
        """Require project, operator authority, and exact plan semantics."""
        if type(self.project) is not ArtifactDefinition:
            raise TypeError("artifact provenance requires a project definition")
        if type(self.execution_policy) is not ArtifactDefinition:
            raise TypeError("artifact provenance requires an ExecutionPolicy definition")
        for profile, label in (
            (self.artifact_profile, "ArtifactProfile"),
            (self.redaction_profile, "RedactionProfile"),
        ):
            if profile is not None and type(profile) is not ArtifactDefinition:
                raise TypeError(f"artifact provenance {label} must be an ArtifactDefinition")
        require_text(
            self.configuration_fingerprint,
            label="configuration fingerprint",
            maximum=128,
        )
        require_text(self.plan_fingerprint, label="plan fingerprint", maximum=128)
        require_text(self.ordering, label="plan ordering", maximum=127)


@final
class ArtifactMetric(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One named non-negative exact count."""

    name: str
    value: int

    def __post_init__(self) -> None:
        """Require a bounded name and exact non-negative integer."""
        require_text(self.name, label="artifact metric name", maximum=63)
        require_count(self.value, label="artifact metric value")


@final
class ArtifactAmount(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One exact non-negative budget amount without lossy unit conversion."""

    unit: ArtifactAmountUnit
    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        """Require a reduced-shape positive denominator and unit coherence."""
        if type(self.unit) is not ArtifactAmountUnit:
            raise TypeError("artifact amount unit must be typed")
        require_count(self.numerator, label="artifact amount numerator")
        if type(self.denominator) is not int or self.denominator <= 0:
            raise ValueError("artifact amount denominator must be a positive integer")
        if self.unit is not ArtifactAmountUnit.RATIO and self.denominator != 1:
            raise ValueError("non-ratio artifact amounts require denominator one")

    @property
    def fraction(self) -> Fraction:
        """Return the exact comparison value without exposing a wire dependency."""
        return Fraction(self.numerator, self.denominator)


@final
class ArtifactBudget(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One effective budget and its final value-free accounting snapshot."""

    resource: str
    scope: str
    limit: ArtifactAmount
    used: ArtifactAmount
    exhausted: bool

    def __post_init__(self) -> None:
        """Require a bounded identity and coherent non-negative accounting."""
        require_text(self.resource, label="artifact budget resource", maximum=127)
        require_text(self.scope, label="artifact budget scope", maximum=255)
        if type(self.limit) is not ArtifactAmount or type(self.used) is not ArtifactAmount:
            raise TypeError("artifact budget amounts must be typed")
        if self.limit.unit is not self.used.unit:
            raise ValueError("artifact budget amounts must use one unit")
        if type(self.exhausted) is not bool:
            raise TypeError("artifact budget exhausted marker must be bool")
        if self.exhausted != (self.used.fraction >= self.limit.fraction):
            raise ValueError("artifact budget exhaustion must match its accounting")


def require_metrics(metrics: object, *, label: str) -> None:
    """Require unique, name-sorted immutable artifact metrics."""
    if type(metrics) is not tuple or any(type(item) is not ArtifactMetric for item in metrics):
        raise TypeError(f"{label} metrics must be immutable and typed")
    names = tuple(item.name for item in metrics)
    if len(set(names)) != len(names) or names != tuple(sorted(names)):
        raise ValueError(f"{label} metric names must be unique and canonical")


@final
class ArtifactBlob(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Content-addressed sanitized blob metadata without any storage locator."""

    sha256: str
    byte_count: int
    media_type: str | None = None

    def __post_init__(self) -> None:
        """Require canonical digest, size, and optional safe media type."""
        if (
            type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("artifact blob digest must be lowercase SHA-256")
        require_count(self.byte_count, label="artifact blob byte count")
        if self.media_type is not None:
            require_text(self.media_type, label="artifact blob media type", maximum=255)


@final
class ArtifactCapture(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Truthful sanitized capture state for one body or evidence source."""

    source_state: ArtifactSourceCaptureState
    extent: ArtifactCaptureExtent
    source_byte_count: int | None
    blob: ArtifactBlob | None
    reason: ArtifactOmissionReason | None = None
    redaction_count: int = 0

    def __post_init__(self) -> None:
        """Prevent partial or omitted capture from masquerading as complete."""
        if type(self.source_state) is not ArtifactSourceCaptureState:
            raise TypeError("artifact source capture state must be typed")
        if type(self.extent) is not ArtifactCaptureExtent:
            raise TypeError("artifact capture extent must be typed")
        if self.source_byte_count is not None:
            require_count(self.source_byte_count, label="artifact source byte count")
        if self.blob is not None and type(self.blob) is not ArtifactBlob:
            raise TypeError("artifact capture blob must be ArtifactBlob")
        if self.reason is not None and type(self.reason) is not ArtifactOmissionReason:
            raise TypeError("artifact capture omission reason must be typed")
        require_count(self.redaction_count, label="artifact redaction count")
        _validate_capture_shape(self)


def _validate_capture_shape(capture: ArtifactCapture) -> None:
    """Keep persisted extent and source truth coherent."""
    if capture.extent is ArtifactCaptureExtent.COMPLETE:
        if capture.blob is None or capture.reason is not None:
            raise ValueError("complete artifact capture requires only a blob")
        if capture.source_state is not ArtifactSourceCaptureState.COMPLETE:
            raise ValueError("incomplete source capture cannot become a complete artifact")
    elif capture.reason is None:
        raise ValueError("non-complete artifact capture requires an explicit reason")
    if capture.extent is ArtifactCaptureExtent.OMITTED and capture.blob is not None:
        raise ValueError("omitted artifact capture cannot retain a blob or raw digest")


@final
class ArtifactFailure(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Structured failure safe for persistence and presentation."""

    stage: str
    code: str
    retryable: bool | None = None

    def __post_init__(self) -> None:
        """Retain bounded tokens instead of exception prose."""
        require_text(self.stage, label="artifact failure stage", maximum=127)
        require_text(self.code, label="artifact failure code", maximum=255)
        if self.retryable is not None and type(self.retryable) is not bool:
            raise TypeError("artifact retryable marker must be bool")
