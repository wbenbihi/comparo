"""Cover diff operation catalogs, directionality, and aggregate invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

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
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffNotEvaluatedReason
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
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
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _location
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _plural_item_selector


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
            evidence=DiffEvidence(retained=(), omitted=Count(value=1)),
        )


def test_diff_evidence_ids_and_outcome_buckets_cannot_contradict_counts() -> None:
    detail = DiffFact(
        fact_id=FactId(value=UUID(int=971)),
        outcome=DiffFactOutcome.SAME,
        comparator=_operation(DiffOperationFamily.COMPARATOR, "exact"),
        baseline_location=_location(972),
        candidate_location=_location(973, candidate=True),
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

    empty_selection = msgspec.structs.replace(
        false_applicability,
        outcome=NotEvaluated(reason=DiffNotEvaluatedReason.EMPTY_SELECTION),
        coverage=msgspec.structs.replace(
            zero_coverage,
            baseline_selected=Count(value=1),
        ),
    )
    assert empty_selection.coverage.candidate_selected.value == 0
    with pytest.raises(ValueError, match="one empty selected side"):
        msgspec.structs.replace(
            empty_selection,
            coverage=msgspec.structs.replace(
                zero_coverage,
                baseline_selected=Count(value=1),
                candidate_selected=Count(value=1),
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
