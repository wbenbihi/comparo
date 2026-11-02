"""Adversarial JSON selection checks for depth, work, and payload bounds."""

from __future__ import annotations

from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.selection import compile_jsonpath
from comparo.core.selection.json_evaluator import JsonEvaluationReady
from comparo.core.selection.json_evaluator import evaluate_json
from tests.core.selection.json_evaluator_factories import selection_meter


def test_descendant_traversal_is_iterative_for_deep_adversarial_json() -> None:
    value: JsonValue = JsonInteger(value=1)
    for _depth in range(5_000):
        value = JsonArray(items=(value,))

    result = evaluate_json(value, compile_jsonpath('$..["absent"]'), selection_meter())

    assert type(result) is JsonEvaluationReady
    assert result.matches == ()


def test_wide_descendant_traversal_stops_at_work_budget() -> None:
    value = JsonArray(items=tuple(JsonInteger(value=index) for index in range(10_000)))

    result = evaluate_json(value, compile_jsonpath("$..*"), selection_meter(work=20))

    assert type(result) is SelectionBudgetExceeded


def test_long_selected_string_stops_during_sizing_before_result_byte_charge() -> None:
    meter = selection_meter(work=20)

    result = evaluate_json(
        JsonString(value="s" * 100_000),
        compile_jsonpath("$"),
        meter,
    )

    assert type(result) is SelectionBudgetExceeded
    assert meter.budgets.result_bytes.usage.consumed == ByteCount(value=0)


def test_wide_selected_root_sizes_incrementally_under_tiny_work_budget() -> None:
    value = JsonArray(items=tuple(JsonInteger(value=index) for index in range(100_000)))

    result = evaluate_json(value, compile_jsonpath("$"), selection_meter(work=2))

    assert type(result) is SelectionBudgetExceeded


def test_huge_integer_size_avoids_interpreter_decimal_conversion_limit() -> None:
    result = evaluate_json(
        JsonInteger(value=10**10_000),
        compile_jsonpath("$"),
        selection_meter(work=20),
    )

    assert type(result) is SelectionBudgetExceeded


def test_huge_numeric_comparison_stops_before_native_cross_type_work() -> None:
    value = JsonArray(items=(JsonInteger(value=10**10_000),))

    result = evaluate_json(value, compile_jsonpath("$[?@ > 1.5]"), selection_meter(work=20))

    assert type(result) is SelectionBudgetExceeded
