"""Sanitized assertion, diff, execution, comparison, and summary contracts."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.artifacts.common import ArtifactComparisonOutcome
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactExecutionOutcome
from comparo.core.artifacts.common import ArtifactFactEngine
from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.common import require_count
from comparo.core.artifacts.common import require_metrics
from comparo.core.artifacts.common import require_stable_token
from comparo.core.artifacts.common import require_text
from comparo.core.artifacts.http import ArtifactAttempt
from comparo.core.artifacts.http import ArtifactTiming
from comparo.core.artifacts.values import ArtifactValue
from comparo.core.artifacts.values import is_artifact_value


@final
class ArtifactNamedValue(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One named exact JSON value that has crossed sanitization."""

    name: str
    value: ArtifactValue

    def __post_init__(self) -> None:
        """Exclude every live or dynamically typed payload from artifact evidence."""
        require_text(self.name, label="artifact value name", maximum=63)
        if not is_artifact_value(self.value):
            raise TypeError("artifact named value must contain an exact ArtifactValue")


@final
class ArtifactCoordinate(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One sanitized Matrix coordinate used to produce a planned cell."""

    axis: str
    value: ArtifactValue

    def __post_init__(self) -> None:
        """Require a bounded axis identity and exact safe value."""
        require_text(self.axis, label="artifact Matrix axis", maximum=127)
        if not is_artifact_value(self.value):
            raise TypeError("artifact Matrix coordinate must contain an exact ArtifactValue")


@final
class ArtifactEvidence(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Bounded sanitized evidence retained for one pure fact."""

    fact_id: str
    outcome: str
    location: str
    values: tuple[ArtifactNamedValue, ...] = ()

    def __post_init__(self) -> None:
        """Require safe coordinates and unique canonical value names."""
        require_stable_token(self.fact_id, label="artifact evidence fact ID", maximum=64)
        require_text(self.outcome, label="artifact evidence outcome", maximum=127)
        require_text(self.location, label="artifact evidence location", maximum=2048)
        if type(self.values) is not tuple or any(
            type(item) is not ArtifactNamedValue for item in self.values
        ):
            raise TypeError("artifact evidence values must be immutable and typed")
        names = tuple(item.name for item in self.values)
        if len(set(names)) != len(names) or names != tuple(sorted(names)):
            raise ValueError("artifact evidence value names must be unique and canonical")


@final
class ArtifactFact(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One sanitized assertion or diff aggregate with exact bounded evidence."""

    fact_id: str
    application_id: str
    engine: ArtifactFactEngine
    rule: ArtifactDefinition
    rule_id: str
    effective_rule_id: str
    severity: ArtifactSeverity
    outcome: ArtifactFactOutcome
    counts: tuple[ArtifactMetric, ...]
    coverage: tuple[ArtifactMetric, ...]
    evidence: tuple[ArtifactEvidence, ...]
    evidence_omitted: int
    issues: tuple[ArtifactFailure, ...] = ()

    def __post_init__(self) -> None:
        """Require independently renderable, canonical fact records."""
        for value, label in (
            (self.fact_id, "artifact fact ID"),
            (self.application_id, "artifact application ID"),
            (self.rule_id, "artifact rule ID"),
            (self.effective_rule_id, "artifact effective rule ID"),
        ):
            require_stable_token(value, label=label, maximum=64)
        if type(self.engine) is not ArtifactFactEngine:
            raise TypeError("artifact fact engine must be typed")
        if type(self.rule) is not ArtifactDefinition:
            raise TypeError("artifact fact rule must be ArtifactDefinition")
        if type(self.severity) is not ArtifactSeverity:
            raise TypeError("artifact fact severity must be typed")
        if type(self.outcome) is not ArtifactFactOutcome:
            raise TypeError("artifact fact outcome must be typed")
        require_metrics(self.counts, label="fact count")
        require_metrics(self.coverage, label="fact coverage")
        if type(self.evidence) is not tuple or any(
            type(item) is not ArtifactEvidence for item in self.evidence
        ):
            raise TypeError("artifact fact evidence must be immutable and typed")
        require_count(self.evidence_omitted, label="artifact omitted evidence")
        if type(self.issues) is not tuple or any(
            type(item) is not ArtifactFailure for item in self.issues
        ):
            raise TypeError("artifact fact issues must be immutable and typed")


@final
class ArtifactExecution(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One terminal Environment execution and its sanitized assertion facts."""

    ordinal: int
    execution_id: str
    subject: ArtifactDefinition
    environment: ArtifactDefinition
    cell_id: str
    trial_id: str
    coordinates: tuple[ArtifactCoordinate, ...]
    outcome: ArtifactExecutionOutcome
    failure: ArtifactFailure | None
    timings: tuple[ArtifactTiming, ...]
    attempts: tuple[ArtifactAttempt, ...]
    assertions: tuple[ArtifactFact, ...]

    def __post_init__(self) -> None:
        """Require coherent observed and non-observed execution shapes."""
        require_count(self.ordinal, label="artifact execution ordinal")
        for value, label in (
            (self.execution_id, "artifact execution ID"),
            (self.cell_id, "artifact cell ID"),
            (self.trial_id, "artifact trial ID"),
        ):
            require_stable_token(value, label=label, maximum=64)
        if (
            type(self.subject) is not ArtifactDefinition
            or type(self.environment) is not ArtifactDefinition
        ):
            raise TypeError("artifact execution definitions must be typed")
        if type(self.outcome) is not ArtifactExecutionOutcome:
            raise TypeError("artifact execution outcome must be typed")
        if self.failure is not None and type(self.failure) is not ArtifactFailure:
            raise TypeError("artifact execution failure must be ArtifactFailure")
        _require_exact_tuple(self.timings, ArtifactTiming, "execution timings")
        _require_exact_tuple(self.coordinates, ArtifactCoordinate, "execution coordinates")
        axes = tuple(item.axis for item in self.coordinates)
        if len(set(axes)) != len(axes):
            raise ValueError("artifact Matrix coordinate axes must be unique")
        _require_exact_tuple(self.attempts, ArtifactAttempt, "execution attempts")
        _require_exact_tuple(self.assertions, ArtifactFact, "execution assertions")
        observed = self.outcome is ArtifactExecutionOutcome.OBSERVED
        if observed != (self.failure is None and bool(self.attempts)):
            raise ValueError("artifact execution outcome contradicts its attempts and failure")
        if any(item.engine is not ArtifactFactEngine.ASSERTION for item in self.assertions):
            raise ValueError("artifact execution may contain only assertion facts")


@final
class ArtifactComparison(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One terminal directed comparison and its sanitized diff facts."""

    ordinal: int
    comparison_id: str
    subject: ArtifactDefinition
    cell_id: str
    trial_id: str
    baseline_execution_id: str
    candidate_execution_id: str
    outcome: ArtifactComparisonOutcome
    unavailable_execution_ids: tuple[str, ...]
    diffs: tuple[ArtifactFact, ...]

    def __post_init__(self) -> None:
        """Keep incomplete and evaluated comparison shapes disjoint."""
        require_count(self.ordinal, label="artifact comparison ordinal")
        for value, label in (
            (self.comparison_id, "artifact comparison ID"),
            (self.cell_id, "artifact comparison cell ID"),
            (self.trial_id, "artifact comparison trial ID"),
            (self.baseline_execution_id, "artifact baseline execution ID"),
            (self.candidate_execution_id, "artifact candidate execution ID"),
        ):
            require_stable_token(value, label=label, maximum=64)
        if type(self.subject) is not ArtifactDefinition:
            raise TypeError("artifact comparison subject must be ArtifactDefinition")
        if type(self.outcome) is not ArtifactComparisonOutcome:
            raise TypeError("artifact comparison outcome must be typed")
        if type(self.unavailable_execution_ids) is not tuple or any(
            type(item) is not str for item in self.unavailable_execution_ids
        ):
            raise TypeError("artifact unavailable execution IDs must be immutable text")
        for execution_id in self.unavailable_execution_ids:
            require_stable_token(
                execution_id,
                label="artifact unavailable execution ID",
                maximum=64,
            )
        _require_exact_tuple(self.diffs, ArtifactFact, "comparison diffs")
        incomplete = self.outcome is ArtifactComparisonOutcome.INCOMPLETE
        incomplete_shape = bool(self.unavailable_execution_ids) and not self.diffs
        evaluated_shape = not self.unavailable_execution_ids
        if (incomplete and not incomplete_shape) or (not incomplete and not evaluated_shape):
            raise ValueError("artifact comparison outcome contradicts its retained facts")
        if any(item.engine is not ArtifactFactEngine.DIFF for item in self.diffs):
            raise ValueError("artifact comparison may contain only diff facts")


@final
class ArtifactSummary(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Precomputed exact outcome tallies consumed without reporter reinterpretation."""

    executions: tuple[ArtifactMetric, ...]
    comparisons: tuple[ArtifactMetric, ...]
    assertions: tuple[ArtifactMetric, ...]
    diffs: tuple[ArtifactMetric, ...]

    def __post_init__(self) -> None:
        """Require canonical metrics for every independent result dimension."""
        require_metrics(self.executions, label="execution summary")
        require_metrics(self.comparisons, label="comparison summary")
        require_metrics(self.assertions, label="assertion summary")
        require_metrics(self.diffs, label="diff summary")


def _require_exact_tuple(values: object, expected: type[object], label: str) -> None:
    if type(values) is not tuple or any(type(item) is not expected for item in values):
        raise TypeError(f"artifact {label} must be immutable and typed")
