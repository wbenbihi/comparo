"""Pure reduction of value-free assertion and diff summaries into a gate verdict."""

from __future__ import annotations

from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.model import GateCoverageAssessment
from comparo.core.domain.gates.model import GateEvaluation
from comparo.core.domain.gates.model import GateResult
from comparo.core.domain.gates.model import GateRuleCounts
from comparo.core.domain.gates.model import GateSeverityAssessment
from comparo.core.domain.gates.model import GateThreshold
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.gates.model import derive_gate_failure_reasons
from comparo.core.domain.gates.summaries import AssertionGateRuleOutcome
from comparo.core.domain.gates.summaries import DiffGateRuleOutcome
from comparo.core.domain.gates.summaries import NormalizedFactSummary
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.primitives import Count

_SEVERITY_ORDER = (GateSeverity.BLOCKING, GateSeverity.WARNING, GateSeverity.INFO)


class _RuleTally:
    """Mutable reducer-local counters that can never escape the gate boundary."""

    __slots__ = (
        "drifted",
        "errors",
        "incomparable",
        "incomplete",
        "unavailable",
        "violated",
    )

    def __init__(self) -> None:
        self.violated = 0
        self.drifted = 0
        self.unavailable = 0
        self.incomparable = 0
        self.incomplete = 0
        self.errors = 0

    def freeze(self) -> GateRuleCounts:
        """Return the immutable value-free representation of these counters."""
        return GateRuleCounts(
            violated_rules=Count(value=self.violated),
            drifted_rules=Count(value=self.drifted),
            unavailable_rules=Count(value=self.unavailable),
            incomparable_rules=Count(value=self.incomparable),
            incomplete_rules=Count(value=self.incomplete),
            error_rules=Count(value=self.errors),
        )


def evaluate_gate(
    profile: CompiledGateProfile | None,
    summary: NormalizedFactSummary,
) -> GateEvaluation:
    """Evaluate one optional policy without retaining facts or performing external effects."""
    if profile is not None and type(profile) is not CompiledGateProfile:
        raise TypeError("gate evaluation profile must be a CompiledGateProfile or None")
    if type(summary) is not NormalizedFactSummary:
        raise TypeError("gate evaluation summary must be a NormalizedFactSummary")
    if profile is None:
        return GateEvaluation(
            result=GateResult(verdict=GateVerdict.NOT_REQUESTED, profile=None),
            coverage=None,
        )

    selected_severities = frozenset(profile.severities)
    selected_roles = frozenset(profile.environment_roles)
    tallies = {severity: _RuleTally() for severity in profile.severities}
    eligible_assertions = 0
    covered_assertions = 0
    eligible_diffs = 0
    covered_diffs = 0

    for assertion_summary in summary.assertions:
        for assertion_rule in assertion_summary.rules:
            if not _assertion_is_eligible(
                assertion_rule,
                selected_severities,
                selected_roles,
            ):
                continue
            eligible_assertions += 1
            covered_assertions += int(_assertion_has_coverage(assertion_rule))
            _record_assertion(tallies[assertion_rule.lineage.severity], assertion_rule)

    for diff_summary in summary.diffs:
        for diff_rule in diff_summary.rules:
            if not _diff_is_eligible(diff_rule, selected_severities, selected_roles):
                continue
            eligible_diffs += 1
            covered_diffs += int(_diff_has_coverage(diff_rule))
            _record_diff(tallies[diff_rule.lineage.severity], diff_rule)

    coverage = GateCoverageAssessment(
        run_complete=summary.is_complete,
        complete_run_required=profile.require_complete_run,
        assertion_coverage_required=profile.require_assertion_coverage,
        diff_coverage_required=profile.require_diff_coverage,
        eligible_assertion_rules=Count(value=eligible_assertions),
        covered_assertion_rules=Count(value=covered_assertions),
        eligible_diff_rules=Count(value=eligible_diffs),
        covered_diff_rules=Count(value=covered_diffs),
    )
    thresholds = {threshold.severity: threshold for threshold in profile.thresholds}
    assessments = tuple(
        _assessment(severity, tallies[severity], thresholds[severity])
        for severity in _SEVERITY_ORDER
        if severity in selected_severities
    )
    reasons = derive_gate_failure_reasons(coverage, assessments)
    result = GateResult(
        verdict=GateVerdict.FAILED if reasons else GateVerdict.PASSED,
        profile=profile.definition,
        reasons=reasons,
    )
    return GateEvaluation(result=result, coverage=coverage, severities=assessments)


def _assertion_is_eligible(
    rule: AssertionGateRuleOutcome,
    severities: frozenset[GateSeverity],
    roles: frozenset[EnvironmentRole],
) -> bool:
    return rule.lineage.severity in severities and not roles.isdisjoint(rule.execution.roles)


def _diff_is_eligible(
    rule: DiffGateRuleOutcome,
    severities: frozenset[GateSeverity],
    roles: frozenset[EnvironmentRole],
) -> bool:
    return rule.lineage.severity in severities and {
        rule.pair.baseline_role,
        rule.pair.candidate_role,
    }.issubset(roles)


def _assertion_has_coverage(rule: AssertionGateRuleOutcome) -> bool:
    if rule.outcome in {AssertionGateOutcome.SATISFIED, AssertionGateOutcome.VIOLATED}:
        return True
    return rule.outcome is AssertionGateOutcome.ERROR and (
        rule.counts.satisfied.value + rule.counts.violated.value > 0
    )


def _diff_has_coverage(rule: DiffGateRuleOutcome) -> bool:
    return (
        rule.counts.same.value
        + rule.counts.changed.value
        + rule.counts.added.value
        + rule.counts.removed.value
        > 0
    )


def _record_assertion(tally: _RuleTally, rule: AssertionGateRuleOutcome) -> None:
    if rule.outcome is AssertionGateOutcome.VIOLATED or rule.counts.violated.value:
        tally.violated += 1
    if rule.outcome is AssertionGateOutcome.UNAVAILABLE:
        tally.unavailable += 1
    if rule.outcome is AssertionGateOutcome.ERROR:
        tally.errors += 1


def _record_diff(tally: _RuleTally, rule: DiffGateRuleOutcome) -> None:
    if rule.outcome is DiffGateOutcome.DRIFTED or (
        rule.counts.changed.value + rule.counts.added.value + rule.counts.removed.value
    ):
        tally.drifted += 1
    if rule.counts.incomparable.value:
        tally.incomparable += 1
    if rule.outcome is DiffGateOutcome.INCOMPLETE:
        tally.incomplete += 1
    if rule.outcome is DiffGateOutcome.ERROR:
        tally.errors += 1


def _assessment(
    severity: GateSeverity,
    tally: _RuleTally,
    threshold: GateThreshold,
) -> GateSeverityAssessment:
    return GateSeverityAssessment(
        severity=severity,
        actual=tally.freeze(),
        allowed=GateRuleCounts(
            violated_rules=threshold.violated_rules,
            drifted_rules=threshold.drifted_rules,
            unavailable_rules=threshold.unavailable_rules,
            incomparable_rules=threshold.incomparable_rules,
            incomplete_rules=threshold.incomplete_rules,
            error_rules=threshold.error_rules,
        ),
    )
