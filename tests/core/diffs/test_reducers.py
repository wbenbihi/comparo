"""Exercise reducer identities, failures, provenance, and exact collection laws."""

from __future__ import annotations

from decimal import ROUND_FLOOR
from decimal import Decimal
from decimal import Inexact
from decimal import Rounded
from decimal import localcontext
from typing import cast
from uuid import UUID

import msgspec
import pytest

import comparo.core.diffs.reducers as diff_reducers
from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs import PreparedExpression
from comparo.core.diffs import PreparedOperandError
from comparo.core.diffs import PreparedOperandReady
from comparo.core.diffs import PreparedOperandUnavailable
from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.reducers import _group_by
from comparo.core.diffs.reducers import _json_values
from comparo.core.diffs.reducers import _object_from_entries
from comparo.core.diffs.reducers import _prepared_text
from comparo.core.diffs.reducers import _unique
from comparo.core.diffs.reducers import apply_reducer
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.strategies import DiffReducer
from comparo.core.domain.diffs.strategies import DuplicateKeyMode
from comparo.core.domain.diffs.strategies import EmptyInputMode
from comparo.core.domain.diffs.strategies import GroupByReducer
from comparo.core.domain.diffs.strategies import ObjectFromEntriesReducer
from comparo.core.domain.diffs.strategies import Reducer
from comparo.core.domain.diffs.strategies import ReducerKind
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import meter
from tests.core.diffs.factories import rule
from tests.core.domain.diffs._invariant_support import _item_selector
from tests.core.domain.diffs._invariant_support import _literal
from tests.core.domain.diffs._invariant_support import _operation

_SIDE = DiffSide.BASELINE
_SELECTOR = rule().pipeline.baseline.selector


def _reducer(
    kind: ReducerKind,
    *,
    empty: EmptyInputMode = EmptyInputMode.ERROR,
    separator: LiteralExpr | None = None,
) -> Reducer:
    return Reducer(
        identity=_operation(DiffOperationFamily.REDUCER, kind.value),
        kind=kind,
        empty=empty,
        separator=separator,
    )


def _group(mode: DuplicateKeyMode = DuplicateKeyMode.COLLECT) -> GroupByReducer:
    return GroupByReducer(
        identity=_operation(DiffOperationFamily.REDUCER, "group-by"),
        key=_item_selector(),
        empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
        duplicate_keys=mode,
    )


def _entries(mode: DuplicateKeyMode) -> ObjectFromEntriesReducer:
    return ObjectFromEntriesReducer(
        identity=_operation(DiffOperationFamily.REDUCER, "object-from-entries"),
        key=_item_selector(),
        value=_item_selector(),
        empty=EmptyInputMode.RETURN_EMPTY_COLLECTION,
        duplicate_keys=mode,
    )


def _prepared(expression: LiteralExpr, result: object) -> PreparedDiffInputs:
    prepared = PreparedExpression(
        expression=expression,
        result=PreparedOperandReady(value=JsonString(value=",")),
    )
    msgspec.structs.force_setattr(prepared, "result", result)
    return PreparedDiffInputs(
        rule_fingerprint=rule().fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        baseline_expressions=(prepared,),
    )


def _apply(
    values: tuple[DerivedValue, ...],
    reducer: DiffReducer | None,
    prepared: PreparedDiffInputs | None = None,
    diff_meter: DiffMeter | None = None,
) -> tuple[DerivedValue, ...]:
    return apply_reducer(
        values,
        reducer,
        inputs(rule()) if prepared is None else prepared,
        meter() if diff_meter is None else diff_meter,
        _SIDE,
        _SELECTOR,
    )


def test_none_reducer_returns_the_already_derived_values_unchanged() -> None:
    values = (derived(JsonInteger(value=1)),)
    assert _apply(values, None) is values


def test_empty_modes_are_explicit_and_emit_typed_identity_values() -> None:
    assert _apply((), _reducer(ReducerKind.COUNT, empty=EmptyInputMode.UNAVAILABLE)) == ()
    with pytest.raises(DiffHaltError) as caught:
        _apply((), _reducer(ReducerKind.COUNT))
    assert caught.value.code is not None
    assert caught.value.side is _SIDE

    cases = (
        (_reducer(ReducerKind.COUNT, empty=EmptyInputMode.RETURN_ZERO), JsonInteger),
        (
            _reducer(
                ReducerKind.CONCATENATE,
                empty=EmptyInputMode.RETURN_EMPTY_TEXT,
                separator=_literal(","),
            ),
            JsonString,
        ),
        (
            _reducer(ReducerKind.COLLECT, empty=EmptyInputMode.RETURN_EMPTY_COLLECTION),
            JsonArray,
        ),
        (_group(), JsonObject),
    )
    for reducer, expected_type in cases:
        result = _apply((), reducer)
        assert len(result) == 1
        assert type(result[0].value) is expected_type
        assert result[0].empty_reducer_identity
        assert result[0].origins == ()


def test_empty_identity_honors_derived_value_and_byte_budgets() -> None:
    reducer = _reducer(ReducerKind.COUNT, empty=EmptyInputMode.RETURN_ZERO)
    for diff_meter in (meter(derived_limit=0), meter(derived_bytes=0)):
        with pytest.raises(DiffHaltError) as caught:
            _apply((), reducer, diff_meter=diff_meter)
        assert caught.value.exhaustion is not None
        assert caught.value.side is _SIDE


def test_scalar_reducers_first_last_and_count_preserve_expected_value() -> None:
    values = (
        derived(JsonInteger(value=3), index=301),
        derived(JsonInteger(value=1), index=302),
    )
    cases = (
        (ReducerKind.FIRST, JsonInteger(value=3)),
        (ReducerKind.LAST, JsonInteger(value=1)),
        (ReducerKind.COUNT, JsonInteger(value=2)),
    )
    for kind, expected in cases:
        reduced = _apply(values, _reducer(kind))[0]
        assert reduced.value == expected
        assert reduced.operations[-1].name == kind.value


def test_concatenate_resolves_side_operand_and_rejects_non_text_values() -> None:
    separator = _literal("|")
    reducer = _reducer(ReducerKind.CONCATENATE, separator=separator)
    prepared = _prepared(separator, PreparedOperandReady(value=JsonString(value="|")))
    result = _apply(
        (derived(JsonString(value="a")), derived(JsonString(value="b"))),
        reducer,
        prepared,
    )[0]
    assert result.value == JsonString(value="a|b")
    with pytest.raises(DiffHaltError) as caught:
        _apply((derived(JsonInteger(value=1)),), reducer, prepared)
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.concatenate-type"


def test_concatenate_taints_public_sources_only_when_sensitive_separator_participates() -> None:
    separator = _literal("|")
    reducer = _reducer(ReducerKind.CONCATENATE, separator=separator)
    origin = TaintOriginId(value="diff-separator-secret")
    prepared = _prepared(
        separator,
        PreparedOperandReady(value=SensitiveValue(JsonString(value="|"), origins=(origin,))),
    )

    joined = _apply(
        (derived(JsonString(value="a")), derived(JsonString(value="b"))),
        reducer,
        prepared,
    )[0]
    assert type(joined.value) is SensitiveValue
    assert joined.value.origins == (origin,)

    unjoined = _apply((derived(JsonString(value="a")),), reducer, prepared)[0]
    assert unjoined.value == JsonString(value="a")


def test_concatenate_preflights_exact_tiny_derived_budget() -> None:
    separator = _literal("|")
    reducer = _reducer(ReducerKind.CONCATENATE, separator=separator)
    prepared = _prepared(separator, PreparedOperandReady(value=JsonString(value="|")))
    with pytest.raises(DiffHaltError) as caught:
        _apply(
            (derived(JsonString(value="a")), derived(JsonString(value="b"))),
            reducer,
            prepared,
            meter(derived_bytes=4),
        )
    assert caught.value.exhaustion is not None
    assert caught.value.partial_derived == ()
    assert caught.value.side is _SIDE


def test_sum_is_exact_under_hostile_decimal_context() -> None:
    reducer = _reducer(ReducerKind.SUM)
    values = (
        derived(JsonDecimal(value=Decimal("0.1"))),
        derived(JsonDecimal(value=Decimal("0.2"))),
    )
    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_FLOOR
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = _apply(values, reducer)[0]
    assert result.value == JsonDecimal(value=Decimal("0.3"))


def test_sum_preflights_exponent_alignment_before_large_exact_arithmetic() -> None:
    values = (
        derived(JsonInteger(value=1)),
        derived(JsonDecimal(value=Decimal("1E-100000"))),
    )
    with pytest.raises(DiffHaltError) as caught:
        _apply(values, _reducer(ReducerKind.SUM), diff_meter=meter(derived_bytes=100))
    assert caught.value.exhaustion is not None


def test_sum_rejects_non_numeric_values_with_specific_diagnostic() -> None:
    with pytest.raises(DiffHaltError) as caught:
        _apply((derived(JsonString(value="not-a-number")),), _reducer(ReducerKind.SUM))
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.sum-type"


def test_minimum_and_maximum_update_or_keep_the_chosen_value() -> None:
    values = (
        derived(JsonInteger(value=2)),
        derived(JsonInteger(value=1)),
        derived(JsonInteger(value=3)),
    )
    assert _apply(values, _reducer(ReducerKind.MINIMUM))[0].value == JsonInteger(value=1)
    assert _apply(values, _reducer(ReducerKind.MAXIMUM))[0].value == JsonInteger(value=3)
    with pytest.raises(DiffHaltError) as caught:
        _apply(
            (derived(JsonInteger(value=1)), derived(JsonString(value="x"))),
            _reducer(ReducerKind.MINIMUM),
        )
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.ordering-type"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ReducerKind.COLLECT, (2, 1, 2)),
        (ReducerKind.DISTINCT, (2, 1)),
        (ReducerKind.SET, (1, 2)),
        (ReducerKind.MULTISET, (1, 2, 2)),
    ],
)
def test_collection_reducers_make_order_and_multiplicity_explicit(
    kind: ReducerKind,
    expected: tuple[int, ...],
) -> None:
    values = tuple(
        derived(JsonInteger(value=value), index=310 + index)
        for index, value in enumerate((2, 1, 2))
    )
    result = cast("JsonArray", _apply(values, _reducer(kind))[0].value)
    assert tuple(cast("JsonInteger", item).value for item in result.items) == expected


def test_collection_reducers_reject_non_json_selected_values() -> None:
    with pytest.raises(DiffHaltError) as caught:
        _apply((derived(ByteCount(value=1)),), _reducer(ReducerKind.COLLECT))
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.collection-type"


def test_group_by_collects_duplicate_string_keys_and_rejects_invalid_keys() -> None:
    result = cast(
        "JsonObject",
        _apply(
            (derived(JsonString(value="a")), derived(JsonString(value="a"))),
            _group(),
        )[0].value,
    )
    assert len(result.members) == 1
    assert type(result.members[0].value) is JsonArray
    assert len(result.members[0].value.items) == 2
    with pytest.raises(DiffHaltError) as caught:
        _apply((derived(JsonInteger(value=1)),), _group())
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.group-key"
    with pytest.raises(DiffHaltError) as duplicate:
        _apply(
            (derived(JsonString(value="a")), derived(JsonString(value="a"))),
            _group(DuplicateKeyMode.ERROR),
        )
    assert duplicate.value.code is not None
    assert duplicate.value.code.value == "diff.reducer.duplicate-key"

    with pytest.raises(DiffHaltError) as non_json:
        _group_by((derived(ByteCount(value=1)),), _group(), meter())
    assert non_json.value.code is not None
    assert non_json.value.code.value == "diff.reducer.group-value-type"


def test_object_from_entries_duplicate_modes_are_explicit() -> None:
    values = (derived(JsonString(value="a")), derived(JsonString(value="a")))
    with pytest.raises(DiffHaltError) as caught:
        _apply(values, _entries(DuplicateKeyMode.ERROR))
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.duplicate-key"
    for mode in (DuplicateKeyMode.KEEP_FIRST, DuplicateKeyMode.KEEP_LAST):
        result = cast("JsonObject", _apply(values, _entries(mode))[0].value)
        assert result.members == (JsonObjectMember(name="a", value=JsonString(value="a")),)


def test_object_from_entries_rejects_non_string_keys() -> None:
    with pytest.raises(DiffHaltError) as caught:
        _apply((derived(JsonInteger(value=1)),), _entries(DuplicateKeyMode.ERROR))
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.entry-cardinality"


def test_object_from_entries_rejects_non_json_selected_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def selected(
        source: DerivedValue,
        selector: object,
        operation: object,
        diff_meter: DiffMeter,
    ) -> tuple[DerivedValue, ...]:
        nonlocal calls
        del source, selector, operation, diff_meter
        calls += 1
        return (
            derived(JsonString(value="key"))
            if calls == 1
            else derived(BytesValue(value=b"not-json")),
        )

    monkeypatch.setattr(diff_reducers, "select_item", selected)
    with pytest.raises(DiffHaltError) as caught:
        _object_from_entries(
            (derived(JsonString(value="source")),),
            _entries(DuplicateKeyMode.ERROR),
            meter(),
        )
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.reducer.entry-value-type"


def test_reducer_dispatch_and_ordinary_dispatch_reject_forged_open_variants() -> None:
    with pytest.raises(TypeError, match="unsupported diff reducer"):
        _apply((derived(JsonInteger(value=1)),), cast("Reducer", object()))
    forged = msgspec.structs.replace(_reducer(ReducerKind.COUNT))
    msgspec.structs.force_setattr(forged, "kind", object())
    with pytest.raises(TypeError, match="unsupported ordinary reducer"):
        _apply((derived(JsonInteger(value=1)),), forged)


def test_finish_failure_is_bound_to_side_and_discards_partial_reducer_output() -> None:
    with pytest.raises(DiffHaltError) as caught:
        _apply(
            (derived(JsonInteger(value=1)),),
            _reducer(ReducerKind.COUNT),
            diff_meter=meter(derived_limit=0),
        )
    assert caught.value.exhaustion is not None
    assert caught.value.partial_derived == ()
    assert caught.value.side is _SIDE


def test_separator_terminal_states_and_type_are_preserved() -> None:
    expression = _literal(",")
    terminals = (
        PreparedOperandUnavailable(code=DiagnosticCode(value="separator.missing")),
        PreparedOperandError(code=DiagnosticCode(value="separator.error")),
        PreparedOperandReady(value=JsonInteger(value=1)),
    )
    for result in terminals:
        with pytest.raises(DiffHaltError):
            _prepared_text(_prepared(expression, result), expression, _SIDE)
    with pytest.raises(TypeError, match="unsupported prepared diff operand"):
        _prepared_text(_prepared(expression, object()), expression, _SIDE)


def test_json_helpers_preserve_first_occurrence_order() -> None:
    values = (JsonInteger(value=2), JsonInteger(value=2), JsonInteger(value=1))
    assert _unique(values, meter()) == (values[0], values[2])
    assert _json_values((derived(values[0]),)) == (values[0],)


def test_reducer_output_preserves_sensitive_taint() -> None:
    origin = TaintOriginId(value="diff-reducer-secret")
    source = derived(JsonInteger(value=1))
    msgspec.structs.force_setattr(
        source,
        "value",
        SensitiveValue(JsonInteger(value=1), origins=(origin,)),
    )
    result = _apply((source,), _reducer(ReducerKind.COUNT))[0]
    assert type(result.value) is SensitiveValue
