"""Trusted normalization from exact evaluator facts into value-free gate inputs."""

from __future__ import annotations

from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.gates.summaries import AssertionGateRuleOutcome
from comparo.core.domain.gates.summaries import AssertionGateSummary
from comparo.core.domain.gates.summaries import DiffGateRuleOutcome
from comparo.core.domain.gates.summaries import DiffGateSummary
from comparo.core.domain.gates.types import EnvironmentRole


def normalize_assertion_fact(
    fact: AssertionFact,
    *,
    role: EnvironmentRole,
) -> AssertionGateSummary:
    """Normalize one exact assertion fact without accepting outcome or severity claims."""
    if type(fact) is not AssertionFact:
        raise TypeError("assertion normalization requires an AssertionFact")
    rule = AssertionGateRuleOutcome(fact=fact)
    return AssertionGateSummary(
        role=role,
        rules=(rule,),
    )


def normalize_diff_fact(
    fact: DiffRuleFact,
    *,
    baseline_role: EnvironmentRole,
    candidate_role: EnvironmentRole,
) -> DiffGateSummary:
    """Normalize one exact diff fact without accepting reclassifiable duplicate claims."""
    if type(fact) is not DiffRuleFact:
        raise TypeError("diff normalization requires a DiffRuleFact")
    rule = DiffGateRuleOutcome(fact=fact)
    return DiffGateSummary(
        baseline_role=baseline_role,
        candidate_role=candidate_role,
        rules=(rule,),
    )
