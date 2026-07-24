"""Pure typed reducers over one independently derived diff side."""

from __future__ import annotations

from typing import cast

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.exact_numbers import sum_decimals
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.item_selection import select_item
from comparo.core.diffs.operands import resolve_operand
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import merged_origins
from comparo.core.diffs.value_support import canonical_key
from comparo.core.diffs.value_support import contain
from comparo.core.diffs.value_support import json_text_size
from comparo.core.diffs.value_support import merge_taint_origins
from comparo.core.diffs.value_support import require_derived_capacity
from comparo.core.diffs.value_support import require_sort_work
from comparo.core.diffs.value_support import require_work
from comparo.core.diffs.value_support import reveal
from comparo.core.diffs.value_support import value_size
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.strategies import DiffReducer
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import GroupByReducer
from comparo.core.domain.diffs.strategies import ObjectFromEntriesReducer
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value
from comparo.core.domain.values import json_number
from comparo.core.evaluation import numeric_value
from comparo.core.evaluation import semantic_compare


def apply_reducer(
    values: tuple[DerivedValue, ...],
    reducer: DiffReducer | None,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
    side: DiffSide,
    selector: CompiledSelector,
) -> tuple[DerivedValue, ...]:
    """Reduce one side or return the already-charged transform output unchanged."""
    if reducer is None:
        return values
    try:
        if not values:
            return _empty(reducer, inputs, meter, side, selector)
        if type(reducer) is Reducer:
            return (_ordinary(values, reducer, inputs, meter, side),)
        if type(reducer) is GroupByReducer:
            return (_group_by(values, reducer, meter),)
        if type(reducer) is ObjectFromEntriesReducer:
            return (_object_from_entries(values, reducer, meter),)
        raise TypeError("unsupported diff reducer")
    except DiffHaltError as halted:
        halted.partial_derived = ()
        halted.side = side
        raise


def _empty(
    reducer: DiffReducer,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
    side: DiffSide,
    selector: CompiledSelector,
) -> tuple[DerivedValue, ...]:
    mode = reducer.empty
    if mode is EmptyInputMode.UNAVAILABLE:
        return ()
    if mode is EmptyInputMode.ERROR:
        raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.empty"))
    del inputs, side
    if mode is EmptyInputMode.RETURN_ZERO:
        result: SelectedValue = JsonInteger(value=0)
    elif mode is EmptyInputMode.RETURN_EMPTY_TEXT:
        result = JsonString(value="")
    elif mode is EmptyInputMode.RETURN_EMPTY_COLLECTION:
        result = JsonArray(items=()) if type(reducer) is Reducer else JsonObject(members=())
    else:  # pragma: no cover - closed by reducer domain validation
        raise TypeError("unsupported empty reducer identity")
    size = value_size(result, meter)
    if (failure := meter.charge_derived(size)) is not None:
        raise DiffHaltError(exhaustion=failure)
    return (
        DerivedValue(
            value=result,
            selector=selector,
            origins=(),
            operations=(reducer.identity,),
            empty_reducer_identity=True,
        ),
    )


def _ordinary(  # noqa: C901
    values: tuple[DerivedValue, ...],
    reducer: Reducer,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
    side: DiffSide,
) -> DerivedValue:
    if reducer.kind is ReducerKind.FIRST:
        return _finish(reveal(values[0].value), values, reducer, meter)
    if reducer.kind is ReducerKind.LAST:
        return _finish(reveal(values[-1].value), values, reducer, meter)
    if reducer.kind is ReducerKind.COUNT:
        return _finish(JsonInteger(value=len(values)), values, reducer, meter)
    if reducer.kind is ReducerKind.CONCATENATE:
        separator, separator_taint = _prepared_text(
            inputs, cast("ValueExpression", reducer.separator), side
        )
        parts = []
        size = 2 + json_text_size(separator, meter, repetitions=max(0, len(values) - 1))
        for value in values:
            require_work(meter)
            raw = reveal(value.value)
            if type(raw) is not JsonString:
                raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.concatenate-type"))
            parts.append(raw.value)
            size += json_text_size(raw.value, meter)
        if (failure := meter.derived_exhaustion(size)) is not None:
            raise DiffHaltError(exhaustion=failure)
        return _finish(
            JsonString(value=separator.join(parts)),
            values,
            reducer,
            meter,
            measured_size=size,
            extra_taint=separator_taint if len(values) > 1 else (),
        )
    if reducer.kind is ReducerKind.SUM:
        _require_sum_capacity(values, meter)
        numbers = []
        try:
            for value in values:
                require_work(meter)
                numbers.append(numeric_value(reveal(value.value)))
        except TypeError as error:
            raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.sum-type")) from error
        total = sum_decimals(tuple(numbers), meter)
        return _finish(json_number(total), values, reducer, meter)
    if reducer.kind in {ReducerKind.MINIMUM, ReducerKind.MAXIMUM}:
        chosen = values[0]
        for value in values[1:]:
            require_work(meter)
            relation = semantic_compare(reveal(value.value), reveal(chosen.value))
            if relation is None:
                raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.ordering-type"))
            if (reducer.kind is ReducerKind.MINIMUM and relation < 0) or (
                reducer.kind is ReducerKind.MAXIMUM and relation > 0
            ):
                chosen = value
        return _finish(reveal(chosen.value), values, reducer, meter)
    _require_collection_capacity(values, meter)
    json_values = _json_values(values)
    if reducer.kind is ReducerKind.COLLECT:
        output = json_values
    elif reducer.kind is ReducerKind.DISTINCT:
        output = _unique(json_values, meter)
    elif reducer.kind is ReducerKind.SET:
        require_sort_work(meter, len(json_values))
        output = tuple(
            sorted(_unique(json_values, meter), key=lambda item: canonical_key(item, meter))
        )
    elif reducer.kind is ReducerKind.MULTISET:
        require_sort_work(meter, len(json_values))
        output = tuple(sorted(json_values, key=lambda item: canonical_key(item, meter)))
    else:
        raise TypeError("unsupported ordinary reducer")
    return _finish(JsonArray(items=output), values, reducer, meter)


def _group_by(
    values: tuple[DerivedValue, ...],
    reducer: GroupByReducer,
    meter: DiffMeter,
) -> DerivedValue:
    _require_group_capacity(values, meter)
    groups: dict[str, list[JsonValue]] = {}
    for value in values:
        raw = reveal(value.value)
        if not is_json_value(raw):
            raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.group-value-type"))
        selected = select_item(value, reducer.key, reducer.identity, meter)
        if len(selected) != 1 or type(reveal(selected[0].value)) is not JsonString:
            raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.group-key"))
        key = cast("JsonString", reveal(selected[0].value)).value
        if key in groups and reducer.duplicate_keys is DuplicateKeyMode.ERROR:
            raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.duplicate-key"))
        groups.setdefault(key, []).append(raw)
    members = tuple(
        JsonObjectMember(name=key, value=JsonArray(items=tuple(group)))
        for key, group in groups.items()
    )
    return _finish(JsonObject(members=members), values, reducer, meter)


def _object_from_entries(
    values: tuple[DerivedValue, ...],
    reducer: ObjectFromEntriesReducer,
    meter: DiffMeter,
) -> DerivedValue:
    _require_group_capacity(values, meter)
    entries: dict[str, JsonValue] = {}
    for source in values:
        keys = select_item(source, reducer.key, reducer.identity, meter)
        selected = select_item(source, reducer.value, reducer.identity, meter)
        if len(keys) != 1 or type(reveal(keys[0].value)) is not JsonString or len(selected) != 1:
            raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.entry-cardinality"))
        key = cast("JsonString", reveal(keys[0].value)).value
        raw = reveal(selected[0].value)
        if not is_json_value(raw):
            raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.entry-value-type"))
        if key in entries:
            if reducer.duplicate_keys is DuplicateKeyMode.ERROR:
                raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.duplicate-key"))
            if reducer.duplicate_keys is DuplicateKeyMode.KEEP_FIRST:
                continue
        entries[key] = raw
    result = JsonObject(
        members=tuple(JsonObjectMember(name=key, value=value) for key, value in entries.items())
    )
    return _finish(result, values, reducer, meter)


def _finish(
    result: SelectedValue,
    sources: tuple[DerivedValue, ...],
    reducer: DiffReducer,
    meter: DiffMeter,
    *,
    measured_size: int | None = None,
    extra_taint: tuple[TaintOriginId, ...] = (),
) -> DerivedValue:
    for source in sources:
        require_work(meter, 1 + len(source.origins))
    size = value_size(result, meter) if measured_size is None else measured_size
    if (failure := meter.charge_derived(size)) is not None:
        raise DiffHaltError(exhaustion=failure)
    combined_taint = merge_taint_origins(
        meter, *(source.taint_origins for source in sources), extra_taint
    )
    return DerivedValue(
        value=contain(result, combined_taint),
        selector=sources[0].selector,
        origins=merged_origins(*sources),
        operations=(*sources[0].operations, reducer.identity),
    )


def _prepared_text(
    inputs: PreparedDiffInputs,
    expression: ValueExpression,
    side: DiffSide,
) -> tuple[str, tuple[TaintOriginId, ...]]:
    result = resolve_operand(inputs, expression, side)
    raw = result.value
    if type(raw) is not JsonString:
        raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.separator-type"))
    return raw.value, result.taint_origins


def _json_values(values: tuple[DerivedValue, ...]) -> tuple[JsonValue, ...]:
    raw = tuple(reveal(value.value) for value in values)
    if any(not is_json_value(item) for item in raw):
        raise DiffHaltError(code=DiagnosticCode(value="diff.reducer.collection-type"))
    return cast("tuple[JsonValue, ...]", raw)


def _unique(values: tuple[JsonValue, ...], meter: DiffMeter) -> tuple[JsonValue, ...]:
    seen = set()
    output = []
    for value in values:
        key = canonical_key(value, meter)
        if key not in seen:
            seen.add(key)
            output.append(value)
    return tuple(output)


def _require_collection_capacity(
    values: tuple[DerivedValue, ...],
    meter: DiffMeter,
) -> None:
    size = 2 + max(0, len(values) - 1)
    for value in values:
        size += value_size(reveal(value.value), meter)
    require_derived_capacity(meter, size)


def _require_sum_capacity(
    values: tuple[DerivedValue, ...],
    meter: DiffMeter,
) -> None:
    minimum_exponent = 0
    maximum_adjusted = 0
    found = False
    for value in values:
        raw = reveal(value.value)
        measured = value_size(raw, meter)
        if type(raw) is JsonInteger:
            exponent = 0
            digit_count = measured - int(raw.value < 0)
        elif type(raw) is JsonDecimal:
            _sign, digits, raw_exponent = raw.value.as_tuple()
            exponent = cast("int", raw_exponent)
            digit_count = max(1, len(digits))
        else:
            continue
        minimum_exponent = min(minimum_exponent, exponent)
        maximum_adjusted = max(maximum_adjusted, digit_count + exponent - 1)
        found = True
    span = maximum_adjusted - min(0, minimum_exponent) + 1 if found else 1
    size = span + max(1, len(values).bit_length()) + 3
    require_derived_capacity(meter, size)


def _require_group_capacity(
    values: tuple[DerivedValue, ...],
    meter: DiffMeter,
) -> None:
    size = 2 + max(0, len(values) - 1)
    for value in values:
        source_size = value_size(reveal(value.value), meter)
        size += 8 + 2 * source_size
    require_derived_capacity(meter, size)


__all__ = ("apply_reducer",)
