"""Verify trusted gate normalization derives every claim from exact source facts."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionOutcome
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import AssertionRuleDefinition
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.results import DiffAggregateOutcome
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffIncompleteReason
from comparo.core.domain.diffs.results import DiffNotEvaluatedReason
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import DiffRuleDefinition
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.rules import DirectedEnvironmentPair
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.gates.normalization import normalize_assertion_fact
from comparo.core.domain.gates.normalization import normalize_diff_fact
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity
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
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonInteger


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="d" * 64))


def _resource(kind: ResourceKind, name: str) -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="demo"),
        kind=kind,
        resource_id=ResourceId(value=name),
    )


def _selector() -> CompiledSelector:
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=SelectorSource.RESPONSE_STATUS,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=SelectorCardinality.EXACTLY_ONE,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(
            kinds=(ValueKind.INTEGER,),
            cardinality=SelectorCardinality.EXACTLY_ONE,
        ),
    )


def _literal(value: int) -> LiteralExpr:
    return LiteralExpr(
        value=JsonInteger(value=value),
        provenance=ValueProvenance(
            entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),),
        ),
    )


def _assertion_definition() -> AssertionRuleDefinition:
    return AssertionRuleDefinition(
        origin=DefinitionRef(
            identity=_resource(ResourceKind.ASSERTION_PROFILE, "api"),
            fingerprint=_fingerprint(),
        ),
        rule_id=RuleId(value="status"),
    )


def _assertion_application() -> AssertionApplication:
    return AssertionApplication(
        application_id=RuleApplicationId(value=UUID(int=201)),
        definition=_assertion_definition(),
        effective_id=EffectiveRuleId(value="status"),
        rule_fingerprint=_fingerprint(),
        environment=DefinitionRef(
            identity=_resource(ResourceKind.ENVIRONMENT, "prod"),
            fingerprint=_fingerprint(),
        ),
        environment_execution_id=EnvironmentExecutionId(value=UUID(int=202)),
    )


def _cardinality_rule(exact: int = 0) -> CompiledAssertionRule:
    return CompiledAssertionRule(
        definition=_assertion_definition(),
        effective_id=EffectiveRuleId(value="status"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="Status"),
        severity=Severity.BLOCKING,
        selector=_selector(),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.CARDINALITY,
            version="1",
        ),
        operator=CardinalityOperator(exact=Count(value=exact)),
        quantifier=None,
        completeness=CompletenessRequirement.COMPLETE,
    )


def _predicate_rule() -> CompiledAssertionRule:
    return CompiledAssertionRule(
        definition=_assertion_definition(),
        effective_id=EffectiveRuleId(value="status"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="Status"),
        severity=Severity.WARNING,
        selector=_selector(),
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.EQUALITY,
            version="1",
        ),
        operator=EqualityOperator(
            relation=EqualityRelation.EQUALS,
            expected=_literal(200),
        ),
        quantifier=Quantifier.ONE,
        completeness=CompletenessRequirement.COMPLETE,
    )


def _assertion_fact(
    outcome: AssertionOutcome,
    counts: AssertionCounts,
    *,
    rule: CompiledAssertionRule | None = None,
) -> AssertionFact:
    return AssertionFact(
        fact_id=FactId(value=UUID(int=203)),
        application=_assertion_application(),
        rule=_cardinality_rule() if rule is None else rule,
        outcome=outcome,
        counts=counts,
        evidence=AssertionEvidence(
            retained=(),
            omitted=Count(value=counts.evaluated.value),
        ),
    )


def _zero_assertion_counts() -> AssertionCounts:
    zero = Count(value=0)
    return AssertionCounts(
        selected=zero,
        evaluated=zero,
        satisfied=zero,
        violated=zero,
        errors=zero,
    )


def _diff_operation(family: DiffOperationFamily, name: str) -> DiffOperationIdentity:
    return DiffOperationIdentity(
        namespace="comparo.core",
        family=family,
        name=name,
        version="1",
    )


def _diff_definition() -> DiffRuleDefinition:
    return DiffRuleDefinition(
        origin=DefinitionRef(
            identity=_resource(ResourceKind.DIFF_PROFILE, "api"),
            fingerprint=_fingerprint(),
        ),
        rule_id=RuleId(value="status"),
    )


def _diff_application() -> DiffApplication:
    return DiffApplication(
        application_id=RuleApplicationId(value=UUID(int=211)),
        definition=_diff_definition(),
        effective_id=EffectiveRuleId(value="status"),
        rule_fingerprint=_fingerprint(),
        pair=DirectedEnvironmentPair(
            comparison_id=ComparisonId(value=UUID(int=212)),
            baseline_environment=_resource(ResourceKind.ENVIRONMENT, "prod"),
            baseline_execution_id=EnvironmentExecutionId(value=UUID(int=213)),
            candidate_environment=_resource(ResourceKind.ENVIRONMENT, "staging"),
            candidate_execution_id=EnvironmentExecutionId(value=UUID(int=214)),
        ),
    )


def _diff_rule() -> CompiledDiffRule:
    side = SidePipeline(selector=_selector())
    return CompiledDiffRule(
        definition=_diff_definition(),
        effective_id=EffectiveRuleId(value="status"),
        fingerprint=_fingerprint(),
        metadata=Metadata(name="Status"),
        severity=DiffSeverity.INFO,
        completeness=DiffCompletenessRequirement.COMPLETE,
        pipeline=DiffPipeline(
            baseline=side,
            candidate=side,
            pairing=SingularPairing(
                identity=_diff_operation(DiffOperationFamily.PAIRING, "singular"),
            ),
            comparator=ExactComparator(
                identity=_diff_operation(DiffOperationFamily.COMPARATOR, "exact"),
            ),
        ),
    )


def _diff_fact(
    outcome: DiffAggregateOutcome,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
) -> DiffRuleFact:
    return DiffRuleFact(
        fact_id=FactId(value=UUID(int=215)),
        application=_diff_application(),
        rule=_diff_rule(),
        outcome=outcome,
        counts=counts,
        coverage=coverage,
        evidence=DiffEvidence(retained=(), omitted=Count(value=counts.total)),
    )


def _diff_counts(*, same: int = 0, changed: int = 0) -> DiffFactCounts:
    return DiffFactCounts(
        same=Count(value=same),
        changed=Count(value=changed),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=0),
        incomparable=Count(value=0),
    )


def _diff_coverage(*, compared: int = 0, not_evaluated: int = 0) -> DiffCoverage:
    paired = compared + not_evaluated
    return DiffCoverage(
        baseline_selected=Count(value=paired),
        candidate_selected=Count(value=paired),
        baseline_derived=Count(value=paired),
        candidate_derived=Count(value=paired),
        paired=Count(value=paired),
        compared=Count(value=compared),
        ignored=Count(value=0),
        not_evaluated=Count(value=not_evaluated),
    )


def test_assertion_normalization_derives_lineage_severity_outcome_and_counts() -> None:
    satisfied = _assertion_fact(Satisfied(), _zero_assertion_counts())
    summary = normalize_assertion_fact(
        satisfied,
        role=EnvironmentRole.BASELINE,
    )
    normalized = summary.rules[0]
    assert normalized.outcome is AssertionGateOutcome.SATISFIED
    assert normalized.lineage.source_fact_id == satisfied.fact_id
    assert normalized.lineage.severity is GateSeverity.BLOCKING
    assert normalized.fact is satisfied
    assert summary.environment == satisfied.application.environment
    assert summary.execution_id == satisfied.application.environment_execution_id


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (
            _assertion_fact(
                Violated(reason=ViolationReason.CARDINALITY),
                _zero_assertion_counts(),
                rule=_cardinality_rule(1),
            ),
            AssertionGateOutcome.VIOLATED,
        ),
        (_assertion_fact(Skipped(), _zero_assertion_counts()), AssertionGateOutcome.SKIPPED),
        (
            _assertion_fact(
                AssertionUnavailable(code=DiagnosticCode(value="assertion.unavailable")),
                _zero_assertion_counts(),
            ),
            AssertionGateOutcome.UNAVAILABLE,
        ),
        (
            _assertion_fact(
                AssertionError(code=DiagnosticCode(value="assertion.error")),
                AssertionCounts(
                    selected=Count(value=1),
                    evaluated=Count(value=1),
                    satisfied=Count(value=0),
                    violated=Count(value=0),
                    errors=Count(value=1),
                ),
                rule=_predicate_rule(),
            ),
            AssertionGateOutcome.ERROR,
        ),
    ],
)
def test_assertion_normalization_maps_every_terminal_outcome(
    fact: AssertionFact,
    expected: AssertionGateOutcome,
) -> None:
    summary = normalize_assertion_fact(
        fact,
        role=EnvironmentRole.OTHER,
    )
    assert summary.rules[0].outcome is expected


def test_diff_normalization_derives_pair_lineage_fact_buckets_and_coverage() -> None:
    fact = _diff_fact(Equivalent(), _diff_counts(same=1), _diff_coverage(compared=1))
    summary = normalize_diff_fact(
        fact,
        baseline_role=EnvironmentRole.BASELINE,
        candidate_role=EnvironmentRole.CANDIDATE,
    )
    normalized = summary.rules[0]
    assert normalized.outcome is DiffGateOutcome.EQUIVALENT
    assert normalized.lineage.source_fact_id == fact.fact_id
    assert normalized.lineage.severity is GateSeverity.INFO
    assert normalized.counts.same.value == 1
    assert summary.pair.baseline_execution_id == fact.application.pair.baseline_execution_id


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (
            _diff_fact(Drifted(), _diff_counts(changed=1), _diff_coverage(compared=1)),
            DiffGateOutcome.DRIFTED,
        ),
        (
            _diff_fact(
                Incomplete(
                    reason=DiffIncompleteReason(
                        side=DiffSide.BOTH,
                        stage=DiffEvaluationStage.DERIVATION,
                        cause=DiffIncompleteCause.PARTIAL,
                    )
                ),
                _diff_counts(),
                _diff_coverage(not_evaluated=1),
            ),
            DiffGateOutcome.INCOMPLETE,
        ),
        (
            _diff_fact(
                DiffEvaluationError(code=DiagnosticCode(value="diff.error")),
                _diff_counts(),
                _diff_coverage(),
            ),
            DiffGateOutcome.ERROR,
        ),
        (
            _diff_fact(
                NotEvaluated(reason=DiffNotEvaluatedReason.FALSE_APPLICABILITY),
                _diff_counts(),
                _diff_coverage(),
            ),
            DiffGateOutcome.NOT_EVALUATED,
        ),
    ],
)
def test_diff_normalization_maps_every_aggregate_outcome(
    fact: DiffRuleFact,
    expected: DiffGateOutcome,
) -> None:
    summary = normalize_diff_fact(
        fact,
        baseline_role=EnvironmentRole.BASELINE,
        candidate_role=EnvironmentRole.CANDIDATE,
    )
    assert summary.rules[0].outcome is expected


def test_normalization_rejects_non_source_objects() -> None:
    with pytest.raises(TypeError, match="AssertionFact"):
        normalize_assertion_fact(
            cast("AssertionFact", "bad"),
            role=EnvironmentRole.BASELINE,
        )
    with pytest.raises(TypeError, match="DiffRuleFact"):
        normalize_diff_fact(
            cast("DiffRuleFact", "bad"),
            baseline_role=EnvironmentRole.BASELINE,
            candidate_role=EnvironmentRole.CANDIDATE,
        )
