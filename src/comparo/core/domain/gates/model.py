"""Gate profiles and verdicts over source-bound normalized rule outcomes."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.gates.summaries import AssertionGateCounts
from comparo.core.domain.gates.summaries import AssertionGateRuleOutcome
from comparo.core.domain.gates.summaries import AssertionGateSummary
from comparo.core.domain.gates.summaries import DiffGateCoverage
from comparo.core.domain.gates.summaries import DiffGateFactCounts
from comparo.core.domain.gates.summaries import DiffGateRuleOutcome
from comparo.core.domain.gates.summaries import DiffGateSummary
from comparo.core.domain.gates.summaries import GateEnvironmentExecution
from comparo.core.domain.gates.summaries import GateEnvironmentPair
from comparo.core.domain.gates.summaries import NormalizedFactSummary
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateRuleLineage
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count

__all__ = (
    "AssertionGateCounts",
    "AssertionGateOutcome",
    "AssertionGateRuleOutcome",
    "AssertionGateSummary",
    "CompiledGateProfile",
    "DiffGateCoverage",
    "DiffGateFactCounts",
    "DiffGateOutcome",
    "DiffGateRuleOutcome",
    "DiffGateSummary",
    "EnvironmentRole",
    "GateCoverageAssessment",
    "GateEnvironmentExecution",
    "GateEnvironmentPair",
    "GateEvaluation",
    "GateFailureReason",
    "GateResult",
    "GateRuleCounts",
    "GateRuleLineage",
    "GateSeverity",
    "GateSeverityAssessment",
    "GateThreshold",
    "GateVerdict",
    "NormalizedFactSummary",
    "derive_gate_failure_reasons",
)


@final
class GateThreshold(msgspec.Struct, frozen=True, kw_only=True):
    """Allowed normalized failure rule counts for one selected severity."""

    severity: GateSeverity
    violated_rules: Count
    drifted_rules: Count
    unavailable_rules: Count
    incomparable_rules: Count
    incomplete_rules: Count
    error_rules: Count

    def __post_init__(self) -> None:
        """Require one severity and exact non-negative allowances."""
        if type(self.severity) is not GateSeverity:
            raise TypeError("gate threshold severity must be a GateSeverity")
        counts = (
            self.violated_rules,
            self.drifted_rules,
            self.unavailable_rules,
            self.incomparable_rules,
            self.incomplete_rules,
            self.error_rules,
        )
        if any(type(count) is not Count for count in counts):
            raise TypeError("gate threshold allowances must use Count values")


@final
class GateRuleCounts(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free counts of adverse rules in one selected severity."""

    violated_rules: Count
    drifted_rules: Count
    unavailable_rules: Count
    incomparable_rules: Count
    incomplete_rules: Count
    error_rules: Count

    def __post_init__(self) -> None:
        """Require exact non-negative Count values for every gate dimension."""
        if any(
            type(count) is not Count
            for count in (
                self.violated_rules,
                self.drifted_rules,
                self.unavailable_rules,
                self.incomparable_rules,
                self.incomplete_rules,
                self.error_rules,
            )
        ):
            raise TypeError("gate rule assessments must use Count values")


@final
class GateSeverityAssessment(msgspec.Struct, frozen=True, kw_only=True):
    """Actual and allowed adverse rule counts for one selected severity."""

    severity: GateSeverity
    actual: GateRuleCounts
    allowed: GateRuleCounts

    def __post_init__(self) -> None:
        """Require one typed severity and two exact value-free count sets."""
        if type(self.severity) is not GateSeverity:
            raise TypeError("gate severity assessment requires a GateSeverity")
        if type(self.actual) is not GateRuleCounts or type(self.allowed) is not GateRuleCounts:
            raise TypeError("gate severity assessment requires GateRuleCounts")


@final
class GateCoverageAssessment(msgspec.Struct, frozen=True, kw_only=True):
    """Auditable global coverage and run-completion reduction inputs."""

    run_complete: bool
    complete_run_required: bool
    assertion_coverage_required: bool
    diff_coverage_required: bool
    eligible_assertion_rules: Count
    covered_assertion_rules: Count
    eligible_diff_rules: Count
    covered_diff_rules: Count

    def __post_init__(self) -> None:
        """Require exact flags and covered-rule subset relationships."""
        if any(
            type(flag) is not bool
            for flag in (
                self.run_complete,
                self.complete_run_required,
                self.assertion_coverage_required,
                self.diff_coverage_required,
            )
        ):
            raise TypeError("gate coverage assessment flags must be bool values")
        counts = (
            self.eligible_assertion_rules,
            self.covered_assertion_rules,
            self.eligible_diff_rules,
            self.covered_diff_rules,
        )
        if any(type(count) is not Count for count in counts):
            raise TypeError("gate coverage assessment must use Count values")
        if self.covered_assertion_rules.value > self.eligible_assertion_rules.value:
            raise ValueError("covered assertion rules cannot exceed eligible assertion rules")
        if self.covered_diff_rules.value > self.eligible_diff_rules.value:
            raise ValueError("covered diff rules cannot exceed eligible diff rules")


@final
class CompiledGateProfile(msgspec.Struct, frozen=True, kw_only=True):
    """A pure reduction policy with no evaluation, fail-fast, or I/O controls."""

    definition: DefinitionRef
    severities: tuple[GateSeverity, ...]
    environment_roles: tuple[EnvironmentRole, ...]
    require_complete_run: bool
    require_assertion_coverage: bool
    require_diff_coverage: bool
    thresholds: tuple[GateThreshold, ...]

    def __post_init__(self) -> None:
        """Require a GateProfile identity and unambiguous selected dimensions."""
        _require_gate_definition(self.definition)
        _require_unique_non_empty(self.severities, GateSeverity, "compiled gate severities")
        _require_unique_non_empty(
            self.environment_roles,
            EnvironmentRole,
            "gate Environment roles",
        )
        if any(
            type(flag) is not bool
            for flag in (
                self.require_complete_run,
                self.require_assertion_coverage,
                self.require_diff_coverage,
            )
        ):
            raise TypeError("gate requirement flags must be bool values")
        if type(self.thresholds) is not tuple:
            raise TypeError("gate thresholds must be an immutable tuple")
        if any(type(threshold) is not GateThreshold for threshold in self.thresholds):
            raise TypeError("gate thresholds contain an unsupported value")
        threshold_severities = tuple(threshold.severity for threshold in self.thresholds)
        if len(set(threshold_severities)) != len(threshold_severities):
            raise ValueError("gate thresholds must define each severity at most once")
        if set(threshold_severities) != set(self.severities):
            raise ValueError("gate thresholds must cover exactly the selected severities")


class GateVerdict(DomainStrEnum):
    """The only possible gate results, independent of run completion."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_REQUESTED = "not-requested"


class GateFailureReason(DomainStrEnum):
    """Value-free reasons a requested gate failed."""

    RUN_INCOMPLETE = "run-incomplete"
    ASSERTION_COVERAGE_EMPTY = "assertion-coverage-empty"
    DIFF_COVERAGE_EMPTY = "diff-coverage-empty"
    ASSERTION_VIOLATION_LIMIT = "assertion-violation-limit"
    DIFF_DRIFT_LIMIT = "diff-drift-limit"
    UNAVAILABLE_LIMIT = "unavailable-limit"
    INCOMPARABLE_RULE_LIMIT = "incomparable-rule-limit"
    INCOMPLETE_LIMIT = "incomplete-limit"
    ERROR_LIMIT = "error-limit"


def derive_gate_failure_reasons(
    coverage: GateCoverageAssessment,
    assessments: tuple[GateSeverityAssessment, ...],
) -> tuple[GateFailureReason, ...]:
    """Derive the complete canonical failure set from value-free assessments."""
    if type(coverage) is not GateCoverageAssessment:
        raise TypeError("gate failure reduction requires a GateCoverageAssessment")
    if type(assessments) is not tuple or any(
        type(item) is not GateSeverityAssessment for item in assessments
    ):
        raise TypeError("gate failure reduction requires GateSeverityAssessment values")
    reasons: list[GateFailureReason] = []
    if coverage.complete_run_required and not coverage.run_complete:
        reasons.append(GateFailureReason.RUN_INCOMPLETE)
    if coverage.assertion_coverage_required and not coverage.covered_assertion_rules.value:
        reasons.append(GateFailureReason.ASSERTION_COVERAGE_EMPTY)
    if coverage.diff_coverage_required and not coverage.covered_diff_rules.value:
        reasons.append(GateFailureReason.DIFF_COVERAGE_EMPTY)
    dimensions = (
        ("violated_rules", GateFailureReason.ASSERTION_VIOLATION_LIMIT),
        ("drifted_rules", GateFailureReason.DIFF_DRIFT_LIMIT),
        ("unavailable_rules", GateFailureReason.UNAVAILABLE_LIMIT),
        ("incomparable_rules", GateFailureReason.INCOMPARABLE_RULE_LIMIT),
        ("incomplete_rules", GateFailureReason.INCOMPLETE_LIMIT),
        ("error_rules", GateFailureReason.ERROR_LIMIT),
    )
    for field, reason in dimensions:
        if any(
            getattr(assessment.actual, field).value > getattr(assessment.allowed, field).value
            for assessment in assessments
        ):
            reasons.append(reason)
    return tuple(reasons)


@final
class GateResult(msgspec.Struct, frozen=True, kw_only=True):
    """A verdict over normalized summaries, never an evaluation control signal."""

    verdict: GateVerdict
    profile: DefinitionRef | None
    reasons: tuple[GateFailureReason, ...] = ()

    def __post_init__(self) -> None:
        """Enforce requested versus not-requested and pass/fail invariants."""
        if type(self.verdict) is not GateVerdict:
            raise TypeError("gate result verdict must be a GateVerdict")
        if self.profile is not None:
            _require_gate_definition(self.profile)
        if type(self.reasons) is not tuple:
            raise TypeError("gate failure reasons must be an immutable tuple")
        if any(type(reason) is not GateFailureReason for reason in self.reasons):
            raise TypeError("gate result contains an unsupported failure reason")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("gate failure reasons must be unique")
        canonical = tuple(reason for reason in GateFailureReason if reason in self.reasons)
        if self.reasons != canonical:
            raise ValueError("gate failure reasons must use canonical order")
        _require_verdict_shape(self.verdict, self.profile, self.reasons)


@final
class GateEvaluation(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free auditable output of one pure gate reduction."""

    result: GateResult
    coverage: GateCoverageAssessment | None
    severities: tuple[GateSeverityAssessment, ...] = ()

    def __post_init__(self) -> None:
        """Keep not-requested and requested evaluation shapes disjoint."""
        if type(self.result) is not GateResult:
            raise TypeError("gate evaluation result must be a GateResult")
        if self.coverage is not None and type(self.coverage) is not GateCoverageAssessment:
            raise TypeError("gate evaluation coverage must be a GateCoverageAssessment")
        if type(self.severities) is not tuple:
            raise TypeError("gate severity assessments must be an immutable tuple")
        if any(type(item) is not GateSeverityAssessment for item in self.severities):
            raise TypeError("gate evaluation contains an unsupported severity assessment")
        dimensions = tuple(item.severity for item in self.severities)
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("gate severity assessments must be unique")
        canonical = tuple(
            severity
            for severity in (GateSeverity.BLOCKING, GateSeverity.WARNING, GateSeverity.INFO)
            if severity in dimensions
        )
        if dimensions != canonical:
            raise ValueError("gate severity assessments must use canonical order")
        _require_gate_evaluation_consistency(self)


def _require_gate_evaluation_consistency(evaluation: GateEvaluation) -> None:
    if evaluation.result.verdict is GateVerdict.NOT_REQUESTED:
        if evaluation.coverage is not None or evaluation.severities:
            raise ValueError("not-requested gate evaluation cannot retain assessments")
        return
    if evaluation.coverage is None or not evaluation.severities:
        raise ValueError("requested gate evaluation requires coverage and severity assessments")
    expected_reasons = derive_gate_failure_reasons(evaluation.coverage, evaluation.severities)
    expected_verdict = GateVerdict.FAILED if expected_reasons else GateVerdict.PASSED
    if (
        evaluation.result.reasons != expected_reasons
        or evaluation.result.verdict is not expected_verdict
    ):
        raise ValueError("gate verdict must match its value-free assessments")


def _require_gate_definition(definition: DefinitionRef) -> None:
    if type(definition) is not DefinitionRef:
        raise TypeError("gate definition must be a DefinitionRef")
    if definition.identity.kind is not ResourceKind.GATE_PROFILE:
        raise ValueError("gate definition must reference a GateProfile")


def _require_unique_non_empty(
    values: tuple[object, ...],
    value_type: type[object],
    label: str,
) -> None:
    if type(values) is not tuple or not values:
        raise ValueError(f"{label} must be a non-empty immutable tuple")
    if any(type(value) is not value_type for value in values):
        raise TypeError(f"{label} contain an unsupported value")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")


def _require_verdict_shape(
    verdict: GateVerdict,
    profile: DefinitionRef | None,
    reasons: tuple[GateFailureReason, ...],
) -> None:
    if verdict is GateVerdict.NOT_REQUESTED and (profile is not None or reasons):
        raise ValueError("not-requested gate result cannot name a profile or failure reasons")
    if verdict is not GateVerdict.NOT_REQUESTED and profile is None:
        raise ValueError("requested gate results require a GateProfile identity")
    if verdict is GateVerdict.FAILED and not reasons:
        raise ValueError("failed gate result requires at least one failure reason")
    if verdict is GateVerdict.PASSED and reasons:
        raise ValueError("passed gate result cannot contain failure reasons")
