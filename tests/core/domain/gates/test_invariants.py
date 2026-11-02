"""Cover source-bound gate summaries and direct-construction invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.gates.model import AssertionGateCounts
from comparo.core.domain.gates.model import AssertionGateOutcome
from comparo.core.domain.gates.model import AssertionGateRuleOutcome
from comparo.core.domain.gates.model import AssertionGateSummary
from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.model import DiffGateCoverage
from comparo.core.domain.gates.model import DiffGateFactCounts
from comparo.core.domain.gates.model import DiffGateOutcome
from comparo.core.domain.gates.model import DiffGateRuleOutcome
from comparo.core.domain.gates.model import DiffGateSummary
from comparo.core.domain.gates.model import EnvironmentRole
from comparo.core.domain.gates.model import GateEnvironmentExecution
from comparo.core.domain.gates.model import GateEnvironmentPair
from comparo.core.domain.gates.model import GateFailureReason
from comparo.core.domain.gates.model import GateResult
from comparo.core.domain.gates.model import GateRuleLineage
from comparo.core.domain.gates.model import GateSeverity
from comparo.core.domain.gates.model import GateThreshold
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.gates.model import NormalizedFactSummary
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.lifecycle import PartialRunReason
from comparo.core.domain.lifecycle import RunComplete
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.primitives import UtcInstant


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="7" * 64))


def _complete() -> RunComplete:
    return RunComplete(completed_at=UtcInstant(unix_nanoseconds=1))


def _resource(kind: ResourceKind, name: str) -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="demo"),
        kind=kind,
        resource_id=ResourceId(value=name),
    )


def _definition() -> DefinitionRef:
    return DefinitionRef(
        identity=_resource(ResourceKind.GATE_PROFILE, "release"),
        fingerprint=_fingerprint(),
    )


def _environment(name: str) -> DefinitionRef:
    return DefinitionRef(
        identity=_resource(ResourceKind.ENVIRONMENT, name),
        fingerprint=_fingerprint(),
    )


def _lineage(index: int = 80, severity: GateSeverity = GateSeverity.BLOCKING) -> GateRuleLineage:
    return GateRuleLineage(
        source_fact_id=FactId(value=UUID(int=index)),
        application_id=RuleApplicationId(value=UUID(int=index + 1)),
        rule_id=RuleId(value=f"rule-{index}"),
        effective_id=EffectiveRuleId(value=f"effective-{index}"),
        rule_fingerprint=_fingerprint(),
        severity=severity,
    )


def _assertion_rule(
    index: int = 80,
    *,
    violated: bool = False,
) -> AssertionGateRuleOutcome:
    zero = Count(value=0)
    return AssertionGateRuleOutcome(
        lineage=_lineage(index),
        execution=GateEnvironmentExecution(
            environment=DefinitionRef(
                identity=_resource(ResourceKind.ENVIRONMENT, "prod"),
                fingerprint=_fingerprint(),
            ),
            execution_id=EnvironmentExecutionId(value=UUID(int=90)),
            roles=(EnvironmentRole.BASELINE,),
        ),
        outcome=(AssertionGateOutcome.VIOLATED if violated else AssertionGateOutcome.SATISFIED),
        counts=AssertionGateCounts(
            selected=zero,
            evaluated=zero,
            satisfied=zero,
            violated=zero,
            errors=zero,
        ),
    )


def _assertion(index: int = 80) -> AssertionGateSummary:
    return AssertionGateSummary(rules=(_assertion_rule(index),))


def _pair() -> GateEnvironmentPair:
    return _diff().pair


def _coverage() -> DiffGateCoverage:
    return DiffGateCoverage(
        baseline_selected_values=Count(value=1),
        candidate_selected_values=Count(value=1),
        baseline_derived_values=Count(value=1),
        candidate_derived_values=Count(value=1),
        baseline_consumed_values=Count(value=1),
        candidate_consumed_values=Count(value=1),
        baseline_resolved_values=Count(value=1),
        candidate_resolved_values=Count(value=1),
        paired_units=Count(value=1),
        compared_facts=Count(value=1),
        ignored_facts=Count(value=0),
        not_evaluated_units=Count(value=0),
    )


def _fact_counts() -> DiffGateFactCounts:
    return DiffGateFactCounts(
        same=Count(value=1),
        changed=Count(value=0),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=0),
        incomparable=Count(value=0),
    )


def _empty_fact_counts() -> DiffGateFactCounts:
    zero = Count(value=0)
    return DiffGateFactCounts(
        same=zero,
        changed=zero,
        added=zero,
        removed=zero,
        ignored=zero,
        incomparable=zero,
    )


def _empty_coverage() -> DiffGateCoverage:
    zero = Count(value=0)
    return DiffGateCoverage(
        baseline_selected_values=zero,
        candidate_selected_values=zero,
        baseline_derived_values=zero,
        candidate_derived_values=zero,
        baseline_consumed_values=zero,
        candidate_consumed_values=zero,
        baseline_resolved_values=zero,
        candidate_resolved_values=zero,
        paired_units=zero,
        compared_facts=zero,
        ignored_facts=zero,
        not_evaluated_units=zero,
    )


def _diff_rule(index: int = 100) -> DiffGateRuleOutcome:
    return DiffGateRuleOutcome(
        lineage=_lineage(index, GateSeverity.INFO),
        pair=GateEnvironmentPair(
            comparison_id=ComparisonId(value=UUID(int=92)),
            baseline_environment=_environment("prod"),
            baseline_execution_id=EnvironmentExecutionId(value=UUID(int=93)),
            baseline_role=EnvironmentRole.BASELINE,
            candidate_environment=_environment("staging"),
            candidate_execution_id=EnvironmentExecutionId(value=UUID(int=94)),
            candidate_role=EnvironmentRole.CANDIDATE,
        ),
        outcome=DiffGateOutcome.EQUIVALENT,
        counts=_fact_counts(),
        coverage=_coverage(),
    )


def _diff(index: int = 100) -> DiffGateSummary:
    return DiffGateSummary(rules=(_diff_rule(index),))


def _threshold(severity: GateSeverity = GateSeverity.BLOCKING) -> GateThreshold:
    zero = Count(value=0)
    return GateThreshold(
        severity=severity,
        violated_rules=zero,
        drifted_rules=zero,
        unavailable_rules=zero,
        incomparable_rules=zero,
        incomplete_rules=zero,
        error_rules=zero,
    )


def _profile() -> CompiledGateProfile:
    return CompiledGateProfile(
        definition=_definition(),
        severities=(GateSeverity.BLOCKING,),
        environment_roles=(EnvironmentRole.BASELINE, EnvironmentRole.CANDIDATE),
        require_complete_run=True,
        require_assertion_coverage=True,
        require_diff_coverage=True,
        thresholds=(_threshold(),),
    )


def test_assertion_gate_projection_is_value_free_and_exactly_coordinated() -> None:
    summary = AssertionGateSummary(rules=(_assertion_rule(80, violated=True),))
    normalized = summary.rules[0]
    assert normalized.outcome is AssertionGateOutcome.VIOLATED
    assert normalized.lineage.severity is GateSeverity.BLOCKING
    assert normalized.selected_matches == normalized.counts.selected
    assert normalized.evaluated_matches == normalized.counts.evaluated
    assert summary.coordinate is normalized.execution
    assert not hasattr(normalized, "fact")
    assert not hasattr(normalized, "evidence")
    with pytest.raises(TypeError, match=r"[Uu]nexpected keyword argument"):
        AssertionGateRuleOutcome(
            fact="not retained",  # type: ignore[call-arg]
        )
    with pytest.raises(ValueError, match="non-empty"):
        msgspec.structs.replace(summary, rules=())
    with pytest.raises(ValueError, match="lineages"):
        msgspec.structs.replace(summary, rules=(_assertion_rule(), _assertion_rule()))


def test_assertion_gate_outcome_requires_its_nonempty_terminal_bucket() -> None:
    evaluated = Count(value=1)
    zero = Count(value=0)
    violated_counts = AssertionGateCounts(
        selected=Count(value=2),
        evaluated=evaluated,
        satisfied=zero,
        violated=evaluated,
        errors=zero,
    )
    with pytest.raises(ValueError, match="satisfied match or complete"):
        msgspec.structs.replace(
            _assertion_rule(),
            outcome=AssertionGateOutcome.SATISFIED,
            counts=violated_counts,
        )

    none_quantifier_counts = msgspec.structs.replace(violated_counts, selected=evaluated)
    assert (
        msgspec.structs.replace(
            _assertion_rule(),
            outcome=AssertionGateOutcome.SATISFIED,
            counts=none_quantifier_counts,
        ).outcome
        is AssertionGateOutcome.SATISFIED
    )

    satisfied_counts = msgspec.structs.replace(
        violated_counts,
        satisfied=evaluated,
        violated=zero,
    )
    with pytest.raises(ValueError, match="violated match"):
        msgspec.structs.replace(
            _assertion_rule(),
            outcome=AssertionGateOutcome.VIOLATED,
            counts=satisfied_counts,
        )

    assert _assertion_rule().counts.evaluated == zero


def test_diff_gate_projection_retains_only_value_free_reduction_facts() -> None:
    rule = msgspec.structs.replace(
        _diff_rule(),
        outcome=DiffGateOutcome.DRIFTED,
        counts=msgspec.structs.replace(
            _fact_counts(),
            same=Count(value=0),
            changed=Count(value=1),
        ),
    )
    assert rule.outcome is DiffGateOutcome.DRIFTED
    assert rule.counts.changed.value == 1
    assert rule.lineage.severity is GateSeverity.INFO
    assert not _diff_rule().incomparable
    assert not hasattr(rule, "fact")
    assert not hasattr(rule, "evidence")
    with pytest.raises(TypeError, match=r"[Uu]nexpected keyword argument"):
        DiffGateRuleOutcome(
            fact="not retained",  # type: ignore[call-arg]
        )


def test_diff_fact_and_coverage_conservation_are_exact() -> None:
    coverage = _coverage()
    with pytest.raises(ValueError, match="every paired"):
        msgspec.structs.replace(coverage, paired_units=Count(value=2))
    with pytest.raises(TypeError, match="Count"):
        msgspec.structs.replace(coverage, compared_facts=cast("Count", 1))
    rule = _diff_rule()
    assert rule.coverage.compared_facts.value == rule.counts.compared
    assert rule.coverage.ignored_facts == rule.counts.ignored


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("baseline_consumed_values", "baseline gate consumed"),
        ("candidate_consumed_values", "candidate gate consumed"),
        ("baseline_resolved_values", "baseline gate resolved"),
        ("candidate_resolved_values", "candidate gate resolved"),
    ],
)
def test_gate_coverage_rejects_each_per_side_conservation_violation(
    field: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        msgspec.structs.replace(_coverage(), **{field: Count(value=2)})


def test_assertion_projection_direct_construction_is_fail_closed() -> None:
    rule = _assertion_rule()
    with pytest.raises(TypeError, match="DefinitionRef"):
        msgspec.structs.replace(
            rule.execution,
            environment=cast("DefinitionRef", "bad"),
        )
    with pytest.raises(ValueError, match="Environment"):
        msgspec.structs.replace(
            rule.execution,
            environment=DefinitionRef(
                identity=_resource(ResourceKind.REQUEST, "request"),
                fingerprint=_fingerprint(),
            ),
        )
    with pytest.raises(TypeError, match="execution ID"):
        msgspec.structs.replace(
            rule.execution,
            execution_id=cast("EnvironmentExecutionId", "bad"),
        )
    with pytest.raises(TypeError, match="Count"):
        msgspec.structs.replace(rule.counts, selected=cast("Count", 1))
    with pytest.raises(ValueError, match="terminal match buckets"):
        msgspec.structs.replace(rule.counts, evaluated=Count(value=1))
    with pytest.raises(ValueError, match="cannot exceed"):
        msgspec.structs.replace(
            rule.counts,
            evaluated=Count(value=1),
            satisfied=Count(value=1),
        )
    with pytest.raises(TypeError, match="GateEnvironmentExecution"):
        msgspec.structs.replace(
            rule,
            execution=cast("GateEnvironmentExecution", "bad"),
        )
    with pytest.raises(TypeError, match="AssertionGateOutcome"):
        msgspec.structs.replace(rule, outcome=cast("AssertionGateOutcome", "bad"))
    with pytest.raises(TypeError, match="AssertionGateCounts"):
        msgspec.structs.replace(rule, counts=cast("AssertionGateCounts", "bad"))


@pytest.mark.parametrize(
    "outcome",
    [AssertionGateOutcome.SKIPPED, AssertionGateOutcome.UNAVAILABLE],
)
def test_absent_assertion_outcomes_require_zero_counts(outcome: AssertionGateOutcome) -> None:
    rule = _assertion_rule()
    assert msgspec.structs.replace(rule, outcome=outcome).counts.evaluated.value == 0
    nonzero = AssertionGateCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=1),
        violated=Count(value=0),
        errors=Count(value=0),
    )
    with pytest.raises(ValueError, match="zero match counts"):
        msgspec.structs.replace(rule, outcome=outcome, counts=nonzero)


def test_assertion_error_projection_requires_and_owns_the_error_bucket() -> None:
    rule = _assertion_rule()
    with pytest.raises(ValueError, match="at least one match error"):
        msgspec.structs.replace(rule, outcome=AssertionGateOutcome.ERROR)
    error_counts = AssertionGateCounts(
        selected=Count(value=1),
        evaluated=Count(value=1),
        satisfied=Count(value=0),
        violated=Count(value=0),
        errors=Count(value=1),
    )
    error = msgspec.structs.replace(
        rule,
        outcome=AssertionGateOutcome.ERROR,
        counts=error_counts,
    )
    assert error.counts.errors.value == 1
    with pytest.raises(ValueError, match="non-error"):
        msgspec.structs.replace(error, outcome=AssertionGateOutcome.VIOLATED)


def test_diff_projection_direct_construction_reconciles_counts_and_coverage() -> None:
    rule = _diff_rule()
    with pytest.raises(TypeError, match="GateRuleLineage"):
        msgspec.structs.replace(rule, lineage=cast("GateRuleLineage", "bad"))
    with pytest.raises(TypeError, match="DiffGateOutcome"):
        msgspec.structs.replace(rule, outcome=cast("DiffGateOutcome", "bad"))
    with pytest.raises(TypeError, match="DiffGateFactCounts"):
        msgspec.structs.replace(rule, counts=cast("DiffGateFactCounts", "bad"))
    with pytest.raises(TypeError, match="DiffGateCoverage"):
        msgspec.structs.replace(rule, coverage=cast("DiffGateCoverage", "bad"))
    with pytest.raises(ValueError, match="non-ignored"):
        msgspec.structs.replace(rule, counts=_empty_fact_counts())
    ignored_counts = msgspec.structs.replace(
        _empty_fact_counts(),
        ignored=Count(value=1),
    )
    with pytest.raises(ValueError, match="ignored gate coverage"):
        msgspec.structs.replace(
            rule,
            outcome=DiffGateOutcome.NOT_EVALUATED,
            counts=ignored_counts,
            coverage=DiffGateCoverage(
                baseline_selected_values=Count(value=1),
                candidate_selected_values=Count(value=1),
                baseline_derived_values=Count(value=1),
                candidate_derived_values=Count(value=1),
                baseline_consumed_values=Count(value=1),
                candidate_consumed_values=Count(value=1),
                baseline_resolved_values=Count(value=0),
                candidate_resolved_values=Count(value=0),
                paired_units=Count(value=1),
                compared_facts=Count(value=0),
                ignored_facts=Count(value=0),
                not_evaluated_units=Count(value=1),
            ),
        )
    zero = Count(value=0)
    with pytest.raises(ValueError, match="paired gate units contradict"):
        DiffGateCoverage(
            baseline_selected_values=zero,
            candidate_selected_values=zero,
            baseline_derived_values=zero,
            candidate_derived_values=zero,
            baseline_consumed_values=zero,
            candidate_consumed_values=zero,
            baseline_resolved_values=zero,
            candidate_resolved_values=zero,
            paired_units=Count(value=1),
            compared_facts=Count(value=1),
            ignored_facts=zero,
            not_evaluated_units=zero,
        )
    one_sided = DiffGateCoverage(
        baseline_selected_values=Count(value=1),
        candidate_selected_values=zero,
        baseline_derived_values=Count(value=1),
        candidate_derived_values=zero,
        baseline_consumed_values=Count(value=1),
        candidate_consumed_values=zero,
        baseline_resolved_values=Count(value=1),
        candidate_resolved_values=zero,
        paired_units=Count(value=1),
        compared_facts=Count(value=1),
        ignored_facts=zero,
        not_evaluated_units=zero,
    )
    with pytest.raises(ValueError, match="one-sided and bilateral"):
        msgspec.structs.replace(rule, coverage=one_sided)
    with pytest.raises(ValueError, match="one-sided and bilateral"):
        msgspec.structs.replace(
            rule,
            coverage=msgspec.structs.replace(
                one_sided,
                baseline_selected_values=zero,
                baseline_derived_values=zero,
                baseline_consumed_values=zero,
                baseline_resolved_values=zero,
                candidate_selected_values=Count(value=1),
                candidate_derived_values=Count(value=1),
                candidate_consumed_values=Count(value=1),
                candidate_resolved_values=Count(value=1),
            ),
        )


def test_diff_projection_preserves_empty_and_not_evaluated_semantics() -> None:
    rule = _diff_rule()
    empty = _empty_coverage()
    counts = _empty_fact_counts()
    not_evaluated = msgspec.structs.replace(
        rule,
        outcome=DiffGateOutcome.NOT_EVALUATED,
        counts=counts,
        coverage=empty,
    )
    assert not_evaluated.outcome is DiffGateOutcome.NOT_EVALUATED
    with pytest.raises(ValueError, match="non-empty comparable"):
        msgspec.structs.replace(not_evaluated, outcome=DiffGateOutcome.EQUIVALENT)
    with pytest.raises(ValueError, match="compared coverage"):
        msgspec.structs.replace(rule, outcome=DiffGateOutcome.NOT_EVALUATED)

    ignored = msgspec.structs.replace(counts, ignored=Count(value=1))
    ignored_coverage = DiffGateCoverage(
        baseline_selected_values=Count(value=1),
        candidate_selected_values=Count(value=1),
        baseline_derived_values=Count(value=1),
        candidate_derived_values=Count(value=1),
        baseline_consumed_values=Count(value=1),
        candidate_consumed_values=Count(value=1),
        baseline_resolved_values=Count(value=1),
        candidate_resolved_values=Count(value=1),
        paired_units=Count(value=1),
        compared_facts=Count(value=0),
        ignored_facts=Count(value=1),
        not_evaluated_units=Count(value=0),
    )
    all_ignored = msgspec.structs.replace(
        rule,
        outcome=DiffGateOutcome.NOT_EVALUATED,
        counts=ignored,
        coverage=ignored_coverage,
    )
    assert all_ignored.counts.ignored.value == 1


def test_complete_gate_outcome_cannot_drop_derived_values() -> None:
    rule = _diff_rule()
    partial = msgspec.structs.replace(
        rule.coverage,
        baseline_selected_values=Count(value=2),
        candidate_selected_values=Count(value=2),
        baseline_derived_values=Count(value=2),
        candidate_derived_values=Count(value=2),
    )
    with pytest.raises(ValueError, match="consume and resolve every derived value"):
        msgspec.structs.replace(rule, coverage=partial)


def test_equivalent_and_drifted_projections_reject_contradictory_buckets() -> None:
    rule = _diff_rule()
    drift_counts = msgspec.structs.replace(
        rule.counts,
        same=Count(value=0),
        changed=Count(value=1),
    )
    with pytest.raises(ValueError, match="non-empty comparable"):
        msgspec.structs.replace(rule, counts=drift_counts)
    with pytest.raises(ValueError, match="resolved concrete drift"):
        msgspec.structs.replace(rule, outcome=DiffGateOutcome.DRIFTED)

    unresolved_counts = msgspec.structs.replace(
        _empty_fact_counts(),
        incomparable=Count(value=1),
    )
    with pytest.raises(ValueError, match="resolved concrete drift"):
        msgspec.structs.replace(
            rule,
            outcome=DiffGateOutcome.DRIFTED,
            counts=msgspec.structs.replace(unresolved_counts, changed=Count(value=1)),
            coverage=msgspec.structs.replace(
                rule.coverage,
                baseline_selected_values=Count(value=2),
                candidate_selected_values=Count(value=2),
                baseline_derived_values=Count(value=2),
                candidate_derived_values=Count(value=2),
                baseline_consumed_values=Count(value=2),
                candidate_consumed_values=Count(value=2),
                baseline_resolved_values=Count(value=2),
                candidate_resolved_values=Count(value=2),
                paired_units=Count(value=2),
                compared_facts=Count(value=2),
            ),
        )
    with pytest.raises(ValueError, match="non-empty comparable"):
        msgspec.structs.replace(
            rule,
            counts=unresolved_counts,
        )


def test_pair_requires_distinct_resources_and_execution_occurrences() -> None:
    pair = _pair()
    with pytest.raises(TypeError, match="DefinitionRef"):
        msgspec.structs.replace(
            pair,
            baseline_environment=cast("DefinitionRef", "bad"),
        )
    with pytest.raises(ValueError, match="distinct Environment resources"):
        msgspec.structs.replace(pair, candidate_environment=pair.baseline_environment)
    with pytest.raises(ValueError, match="distinct Environment resources"):
        msgspec.structs.replace(
            pair,
            candidate_environment=msgspec.structs.replace(
                pair.baseline_environment,
                fingerprint=Fingerprint(
                    algorithm="sha256",
                    digest=Sha256Digest(hex_value="8" * 64),
                ),
            ),
        )
    with pytest.raises(ValueError, match="execution IDs"):
        msgspec.structs.replace(pair, candidate_execution_id=pair.baseline_execution_id)
    with pytest.raises(TypeError, match="roles"):
        msgspec.structs.replace(pair, candidate_role=cast("EnvironmentRole", "candidate"))
    with pytest.raises(ValueError, match="Environment"):
        msgspec.structs.replace(
            pair,
            baseline_environment=DefinitionRef(
                identity=_resource(ResourceKind.REQUEST, "request"),
                fingerprint=_fingerprint(),
            ),
        )
    with pytest.raises(ValueError, match="preserve baseline and candidate"):
        msgspec.structs.replace(pair, baseline_role=EnvironmentRole.CANDIDATE)


def test_summaries_reject_cross_environment_and_cross_pair_relabeling() -> None:
    first = _assertion_rule(80)
    other_environment = DefinitionRef(
        identity=_resource(ResourceKind.ENVIRONMENT, "staging"),
        fingerprint=_fingerprint(),
    )
    cross_environment = AssertionGateRuleOutcome(
        lineage=_lineage(82),
        execution=msgspec.structs.replace(first.execution, environment=other_environment),
        outcome=AssertionGateOutcome.SATISFIED,
        counts=first.counts,
    )
    with pytest.raises(ValueError, match="one exact Environment execution"):
        AssertionGateSummary(rules=(first, cross_environment))

    source = _diff_rule(100)
    cross_pair = DiffGateRuleOutcome(
        lineage=_lineage(102, GateSeverity.INFO),
        pair=msgspec.structs.replace(
            source.pair,
            comparison_id=ComparisonId(value=UUID(int=999)),
        ),
        outcome=DiffGateOutcome.EQUIVALENT,
        counts=source.counts,
        coverage=source.coverage,
    )
    with pytest.raises(ValueError, match="one exact directed execution pair"):
        DiffGateSummary(rules=(source, cross_pair))


def test_normalized_summary_rejects_duplicate_source_lineage_globally() -> None:
    normalized = NormalizedFactSummary(
        completion=_complete(),
        assertions=(_assertion(),),
        diffs=(_diff(),),
    )
    assert normalized.diffs[0].pair == _pair()
    with pytest.raises(ValueError, match="assertion summary coordinates"):
        msgspec.structs.replace(normalized, assertions=(_assertion(), _assertion(82)))
    relabeled_rule = msgspec.structs.replace(
        _assertion_rule(82),
        execution=msgspec.structs.replace(
            _assertion_rule(82).execution,
            roles=(EnvironmentRole.OTHER,),
        ),
    )
    with pytest.raises(ValueError, match="assertion summary coordinates"):
        msgspec.structs.replace(
            normalized,
            assertions=(
                _assertion(),
                AssertionGateSummary(rules=(relabeled_rule,)),
            ),
        )
    with pytest.raises(ValueError, match="diff summary coordinates"):
        msgspec.structs.replace(normalized, diffs=(_diff(), _diff(102)))
    source_diff = _diff_rule()
    duplicate_fact = DiffGateRuleOutcome(
        lineage=msgspec.structs.replace(
            source_diff.lineage,
            source_fact_id=_assertion_rule().lineage.source_fact_id,
        ),
        pair=source_diff.pair,
        outcome=source_diff.outcome,
        counts=source_diff.counts,
        coverage=source_diff.coverage,
    )
    with pytest.raises(ValueError, match="source fact IDs"):
        NormalizedFactSummary(
            completion=_complete(),
            assertions=(_assertion(),),
            diffs=(DiffGateSummary(rules=(duplicate_fact,)),),
        )
    duplicate_application = DiffGateRuleOutcome(
        lineage=msgspec.structs.replace(
            source_diff.lineage,
            application_id=_assertion_rule().lineage.application_id,
        ),
        pair=source_diff.pair,
        outcome=source_diff.outcome,
        counts=source_diff.counts,
        coverage=source_diff.coverage,
    )
    with pytest.raises(ValueError, match="applications"):
        NormalizedFactSummary(
            completion=_complete(),
            assertions=(_assertion(),),
            diffs=(DiffGateSummary(rules=(duplicate_application,)),),
        )


def test_normalized_summary_reconciles_assertion_roles_from_all_directed_pairs() -> None:
    assertion_rule = _assertion_rule()
    coordinate = assertion_rule.execution
    baseline_pair = msgspec.structs.replace(
        _pair(),
        baseline_environment=coordinate.environment,
        baseline_execution_id=coordinate.execution_id,
    )
    baseline_diff = DiffGateSummary(
        rules=(msgspec.structs.replace(_diff_rule(), pair=baseline_pair),),
    )
    relabeled = AssertionGateSummary(
        rules=(
            msgspec.structs.replace(
                assertion_rule,
                execution=msgspec.structs.replace(
                    coordinate,
                    roles=(EnvironmentRole.OTHER,),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="roles contradict directed pairs"):
        NormalizedFactSummary(
            completion=_complete(),
            assertions=(relabeled,),
            diffs=(baseline_diff,),
        )

    candidate_pair = msgspec.structs.replace(
        _pair(),
        comparison_id=ComparisonId(value=UUID(int=995)),
        baseline_environment=_environment("dev"),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=996)),
        candidate_environment=coordinate.environment,
        candidate_execution_id=coordinate.execution_id,
    )
    candidate_diff = DiffGateSummary(
        rules=(msgspec.structs.replace(_diff_rule(102), pair=candidate_pair),),
    )
    both_roles = AssertionGateSummary(
        rules=(
            msgspec.structs.replace(
                assertion_rule,
                execution=msgspec.structs.replace(
                    coordinate,
                    roles=(EnvironmentRole.BASELINE, EnvironmentRole.CANDIDATE),
                ),
            ),
        ),
    )
    normalized = NormalizedFactSummary(
        completion=_complete(),
        assertions=(both_roles,),
        diffs=(baseline_diff, candidate_diff),
    )
    assert normalized.assertions[0].roles == (
        EnvironmentRole.BASELINE,
        EnvironmentRole.CANDIDATE,
    )


def test_normalized_summary_rejects_global_coordinate_split_brain() -> None:
    first = _diff()
    conflicting_pair = msgspec.structs.replace(
        first.pair,
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=997)),
    )
    conflicting = DiffGateSummary(
        rules=(msgspec.structs.replace(_diff_rule(102), pair=conflicting_pair),),
    )
    with pytest.raises(ValueError, match="ComparisonId cannot identify multiple"):
        NormalizedFactSummary(
            completion=_complete(),
            diffs=(first, conflicting),
        )

    assertion = _assertion()
    reused_execution = msgspec.structs.replace(
        first.pair,
        baseline_environment=_environment("dev"),
        baseline_execution_id=assertion.execution_id,
    )
    reused = DiffGateSummary(
        rules=(msgspec.structs.replace(_diff_rule(), pair=reused_execution),),
    )
    with pytest.raises(ValueError, match="EnvironmentExecutionId cannot identify multiple"):
        NormalizedFactSummary(
            completion=_complete(),
            assertions=(assertion,),
            diffs=(reused,),
        )


def test_profile_thresholds_are_explicit_rule_count_limits() -> None:
    threshold = _threshold()
    with pytest.raises(TypeError, match="severity"):
        msgspec.structs.replace(threshold, severity=cast("GateSeverity", "blocking"))
    with pytest.raises(TypeError, match="allowances"):
        msgspec.structs.replace(threshold, drifted_rules=cast("Count", 0))
    profile = _profile()
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(
            profile,
            severities=(GateSeverity.BLOCKING, GateSeverity.BLOCKING),
        )
    with pytest.raises(ValueError, match="non-empty"):
        msgspec.structs.replace(profile, environment_roles=())
    with pytest.raises(TypeError, match="bool"):
        msgspec.structs.replace(profile, require_complete_run=cast("bool", 1))
    with pytest.raises(ValueError, match="exactly"):
        msgspec.structs.replace(profile, thresholds=())
    with pytest.raises(TypeError, match="unsupported value"):
        msgspec.structs.replace(
            profile,
            thresholds=(cast("GateThreshold", "bad"),),
        )
    with pytest.raises(ValueError, match="at most once"):
        msgspec.structs.replace(profile, thresholds=(_threshold(), _threshold()))


def test_gate_result_rejects_contradictory_requested_states() -> None:
    definition = _definition()
    with pytest.raises(ValueError, match="not-requested"):
        GateResult(verdict=GateVerdict.NOT_REQUESTED, profile=definition)
    with pytest.raises(ValueError, match="require"):
        GateResult(verdict=GateVerdict.PASSED, profile=None)
    with pytest.raises(ValueError, match="cannot contain"):
        GateResult(
            verdict=GateVerdict.PASSED,
            profile=definition,
            reasons=(GateFailureReason.RUN_INCOMPLETE,),
        )
    with pytest.raises(ValueError, match="unique"):
        GateResult(
            verdict=GateVerdict.FAILED,
            profile=definition,
            reasons=(GateFailureReason.RUN_INCOMPLETE, GateFailureReason.RUN_INCOMPLETE),
        )


def test_gate_lineage_and_summary_coordinates_are_strictly_typed() -> None:
    lineage = _lineage()
    replacements = (
        ("source_fact_id", cast("FactId", "bad")),
        ("application_id", cast("RuleApplicationId", "bad")),
        ("rule_id", cast("RuleId", "bad")),
        ("effective_id", cast("EffectiveRuleId", "bad")),
        ("rule_fingerprint", cast("Fingerprint", "bad")),
        ("severity", cast("GateSeverity", "bad")),
    )
    for field, value in replacements:
        with pytest.raises(TypeError):
            msgspec.structs.replace(lineage, **{field: value})
    with pytest.raises(TypeError, match="GateRuleLineage"):
        msgspec.structs.replace(
            _assertion_rule(),
            lineage=cast("GateRuleLineage", "bad"),
        )
    with pytest.raises(TypeError, match="role"):
        msgspec.structs.replace(
            _assertion_rule().execution,
            roles=(cast("EnvironmentRole", "baseline"),),
        )
    execution = _assertion_rule().execution
    assert msgspec.structs.replace(
        execution,
        roles=(EnvironmentRole.BASELINE, EnvironmentRole.CANDIDATE),
    ).roles == (EnvironmentRole.BASELINE, EnvironmentRole.CANDIDATE)
    with pytest.raises(ValueError, match="non-empty"):
        msgspec.structs.replace(execution, roles=())
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(
            execution,
            roles=(EnvironmentRole.BASELINE, EnvironmentRole.BASELINE),
        )
    with pytest.raises(ValueError, match="canonical"):
        msgspec.structs.replace(
            execution,
            roles=(EnvironmentRole.CANDIDATE, EnvironmentRole.BASELINE),
        )
    with pytest.raises(ValueError, match="cannot be combined"):
        msgspec.structs.replace(
            execution,
            roles=(EnvironmentRole.BASELINE, EnvironmentRole.OTHER),
        )
    with pytest.raises(TypeError, match="comparison ID"):
        msgspec.structs.replace(
            _pair(),
            comparison_id=cast("ComparisonId", "bad"),
        )
    with pytest.raises(TypeError, match="execution IDs"):
        msgspec.structs.replace(
            _pair(),
            baseline_execution_id=cast("EnvironmentExecutionId", "bad"),
        )
    with pytest.raises(TypeError, match="Count"):
        msgspec.structs.replace(
            _fact_counts(),
            changed=cast("Count", 0),
        )
    with pytest.raises(TypeError, match="GateEnvironmentPair"):
        msgspec.structs.replace(
            _diff_rule(),
            pair=cast("GateEnvironmentPair", "bad"),
        )
    with pytest.raises(TypeError, match="roles"):
        msgspec.structs.replace(
            _pair(),
            baseline_role=cast("EnvironmentRole", "bad"),
        )


def test_normalized_gate_summary_closed_collections_reject_untrusted_shapes() -> None:
    with pytest.raises(TypeError, match="exact lifecycle outcome"):
        NormalizedFactSummary(completion=cast("RunComplete", "complete"))
    with pytest.raises(TypeError, match="normalized assertions"):
        NormalizedFactSummary(
            completion=_complete(),
            assertions=(cast("AssertionGateSummary", "bad"),),
        )
    with pytest.raises(TypeError, match="normalized diffs"):
        NormalizedFactSummary(
            completion=_complete(),
            diffs=(cast("DiffGateSummary", "bad"),),
        )
    with pytest.raises(TypeError, match="unsupported rule outcome"):
        msgspec.structs.replace(
            _assertion(),
            rules=(cast("AssertionGateRuleOutcome", "bad"),),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        msgspec.structs.replace(
            _assertion(),
            rules=cast("tuple[AssertionGateRuleOutcome, ...]", [_assertion_rule()]),
        )


def test_normalized_summary_derives_completeness_from_the_exact_lifecycle_outcome() -> None:
    completed_at = UtcInstant(unix_nanoseconds=2)
    partial = RunPartial(
        reasons=(PartialRunReason.EVALUATION_INCOMPLETE,),
        completed_at=completed_at,
    )
    summary = NormalizedFactSummary(completion=partial)

    assert not summary.is_complete
    assert msgspec.structs.replace(
        summary,
        completion=RunComplete(completed_at=completed_at),
    ).is_complete
    assert not hasattr(summary.completion, "COMPLETE")


def test_profile_and_result_closed_unions_reject_ambiguous_values() -> None:
    profile = _profile()
    with pytest.raises(TypeError, match="DefinitionRef"):
        msgspec.structs.replace(profile, definition=cast("DefinitionRef", "bad"))
    with pytest.raises(ValueError, match="GateProfile"):
        msgspec.structs.replace(
            profile,
            definition=DefinitionRef(
                identity=_resource(ResourceKind.REQUEST, "request"),
                fingerprint=_fingerprint(),
            ),
        )
    with pytest.raises(TypeError, match="unsupported"):
        msgspec.structs.replace(profile, severities=(cast("GateSeverity", "blocking"),))
    with pytest.raises(TypeError, match="immutable tuple"):
        msgspec.structs.replace(
            profile,
            thresholds=cast("tuple[GateThreshold, ...]", [_threshold()]),
        )
    definition = _definition()
    with pytest.raises(TypeError, match="verdict"):
        GateResult(verdict=cast("GateVerdict", "passed"), profile=definition)
    with pytest.raises(TypeError, match="immutable tuple"):
        GateResult(
            verdict=GateVerdict.FAILED,
            profile=definition,
            reasons=cast("tuple[GateFailureReason, ...]", [GateFailureReason.RUN_INCOMPLETE]),
        )
    with pytest.raises(TypeError, match="unsupported failure reason"):
        GateResult(
            verdict=GateVerdict.FAILED,
            profile=definition,
            reasons=(cast("GateFailureReason", "bad"),),
        )
