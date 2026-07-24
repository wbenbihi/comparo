"""Trusted projection from evaluator facts into serializable value-free gate inputs."""

from __future__ import annotations

from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.gates.summaries import AssertionGateCounts
from comparo.core.domain.gates.summaries import AssertionGateRuleOutcome
from comparo.core.domain.gates.summaries import AssertionGateSummary
from comparo.core.domain.gates.summaries import DiffGateCoverage
from comparo.core.domain.gates.summaries import DiffGateFactCounts
from comparo.core.domain.gates.summaries import DiffGateRuleOutcome
from comparo.core.domain.gates.summaries import DiffGateSummary
from comparo.core.domain.gates.summaries import GateEnvironmentExecution
from comparo.core.domain.gates.summaries import GateEnvironmentPair
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateRuleLineage
from comparo.core.domain.gates.types import GateSeverity


def normalize_assertion_fact(
    fact: AssertionFact,
    *,
    execution: GateEnvironmentExecution,
) -> AssertionGateSummary:
    """Project a fact under its exact invocation-owned execution role assignment."""
    if type(fact) is not AssertionFact:
        raise TypeError("assertion normalization requires an AssertionFact")
    if type(execution) is not GateEnvironmentExecution:
        raise TypeError("assertion normalization requires a GateEnvironmentExecution")
    if (
        execution.environment != fact.application.environment
        or execution.execution_id != fact.application.environment_execution_id
    ):
        raise ValueError("assertion execution-role assignment does not identify its application")
    rule = AssertionGateRuleOutcome(
        lineage=_assertion_lineage(fact),
        execution=execution,
        outcome=_assertion_outcome(fact),
        counts=AssertionGateCounts(
            selected=fact.counts.selected,
            evaluated=fact.counts.evaluated,
            satisfied=fact.counts.satisfied,
            violated=fact.counts.violated,
            errors=fact.counts.errors,
        ),
    )
    return AssertionGateSummary(rules=(rule,))


def normalize_diff_fact(
    fact: DiffRuleFact,
) -> DiffGateSummary:
    """Project one exact diff fact while discarding pipelines and bounded evidence."""
    if type(fact) is not DiffRuleFact:
        raise TypeError("diff normalization requires a DiffRuleFact")
    source_pair = fact.application.pair
    rule = DiffGateRuleOutcome(
        lineage=_diff_lineage(fact),
        pair=GateEnvironmentPair(
            comparison_id=source_pair.comparison_id,
            baseline_environment=source_pair.baseline_environment,
            baseline_execution_id=source_pair.baseline_execution_id,
            baseline_role=EnvironmentRole.BASELINE,
            candidate_environment=source_pair.candidate_environment,
            candidate_execution_id=source_pair.candidate_execution_id,
            candidate_role=EnvironmentRole.CANDIDATE,
        ),
        outcome=_diff_outcome(fact),
        counts=DiffGateFactCounts(
            same=fact.counts.same,
            changed=fact.counts.changed,
            added=fact.counts.added,
            removed=fact.counts.removed,
            ignored=fact.counts.ignored,
            incomparable=fact.counts.incomparable,
        ),
        coverage=DiffGateCoverage(
            baseline_selected_values=fact.coverage.baseline_selected,
            candidate_selected_values=fact.coverage.candidate_selected,
            baseline_derived_values=fact.coverage.baseline_derived,
            candidate_derived_values=fact.coverage.candidate_derived,
            baseline_consumed_values=fact.coverage.baseline_consumed,
            candidate_consumed_values=fact.coverage.candidate_consumed,
            baseline_resolved_values=fact.coverage.baseline_resolved,
            candidate_resolved_values=fact.coverage.candidate_resolved,
            paired_units=fact.coverage.paired,
            compared_facts=fact.coverage.compared,
            ignored_facts=fact.coverage.ignored,
            not_evaluated_units=fact.coverage.not_evaluated,
        ),
    )
    return DiffGateSummary(rules=(rule,))


def _assertion_lineage(fact: AssertionFact) -> GateRuleLineage:
    return GateRuleLineage(
        source_fact_id=fact.fact_id,
        application_id=fact.application.application_id,
        rule_id=fact.rule.definition.rule_id,
        effective_id=fact.rule.effective_id,
        rule_fingerprint=fact.rule.fingerprint,
        severity=GateSeverity(fact.rule.severity.value),
    )


def _diff_lineage(fact: DiffRuleFact) -> GateRuleLineage:
    return GateRuleLineage(
        source_fact_id=fact.fact_id,
        application_id=fact.application.application_id,
        rule_id=fact.rule.definition.rule_id,
        effective_id=fact.rule.effective_id,
        rule_fingerprint=fact.rule.fingerprint,
        severity=GateSeverity(fact.rule.severity.value),
    )


def _assertion_outcome(fact: AssertionFact) -> AssertionGateOutcome:
    mapping = {
        Satisfied: AssertionGateOutcome.SATISFIED,
        Violated: AssertionGateOutcome.VIOLATED,
        Skipped: AssertionGateOutcome.SKIPPED,
        AssertionUnavailable: AssertionGateOutcome.UNAVAILABLE,
        AssertionError: AssertionGateOutcome.ERROR,
    }
    return mapping[type(fact.outcome)]


def _diff_outcome(fact: DiffRuleFact) -> DiffGateOutcome:
    mapping = {
        Equivalent: DiffGateOutcome.EQUIVALENT,
        Drifted: DiffGateOutcome.DRIFTED,
        Incomplete: DiffGateOutcome.INCOMPLETE,
        DiffEvaluationError: DiffGateOutcome.ERROR,
        NotEvaluated: DiffGateOutcome.NOT_EVALUATED,
    }
    return mapping[type(fact.outcome)]
