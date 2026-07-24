"""Cover source-bound gate summaries and direct-construction invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
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
from comparo.core.domain.gates.model import GateEnvironmentPair
from comparo.core.domain.gates.model import GateFailureReason
from comparo.core.domain.gates.model import GateResult
from comparo.core.domain.gates.model import GateRuleLineage
from comparo.core.domain.gates.model import GateSeverity
from comparo.core.domain.gates.model import GateThreshold
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.gates.model import NormalizedFactSummary
from comparo.core.domain.gates.model import RunCompletion
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
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from tests.core.domain.gates.test_normalization import _assertion_fact
from tests.core.domain.gates.test_normalization import _cardinality_rule
from tests.core.domain.gates.test_normalization import _diff_counts
from tests.core.domain.gates.test_normalization import _diff_coverage
from tests.core.domain.gates.test_normalization import _diff_fact
from tests.core.domain.gates.test_normalization import _zero_assertion_counts


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="7" * 64))


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
    outcome = Violated(reason=ViolationReason.CARDINALITY) if violated else Satisfied()
    fact = _assertion_fact(
        outcome,
        _zero_assertion_counts(),
        rule=_cardinality_rule(1) if violated else None,
    )
    fact = msgspec.structs.replace(
        fact,
        fact_id=FactId(value=UUID(int=index)),
        application=msgspec.structs.replace(
            fact.application,
            application_id=RuleApplicationId(value=UUID(int=index + 1)),
        ),
    )
    return AssertionGateRuleOutcome(fact=fact)


def _assertion(index: int = 80) -> AssertionGateSummary:
    return AssertionGateSummary(
        role=EnvironmentRole.BASELINE,
        rules=(_assertion_rule(index),),
    )


def _pair() -> GateEnvironmentPair:
    return _diff().pair


def _coverage() -> DiffGateCoverage:
    return DiffGateCoverage(
        baseline_selected_values=Count(value=1),
        candidate_selected_values=Count(value=1),
        baseline_derived_values=Count(value=1),
        candidate_derived_values=Count(value=1),
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


def _diff_rule(index: int = 100) -> DiffGateRuleOutcome:
    fact = _diff_fact(Equivalent(), _diff_counts(same=1), _diff_coverage(compared=1))
    fact = msgspec.structs.replace(
        fact,
        fact_id=FactId(value=UUID(int=index)),
        application=msgspec.structs.replace(
            fact.application,
            application_id=RuleApplicationId(value=UUID(int=index + 1)),
        ),
    )
    return DiffGateRuleOutcome(fact=fact)


def _diff(index: int = 100) -> DiffGateSummary:
    return DiffGateSummary(
        baseline_role=EnvironmentRole.BASELINE,
        candidate_role=EnvironmentRole.CANDIDATE,
        rules=(_diff_rule(index),),
    )


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


def test_assertion_gate_claims_are_derived_from_the_exact_source_fact() -> None:
    summary = AssertionGateSummary(
        role=EnvironmentRole.BASELINE,
        rules=(_assertion_rule(80, violated=True),),
    )
    normalized = summary.rules[0]
    assert normalized.outcome is AssertionGateOutcome.VIOLATED
    assert normalized.lineage.severity is GateSeverity.BLOCKING
    assert normalized.selected_matches == normalized.fact.counts.selected
    assert normalized.evaluated_matches == normalized.fact.counts.evaluated
    with pytest.raises(TypeError, match=r"[Uu]nexpected keyword argument"):
        AssertionGateRuleOutcome(
            fact=normalized.fact,
            outcome=AssertionGateOutcome.SATISFIED,  # type: ignore[call-arg]
        )
    with pytest.raises(ValueError, match="non-empty"):
        msgspec.structs.replace(summary, rules=())
    with pytest.raises(ValueError, match="lineages"):
        msgspec.structs.replace(summary, rules=(_assertion_rule(), _assertion_rule()))


def test_diff_gate_claims_are_derived_from_the_exact_source_fact() -> None:
    source = _diff_fact(Drifted(), _diff_counts(changed=1), _diff_coverage(compared=1))
    rule = DiffGateRuleOutcome(fact=source)
    assert rule.outcome is DiffGateOutcome.DRIFTED
    assert rule.counts.changed.value == 1
    assert rule.lineage.severity is GateSeverity.INFO
    assert not _diff_rule().incomparable
    with pytest.raises(TypeError, match=r"[Uu]nexpected keyword argument"):
        DiffGateRuleOutcome(
            fact=source,
            outcome=DiffGateOutcome.EQUIVALENT,  # type: ignore[call-arg]
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


def test_pair_requires_distinct_resources_and_execution_occurrences() -> None:
    pair = _pair()
    with pytest.raises(TypeError, match="ResourceIdentity"):
        msgspec.structs.replace(
            pair,
            baseline_environment=cast("ResourceIdentity", "bad"),
        )
    with pytest.raises(ValueError, match="distinct Environment resources"):
        msgspec.structs.replace(pair, candidate_environment=pair.baseline_environment)
    with pytest.raises(ValueError, match="execution IDs"):
        msgspec.structs.replace(pair, candidate_execution_id=pair.baseline_execution_id)
    with pytest.raises(TypeError, match="roles"):
        msgspec.structs.replace(pair, candidate_role=cast("EnvironmentRole", "candidate"))
    with pytest.raises(ValueError, match="Environment"):
        msgspec.structs.replace(
            pair,
            baseline_environment=_resource(ResourceKind.REQUEST, "request"),
        )


def test_summaries_reject_cross_environment_and_cross_pair_relabeling() -> None:
    first = _assertion_rule(80)
    other_environment = DefinitionRef(
        identity=_resource(ResourceKind.ENVIRONMENT, "staging"),
        fingerprint=_fingerprint(),
    )
    cross_environment = AssertionGateRuleOutcome(
        fact=msgspec.structs.replace(
            _assertion_rule(82).fact,
            application=msgspec.structs.replace(
                _assertion_rule(82).fact.application,
                environment=other_environment,
            ),
        )
    )
    with pytest.raises(ValueError, match="one exact Environment execution"):
        AssertionGateSummary(
            role=EnvironmentRole.BASELINE,
            rules=(first, cross_environment),
        )

    source = _diff_rule(100)
    cross_pair = DiffGateRuleOutcome(
        fact=msgspec.structs.replace(
            _diff_rule(102).fact,
            application=msgspec.structs.replace(
                _diff_rule(102).fact.application,
                pair=msgspec.structs.replace(
                    _diff_rule(102).fact.application.pair,
                    comparison_id=ComparisonId(value=UUID(int=999)),
                ),
            ),
        )
    )
    with pytest.raises(ValueError, match="one exact directed execution pair"):
        DiffGateSummary(
            baseline_role=EnvironmentRole.BASELINE,
            candidate_role=EnvironmentRole.CANDIDATE,
            rules=(source, cross_pair),
        )


def test_normalized_summary_rejects_duplicate_source_lineage_globally() -> None:
    normalized = NormalizedFactSummary(
        completion=RunCompletion.COMPLETE,
        assertions=(_assertion(),),
        diffs=(_diff(),),
    )
    assert normalized.diffs[0].pair == _pair()
    with pytest.raises(ValueError, match="assertion summary coordinates"):
        msgspec.structs.replace(normalized, assertions=(_assertion(), _assertion(82)))
    with pytest.raises(ValueError, match="diff summary coordinates"):
        msgspec.structs.replace(normalized, diffs=(_diff(), _diff(102)))
    source_diff = _diff_rule()
    duplicate_fact = DiffGateRuleOutcome(
        fact=msgspec.structs.replace(
            source_diff.fact,
            fact_id=_assertion_rule().fact.fact_id,
        )
    )
    with pytest.raises(ValueError, match="source fact IDs"):
        NormalizedFactSummary(
            completion=RunCompletion.COMPLETE,
            assertions=(_assertion(),),
            diffs=(
                DiffGateSummary(
                    baseline_role=EnvironmentRole.BASELINE,
                    candidate_role=EnvironmentRole.CANDIDATE,
                    rules=(duplicate_fact,),
                ),
            ),
        )
    duplicate_application = DiffGateRuleOutcome(
        fact=msgspec.structs.replace(
            source_diff.fact,
            application=msgspec.structs.replace(
                source_diff.fact.application,
                application_id=_assertion_rule().fact.application.application_id,
            ),
        )
    )
    with pytest.raises(ValueError, match="applications"):
        NormalizedFactSummary(
            completion=RunCompletion.COMPLETE,
            assertions=(_assertion(),),
            diffs=(
                DiffGateSummary(
                    baseline_role=EnvironmentRole.BASELINE,
                    candidate_role=EnvironmentRole.CANDIDATE,
                    rules=(duplicate_application,),
                ),
            ),
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
    with pytest.raises(TypeError, match="AssertionFact"):
        msgspec.structs.replace(
            _assertion_rule(),
            fact=cast("object", "bad"),
        )
    with pytest.raises(TypeError, match="role"):
        msgspec.structs.replace(
            _assertion(),
            role=cast("EnvironmentRole", "baseline"),
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
    with pytest.raises(TypeError, match="DiffRuleFact"):
        msgspec.structs.replace(
            _diff_rule(),
            fact=cast("object", "bad"),
        )
    with pytest.raises(TypeError, match="roles"):
        msgspec.structs.replace(
            _diff(),
            baseline_role=cast("EnvironmentRole", "bad"),
        )


def test_normalized_gate_summary_closed_collections_reject_untrusted_shapes() -> None:
    with pytest.raises(TypeError, match="RunCompletion"):
        NormalizedFactSummary(completion=cast("RunCompletion", "complete"))
    with pytest.raises(TypeError, match="normalized assertions"):
        NormalizedFactSummary(
            completion=RunCompletion.COMPLETE,
            assertions=(cast("AssertionGateSummary", "bad"),),
        )
    with pytest.raises(TypeError, match="normalized diffs"):
        NormalizedFactSummary(
            completion=RunCompletion.COMPLETE,
            diffs=(cast("DiffGateSummary", "bad"),),
        )
    with pytest.raises(TypeError, match="unsupported rule outcome"):
        msgspec.structs.replace(
            _assertion(),
            rules=(cast("AssertionGateRuleOutcome", "bad"),),
        )


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
