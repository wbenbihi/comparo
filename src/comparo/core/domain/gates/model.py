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
    "GateEnvironmentExecution",
    "GateEnvironmentPair",
    "GateFailureReason",
    "GateResult",
    "GateRuleLineage",
    "GateSeverity",
    "GateThreshold",
    "GateVerdict",
    "NormalizedFactSummary",
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
        _require_verdict_shape(self.verdict, self.profile, self.reasons)


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
