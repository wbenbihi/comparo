"""Cover diff operation catalogs, directionality, and aggregate invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import CollectionMap
from comparo.core.domain.diffs.operations import DiffDigestAlgorithm
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DigestTransform
from comparo.core.domain.diffs.operations import JsonShapeArrayMode
from comparo.core.domain.diffs.operations import JsonShapeTransform
from comparo.core.domain.diffs.operations import LengthTransform
from comparo.core.domain.diffs.operations import LengthUnit
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffIncompleteReason
from comparo.core.domain.diffs.results import DiffNotEvaluatedReason
from comparo.core.domain.diffs.results import DiffPipelineIssue
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import GroupByReducer
from comparo.core.domain.diffs.strategies import KeyedPairing
from comparo.core.domain.diffs.strategies import ObjectFromEntriesReducer
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.diffs.strategies import TextPredicateComparator
from comparo.core.domain.diffs.strategies import TextPredicateRelation
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from tests.core.domain.diffs._invariant_support import _application
from tests.core.domain.diffs._invariant_support import _compiled_rule
from tests.core.domain.diffs._invariant_support import _evidence_exhaustion
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _location
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _plural_item_selector
from tests.core.domain.diffs._invariant_support import _value_provenance
from tests.core.domain.selectors.test_result_coverage import _failure


def test_diff_coverage_and_aggregate_precedence_never_hide_unresolved_work() -> None:
    with pytest.raises(ValueError, match="every paired"):
        DiffCoverage(
            baseline_selected=Count(value=2),
            candidate_selected=Count(value=2),
            baseline_derived=Count(value=2),
            candidate_derived=Count(value=2),
            baseline_consumed=Count(value=2),
            candidate_consumed=Count(value=2),
            baseline_resolved=Count(value=1),
            candidate_resolved=Count(value=1),
            paired=Count(value=2),
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
    coverage = DiffCoverage(
        baseline_selected=Count(value=2),
        candidate_selected=Count(value=2),
        baseline_derived=Count(value=2),
        candidate_derived=Count(value=2),
        baseline_consumed=Count(value=2),
        candidate_consumed=Count(value=2),
        baseline_resolved=Count(value=1),
        candidate_resolved=Count(value=1),
        paired=Count(value=2),
        compared=Count(value=1),
        ignored=Count(value=0),
        not_evaluated=Count(value=1),
    )
    with pytest.raises(ValueError, match=r"singular pairing|complete comparable coverage"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=970)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Equivalent(),
            counts=counts,
            coverage=coverage,
            evidence=DiffEvidence(
                retained=(), omitted=Count(value=1), exhaustion=_evidence_exhaustion()
            ),
        )


def test_diff_evidence_ids_and_outcome_buckets_cannot_contradict_counts() -> None:
    detail = DiffFact(
        fact_id=FactId(value=UUID(int=971)),
        outcome=DiffFactOutcome.SAME,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline=_value_provenance(_location(972)),
        candidate=_value_provenance(_location(973, candidate=True)),
    )
    with pytest.raises(ValueError, match="unique"):
        DiffEvidence(retained=(detail, detail), omitted=Count(value=0))
    with pytest.raises(ValueError, match="contradicts"):
        DiffRuleFact(
            fact_id=FactId(value=UUID(int=974)),
            application=_application(),
            rule=_compiled_rule(),
            outcome=Drifted(),
            counts=DiffFactCounts(
                same=Count(value=0),
                changed=Count(value=1),
                added=Count(value=0),
                removed=Count(value=0),
                ignored=Count(value=0),
                incomparable=Count(value=0),
            ),
            coverage=DiffCoverage(
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
            ),
            evidence=DiffEvidence(retained=(detail,), omitted=Count(value=0)),
        )


def test_diff_evidence_omission_requires_exact_evidence_exhaustion() -> None:
    with pytest.raises(TypeError, match="BudgetExceeded"):
        DiffEvidence(
            retained=(),
            omitted=Count(value=0),
            exhaustion=cast("BudgetExceeded", "bad"),
        )
    with pytest.raises(ValueError, match="exactly when facts were omitted"):
        DiffEvidence(retained=(), omitted=Count(value=1))
    with pytest.raises(ValueError, match="exactly when facts were omitted"):
        DiffEvidence(
            retained=(),
            omitted=Count(value=0),
            exhaustion=_evidence_exhaustion(),
        )

    wrong_stage = _evidence_exhaustion()
    msgspec.structs.force_setattr(wrong_stage.budget, "stage", BudgetStage.EVALUATION)
    with pytest.raises(ValueError, match="evidence-stage"):
        DiffEvidence(retained=(), omitted=Count(value=1), exhaustion=wrong_stage)

    wrong_resource = _evidence_exhaustion()
    msgspec.structs.force_setattr(wrong_resource.budget, "resource", BudgetResource.FACTS)
    with pytest.raises(ValueError, match="exact evidence budget"):
        DiffEvidence(retained=(), omitted=Count(value=1), exhaustion=wrong_resource)

    wrong_effect = _evidence_exhaustion()
    msgspec.structs.force_setattr(
        wrong_effect,
        "effect",
        BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
    )
    with pytest.raises(ValueError, match="exact evidence budget"):
        DiffEvidence(retained=(), omitted=Count(value=1), exhaustion=wrong_effect)


def test_diff_issue_and_incomplete_models_reject_untyped_or_misaligned_states() -> None:
    issue = DiffPipelineIssue(
        side=DiffSide.BASELINE,
        stage=DiffEvaluationStage.SELECTION,
        cause=DiffIncompleteCause.UNAVAILABLE,
        code=DiagnosticCode(value="diff.selection.unavailable"),
    )
    with pytest.raises(TypeError, match="side must be typed"):
        msgspec.structs.replace(issue, side=cast("DiffSide", "baseline"))
    with pytest.raises(TypeError, match="stage must be typed"):
        msgspec.structs.replace(issue, stage=cast("DiffEvaluationStage", "selection"))
    with pytest.raises(TypeError, match="cause must be typed"):
        msgspec.structs.replace(issue, cause=cast("DiffIncompleteCause", "unavailable"))
    with pytest.raises(TypeError, match="DiagnosticCode"):
        msgspec.structs.replace(issue, code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError, match="BudgetExceeded"):
        msgspec.structs.replace(issue, exhaustion=cast("object", "bad"))
    with pytest.raises(TypeError, match="view provenance"):
        msgspec.structs.replace(issue, view_failure=cast("object", "bad"))
    with pytest.raises(ValueError, match="exactly budget-exceeded"):
        msgspec.structs.replace(issue, exhaustion=_evidence_exhaustion())
    with pytest.raises(ValueError, match="exactly budget-exceeded"):
        msgspec.structs.replace(issue, cause=DiffIncompleteCause.BUDGET_EXCEEDED, code=None)
    assert msgspec.structs.replace(issue, view_failure=_failure()).view_failure is not None

    reason = DiffIncompleteReason(
        side=DiffSide.BASELINE,
        stage=DiffEvaluationStage.SELECTION,
        cause=DiffIncompleteCause.UNAVAILABLE,
    )
    for field in ("side", "stage", "cause"):
        with pytest.raises(TypeError, match="typed side, stage, and cause"):
            msgspec.structs.replace(reason, **{field: "bad"})
    incomplete = Incomplete(reason=reason, issues=(issue,))
    with pytest.raises(TypeError, match="DiffIncompleteReason"):
        msgspec.structs.replace(incomplete, reason=cast("DiffIncompleteReason", "bad"))
    with pytest.raises(TypeError, match="immutable typed tuple"):
        msgspec.structs.replace(incomplete, issues=cast("tuple[DiffPipelineIssue, ...]", [issue]))
    with pytest.raises(ValueError, match="first pipeline issue"):
        msgspec.structs.replace(
            incomplete,
            reason=msgspec.structs.replace(reason, side=DiffSide.CANDIDATE),
        )
    with pytest.raises(TypeError, match="DiagnosticCode"):
        DiffEvaluationError(code=cast("DiagnosticCode", "bad"))


def test_not_evaluated_reasons_require_reason_specific_zero_coverage() -> None:
    zero = Count(value=0)
    counts = DiffFactCounts(
        same=zero,
        changed=zero,
        added=zero,
        removed=zero,
        ignored=zero,
        incomparable=zero,
    )
    zero_coverage = DiffCoverage(
        baseline_selected=zero,
        candidate_selected=zero,
        baseline_derived=zero,
        candidate_derived=zero,
        baseline_consumed=zero,
        candidate_consumed=zero,
        baseline_resolved=zero,
        candidate_resolved=zero,
        paired=zero,
        compared=zero,
        ignored=zero,
        not_evaluated=zero,
    )
    false_applicability = DiffRuleFact(
        fact_id=FactId(value=UUID(int=975)),
        application=_application(),
        rule=_compiled_rule(DiffSeverity.INFO),
        outcome=NotEvaluated(reason=DiffNotEvaluatedReason.FALSE_APPLICABILITY),
        counts=counts,
        coverage=zero_coverage,
        evidence=DiffEvidence(retained=(), omitted=zero),
    )
    assert type(false_applicability.outcome) is NotEvaluated
    with pytest.raises(ValueError, match="zero pipeline"):
        msgspec.structs.replace(
            false_applicability,
            coverage=msgspec.structs.replace(
                zero_coverage,
                baseline_selected=Count(value=1),
            ),
        )

    with pytest.raises(ValueError, match="both selected"):
        msgspec.structs.replace(
            false_applicability,
            outcome=NotEvaluated(reason=DiffNotEvaluatedReason.EMPTY_SELECTION),
            coverage=msgspec.structs.replace(
                zero_coverage,
                baseline_selected=Count(value=1),
            ),
        )
    empty_derivation = msgspec.structs.replace(
        false_applicability,
        outcome=NotEvaluated(reason=DiffNotEvaluatedReason.EMPTY_DERIVATION),
        coverage=msgspec.structs.replace(
            zero_coverage,
            baseline_selected=Count(value=1),
        ),
    )
    assert empty_derivation.coverage.candidate_selected.value == 0
    with pytest.raises(ValueError, match="at least one selected"):
        msgspec.structs.replace(
            empty_derivation,
            coverage=zero_coverage,
        )
    with pytest.raises(ValueError, match="derived and resolved"):
        msgspec.structs.replace(
            empty_derivation,
            coverage=msgspec.structs.replace(
                zero_coverage,
                baseline_selected=Count(value=1),
                candidate_selected=Count(value=1),
                baseline_derived=Count(value=1),
            ),
        )


def test_new_transform_contracts_reject_untyped_static_configuration() -> None:
    with pytest.raises(TypeError, match="collection map"):
        CollectionMap(
            identity=_operation(DiffOperationFamily.TRANSFORM, "collection-map"),
            selector=cast("CompiledItemSelector", "bad"),
        )
    with pytest.raises(TypeError, match="collection filter"):
        CollectionFilter(
            identity=_operation(DiffOperationFamily.TRANSFORM, "collection-filter"),
            predicate=cast("CompiledItemPredicate", "bad"),
        )
    with pytest.raises(TypeError, match="LengthUnit"):
        LengthTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "length"),
            unit=cast("LengthUnit", "items"),
        )
    with pytest.raises(TypeError, match="array mode"):
        JsonShapeTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "json-shape"),
            arrays=cast("JsonShapeArrayMode", "positional"),
        )
    with pytest.raises(TypeError, match="digest transform"):
        DigestTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "digest"),
            algorithm=cast("DiffDigestAlgorithm", "sha256"),
        )


def test_reducer_and_text_strategy_fields_reject_every_ambiguous_mode() -> None:
    with pytest.raises(TypeError, match="separator"):
        Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "concatenate"),
            kind=ReducerKind.CONCATENATE,
            empty=EmptyInputMode.RETURN_EMPTY_TEXT,
            separator=cast("LiteralExpr", "bad"),
        )
    group = GroupByReducer(
        identity=_operation(DiffOperationFamily.REDUCER, "group-by"),
        key=_item_selector(),
        empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
        duplicate_keys=DuplicateKeyMode.COLLECT,
    )
    with pytest.raises(ValueError, match="statically singular"):
        msgspec.structs.replace(group, key=_plural_item_selector())
    with pytest.raises(TypeError, match="empty behavior"):
        msgspec.structs.replace(group, empty=cast("EmptyInputMode", "error"))
    with pytest.raises(ValueError, match="object-compatible"):
        msgspec.structs.replace(group, empty=EmptyInputMode.RETURN_ZERO)
    with pytest.raises(TypeError, match="duplicate behavior"):
        msgspec.structs.replace(
            group,
            duplicate_keys=cast("DuplicateKeyMode", "collect"),
        )

    object_reducer = ObjectFromEntriesReducer(
        identity=_operation(DiffOperationFamily.REDUCER, "object-from-entries"),
        key=_item_selector(),
        value=_item_selector(),
        empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
        duplicate_keys=DuplicateKeyMode.ERROR,
    )
    with pytest.raises(TypeError, match="CompiledItemSelector"):
        msgspec.structs.replace(
            object_reducer,
            value=cast("CompiledItemSelector", "bad"),
        )
    with pytest.raises(ValueError, match="must be singular"):
        msgspec.structs.replace(object_reducer, key=_plural_item_selector())
    with pytest.raises(TypeError, match="empty behavior"):
        msgspec.structs.replace(
            object_reducer,
            empty=cast("EmptyInputMode", "error"),
        )
    with pytest.raises(ValueError, match="object-compatible"):
        msgspec.structs.replace(object_reducer, empty=EmptyInputMode.RETURN_ZERO)
    with pytest.raises(TypeError, match="duplicate behavior"):
        msgspec.structs.replace(
            object_reducer,
            duplicate_keys=cast("DuplicateKeyMode", "error"),
        )

    keyed = KeyedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
        baseline_key=_item_selector(),
        candidate_key=_item_selector(),
        duplicate_keys=DuplicateKeyMode.ERROR,
    )
    with pytest.raises(ValueError, match="error or be incomparable"):
        msgspec.structs.replace(keyed, duplicate_keys=DuplicateKeyMode.KEEP_FIRST)
    comparator = TextPredicateComparator(
        identity=_operation(DiffOperationFamily.COMPARATOR, "text-predicate"),
        relation=TextPredicateRelation.CANDIDATE_CONTAINS_BASELINE,
    )
    with pytest.raises(TypeError, match="text predicate relation"):
        msgspec.structs.replace(
            comparator,
            relation=cast("TextPredicateRelation", "candidate-contains-baseline"),
        )
