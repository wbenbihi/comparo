"""Persistable run completion and gate assessment contracts."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.artifacts.common import ArtifactCompletionKind
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.common import require_metrics
from comparo.core.artifacts.common import require_text


@final
class ArtifactCompletion(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One terminal lifecycle state with only structured reasons and codes."""

    kind: ArtifactCompletionKind
    instant_unix_nanoseconds: int | None
    reasons: tuple[str, ...] = ()
    stage: str | None = None
    code: str | None = None

    def __post_init__(self) -> None:
        """Keep lifecycle truth exhaustive and value-free."""
        if type(self.kind) is not ArtifactCompletionKind:
            raise TypeError("artifact completion kind must be typed")
        if (
            self.instant_unix_nanoseconds is not None
            and type(self.instant_unix_nanoseconds) is not int
        ):
            raise TypeError("artifact completion instant must be integer nanoseconds")
        if type(self.reasons) is not tuple or any(type(item) is not str for item in self.reasons):
            raise TypeError("artifact completion reasons must be immutable text")
        for reason in self.reasons:
            require_text(reason, label="artifact completion reason", maximum=127)
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("artifact completion reasons must be unique")
        for value, label in ((self.stage, "completion stage"), (self.code, "completion code")):
            if value is not None:
                require_text(value, label=label, maximum=127)
        _require_completion_shape(self)


def _require_completion_shape(completion: ArtifactCompletion) -> None:
    has_instant = completion.instant_unix_nanoseconds is not None
    if completion.kind is ArtifactCompletionKind.COMPLETE:
        valid = (
            has_instant
            and not completion.reasons
            and completion.stage is None
            and completion.code is None
        )
    elif completion.kind is ArtifactCompletionKind.PARTIAL:
        valid = (
            has_instant
            and bool(completion.reasons)
            and completion.stage is None
            and completion.code is None
        )
    elif completion.kind is ArtifactCompletionKind.CANCELLED:
        valid = (
            has_instant
            and len(completion.reasons) == 1
            and completion.stage is not None
            and completion.code is None
        )
    else:
        valid = (
            not has_instant
            and not completion.reasons
            and completion.stage is not None
            and completion.code is not None
        )
    if not valid:
        raise ValueError("artifact completion kind contradicts its retained fields")


@final
class ArtifactGateCounts(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One severity's exact actual and allowed gate dimensions."""

    severity: ArtifactSeverity
    actual: tuple[ArtifactMetric, ...]
    allowed: tuple[ArtifactMetric, ...]

    def __post_init__(self) -> None:
        """Require matching canonical metric dimensions."""
        if type(self.severity) is not ArtifactSeverity:
            raise TypeError("artifact gate severity must be typed")
        require_metrics(self.actual, label="actual gate")
        require_metrics(self.allowed, label="allowed gate")
        if tuple(item.name for item in self.actual) != tuple(item.name for item in self.allowed):
            raise ValueError("actual and allowed gate dimensions must match")


@final
class ArtifactGate(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """A gate verdict and its auditable coverage inputs, independent of completion."""

    verdict: ArtifactGateVerdict
    profile: ArtifactDefinition | None
    reasons: tuple[str, ...]
    coverage: tuple[ArtifactMetric, ...]
    assessments: tuple[ArtifactGateCounts, ...]

    def __post_init__(self) -> None:  # noqa: C901 - closed gate-state truth table
        """Keep not-requested and requested shapes disjoint."""
        if type(self.verdict) is not ArtifactGateVerdict:
            raise TypeError("artifact gate verdict must be typed")
        if self.profile is not None and type(self.profile) is not ArtifactDefinition:
            raise TypeError("artifact gate profile must be an ArtifactDefinition")
        if type(self.reasons) is not tuple or any(type(item) is not str for item in self.reasons):
            raise TypeError("artifact gate reasons must be immutable text")
        for reason in self.reasons:
            require_text(reason, label="artifact gate reason", maximum=127)
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("artifact gate reasons must be unique")
        require_metrics(self.coverage, label="gate coverage")
        if type(self.assessments) is not tuple or any(
            type(item) is not ArtifactGateCounts for item in self.assessments
        ):
            raise TypeError("artifact gate assessments must be immutable and typed")
        severities = tuple(item.severity for item in self.assessments)
        if len(set(severities)) != len(severities):
            raise ValueError("artifact gate assessment severities must be unique")
        not_requested = self.verdict is ArtifactGateVerdict.NOT_REQUESTED
        empty_requested_shape = (
            self.profile is None and not self.reasons and not self.coverage and not self.assessments
        )
        if not_requested != empty_requested_shape:
            raise ValueError("not-requested gate shape contradicts its retained assessments")
        if not not_requested:
            if self.profile is None:
                raise ValueError("requested artifact gate requires its profile")
            exceeded = any(
                actual.value > allowed.value
                for assessment in self.assessments
                for actual, allowed in zip(
                    assessment.actual,
                    assessment.allowed,
                    strict=True,
                )
            )
            if self.verdict is ArtifactGateVerdict.PASSED and (self.reasons or exceeded):
                raise ValueError("passed artifact gate contradicts its retained assessments")
            if self.verdict is ArtifactGateVerdict.FAILED and not (self.reasons or exceeded):
                raise ValueError("failed artifact gate requires an adverse assessment or reason")
