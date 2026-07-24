"""Cover diff operation catalogs, directionality, and aggregate invariants."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.diffs.operations import CaseMode
from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import CollectionMap
from comparo.core.domain.diffs.operations import DiffDigestAlgorithm
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import DigestTransform
from comparo.core.domain.diffs.operations import Exclusion
from comparo.core.domain.diffs.operations import JsonShapeArrayMode
from comparo.core.domain.diffs.operations import JsonShapeTransform
from comparo.core.domain.diffs.operations import LengthTransform
from comparo.core.domain.diffs.operations import LengthUnit
from comparo.core.domain.diffs.operations import NewlineMode
from comparo.core.domain.diffs.operations import Projection
from comparo.core.domain.diffs.operations import RelativeSelection
from comparo.core.domain.diffs.operations import SortBy
from comparo.core.domain.diffs.operations import SortDirection
from comparo.core.domain.diffs.operations import StreamProjection
from comparo.core.domain.diffs.operations import StreamSemanticSource
from comparo.core.domain.diffs.operations import TextNormalization
from comparo.core.domain.diffs.operations import TrimMode
from comparo.core.domain.diffs.operations import ValueTypeTransform
from comparo.core.domain.diffs.operations import is_diff_transform
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CollectionComparisonKind
from comparo.core.domain.diffs.strategies import CompatibilityComparator
from comparo.core.domain.diffs.strategies import CompatibilityKind
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import GroupByReducer
from comparo.core.domain.diffs.strategies import IgnoreComparator
from comparo.core.domain.diffs.strategies import KeyedPairing
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import ObjectFromEntriesReducer
from comparo.core.domain.diffs.strategies import OrderedPairing
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.diffs.strategies import TextPredicateComparator
from comparo.core.domain.diffs.strategies import TextPredicateRelation
from comparo.core.domain.diffs.strategies import UnorderedPairing
from comparo.core.domain.diffs.strategies import UnorderedPairingKind
from comparo.core.domain.diffs.strategies import is_diff_comparator
from comparo.core.domain.diffs.strategies import is_diff_pairing
from comparo.core.domain.diffs.strategies import is_diff_reducer
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.paths import JsonPointer
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _literal
from tests.core.domain.diffs._invariant_support import _operation
from tests.core.domain.diffs._invariant_support import _predicate


def test_every_transform_family_is_typed_and_identity_checked() -> None:
    transforms = (
        RelativeSelection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "relative-select"),
            selector=_item_selector(),
        ),
        Projection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "project"),
            pointers=(JsonPointer(value="/name"),),
        ),
        Exclusion(
            identity=_operation(DiffOperationFamily.TRANSFORM, "exclude"),
            pointers=(JsonPointer(value="/id"),),
        ),
        TextNormalization(
            identity=_operation(DiffOperationFamily.TRANSFORM, "normalize-text"),
            trim=TrimMode.BOTH,
            newlines=NewlineMode.LF,
            case=CaseMode.CASEFOLD_UNICODE,
        ),
        CollectionMap(
            identity=_operation(DiffOperationFamily.TRANSFORM, "collection-map"),
            selector=_item_selector(),
        ),
        CollectionFilter(
            identity=_operation(DiffOperationFamily.TRANSFORM, "collection-filter"),
            predicate=_predicate(),
        ),
        LengthTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "length"),
            unit=LengthUnit.ITEMS,
        ),
        ValueTypeTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "value-type"),
        ),
        JsonShapeTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "json-shape"),
            arrays=JsonShapeArrayMode.POSITIONAL,
        ),
        DigestTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "digest"),
            algorithm=DiffDigestAlgorithm.SHA256,
        ),
        StreamProjection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
            source=StreamSemanticSource.SSE_JSON_DATA,
            selector=_item_selector(),
        ),
        SortBy(
            identity=_operation(DiffOperationFamily.TRANSFORM, "sort-by"),
            key=_item_selector(),
            direction=SortDirection.ASCENDING,
        ),
    )
    assert all(is_diff_transform(transform) for transform in transforms)
    assert not is_diff_transform("exclude")
    with pytest.raises(ValueError, match="does not match"):
        Projection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "exclude"),
            pointers=(JsonPointer(value="/id"),),
        )


def test_reducer_pairing_and_comparator_closed_unions() -> None:
    reducers = (
        Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "count"),
            kind=ReducerKind.COUNT,
            empty=EmptyInputMode.RETURN_ZERO,
        ),
        Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "concatenate"),
            kind=ReducerKind.CONCATENATE,
            empty=EmptyInputMode.RETURN_EMPTY_TEXT,
            separator=_literal(),
        ),
        GroupByReducer(
            identity=_operation(DiffOperationFamily.REDUCER, "group-by"),
            key=_item_selector(),
            empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
            duplicate_keys=DuplicateKeyMode.COLLECT,
        ),
        ObjectFromEntriesReducer(
            identity=_operation(DiffOperationFamily.REDUCER, "object-from-entries"),
            key=_item_selector(),
            value=_item_selector(),
            empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
            duplicate_keys=DuplicateKeyMode.ERROR,
        ),
    )
    pairings = (
        SingularPairing(identity=_operation(DiffOperationFamily.PAIRING, "singular")),
        OrderedPairing(identity=_operation(DiffOperationFamily.PAIRING, "ordered")),
        KeyedPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
            baseline_key=_item_selector(),
            candidate_key=_item_selector(),
            duplicate_keys=DuplicateKeyMode.INCOMPARABLE,
        ),
        UnorderedPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "set"),
            kind=UnorderedPairingKind.SET,
        ),
    )
    comparators = (
        ExactComparator(identity=_operation(DiffOperationFamily.COMPARATOR, "exact")),
        IgnoreComparator(identity=_operation(DiffOperationFamily.COMPARATOR, "ignore")),
        CompatibilityComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "json-shape"),
            kind=CompatibilityKind.JSON_SHAPE,
        ),
        NumericToleranceComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
            absolute_tolerance=_literal("0.1"),
        ),
        CollectionComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "ordered"),
            kind=CollectionComparisonKind.ORDERED,
        ),
        TextPredicateComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "text-predicate"),
            relation=TextPredicateRelation.CANDIDATE_CONTAINS_BASELINE,
        ),
    )
    assert all(is_diff_reducer(reducer) for reducer in reducers)
    assert all(is_diff_pairing(pairing) for pairing in pairings)
    assert all(is_diff_comparator(comparator) for comparator in comparators)
    assert not is_diff_reducer("count")
    assert not is_diff_pairing("ordered")
    assert not is_diff_comparator("exact")


def test_operation_strategies_reject_untyped_or_mismatched_fields() -> None:
    with pytest.raises(ValueError, match="namespace"):
        DiffOperationIdentity(
            namespace="Invalid Namespace",
            family=DiffOperationFamily.TRANSFORM,
            name="project",
            version="1",
        )
    with pytest.raises(TypeError, match="family"):
        DiffOperationIdentity(
            namespace="comparo.core",
            family=cast("DiffOperationFamily", "transform"),
            name="project",
            version="1",
        )
    with pytest.raises(ValueError, match="positive"):
        DiffOperationIdentity(
            namespace="comparo.core",
            family=DiffOperationFamily.TRANSFORM,
            name="project",
            version="0",
        )
    with pytest.raises(ValueError, match="decimal"):
        DiffOperationIdentity(
            namespace="comparo.core",
            family=DiffOperationFamily.TRANSFORM,
            name="project",
            version="one",
        )
    with pytest.raises(TypeError, match="CompiledItemSelector"):
        RelativeSelection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "relative-select"),
            selector=cast("CompiledItemSelector", "bad"),
        )
    with pytest.raises(ValueError, match="non-empty"):
        Projection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "project"),
            pointers=(),
        )
    with pytest.raises(TypeError, match="JsonPointer"):
        Exclusion(
            identity=_operation(DiffOperationFamily.TRANSFORM, "exclude"),
            pointers=(cast("JsonPointer", "bad"),),
        )
    with pytest.raises(ValueError, match="unique"):
        Exclusion(
            identity=_operation(DiffOperationFamily.TRANSFORM, "exclude"),
            pointers=(JsonPointer(value="/id"), JsonPointer(value="/id")),
        )
    with pytest.raises(TypeError, match="TrimMode"):
        TextNormalization(
            identity=_operation(DiffOperationFamily.TRANSFORM, "normalize-text"),
            trim=cast("TrimMode", "both"),
            newlines=NewlineMode.LF,
            case=CaseMode.PRESERVE,
        )
    with pytest.raises(TypeError, match="newline"):
        TextNormalization(
            identity=_operation(DiffOperationFamily.TRANSFORM, "normalize-text"),
            trim=TrimMode.NONE,
            newlines=cast("NewlineMode", "lf"),
            case=CaseMode.PRESERVE,
        )
    with pytest.raises(TypeError, match="case"):
        TextNormalization(
            identity=_operation(DiffOperationFamily.TRANSFORM, "normalize-text"),
            trim=TrimMode.NONE,
            newlines=NewlineMode.PRESERVE,
            case=cast("CaseMode", "preserve"),
        )
    with pytest.raises(TypeError, match="stream source"):
        StreamProjection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
            source=cast("StreamSemanticSource", "sse-data"),
            selector=_item_selector(),
        )
    with pytest.raises(TypeError, match="stream projection selector"):
        StreamProjection(
            identity=_operation(DiffOperationFamily.TRANSFORM, "stream-project"),
            source=StreamSemanticSource.SSE_DATA,
            selector=cast("CompiledItemSelector", "bad"),
        )
    with pytest.raises(TypeError, match="sort key"):
        SortBy(
            identity=_operation(DiffOperationFamily.TRANSFORM, "sort-by"),
            key=cast("CompiledItemSelector", "bad"),
            direction=SortDirection.ASCENDING,
        )
    with pytest.raises(TypeError, match="sort direction"):
        SortBy(
            identity=_operation(DiffOperationFamily.TRANSFORM, "sort-by"),
            key=_item_selector(),
            direction=cast("SortDirection", "ascending"),
        )
    with pytest.raises(TypeError, match="ReducerKind"):
        Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "count"),
            kind=cast("ReducerKind", "count"),
            empty=EmptyInputMode.ERROR,
        )
    with pytest.raises(TypeError, match="empty mode"):
        Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "count"),
            kind=ReducerKind.COUNT,
            empty=cast("EmptyInputMode", "error"),
        )
    with pytest.raises(ValueError, match="separator"):
        Reducer(
            identity=_operation(DiffOperationFamily.REDUCER, "concatenate"),
            kind=ReducerKind.CONCATENATE,
            empty=EmptyInputMode.ERROR,
        )
    with pytest.raises(ValueError, match="at least one"):
        NumericToleranceComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
        )
    with pytest.raises(TypeError, match="ValueExpression"):
        NumericToleranceComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
            absolute_tolerance=cast("LiteralExpr", "bad"),
        )
    with pytest.raises(TypeError, match="group-by key"):
        GroupByReducer(
            identity=_operation(DiffOperationFamily.REDUCER, "group-by"),
            key=cast("CompiledItemSelector", "bad"),
            empty=EmptyInputMode.ERROR,
            duplicate_keys=DuplicateKeyMode.COLLECT,
        )
    with pytest.raises(TypeError, match="DuplicateKeyMode"):
        KeyedPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
            baseline_key=_item_selector(),
            candidate_key=_item_selector(),
            duplicate_keys=cast("DuplicateKeyMode", "error"),
        )
    with pytest.raises(TypeError, match="baseline key"):
        KeyedPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
            baseline_key=cast("CompiledItemSelector", "bad"),
            candidate_key=_item_selector(),
            duplicate_keys=DuplicateKeyMode.INCOMPARABLE,
        )
    with pytest.raises(TypeError, match="candidate key"):
        KeyedPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
            baseline_key=_item_selector(),
            candidate_key=cast("CompiledItemSelector", "bad"),
            duplicate_keys=DuplicateKeyMode.INCOMPARABLE,
        )
    with pytest.raises(TypeError, match="unordered pairing kind"):
        UnorderedPairing(
            identity=_operation(DiffOperationFamily.PAIRING, "set"),
            kind=cast("UnorderedPairingKind", "set"),
        )
    with pytest.raises(TypeError, match="compatibility kind"):
        CompatibilityComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "type"),
            kind=cast("CompatibilityKind", "type"),
        )
    with pytest.raises(TypeError, match="collection comparison kind"):
        CollectionComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "ordered"),
            kind=cast("CollectionComparisonKind", "ordered"),
        )
    with pytest.raises(TypeError, match="diff identity"):
        ExactComparator(identity=cast("DiffOperationIdentity", "bad"))
    with pytest.raises(ValueError, match="wrong operation family"):
        ExactComparator(identity=_operation(DiffOperationFamily.PAIRING, "exact"))
