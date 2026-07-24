"""Exercise static value/cardinality propagation across complete diff pipelines."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.diffs.contracts import derive_side_contract
from comparo.core.domain.diffs.contracts import require_pipeline_contract
from comparo.core.domain.diffs.operations import CaseMode
from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import CollectionMap
from comparo.core.domain.diffs.operations import DiffDigestAlgorithm
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.operations import DiffOperationIdentity
from comparo.core.domain.diffs.operations import DiffTransform
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
from comparo.core.domain.diffs.operations import TextNormalization
from comparo.core.domain.diffs.operations import TrimMode
from comparo.core.domain.diffs.operations import ValueTypeTransform
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.rules import SidePipeline
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CollectionComparisonKind
from comparo.core.domain.diffs.strategies import CompatibilityComparator
from comparo.core.domain.diffs.strategies import CompatibilityKind
from comparo.core.domain.diffs.strategies import DiffComparator
from comparo.core.domain.diffs.strategies import DiffReducer
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
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.items import CompiledItemPredicate
from comparo.core.domain.selectors.items import CompiledItemSelector
from comparo.core.domain.selectors.items import ItemEqualityPredicate
from comparo.core.domain.selectors.items import ItemPredicateIdentity
from comparo.core.domain.selectors.items import ItemPredicateKind
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.selection import compile_jsonpath


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="c" * 64))


def _operation(family: DiffOperationFamily, name: str) -> DiffOperationIdentity:
    return DiffOperationIdentity(
        namespace="comparo.core",
        family=family,
        name=name,
        version="1",
    )


def _contract(kind: ValueKind, cardinality: SelectorCardinality) -> ValueContract:
    return ValueContract(kinds=(kind,), cardinality=cardinality)


def _selector(
    kind: ValueKind,
    cardinality: SelectorCardinality = SelectorCardinality.EXACTLY_ONE,
) -> CompiledSelector:
    plural = cardinality is SelectorCardinality.ZERO_OR_MORE
    body_view = BodyViewKind.ENTITY_BYTES if kind is ValueKind.BYTES else None
    source = SelectorSource.RESPONSE_BODY if body_view is not None else SelectorSource.SCENARIO_STEP
    capabilities = SelectorCapabilities(
        cardinality=cardinality,
        access=SelectionAccess.INCREMENTAL,
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
    )
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=source,
        path=(compile_jsonpath("$[*]") if plural else NoPath()),
        capabilities=capabilities,
        output=_contract(kind, cardinality),
        body_view=body_view,
    )


def _item(
    input_kind: ValueKind,
    output_kind: ValueKind,
    cardinality: SelectorCardinality = SelectorCardinality.EXACTLY_ONE,
) -> CompiledItemSelector:
    if cardinality is SelectorCardinality.EXACTLY_ONE:
        capabilities = SelectorCapabilities(
            cardinality=cardinality,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        )
        path: SelectorPath
        path = NoPath() if input_kind is output_kind else compile_jsonpath("$")
    else:
        capabilities = SelectorCapabilities(
            cardinality=cardinality,
            access=SelectionAccess.INDEXED,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        )
        path = compile_jsonpath("$[::-1]")
    return CompiledItemSelector(
        fingerprint=_fingerprint(),
        input=_contract(input_kind, SelectorCardinality.EXACTLY_ONE),
        path=path,
        capabilities=capabilities,
        output=_contract(output_kind, cardinality),
    )


def _literal(value: int = 0) -> LiteralExpr:
    return LiteralExpr(
        value=JsonInteger(value=value),
        provenance=ValueProvenance(
            entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),),
        ),
    )


def _text_literal(value: str = ",") -> LiteralExpr:
    return LiteralExpr(
        value=JsonString(value=value),
        provenance=ValueProvenance(
            entries=(ProvenanceEntry(kind=ProvenanceKind.PROFILE),),
        ),
    )


def _side(kind: ValueKind, *transforms: DiffTransform) -> SidePipeline:
    return SidePipeline(
        selector=_selector(kind),
        transforms=transforms,
    )


def _ordered() -> OrderedPairing:
    return OrderedPairing(identity=_operation(DiffOperationFamily.PAIRING, "ordered"))


def _exact() -> ExactComparator:
    return ExactComparator(identity=_operation(DiffOperationFamily.COMPARATOR, "exact"))


def test_contract_entry_points_reject_values_outside_closed_stage_unions() -> None:
    text = _contract(ValueKind.STRING, SelectorCardinality.ZERO_OR_MORE)
    with pytest.raises(TypeError, match="selector output"):
        derive_side_contract(cast("ValueContract", "bad"), (), None)
    with pytest.raises(TypeError, match="transform"):
        derive_side_contract(text, (cast("DiffTransform", "bad"),), None)
    with pytest.raises(TypeError, match="reducer"):
        derive_side_contract(text, (), cast("DiffReducer", "bad"))
    with pytest.raises(TypeError, match="comparator"):
        require_pipeline_contract(
            text,
            text,
            _ordered(),
            cast("DiffComparator", "bad"),
        )


def test_concatenate_separator_and_supported_scalar_comparators_are_typed() -> None:
    invalid_separator = Reducer(
        identity=_operation(DiffOperationFamily.REDUCER, "concatenate"),
        kind=ReducerKind.CONCATENATE,
        empty=EmptyInputMode.RETURN_EMPTY_TEXT,
        separator=_literal(1),
    )
    with pytest.raises(ValueError, match="separator must resolve to text"):
        derive_side_contract(
            _contract(ValueKind.STRING, SelectorCardinality.ZERO_OR_MORE),
            (),
            invalid_separator,
        )
    number = _contract(ValueKind.NUMBER, SelectorCardinality.ZERO_OR_MORE)
    require_pipeline_contract(
        number,
        number,
        _ordered(),
        NumericToleranceComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
            absolute_tolerance=_literal(),
        ),
    )
    with pytest.raises(ValueError, match="tolerance must resolve to a number"):
        require_pipeline_contract(
            number,
            number,
            _ordered(),
            NumericToleranceComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
                absolute_tolerance=_text_literal("near"),
            ),
        )
    text = _contract(ValueKind.STRING, SelectorCardinality.ZERO_OR_MORE)
    require_pipeline_contract(
        text,
        text,
        _ordered(),
        TextPredicateComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "text-predicate"),
            relation=TextPredicateRelation.CANDIDATE_CONTAINS_BASELINE,
        ),
    )


def test_each_transform_derives_an_explicit_output_contract() -> None:
    text = TextNormalization(
        identity=_operation(DiffOperationFamily.TRANSFORM, "normalize-text"),
        trim=TrimMode.BOTH,
        newlines=NewlineMode.LF,
        case=CaseMode.PRESERVE,
    )
    assert _side(ValueKind.STRING, text).output_contract.kinds == (ValueKind.STRING,)
    project = Projection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "project"),
        pointers=(JsonPointer(value="/id"),),
    )
    exclude = Exclusion(
        identity=_operation(DiffOperationFamily.TRANSFORM, "exclude"),
        pointers=(JsonPointer(value="/createdAt"),),
    )
    assert _side(ValueKind.OBJECT, project, exclude).output_contract.kinds == (ValueKind.OBJECT,)
    for unit, kind in (
        (LengthUnit.UNICODE_SCALARS, ValueKind.STRING),
        (LengthUnit.BYTES, ValueKind.BYTES),
        (LengthUnit.ITEMS, ValueKind.ARRAY),
        (LengthUnit.MEMBERS, ValueKind.OBJECT),
    ):
        transform = LengthTransform(
            identity=_operation(DiffOperationFamily.TRANSFORM, "length"),
            unit=unit,
        )
        assert _side(kind, transform).output_contract.kinds == (ValueKind.INTEGER,)
    value_type = ValueTypeTransform(
        identity=_operation(DiffOperationFamily.TRANSFORM, "value-type"),
    )
    assert _side(ValueKind.BOOLEAN, value_type).output_contract.kinds == (ValueKind.STRING,)
    shape = JsonShapeTransform(
        identity=_operation(DiffOperationFamily.TRANSFORM, "json-shape"),
        arrays=JsonShapeArrayMode.UNION,
    )
    assert _side(ValueKind.ARRAY, shape).output_contract.kinds == (ValueKind.OBJECT,)
    digest = DigestTransform(
        identity=_operation(DiffOperationFamily.TRANSFORM, "digest"),
        algorithm=DiffDigestAlgorithm.SHA256,
    )
    assert _side(ValueKind.BYTES, digest).output_contract.kinds == (ValueKind.BYTES,)


def test_relative_map_filter_and_sort_use_current_item_contracts() -> None:
    relative = RelativeSelection(
        identity=_operation(DiffOperationFamily.TRANSFORM, "relative-select"),
        selector=_item(ValueKind.OBJECT, ValueKind.STRING),
    )
    assert _side(ValueKind.OBJECT, relative).output_contract.kinds == (ValueKind.STRING,)
    mapped = CollectionMap(
        identity=_operation(DiffOperationFamily.TRANSFORM, "collection-map"),
        selector=_item(
            ValueKind.ARRAY,
            ValueKind.STRING,
            SelectorCardinality.ZERO_OR_MORE,
        ),
    )
    assert _side(ValueKind.ARRAY, mapped).output_cardinality is SelectorCardinality.ZERO_OR_MORE
    predicate = CompiledItemPredicate(
        identity=ItemPredicateIdentity(
            namespace="comparo.core",
            kind=ItemPredicateKind.EQUALS,
            version="1",
        ),
        selector=_item(ValueKind.STRING, ValueKind.STRING),
        operator=ItemEqualityPredicate(expected=_text_literal("ready")),
    )
    filtered = CollectionFilter(
        identity=_operation(DiffOperationFamily.TRANSFORM, "collection-filter"),
        predicate=predicate,
    )
    assert _side(ValueKind.STRING, filtered).output_cardinality is SelectorCardinality.ZERO_OR_ONE
    sorted_values = SortBy(
        identity=_operation(DiffOperationFamily.TRANSFORM, "sort-by"),
        key=_item(ValueKind.OBJECT, ValueKind.STRING),
        direction=SortDirection.ASCENDING,
    )
    assert _side(ValueKind.OBJECT, sorted_values).output_contract.kinds == (ValueKind.OBJECT,)
    with pytest.raises(ValueError, match="input contract"):
        _side(ValueKind.INTEGER, relative)


@pytest.mark.parametrize(
    ("kind", "reducer_kind", "empty", "output"),
    [
        (ValueKind.STRING, ReducerKind.FIRST, EmptyInputMode.UNAVAILABLE, ValueKind.STRING),
        (ValueKind.STRING, ReducerKind.LAST, EmptyInputMode.ERROR, ValueKind.STRING),
        (ValueKind.STRING, ReducerKind.COUNT, EmptyInputMode.RETURN_ZERO, ValueKind.INTEGER),
        (ValueKind.INTEGER, ReducerKind.SUM, EmptyInputMode.RETURN_ZERO, ValueKind.INTEGER),
        (ValueKind.INTEGER, ReducerKind.MINIMUM, EmptyInputMode.ERROR, ValueKind.INTEGER),
        (ValueKind.INTEGER, ReducerKind.MAXIMUM, EmptyInputMode.ERROR, ValueKind.INTEGER),
        (
            ValueKind.STRING,
            ReducerKind.COLLECT,
            EmptyInputMode.RETURN_EMPTY_COLLECTION,
            ValueKind.ARRAY,
        ),
        (
            ValueKind.STRING,
            ReducerKind.SET,
            EmptyInputMode.RETURN_EMPTY_COLLECTION,
            ValueKind.ARRAY,
        ),
        (
            ValueKind.STRING,
            ReducerKind.MULTISET,
            EmptyInputMode.RETURN_EMPTY_COLLECTION,
            ValueKind.ARRAY,
        ),
        (
            ValueKind.STRING,
            ReducerKind.DISTINCT,
            EmptyInputMode.RETURN_EMPTY_COLLECTION,
            ValueKind.ARRAY,
        ),
    ],
)
def test_reducers_derive_scalar_output_contracts(
    kind: ValueKind,
    reducer_kind: ReducerKind,
    empty: EmptyInputMode,
    output: ValueKind,
) -> None:
    reducer = Reducer(
        identity=_operation(DiffOperationFamily.REDUCER, reducer_kind.value),
        kind=reducer_kind,
        empty=empty,
    )
    side = SidePipeline(selector=_selector(kind, SelectorCardinality.ZERO_OR_MORE), reducer=reducer)
    assert side.output_contract.kinds == (output,)
    assert side.output_cardinality is SelectorCardinality.EXACTLY_ONE


def test_concatenate_and_keyed_reducers_validate_item_contracts() -> None:
    concatenate = Reducer(
        identity=_operation(DiffOperationFamily.REDUCER, "concatenate"),
        kind=ReducerKind.CONCATENATE,
        empty=EmptyInputMode.RETURN_EMPTY_TEXT,
        separator=_text_literal(),
    )
    side = SidePipeline(
        selector=_selector(ValueKind.STRING, SelectorCardinality.ZERO_OR_MORE),
        reducer=concatenate,
    )
    assert side.output_contract.kinds == (ValueKind.STRING,)
    group = GroupByReducer(
        identity=_operation(DiffOperationFamily.REDUCER, "group-by"),
        key=_item(ValueKind.OBJECT, ValueKind.STRING),
        empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
        duplicate_keys=DuplicateKeyMode.COLLECT,
    )
    entries = ObjectFromEntriesReducer(
        identity=_operation(DiffOperationFamily.REDUCER, "object-from-entries"),
        key=_item(ValueKind.OBJECT, ValueKind.STRING),
        value=_item(ValueKind.OBJECT, ValueKind.INTEGER),
        empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
        duplicate_keys=DuplicateKeyMode.ERROR,
    )
    for reducer in (group, entries):
        output = SidePipeline(
            selector=_selector(ValueKind.OBJECT, SelectorCardinality.ZERO_OR_MORE),
            reducer=reducer,
        ).output_contract
        assert output.kinds == (ValueKind.OBJECT,)


def test_pairing_and_comparator_contracts_reject_impossible_pipelines() -> None:
    text = SidePipeline(selector=_selector(ValueKind.STRING, SelectorCardinality.ZERO_OR_MORE))
    number = SidePipeline(selector=_selector(ValueKind.INTEGER, SelectorCardinality.ZERO_OR_MORE))
    DiffPipeline(baseline=text, candidate=text, pairing=_ordered(), comparator=_exact())
    with pytest.raises(ValueError, match="never be equal"):
        DiffPipeline(baseline=text, candidate=number, pairing=_ordered(), comparator=_exact())
    with pytest.raises(ValueError, match="numeric comparator"):
        DiffPipeline(
            baseline=text,
            candidate=text,
            pairing=_ordered(),
            comparator=NumericToleranceComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
                absolute_tolerance=_literal(),
            ),
        )
    with pytest.raises(ValueError, match="text comparator"):
        DiffPipeline(
            baseline=number,
            candidate=number,
            pairing=_ordered(),
            comparator=TextPredicateComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "text-predicate"),
                relation=TextPredicateRelation.CANDIDATE_CONTAINS_BASELINE,
            ),
        )
    objects = SidePipeline(selector=_selector(ValueKind.OBJECT, SelectorCardinality.ZERO_OR_MORE))
    DiffPipeline(
        baseline=objects,
        candidate=objects,
        pairing=_ordered(),
        comparator=CollectionComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "ordered"),
            kind=CollectionComparisonKind.ORDERED,
        ),
    )
    DiffPipeline(
        baseline=text,
        candidate=number,
        pairing=_ordered(),
        comparator=IgnoreComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "ignore"),
        ),
    )


def test_compatibility_and_keyed_pairing_contracts_are_explicit() -> None:
    objects = SidePipeline(selector=_selector(ValueKind.OBJECT, SelectorCardinality.ZERO_OR_MORE))
    for kind in (CompatibilityKind.TYPE, CompatibilityKind.JSON_SHAPE, CompatibilityKind.SUBSET):
        DiffPipeline(
            baseline=objects,
            candidate=objects,
            pairing=_ordered(),
            comparator=CompatibilityComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, kind.value),
                kind=kind,
            ),
        )
    keyed = KeyedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
        baseline_key=_item(ValueKind.OBJECT, ValueKind.STRING),
        candidate_key=_item(ValueKind.OBJECT, ValueKind.STRING),
        duplicate_keys=DuplicateKeyMode.ERROR,
    )
    DiffPipeline(baseline=objects, candidate=objects, pairing=keyed, comparator=_exact())
    bad_keyed = KeyedPairing(
        identity=_operation(DiffOperationFamily.PAIRING, "keyed"),
        baseline_key=_item(ValueKind.OBJECT, ValueKind.STRING),
        candidate_key=_item(ValueKind.OBJECT, ValueKind.INTEGER),
        duplicate_keys=DuplicateKeyMode.ERROR,
    )
    with pytest.raises(ValueError, match="cannot match"):
        DiffPipeline(baseline=objects, candidate=objects, pairing=bad_keyed, comparator=_exact())


def test_singular_pairing_and_negative_tolerance_fail_at_construction() -> None:
    plural = SidePipeline(selector=_selector(ValueKind.STRING, SelectorCardinality.ZERO_OR_MORE))
    with pytest.raises(ValueError, match="singular pairing"):
        DiffPipeline(
            baseline=plural,
            candidate=plural,
            pairing=SingularPairing(
                identity=_operation(DiffOperationFamily.PAIRING, "singular"),
            ),
            comparator=_exact(),
        )
    with pytest.raises(ValueError, match="cannot be negative"):
        NumericToleranceComparator(
            identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
            absolute_tolerance=_literal(-1),
        )
