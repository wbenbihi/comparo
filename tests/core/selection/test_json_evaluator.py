"""Exercise pure RFC 6901/9535 JSON evaluation and deterministic accounting."""

from __future__ import annotations

from decimal import Decimal

from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath
from comparo.core.selection.json_evaluator import JsonEvaluationReady
from comparo.core.selection.json_evaluator import evaluate_json
from comparo.core.selection.value_size import json_value_size
from tests.core.selection.json_evaluator_factories import json_object
from tests.core.selection.json_evaluator_factories import selection_meter


def test_json_pointer_decodes_tokens_in_rfc_order_and_treats_absence_as_empty() -> None:
    value = json_object(
        **{
            "a/b": json_object(
                **{"~key": JsonArray(items=(JsonString(value="zero"), JsonString(value="one")))}
            )
        }
    )

    selected = evaluate_json(value, compile_json_pointer("/a~1b/~0key/1"), selection_meter())
    leading_zero = evaluate_json(
        value,
        compile_json_pointer("/a~1b/~0key/01"),
        selection_meter(),
    )

    assert type(selected) is JsonEvaluationReady
    assert selected.matches[0].value == JsonString(value="one")
    assert selected.matches[0].pointer.value == "/a~1b/~0key/1"
    assert type(leading_zero) is JsonEvaluationReady
    assert leading_zero.matches == ()


def test_jsonpath_preserves_source_order_and_duplicate_occurrences() -> None:
    value = json_object(
        a=JsonInteger(value=1),
        branch=json_object(a=JsonInteger(value=2)),
    )

    duplicated = evaluate_json(value, compile_jsonpath('$["a","a"]'), selection_meter())
    descended = evaluate_json(value, compile_jsonpath('$..["a"]'), selection_meter())

    assert type(duplicated) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in duplicated.matches) == ("/a", "/a")
    assert type(descended) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in descended.matches) == ("/a", "/branch/a")


def test_array_indexes_and_slices_follow_rfc_negative_and_zero_step_rules() -> None:
    value = JsonArray(items=tuple(JsonInteger(value=index) for index in range(6)))

    reversed_result = evaluate_json(value, compile_jsonpath("$[::-2]"), selection_meter())
    negative_index = evaluate_json(value, compile_jsonpath("$[-2]"), selection_meter())
    zero_step = evaluate_json(value, compile_jsonpath("$[::0]"), selection_meter())

    assert type(reversed_result) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in reversed_result.matches) == ("/5", "/3", "/1")
    assert type(negative_index) is JsonEvaluationReady
    assert negative_index.matches[0].value == JsonInteger(value=4)
    assert type(zero_step) is JsonEvaluationReady
    assert zero_step.matches == ()


def test_filter_existence_is_distinct_from_contained_false_and_missing() -> None:
    value = JsonArray(
        items=(
            json_object(present=JsonBoolean(value=False)),
            json_object(other=JsonNull()),
        )
    )

    present = evaluate_json(value, compile_jsonpath("$[?@.present]"), selection_meter())
    missing_equal = evaluate_json(
        value,
        compile_jsonpath("$[?@.left == @.right]"),
        selection_meter(),
    )
    missing_less_or_equal = evaluate_json(
        value,
        compile_jsonpath("$[?@.left <= @.right]"),
        selection_meter(),
    )

    assert type(present) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in present.matches) == ("/0",)
    assert type(missing_equal) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in missing_equal.matches) == ("/0", "/1")
    assert type(missing_less_or_equal) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in missing_less_or_equal.matches) == (
        "/0",
        "/1",
    )


def test_filters_compare_exact_numbers_strings_arrays_and_unordered_objects() -> None:
    first = JsonObject(
        members=(
            JsonObjectMember(name="a", value=JsonInteger(value=1)),
            JsonObjectMember(name="b", value=JsonString(value="x")),
        )
    )
    second = JsonObject(members=tuple(reversed(first.members)))
    value = JsonArray(
        items=(
            json_object(
                number=JsonDecimal(value=Decimal("1.5")),
                text=JsonString(value="é"),
                left=first,
                right=second,
            ),
            json_object(
                number=JsonInteger(value=2),
                text=JsonString(value="a"),
                left=first,
                right=json_object(a=JsonInteger(value=9)),
            ),
        )
    )

    result = evaluate_json(
        value,
        compile_jsonpath('$[?@.number < 2 && @.text > "z" && @.left == @.right]'),
        selection_meter(),
    )

    assert type(result) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in result.matches) == ("/0",)


def test_length_count_and_value_functions_have_typed_rfc_semantics() -> None:
    value = JsonArray(
        items=(
            json_object(
                name=JsonString(value="éx"),
                items=JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))),
                enabled=JsonBoolean(value=True),
            ),
            json_object(
                name=JsonString(value="x"),
                items=JsonArray(items=()),
                enabled=JsonBoolean(value=False),
            ),
        )
    )
    path = compile_jsonpath(
        "$[?length(@.name)==2 && count(@.items[*])==2 && value(@.enabled)==true]"
    )

    result = evaluate_json(value, path, selection_meter())

    assert type(result) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in result.matches) == ("/0",)


def test_match_and_search_are_explicitly_unsupported_without_using_python_regex() -> None:
    meter = selection_meter()
    before = meter.budgets

    result = evaluate_json(
        JsonArray(items=(JsonString(value="aaaa"),)),
        compile_jsonpath('$[?match(@,"(a+)+$")]'),
        meter,
    )

    assert type(result) is SelectionUnsupported
    assert result.code.value == "selection.function.iregexp-unsupported"
    assert meter.budgets == before


def test_sensitive_root_taints_every_selected_descendant_without_payload_logging() -> None:
    sensitive = SensitiveValue(
        json_object(secret=JsonString(value="do-not-log")),
        origins=(TaintOriginId(value="project/secrets/api-token"),),
    )

    result = evaluate_json(sensitive, compile_jsonpath("$.secret"), selection_meter())

    assert type(result) is JsonEvaluationReady
    selected = result.matches[0].value
    assert isinstance(selected, SensitiveValue)
    assert selected.origins == sensitive.origins
    assert str(selected) == "<sensitive>"


def test_result_accounting_charges_duplicates_and_compact_json_bytes_before_append() -> None:
    value = json_object(a=JsonString(value="é"))
    meter = selection_meter()

    result = evaluate_json(value, compile_jsonpath('$["a","a"]'), meter)

    assert type(result) is JsonEvaluationReady
    assert meter.budgets.matches.usage.consumed == Count(value=2)
    assert meter.budgets.result_bytes.usage.consumed == ByteCount(value=8)


def test_budget_exhaustion_is_exact_and_does_not_return_partial_matches() -> None:
    result = evaluate_json(
        JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))),
        compile_jsonpath("$[*]"),
        selection_meter(matches=1),
    )

    assert type(result) is SelectionBudgetExceeded
    assert result.exhaustion.attempted == Count(value=2)


def test_payload_size_counts_utf8_escaping_and_fixed_decimal_without_serializing() -> None:
    value = JsonArray(
        items=(
            json_object(é=JsonString(value='"\n😀')),
            JsonDecimal(value=Decimal("-0.01")),
        )
    )

    sized = json_value_size(value, selection_meter())

    assert sized == ByteCount(value=25)
