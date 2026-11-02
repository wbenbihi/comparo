"""Static value/cardinality propagation for compiled diff pipelines."""

from __future__ import annotations

from typing import TypeGuard
from typing import cast

from comparo.core.domain.diffs.operations import CollectionFilter
from comparo.core.domain.diffs.operations import CollectionMap
from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.operations import DigestTransform
from comparo.core.domain.diffs.operations import Exclusion
from comparo.core.domain.diffs.operations import JsonShapeTransform
from comparo.core.domain.diffs.operations import LengthTransform
from comparo.core.domain.diffs.operations import LengthUnit
from comparo.core.domain.diffs.operations import Projection
from comparo.core.domain.diffs.operations import RelativeSelection
from comparo.core.domain.diffs.operations import SortBy
from comparo.core.domain.diffs.operations import StreamProjection
from comparo.core.domain.diffs.operations import StreamSemanticSource
from comparo.core.domain.diffs.operations import TextNormalization
from comparo.core.domain.diffs.operations import ValueTypeTransform
from comparo.core.domain.diffs.strategies import CollectionComparator
from comparo.core.domain.diffs.strategies import CompatibilityComparator
from comparo.core.domain.diffs.strategies import CompatibilityKind
from comparo.core.domain.diffs.strategies import DiffComparator
from comparo.core.domain.diffs.strategies import DiffPairing
from comparo.core.domain.diffs.strategies import DiffReducer
from comparo.core.domain.diffs.strategies import ExactComparator
from comparo.core.domain.diffs.strategies import GroupByReducer
from comparo.core.domain.diffs.strategies import IgnoreComparator
from comparo.core.domain.diffs.strategies import KeyedPairing
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.diffs.strategies import ObjectFromEntriesReducer
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.diffs.strategies import SingularPairing
from comparo.core.domain.diffs.strategies import TextPredicateComparator
from comparo.core.domain.expression_analysis import expression_dependencies
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.selectors.contracts import JSON_VALUE_KINDS
from comparo.core.domain.selectors.contracts import NUMERIC_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ORDERED_VALUE_KINDS
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.contracts import contract_accepts
from comparo.core.domain.selectors.contracts import contract_can_feed
from comparo.core.domain.selectors.contracts import contracts_overlap
from comparo.core.domain.selectors.contracts import expression_accepts
from comparo.core.domain.selectors.contracts import scalar_contract
from comparo.core.domain.selectors.contracts import with_cardinality
from comparo.core.domain.selectors.sources import SelectorCardinality

_JSON_CONTAINERS = frozenset({ValueKind.ARRAY, ValueKind.OBJECT})
_JSON_ARRAYS = frozenset({ValueKind.ARRAY})
_KEY_KINDS = frozenset(
    {
        ValueKind.BOOLEAN,
        ValueKind.INTEGER,
        ValueKind.NUMBER,
        ValueKind.STRING,
    }
)
_COMPARISON_SHARED_SCOPES = frozenset(
    {ValueScopeKind.RUN, ValueScopeKind.CELL, ValueScopeKind.TRIAL}
)


def derive_side_contract(
    selected: ValueContract,
    transforms: tuple[DiffTransform, ...],
    reducer: DiffReducer | None,
) -> ValueContract:
    """Validate and derive one complete side output contract in authored order."""
    if type(selected) is not ValueContract:
        raise TypeError("side selector output must be a ValueContract")
    current = selected
    for transform in transforms:
        current = _derive_transform(current, transform)
    if reducer is not None:
        current = _derive_reducer(current, reducer)
    return current


def require_pipeline_contract(
    baseline: ValueContract,
    candidate: ValueContract,
    pairing: DiffPairing,
    comparator: DiffComparator,
) -> None:
    """Reject pairer or comparator combinations incompatible with side outputs."""
    if type(pairing) is SingularPairing and (
        baseline.cardinality is SelectorCardinality.ZERO_OR_MORE
        or candidate.cardinality is SelectorCardinality.ZERO_OR_MORE
    ):
        raise ValueError("singular pairing requires singular or reduced side outputs")
    if type(pairing) is KeyedPairing:
        _require_item_input(baseline, pairing.baseline_key.input, "baseline pairing key")
        _require_item_input(candidate, pairing.candidate_key.input, "candidate pairing key")
        _require_kinds(pairing.baseline_key.output, _KEY_KINDS, "baseline pairing key")
        _require_kinds(pairing.candidate_key.output, _KEY_KINDS, "candidate pairing key")
        if not contracts_overlap(pairing.baseline_key.output, pairing.candidate_key.output):
            raise ValueError("keyed pairing key contracts cannot match")
    _require_comparator(baseline, candidate, comparator)


def _derive_transform(  # noqa: C901 -- closed transform dispatch remains explicit
    current: ValueContract,
    transform: DiffTransform,
) -> ValueContract:
    if type(transform) is StreamProjection:
        return _derive_stream_transform(transform)
    if _is_item_selection_transform(transform):
        _require_item_input(current, transform.selector.input, "relative selector")
        cardinality = _compose_cardinality(
            current.cardinality,
            transform.selector.output.cardinality,
        )
        return with_cardinality(transform.selector.output, cardinality)
    if type(transform) is CollectionFilter:
        _require_item_input(current, transform.predicate.selector.input, "filter predicate")
        return with_cardinality(
            current,
            _compose_cardinality(current.cardinality, SelectorCardinality.ZERO_OR_ONE),
        )
    if type(transform) in {Projection, Exclusion}:
        _require_kinds(current, _JSON_CONTAINERS, "JSON pointer transform")
        return current
    if type(transform) is TextNormalization:
        _require_kinds(current, frozenset({ValueKind.STRING}), "text normalization")
        return current
    if type(transform) is LengthTransform:
        accepted = {
            LengthUnit.UNICODE_SCALARS: frozenset({ValueKind.STRING}),
            LengthUnit.BYTES: frozenset({ValueKind.BYTES}),
            LengthUnit.ITEMS: frozenset({ValueKind.ARRAY}),
            LengthUnit.MEMBERS: frozenset({ValueKind.OBJECT}),
        }[transform.unit]
        _require_kinds(current, accepted, "length transform")
        return ValueContract(
            kinds=(ValueKind.INTEGER,),
            cardinality=current.cardinality,
        )
    if type(transform) is ValueTypeTransform:
        return ValueContract(
            kinds=(ValueKind.STRING,),
            cardinality=current.cardinality,
        )
    if type(transform) is JsonShapeTransform:
        _require_kinds(current, JSON_VALUE_KINDS, "JSON shape transform")
        return ValueContract(
            kinds=(ValueKind.OBJECT,),
            cardinality=current.cardinality,
        )
    if type(transform) is DigestTransform:
        _require_kinds(
            current,
            frozenset({ValueKind.BYTES, ValueKind.STRING}),
            "digest transform",
        )
        return ValueContract(
            kinds=(ValueKind.BYTES,),
            cardinality=current.cardinality,
        )
    if type(transform) is SortBy:
        _require_item_input(current, transform.key.input, "sort key")
        _require_kinds(transform.key.output, _KEY_KINDS, "sort key")
        return current
    raise TypeError("unsupported diff transform contract")


def _derive_stream_transform(transform: StreamProjection) -> ValueContract:
    item_contract = _stream_item_contract(transform.source)
    _require_item_input(item_contract, transform.selector.input, "stream projection")
    return with_cardinality(transform.selector.output, SelectorCardinality.ZERO_OR_MORE)


def _stream_item_contract(source: StreamSemanticSource) -> ValueContract:
    if source is StreamSemanticSource.SSE_EVENT:
        return scalar_contract(ValueKind.OBJECT)
    if source is StreamSemanticSource.SSE_DATA:
        return scalar_contract(ValueKind.STRING)
    return scalar_contract(ValueKind.UNKNOWN)


def _derive_reducer(current: ValueContract, reducer: DiffReducer) -> ValueContract:
    if type(reducer) is Reducer:
        if reducer.kind in {ReducerKind.FIRST, ReducerKind.LAST}:
            return with_cardinality(current, SelectorCardinality.EXACTLY_ONE)
        if reducer.kind is ReducerKind.COUNT:
            return scalar_contract(ValueKind.INTEGER)
        if reducer.kind is ReducerKind.CONCATENATE:
            _require_kinds(current, frozenset({ValueKind.STRING}), "concatenate reducer")
            separator = cast("ValueExpression", reducer.separator)
            if not expression_accepts(separator, frozenset({ValueKind.STRING})):
                raise ValueError("concatenate separator must resolve to text")
            return scalar_contract(ValueKind.STRING)
        if reducer.kind is ReducerKind.SUM:
            _require_kinds(current, NUMERIC_VALUE_KINDS, "sum reducer")
            return scalar_contract(*current.kinds)
        if reducer.kind in {ReducerKind.MINIMUM, ReducerKind.MAXIMUM}:
            _require_kinds(current, ORDERED_VALUE_KINDS, "ordered reducer")
            _require_compatible_ordering_family(current)
            return scalar_contract(*current.kinds)
        return scalar_contract(ValueKind.ARRAY)
    if type(reducer) is GroupByReducer:
        _require_item_input(current, reducer.key.input, "group-by key")
        _require_kinds(reducer.key.output, frozenset({ValueKind.STRING}), "group-by key")
        return scalar_contract(ValueKind.OBJECT)
    if type(reducer) is ObjectFromEntriesReducer:
        _require_item_input(current, reducer.key.input, "object key")
        _require_item_input(current, reducer.value.input, "object value")
        _require_kinds(reducer.key.output, frozenset({ValueKind.STRING}), "object key")
        return scalar_contract(ValueKind.OBJECT)
    raise TypeError("unsupported diff reducer contract")


def _require_compatible_ordering_family(contract: ValueContract) -> None:
    if ValueKind.UNKNOWN in contract.kinds:
        return
    kinds = frozenset(contract.kinds)
    if len(kinds) > 1 and not kinds <= NUMERIC_VALUE_KINDS:
        raise ValueError("ordered reducer requires one compatible ordering family")


def _require_comparator(
    baseline: ValueContract,
    candidate: ValueContract,
    comparator: DiffComparator,
) -> None:
    if type(comparator) is IgnoreComparator:
        return
    if type(comparator) is ExactComparator:
        if not contracts_overlap(baseline, candidate):
            raise ValueError("exact comparator side contracts can never be equal")
        return
    if type(comparator) is NumericToleranceComparator:
        _require_both(baseline, candidate, NUMERIC_VALUE_KINDS, "numeric comparator")
        _require_numeric_tolerances(comparator)
        return
    if type(comparator) is TextPredicateComparator:
        _require_both(
            baseline,
            candidate,
            frozenset({ValueKind.STRING}),
            "text comparator",
        )
        return
    if type(comparator) is CollectionComparator:
        _require_both(baseline, candidate, _JSON_ARRAYS, "collection comparator")
        return
    if type(comparator) is CompatibilityComparator:
        if comparator.kind is CompatibilityKind.TYPE:
            return
        accepted = (
            JSON_VALUE_KINDS
            if comparator.kind is CompatibilityKind.JSON_SHAPE
            else _JSON_CONTAINERS
        )
        _require_both(baseline, candidate, accepted, "compatibility comparator")
        return
    raise TypeError("unsupported diff comparator contract")


def _require_numeric_tolerances(comparator: NumericToleranceComparator) -> None:
    for tolerance in (
        comparator.absolute_tolerance,
        comparator.relative_tolerance,
    ):
        if tolerance is not None and not expression_accepts(
            tolerance,
            NUMERIC_VALUE_KINDS,
        ):
            raise ValueError("numeric comparator tolerance must resolve to a number")
        if tolerance is not None and any(
            dependency.available_at not in _COMPARISON_SHARED_SCOPES
            for dependency in expression_dependencies(tolerance)
        ):
            raise ValueError("numeric comparator tolerance cannot depend on a side-specific scope")


def _require_both(
    baseline: ValueContract,
    candidate: ValueContract,
    accepted: frozenset[ValueKind],
    stage: str,
) -> None:
    _require_kinds(baseline, accepted, stage)
    _require_kinds(candidate, accepted, stage)


def _require_kinds(
    contract: ValueContract,
    accepted: frozenset[ValueKind],
    stage: str,
) -> None:
    if not contract_accepts(contract, accepted):
        raise ValueError(f"{stage} cannot consume the current value contract")


def _require_item_input(
    current: ValueContract,
    item_input: ValueContract,
    stage: str,
) -> None:
    if not contract_can_feed(
        with_cardinality(current, SelectorCardinality.EXACTLY_ONE),
        item_input,
    ):
        raise ValueError(f"{stage} input contract does not accept current values")


def _compose_cardinality(
    outer: SelectorCardinality,
    inner: SelectorCardinality,
) -> SelectorCardinality:
    if SelectorCardinality.ZERO_OR_MORE in {outer, inner}:
        return SelectorCardinality.ZERO_OR_MORE
    if SelectorCardinality.ZERO_OR_ONE in {outer, inner}:
        return SelectorCardinality.ZERO_OR_ONE
    return SelectorCardinality.EXACTLY_ONE


def _is_item_selection_transform(
    transform: DiffTransform,
) -> TypeGuard[RelativeSelection | CollectionMap]:
    return type(transform) in {RelativeSelection, CollectionMap}
