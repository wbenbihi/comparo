"""Cover diff operation catalogs, directionality, and aggregate invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import SortBy
from comparo.core.domain.diffs.operations import SortDirection
from comparo.core.domain.diffs.results import DiffAggregateOutcome
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffIncompleteReason
from comparo.core.domain.diffs.results import DiffNotEvaluatedReason
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import DiffValueProvenance
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import GroupByReducer
from comparo.core.domain.diffs.strategies import KeyedPairing
from comparo.core.domain.diffs.strategies import ObjectFromEntriesReducer
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.diffs._invariant_support import _application
from tests.core.domain.diffs._invariant_support import _compiled_rule
from tests.core.domain.diffs._invariant_support import _evidence_exhaustion
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _location
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _plural_item_selector
from tests.core.domain.diffs._invariant_support import _plural_selector
from tests.core.domain.diffs._invariant_support import _value_provenance


@pytest.mark.parametrize(
    ("outcome", "same", "changed", "incomparable", "compared"),
    [
        (Equivalent(), 0, 0, 0, 0),
        (Equivalent(), 0, 1, 0, 1),
        (Equivalent(), 0, 0, 1, 1),
        (Drifted(), 1, 0, 0, 1),
        (NotEvaluated(reason=DiffNotEvaluatedReason.EMPTY_SELECTION), 1, 0, 0, 1),
    ],
)
def test_impossible_aggregate_outcomes_are_rejected(
    outcome: DiffAggregateOutcome,
    same: int,
    changed: int,
    incomparable: int,
    compared: int,
) -> None:
    counts = DiffFactCounts(
        same=Count(value=same),
        changed=Count(value=changed),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=0),
        incomparable=Count(value=incomparable),
    )
    coverage = DiffCoverage(
        baseline_selected=Count(value=compared),
        candidate_selected=Count(value=compared),
        baseline_derived=Count(value=compared),
        candidate_derived=Count(value=compared),
        baseline_consumed=Count(value=compared),
        candidate_consumed=Count(value=compared),
        baseline_resolved=Count(value=compared),
        candidate_resolved=Count(value=compared),
        paired=Count(value=compared),
        compared=Count(value=compared),
        ignored=Count(value=0),
        not_evaluated=Count(value=0),
    )
    with pytest.raises(ValueError, match=r"equivalent|drifted|not-evaluated"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=71)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=outcome,
            counts=counts,
            coverage=coverage,
            evidence=DiffEvidence(
                retained=(),
                omitted=Count(value=counts.total),
                exhaustion=None if counts.total == 0 else _evidence_exhaustion(),
            ),
        )


def test_entirely_ignored_comparison_is_not_evaluated_not_equivalent() -> None:
    counts = DiffFactCounts(
        same=Count(value=0),
        changed=Count(value=0),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=1),
        incomparable=Count(value=0),
    )
    coverage = DiffCoverage(
        baseline_selected=Count(value=1),
        candidate_selected=Count(value=1),
        baseline_derived=Count(value=1),
        candidate_derived=Count(value=1),
        baseline_consumed=Count(value=1),
        candidate_consumed=Count(value=1),
        baseline_resolved=Count(value=1),
        candidate_resolved=Count(value=1),
        paired=Count(value=1),
        compared=Count(value=0),
        ignored=Count(value=1),
        not_evaluated=Count(value=0),
    )
    fact = DiffRuleFact(
        fact_id=FactId(value=UUID(int=72)),
        application=_application(),
        rule=_compiled_rule(DiffSeverity.INFO),
        outcome=NotEvaluated(reason=DiffNotEvaluatedReason.ALL_IGNORED),
        counts=counts,
        coverage=coverage,
        evidence=DiffEvidence(
            retained=(), omitted=Count(value=1), exhaustion=_evidence_exhaustion()
        ),
    )
    assert type(fact.outcome) is NotEvaluated


def test_incomparable_fact_requires_safe_diagnostic() -> None:
    code = DiagnosticCode(value="diff.keys-duplicate")
    fact = DiffFact(
        fact_id=FactId(value=UUID(int=73)),
        outcome=DiffFactOutcome.INCOMPARABLE,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline=_value_provenance(_location(74)),
        candidate=_value_provenance(_location(75, candidate=True)),
        diagnostic=code,
    )
    assert fact.diagnostic == code
    with pytest.raises(ValueError, match="diagnostics"):
        DiffFact(
            fact_id=FactId(value=UUID(int=76)),
            outcome=DiffFactOutcome.INCOMPARABLE,
            comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        )


def test_diff_fact_and_evidence_reject_untyped_or_locationless_states() -> None:
    comparator = _operation(DiffOperationFamily.COMPARATOR, "exact")
    valid = DiffFact(
        fact_id=FactId(value=UUID(int=77)),
        outcome=DiffFactOutcome.SAME,
        comparator=comparator,
        baseline=_value_provenance(_location(78)),
        candidate=_value_provenance(_location(79, candidate=True)),
    )
    with pytest.raises(TypeError, match="typed ID and outcome"):
        msgspec.structs.replace(valid, fact_id=cast("FactId", "bad"))
    with pytest.raises(TypeError, match="typed ID and outcome"):
        msgspec.structs.replace(valid, outcome=cast("DiffFactOutcome", "same"))
    with pytest.raises(TypeError, match="side provenance"):
        msgspec.structs.replace(
            valid,
            baseline=cast("DiffValueProvenance", "bad"),
        )
    with pytest.raises(TypeError, match="diagnostic"):
        msgspec.structs.replace(valid, diagnostic=cast("DiagnosticCode", "bad"))
    with pytest.raises(ValueError, match="both locations"):
        msgspec.structs.replace(valid, baseline=None)
    with pytest.raises(ValueError, match="at least one side provenance"):
        DiffFact(
            fact_id=FactId(value=UUID(int=80)),
            outcome=DiffFactOutcome.IGNORED,
            comparator=comparator,
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        DiffEvidence(
            retained=cast("tuple[DiffFact, ...]", [valid]),
            omitted=Count(value=0),
        )
    with pytest.raises(TypeError, match="unsupported fact"):
        DiffEvidence(
            retained=(cast("DiffFact", "bad"),),
            omitted=Count(value=0),
        )
    with pytest.raises(TypeError, match="omitted"):
        DiffEvidence(retained=(valid,), omitted=cast("Count", 0))


def test_diff_value_provenance_rejects_ambiguous_lineage() -> None:
    location = _location(8_100)
    valid = _value_provenance(location)
    with pytest.raises(TypeError, match="CompiledSelector"):
        msgspec.structs.replace(valid, selector=cast("object", "bad"))
    with pytest.raises(TypeError, match="concrete locations"):
        msgspec.structs.replace(valid, locations=cast("tuple[ExchangeLocation, ...]", [location]))
    with pytest.raises(TypeError, match="concrete locations"):
        msgspec.structs.replace(valid, locations=(cast("ExchangeLocation", "bad"),))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(valid, locations=(location, location))
    with pytest.raises(TypeError, match="operations"):
        msgspec.structs.replace(valid, operations=cast("tuple[DiffOperationIdentity, ...]", []))
    with pytest.raises(TypeError, match="operations"):
        msgspec.structs.replace(valid, operations=(cast("DiffOperationIdentity", "bad"),))
    taint = TaintOriginId(value="test.secret")
    with pytest.raises(TypeError, match="taint origins"):
        msgspec.structs.replace(valid, taint_origins=cast("tuple[TaintOriginId, ...]", [taint]))
    with pytest.raises(TypeError, match="taint origins"):
        msgspec.structs.replace(valid, taint_origins=(cast("TaintOriginId", "bad"),))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(valid, taint_origins=(taint, taint))
    with pytest.raises(TypeError, match="marker"):
        msgspec.structs.replace(valid, empty_reducer_identity=cast("bool", 1))
    with pytest.raises(ValueError, match="exactly originless"):
        msgspec.structs.replace(valid, empty_reducer_identity=True)
    with pytest.raises(ValueError, match="exactly originless"):
        msgspec.structs.replace(valid, locations=())
    with pytest.raises(ValueError, match="terminal reducer"):
        msgspec.structs.replace(
            valid,
            locations=(),
            operations=(_operation(DiffOperationFamily.COMPARATOR, "exact"),),
            empty_reducer_identity=True,
        )
    originless = msgspec.structs.replace(
        valid,
        locations=(),
        operations=(_operation(DiffOperationFamily.REDUCER, "count"),),
        empty_reducer_identity=True,
    )
    assert originless.locations == ()


def test_diff_rule_fact_rejects_untyped_and_inconsistent_aggregate_data() -> None:
    detail = DiffFact(
        fact_id=FactId(value=UUID(int=81)),
        outcome=DiffFactOutcome.SAME,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline=_value_provenance(_location(82)),
        candidate=_value_provenance(_location(83, candidate=True)),
    )
    counts = DiffFactCounts(
        same=Count(value=1),
        changed=Count(value=0),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=0),
        incomparable=Count(value=0),
    )
    coverage = DiffCoverage(
        baseline_selected=Count(value=1),
        candidate_selected=Count(value=1),
        baseline_derived=Count(value=1),
        candidate_derived=Count(value=1),
        baseline_consumed=Count(value=1),
        candidate_consumed=Count(value=1),
        baseline_resolved=Count(value=1),
        candidate_resolved=Count(value=1),
        paired=Count(value=1),
        compared=Count(value=1),
        ignored=Count(value=0),
        not_evaluated=Count(value=0),
    )
    fact = DiffRuleFact(
        fact_id=FactId(value=UUID(int=84)),
        application=_application(),
        rule=_compiled_rule(),
        outcome=Equivalent(),
        counts=counts,
        coverage=coverage,
        evidence=DiffEvidence(retained=(detail,), omitted=Count(value=0)),
    )
    assert fact.severity is DiffSeverity.BLOCKING
    dependency = TaintOriginId(value="diff-rule-dependency")
    with pytest.raises(TypeError, match="immutable and typed"):
        msgspec.structs.replace(
            detail,
            dependency_taint_origins=cast("tuple[TaintOriginId, ...]", [dependency]),
        )
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(
            detail,
            dependency_taint_origins=(dependency, dependency),
        )
    with pytest.raises(TypeError, match="immutable and typed"):
        msgspec.structs.replace(
            fact,
            dependency_taint_origins=cast("tuple[TaintOriginId, ...]", [dependency]),
        )
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(
            fact,
            dependency_taint_origins=(dependency, dependency),
        )
    dependent_detail = msgspec.structs.replace(
        detail,
        dependency_taint_origins=(dependency,),
    )
    with pytest.raises(ValueError, match="summarized by its parent"):
        msgspec.structs.replace(
            fact,
            evidence=DiffEvidence(retained=(dependent_detail,), omitted=Count(value=0)),
        )
    tampered = msgspec.structs.replace(
        detail,
        baseline=msgspec.structs.replace(
            cast("DiffValueProvenance", detail.baseline),
            operations=(_operation(DiffOperationFamily.TRANSFORM, "length"),),
        ),
    )
    with pytest.raises(ValueError, match="operations contradict"):
        msgspec.structs.replace(
            fact,
            evidence=DiffEvidence(retained=(tampered,), omitted=Count(value=0)),
        )
    wrong_selector = msgspec.structs.replace(
        detail,
        baseline=msgspec.structs.replace(
            cast("DiffValueProvenance", detail.baseline),
            selector=_plural_selector(),
        ),
    )
    with pytest.raises(ValueError, match="different selector"):
        msgspec.structs.replace(
            fact,
            evidence=DiffEvidence(retained=(wrong_selector,), omitted=Count(value=0)),
        )
    with pytest.raises(TypeError, match="fact and application"):
        msgspec.structs.replace(fact, application=cast("DiffApplication", "bad"))
    with pytest.raises(TypeError, match="CompiledDiffRule"):
        msgspec.structs.replace(fact, rule=cast("CompiledDiffRule", "bad"))
    with pytest.raises(ValueError, match="effective compiled rule"):
        msgspec.structs.replace(
            fact,
            application=msgspec.structs.replace(
                fact.application,
                effective_id=EffectiveRuleId(value="other"),
            ),
        )
    with pytest.raises(ValueError, match="comparator"):
        msgspec.structs.replace(
            fact,
            evidence=DiffEvidence(
                retained=(
                    msgspec.structs.replace(
                        detail,
                        comparator=_operation(DiffOperationFamily.COMPARATOR, "ignore"),
                    ),
                ),
                omitted=Count(value=0),
            ),
        )
    with pytest.raises(TypeError, match="severity"):
        msgspec.structs.replace(fact, severity=cast("DiffSeverity", "bad"))
    with pytest.raises(TypeError, match="aggregate outcome"):
        msgspec.structs.replace(
            fact,
            outcome=cast("DiffAggregateOutcome", "equivalent"),
        )
    with pytest.raises(TypeError, match="counts and coverage"):
        msgspec.structs.replace(fact, counts=cast("DiffFactCounts", "bad"))
    with pytest.raises(TypeError, match="evidence"):
        msgspec.structs.replace(fact, evidence=cast("DiffEvidence", "bad"))
    with pytest.raises(ValueError, match="evidence must cover"):
        msgspec.structs.replace(
            fact,
            evidence=DiffEvidence(retained=(), omitted=Count(value=0)),
        )
    with pytest.raises(ValueError, match="compared coverage"):
        msgspec.structs.replace(
            fact,
            coverage=msgspec.structs.replace(
                coverage,
                compared=Count(value=0),
                not_evaluated=Count(value=1),
                baseline_resolved=Count(value=0),
                candidate_resolved=Count(value=0),
            ),
        )
    ignored_counts = DiffFactCounts(
        same=Count(value=0),
        changed=Count(value=0),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=1),
        incomparable=Count(value=0),
    )
    with pytest.raises(ValueError, match="ignored coverage"):
        msgspec.structs.replace(
            fact,
            outcome=NotEvaluated(reason=DiffNotEvaluatedReason.ALL_IGNORED),
            counts=ignored_counts,
            coverage=msgspec.structs.replace(
                coverage,
                compared=Count(value=0),
                not_evaluated=Count(value=1),
                baseline_resolved=Count(value=0),
                candidate_resolved=Count(value=0),
            ),
            evidence=DiffEvidence(
                retained=(), omitted=Count(value=1), exhaustion=_evidence_exhaustion()
            ),
        )
    code = DiagnosticCode(value="diff.evaluator-error")
    assert type(msgspec.structs.replace(fact, outcome=DiffEvaluationError(code=code)).outcome) is (
        DiffEvaluationError
    )
    with pytest.raises(TypeError, match="DiagnosticCode"):
        DiffEvaluationError(code=cast("DiagnosticCode", "bad"))


def test_incomplete_outcome_retains_side_stage_and_cause() -> None:
    reason = DiffIncompleteReason(
        side=DiffSide.CANDIDATE,
        stage=DiffEvaluationStage.DECODE,
        cause=DiffIncompleteCause.BUDGET_EXCEEDED,
    )
    outcome = Incomplete(reason=reason)
    assert outcome.reason == reason
    with pytest.raises(TypeError, match="typed side"):
        DiffIncompleteReason(
            side=cast("DiffSide", "candidate"),
            stage=DiffEvaluationStage.DECODE,
            cause=DiffIncompleteCause.PARTIAL,
        )


def test_builtin_diff_identity_cannot_claim_foreign_dispatch_semantics() -> None:
    with pytest.raises(ValueError, match=r"comparo\.core"):
        ExactComparator(
            identity=DiffOperationIdentity(
                namespace="community.example",
                family=DiffOperationFamily.COMPARATOR,
                name="exact",
                version="1",
            )
        )
    with pytest.raises(ValueError, match="version 1"):
        ExactComparator(
            identity=DiffOperationIdentity(
                namespace="comparo.core",
                family=DiffOperationFamily.COMPARATOR,
                name="exact",
                version="2",
            )
        )


def test_singular_key_and_pairing_contracts_reject_plural_stage_outputs() -> None:
    plural = _plural_item_selector()
    with pytest.raises(ValueError, match="sort key"):
        SortBy(
            identity=_operation(DiffOperationFamily.TRANSFORM, "sort-by"),
            key=plural,
            direction=SortDirection.ASCENDING,
        )
    with pytest.raises(ValueError, match="keyed pairing"):
        KeyedPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
            baseline_key=plural,
            candidate_key=_item_selector(),
            duplicate_keys=DuplicateKeyMode.ERROR,
        )
    side = SidePipeline(selector=_plural_selector())
    with pytest.raises(ValueError, match="singular pairing"):
        DiffPipeline(
            baseline=side,
            candidate=side,
            pairing=SingularPairing(
                identity=_operation(DiffOperationFamily.PAIRING, "singular"),
            ),
            comparator=ExactComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "exact"),
            ),
        )
    reduced = SidePipeline(
        selector=_plural_selector(),
        reducer=Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "count"),
            kind=ReducerKind.COUNT,
            empty=EmptyInputMode.RETURN_ZERO,
        ),
    )
    assert reduced.output_cardinality is SelectorCardinality.EXACTLY_ONE


def test_reducer_empty_and_duplicate_contracts_are_operation_specific() -> None:
    with pytest.raises(ValueError, match="no identity"):
        Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "first"),
            kind=ReducerKind.FIRST,
            empty=EmptyInputMode.RETURN_ZERO,
        )
    with pytest.raises(ValueError, match="duplicate keys"):
        GroupByReducer(
            identity=_operation(DiffOperationFamily.REDUCER, "group-by"),
            key=_item_selector(),
            empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
            duplicate_keys=DuplicateKeyMode.KEEP_FIRST,
        )
    with pytest.raises(ValueError, match="object duplicate"):
        ObjectFromEntriesReducer(
            identity=_operation(DiffOperationFamily.REDUCER, "object-from-entries"),
            key=_item_selector(),
            value=_item_selector(),
            empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
            duplicate_keys=DuplicateKeyMode.COLLECT,
        )


def test_diff_retained_locations_bind_directed_side_and_selector_source() -> None:
    application = _application()
    rule = _compiled_rule()
    detail = DiffFact(
        fact_id=FactId(value=UUID(int=850)),
        outcome=DiffFactOutcome.SAME,
        comparator=rule.pipeline.comparator.identity,
        baseline=_value_provenance(_location(851)),
        candidate=_value_provenance(_location(852, candidate=True)),
    )
    counts = DiffFactCounts(
        same=Count(value=1),
        changed=Count(value=0),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=0),
        incomparable=Count(value=0),
    )
    coverage = DiffCoverage(
        baseline_selected=Count(value=1),
        candidate_selected=Count(value=1),
        baseline_derived=Count(value=1),
        candidate_derived=Count(value=1),
        baseline_consumed=Count(value=1),
        candidate_consumed=Count(value=1),
        baseline_resolved=Count(value=1),
        candidate_resolved=Count(value=1),
        paired=Count(value=1),
        compared=Count(value=1),
        ignored=Count(value=0),
        not_evaluated=Count(value=0),
    )
    valid = DiffRuleFact(
        fact_id=FactId(value=UUID(int=853)),
        application=application,
        rule=rule,
        outcome=Equivalent(),
        counts=counts,
        coverage=coverage,
        evidence=DiffEvidence(retained=(detail,), omitted=Count(value=0)),
    )

    with pytest.raises(ValueError, match="parent fact ID"):
        msgspec.structs.replace(valid, fact_id=detail.fact_id)

    assert detail.baseline is not None
    baseline_provenance = detail.baseline
    baseline = cast("ExchangeLocation", baseline_provenance.locations[0])
    with pytest.raises(ValueError, match=r"baseline.*different execution"):
        msgspec.structs.replace(
            valid,
            evidence=DiffEvidence(
                retained=(
                    msgspec.structs.replace(
                        detail,
                        baseline=msgspec.structs.replace(
                            baseline_provenance,
                            locations=(
                                msgspec.structs.replace(
                                    baseline,
                                    environment_execution_id=EnvironmentExecutionId(
                                        value=UUID(int=854)
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
                omitted=Count(value=0),
            ),
        )
    with pytest.raises(ValueError, match="source does not match"):
        msgspec.structs.replace(
            valid,
            evidence=DiffEvidence(
                retained=(
                    msgspec.structs.replace(
                        detail,
                        baseline=msgspec.structs.replace(
                            baseline_provenance,
                            locations=(
                                msgspec.structs.replace(
                                    baseline,
                                    source=SelectorSource.RESPONSE_PROTOCOL,
                                    field=StaticFieldPath(segments=("response", "protocol")),
                                ),
                            ),
                        ),
                    ),
                ),
                omitted=Count(value=0),
            ),
        )

    assert detail.candidate is not None
    candidate_provenance = detail.candidate
    candidate = cast("ExchangeLocation", candidate_provenance.locations[0])
    with pytest.raises(ValueError, match=r"candidate.*different execution"):
        msgspec.structs.replace(
            valid,
            evidence=DiffEvidence(
                retained=(
                    msgspec.structs.replace(
                        detail,
                        candidate=msgspec.structs.replace(
                            candidate_provenance,
                            locations=(
                                msgspec.structs.replace(
                                    candidate,
                                    environment_execution_id=EnvironmentExecutionId(
                                        value=UUID(int=856)
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
                omitted=Count(value=0),
            ),
        )

    removed = msgspec.structs.replace(
        detail,
        fact_id=FactId(value=UUID(int=857)),
        outcome=DiffFactOutcome.REMOVED,
        candidate=None,
    )
    removed_counts = msgspec.structs.replace(
        counts,
        same=Count(value=0),
        removed=Count(value=1),
    )
    removed_coverage = msgspec.structs.replace(
        coverage,
        candidate_selected=Count(value=0),
        candidate_derived=Count(value=0),
        candidate_consumed=Count(value=0),
        candidate_resolved=Count(value=0),
    )
    assert (
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=858)),
            application=application,
            rule=rule,
            outcome=Drifted(),
            counts=removed_counts,
            coverage=removed_coverage,
            evidence=DiffEvidence(retained=(removed,), omitted=Count(value=0)),
        ).counts.removed.value
        == 1
    )

    added = msgspec.structs.replace(
        detail,
        fact_id=FactId(value=UUID(int=859)),
        outcome=DiffFactOutcome.ADDED,
        baseline=None,
    )
    added_counts = msgspec.structs.replace(
        counts,
        same=Count(value=0),
        added=Count(value=1),
    )
    added_coverage = msgspec.structs.replace(
        coverage,
        baseline_selected=Count(value=0),
        baseline_derived=Count(value=0),
        baseline_consumed=Count(value=0),
        baseline_resolved=Count(value=0),
    )
    assert (
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=860)),
            application=application,
            rule=rule,
            outcome=Drifted(),
            counts=added_counts,
            coverage=added_coverage,
            evidence=DiffEvidence(retained=(added,), omitted=Count(value=0)),
        ).counts.added.value
        == 1
    )


def test_diff_rejects_synthetic_and_one_sided_equivalent_coverage() -> None:
    zero = Count(value=0)
    with pytest.raises(ValueError, match="paired units contradict"):
        DiffCoverage(
            baseline_selected=zero,
            candidate_selected=zero,
            baseline_derived=zero,
            candidate_derived=zero,
            baseline_consumed=zero,
            candidate_consumed=zero,
            baseline_resolved=zero,
            candidate_resolved=zero,
            paired=Count(value=1),
            compared=Count(value=1),
            ignored=zero,
            not_evaluated=zero,
        )

    one_sided = DiffCoverage(
        baseline_selected=Count(value=1),
        candidate_selected=zero,
        baseline_derived=Count(value=1),
        candidate_derived=zero,
        baseline_consumed=Count(value=1),
        candidate_consumed=zero,
        baseline_resolved=Count(value=1),
        candidate_resolved=zero,
        paired=Count(value=1),
        compared=Count(value=1),
        ignored=zero,
        not_evaluated=zero,
    )
    counts = DiffFactCounts(
        same=Count(value=1),
        changed=zero,
        added=zero,
        removed=zero,
        ignored=zero,
        incomparable=zero,
    )
    with pytest.raises(ValueError, match="one-sided and bilateral"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=855)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Equivalent(),
            counts=counts,
            coverage=one_sided,
            evidence=DiffEvidence(
                retained=(), omitted=Count(value=1), exhaustion=_evidence_exhaustion()
            ),
        )

    with pytest.raises(ValueError, match="one-sided and bilateral"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=861)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Equivalent(),
            counts=counts,
            coverage=msgspec.structs.replace(
                one_sided,
                baseline_selected=zero,
                baseline_derived=zero,
                baseline_consumed=zero,
                baseline_resolved=zero,
                candidate_selected=Count(value=1),
                candidate_derived=Count(value=1),
                candidate_consumed=Count(value=1),
                candidate_resolved=Count(value=1),
            ),
            evidence=DiffEvidence(
                retained=(), omitted=Count(value=1), exhaustion=_evidence_exhaustion()
            ),
        )


def test_complete_diff_outcomes_cannot_drop_derived_values() -> None:
    coverage = DiffCoverage(
        baseline_selected=Count(value=2),
        candidate_selected=Count(value=2),
        baseline_derived=Count(value=2),
        candidate_derived=Count(value=2),
        baseline_consumed=Count(value=1),
        candidate_consumed=Count(value=1),
        baseline_resolved=Count(value=1),
        candidate_resolved=Count(value=1),
        paired=Count(value=1),
        compared=Count(value=1),
        ignored=Count(value=0),
        not_evaluated=Count(value=0),
    )
    counts = DiffFactCounts(
        same=Count(value=1),
        changed=Count(value=0),
        added=Count(value=0),
        removed=Count(value=0),
        ignored=Count(value=0),
        incomparable=Count(value=0),
    )
    with pytest.raises(ValueError, match="consume and resolve every derived value"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=862)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Equivalent(),
            counts=counts,
            coverage=coverage,
            evidence=DiffEvidence(
                retained=(), omitted=Count(value=1), exhaustion=_evidence_exhaustion()
            ),
        )


def test_ignored_and_incomparable_facts_have_exact_side_footprints() -> None:
    zero = Count(value=0)
    one = Count(value=1)
    rule = _compiled_rule()
    application = _application()
    ignored_counts = DiffFactCounts(
        same=zero,
        changed=zero,
        added=zero,
        removed=zero,
        ignored=one,
        incomparable=zero,
    )
    baseline_only_coverage = DiffCoverage(
        baseline_selected=one,
        candidate_selected=zero,
        baseline_derived=one,
        candidate_derived=zero,
        baseline_consumed=one,
        candidate_consumed=zero,
        baseline_resolved=one,
        candidate_resolved=zero,
        paired=one,
        compared=zero,
        ignored=one,
        not_evaluated=zero,
    )
    ignored = DiffFact(
        fact_id=FactId(value=UUID(int=863)),
        outcome=DiffFactOutcome.IGNORED,
        comparator=rule.pipeline.comparator.identity,
        baseline=_value_provenance(_location(864)),
    )
    assert (
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=865)),
            application=application,
            rule=rule,
            outcome=NotEvaluated(reason=DiffNotEvaluatedReason.ALL_IGNORED),
            counts=ignored_counts,
            coverage=baseline_only_coverage,
            evidence=DiffEvidence(retained=(ignored,), omitted=zero),
        ).coverage.baseline_resolved
        == one
    )

    bilateral_ignored = msgspec.structs.replace(
        ignored,
        fact_id=FactId(value=UUID(int=866)),
        candidate=_value_provenance(_location(867, candidate=True)),
    )
    bilateral_coverage = msgspec.structs.replace(
        baseline_only_coverage,
        candidate_selected=one,
        candidate_derived=one,
        candidate_consumed=one,
        candidate_resolved=one,
    )
    assert (
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=868)),
            application=application,
            rule=rule,
            outcome=NotEvaluated(reason=DiffNotEvaluatedReason.ALL_IGNORED),
            counts=ignored_counts,
            coverage=bilateral_coverage,
            evidence=DiffEvidence(retained=(bilateral_ignored,), omitted=zero),
        ).coverage.candidate_resolved
        == one
    )
    with pytest.raises(ValueError, match="retained ignored or incomparable"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=869)),
            application=application,
            rule=rule,
            outcome=NotEvaluated(reason=DiffNotEvaluatedReason.ALL_IGNORED),
            counts=ignored_counts,
            coverage=baseline_only_coverage,
            evidence=DiffEvidence(retained=(bilateral_ignored,), omitted=zero),
        )

    incomparable = DiffFact(
        fact_id=FactId(value=UUID(int=870)),
        outcome=DiffFactOutcome.INCOMPARABLE,
        comparator=rule.pipeline.comparator.identity,
        candidate=_value_provenance(_location(871, candidate=True)),
        diagnostic=DiagnosticCode(value="diff.incomparable-value"),
    )
    incomparable_counts = msgspec.structs.replace(
        ignored_counts,
        ignored=zero,
        incomparable=one,
    )
    candidate_only_coverage = msgspec.structs.replace(
        baseline_only_coverage,
        baseline_selected=zero,
        baseline_derived=zero,
        baseline_consumed=zero,
        baseline_resolved=zero,
        candidate_selected=one,
        candidate_derived=one,
        candidate_consumed=one,
        candidate_resolved=one,
        compared=one,
        ignored=zero,
    )
    assert (
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=872)),
            application=application,
            rule=rule,
            outcome=Incomplete(
                reason=DiffIncompleteReason(
                    side=DiffSide.CANDIDATE,
                    stage=DiffEvaluationStage.PAIRING,
                    cause=DiffIncompleteCause.PARTIAL,
                )
            ),
            counts=incomparable_counts,
            coverage=candidate_only_coverage,
            evidence=DiffEvidence(retained=(incomparable,), omitted=zero),
        ).counts.incomparable
        == one
    )
