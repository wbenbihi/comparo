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
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.sources import SelectorCardinality
from tests.core.domain.diffs._invariant_support import _application
from tests.core.domain.diffs._invariant_support import _compiled_rule
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _location
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _plural_item_selector
from tests.core.domain.diffs._invariant_support import _plural_selector


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
            evidence=DiffEvidence(retained=(), omitted=Count(value=counts.total)),
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
        evidence=DiffEvidence(retained=(), omitted=Count(value=1)),
    )
    assert type(fact.outcome) is NotEvaluated


def test_incomparable_fact_requires_safe_diagnostic() -> None:
    code = DiagnosticCode(value="diff.keys-duplicate")
    fact = DiffFact(
        fact_id=FactId(value=UUID(int=73)),
        outcome=DiffFactOutcome.INCOMPARABLE,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline_location=_location(74),
        candidate_location=_location(75),
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
        baseline_location=_location(78),
        candidate_location=_location(79),
    )
    with pytest.raises(TypeError, match="typed ID and outcome"):
        msgspec.structs.replace(valid, fact_id=cast("FactId", "bad"))
    with pytest.raises(TypeError, match="typed ID and outcome"):
        msgspec.structs.replace(valid, outcome=cast("DiffFactOutcome", "same"))
    with pytest.raises(TypeError, match="location"):
        msgspec.structs.replace(
            valid,
            baseline_location=cast("ExchangeLocation", "bad"),
        )
    with pytest.raises(TypeError, match="diagnostic"):
        msgspec.structs.replace(valid, diagnostic=cast("DiagnosticCode", "bad"))
    with pytest.raises(ValueError, match="both locations"):
        msgspec.structs.replace(valid, baseline_location=None)
    with pytest.raises(ValueError, match="at least one location"):
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


def test_diff_rule_fact_rejects_untyped_and_inconsistent_aggregate_data() -> None:
    detail = DiffFact(
        fact_id=FactId(value=UUID(int=81)),
        outcome=DiffFactOutcome.SAME,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline_location=_location(82),
        candidate_location=_location(83),
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
            ),
            evidence=DiffEvidence(retained=(), omitted=Count(value=1)),
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
