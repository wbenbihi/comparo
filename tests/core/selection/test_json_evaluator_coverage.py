"""Close defensive and semantic branches in the pure JSON selector runtime."""

from __future__ import annotations

from decimal import Decimal
from decimal import InvalidOperation
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.selectors.jsonpath import FunctionExpression
from comparo.core.domain.selectors.jsonpath import LiteralExpression
from comparo.core.domain.selectors.jsonpath import StandardFunction
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath
from comparo.core.selection import parser as parser_module
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.errors import SelectorSyntaxCode
from comparo.core.selection.errors import SelectorSyntaxError
from comparo.core.selection.json_evaluator import JsonEvaluationMatch
from comparo.core.selection.json_evaluator import JsonEvaluationReady
from comparo.core.selection.json_evaluator import evaluate_json
from comparo.core.selection.json_runtime import JsonBudgetExceededError
from comparo.core.selection.json_runtime import JsonNode
from comparo.core.selection.json_runtime import charge_match
from comparo.core.selection.json_runtime import charge_result_bytes
from comparo.core.selection.json_runtime import charge_work
from comparo.core.selection.json_runtime import compare_strings
from comparo.core.selection.json_runtime import iter_children
from comparo.core.selection.json_runtime import render_json_pointer
from comparo.core.selection.json_runtime import strings_equal
from comparo.core.selection.jsonpath_evaluator import _evaluate_logical
from comparo.core.selection.jsonpath_evaluator import _evaluate_node_argument
from comparo.core.selection.jsonpath_evaluator import _evaluate_operand
from comparo.core.selection.jsonpath_evaluator import _evaluate_value_function
from comparo.core.selection.limits import SelectorParserLimits
from comparo.core.selection.value_size import _integer_size
from comparo.core.selection.value_size import _json_string_size
from comparo.core.selection.value_size import _media_parameter_size
from comparo.core.selection.value_size import _media_type_size
from comparo.core.selection.value_size import _utf8_size
from comparo.core.selection.value_size import json_value_size
from comparo.core.selection.value_size import selected_value_size
from tests.core.selection.json_evaluator_factories import json_object
from tests.core.selection.json_evaluator_factories import selection_meter


def _root(value: JsonValue | None = None) -> JsonNode:
    return JsonNode(value=JsonNull() if value is None else value, parent=None, token=None)


def test_json_result_contracts_reject_wrong_runtime_types() -> None:
    with pytest.raises(TypeError, match="sensitive JSON"):
        JsonEvaluationMatch(
            value=cast(
                "SensitiveValue[JsonValue]",
                SensitiveValue(
                    BytesValue(value=b"not-json"),
                    origins=(TaintOriginId(value="test.secret"),),
                ),
            ),
            pointer=JsonPointer(value=""),
        )
    with pytest.raises(TypeError, match="exact JSON"):
        JsonEvaluationMatch(
            value=cast("JsonValue", BytesValue(value=b"not-json")),
            pointer=JsonPointer(value=""),
        )
    with pytest.raises(TypeError, match="JsonPointer"):
        JsonEvaluationMatch(value=JsonNull(), pointer=cast("JsonPointer", ""))
    with pytest.raises(TypeError, match="immutable typed tuple"):
        JsonEvaluationReady(matches=cast("tuple[JsonEvaluationMatch, ...]", []))
    with pytest.raises(TypeError, match="immutable typed tuple"):
        JsonEvaluationReady(matches=cast("tuple[JsonEvaluationMatch, ...]", (JsonNull(),)))


def test_json_evaluation_rejects_untyped_inputs_path_and_meter() -> None:
    path = compile_json_pointer("")
    with pytest.raises(TypeError, match="SelectionMeter"):
        evaluate_json(JsonNull(), path, cast("SelectionMeter", object()))
    with pytest.raises(TypeError, match="compiled JSON path"):
        evaluate_json(JsonNull(), cast("JsonPointerPath", NoPath()), selection_meter())
    with pytest.raises(TypeError, match="exact JsonValue"):
        evaluate_json(cast("JsonValue", BytesValue(value=b"x")), path, selection_meter())
    sensitive = cast(
        "SensitiveValue[JsonValue]",
        SensitiveValue(
            BytesValue(value=b"x"),
            origins=(TaintOriginId(value="test.secret"),),
        ),
    )
    with pytest.raises(TypeError, match="contain a JsonValue"):
        evaluate_json(sensitive, path, selection_meter())


def test_pointer_root_misses_and_all_array_token_guards() -> None:
    value = JsonArray(items=(JsonString(value="zero"), JsonString(value="one")))
    root = evaluate_json(value, compile_json_pointer(""), selection_meter())
    primitive_miss = evaluate_json(JsonNull(), compile_json_pointer("/x"), selection_meter())
    object_miss = evaluate_json(
        json_object(a=JsonNull()), compile_json_pointer("/z"), selection_meter()
    )

    assert type(root) is JsonEvaluationReady
    assert root.matches[0].pointer == JsonPointer(value="")
    assert type(primitive_miss) is JsonEvaluationReady
    assert not primitive_miss.matches
    assert type(object_miss) is JsonEvaluationReady
    assert not object_miss.matches
    for token in ("0", "2", "", "01", "a", chr(0xFF11), "10", "999"):
        result = evaluate_json(value, compile_json_pointer(f"/{token}"), selection_meter())
        assert type(result) is JsonEvaluationReady
    empty_result = evaluate_json(JsonArray(items=()), compile_json_pointer("/0"), selection_meter())
    assert type(empty_result) is JsonEvaluationReady
    assert empty_result.matches == ()


def test_runtime_nodes_iteration_comparison_rendering_and_budget_halts() -> None:
    with pytest.raises(ValueError, match="exactly child"):
        JsonNode(value=JsonNull(), parent=_root(), token=None)
    with pytest.raises(ValueError, match="exactly child"):
        JsonNode(value=JsonNull(), parent=None, token="x")
    with pytest.raises(TypeError, match="tokens"):
        JsonNode(value=JsonNull(), parent=_root(), token=cast("str", 1))

    array = _root(JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))))
    obj = _root(json_object(a=JsonNull(), b=JsonNull()))
    assert [node.token for node in iter_children(array, selection_meter(), reverse=True)] == [
        "1",
        "0",
    ]
    assert [node.token for node in iter_children(obj, selection_meter())] == ["a", "b"]
    assert [node.token for node in iter_children(obj, selection_meter(), reverse=True)] == [
        "b",
        "a",
    ]
    assert tuple(iter_children(_root(JsonNull()), selection_meter())) == ()

    escaped = JsonNode(value=JsonNull(), parent=_root(), token="a~/")
    assert render_json_pointer(escaped, selection_meter()).value == "/a~0~1"
    assert strings_equal("aa", "ab", selection_meter()) is False
    assert strings_equal("a", "aa", selection_meter()) is False
    assert strings_equal("aa", "aa", selection_meter()) is True
    assert compare_strings("a", "b", selection_meter()) < 0
    assert compare_strings("aa", "a", selection_meter()) > 0
    assert compare_strings("a", "a", selection_meter()) == 0

    for operation in (charge_work, charge_match):
        with pytest.raises(JsonBudgetExceededError):
            operation(selection_meter(work=0, matches=0, result_bytes=0))
    with pytest.raises(JsonBudgetExceededError):
        charge_result_bytes(selection_meter(result_bytes=0), 1)


def test_json_finalization_propagates_all_three_budget_dimensions() -> None:
    assert (
        type(evaluate_json(JsonNull(), compile_json_pointer(""), selection_meter(work=0)))
        is SelectionBudgetExceeded
    )
    assert (
        type(evaluate_json(JsonNull(), compile_json_pointer(""), selection_meter(matches=0)))
        is SelectionBudgetExceeded
    )
    assert (
        type(evaluate_json(JsonNull(), compile_json_pointer(""), selection_meter(result_bytes=0)))
        is SelectionBudgetExceeded
    )


@pytest.mark.parametrize(
    "path",
    [
        "$['name']",
        "$[0]",
        "$[10]",
        "$[-10]",
        "$[1:2]",
        "$[?@]",
    ],
)
def test_selectors_on_incompatible_or_out_of_range_nodes_are_empty(path: str) -> None:
    result = evaluate_json(JsonNull(), compile_jsonpath(path), selection_meter())
    assert type(result) is JsonEvaluationReady
    assert result.matches == ()


def test_out_of_range_indexes_on_arrays_are_empty() -> None:
    value = JsonArray(items=(JsonNull(),))
    for path in ("$[10]", "$[-10]"):
        result = evaluate_json(value, compile_jsonpath(path), selection_meter())
        assert type(result) is JsonEvaluationReady
        assert result.matches == ()


def test_logical_short_circuit_negation_and_every_ordering_operator() -> None:
    value = JsonArray(
        items=(
            json_object(x=JsonInteger(value=1), s=JsonString(value="a")),
            json_object(x=JsonInteger(value=2), s=JsonString(value="b")),
            json_object(x=JsonNull(), s=JsonInteger(value=1)),
        )
    )
    expressions = (
        "$[?!@.missing]",
        '$[?@.x==1 && @.s=="a"]',
        '$[?@.x==9 && @.s=="a"]',
        '$[?@.x==1 || @.s=="b"]',
        '$[?@.x==1 || @.s=="z"]',
        "$[?@.x!=1]",
        "$[?@.x<=1]",
        "$[?@.x>=2]",
        "$[?@.x<2]",
        "$[?@.x>1]",
        '$[?@.s<"b"]',
        "$[?@.s>1]",
    )
    for expression in expressions:
        result = evaluate_json(value, compile_jsonpath(expression), selection_meter())
        assert type(result) is JsonEvaluationReady

    root = _root(value)
    current = next(iter_children(root, selection_meter()))
    function = FunctionExpression(
        function=StandardFunction.LENGTH,
        arguments=(LiteralExpression(value=JsonString(value="x")),),
    )
    assert _evaluate_logical(function, root, current, selection_meter()) is False
    unsupported_value_function = FunctionExpression(
        function=StandardFunction.MATCH,
        arguments=(
            LiteralExpression(value=JsonString(value="x")),
            LiteralExpression(value=JsonString(value="x")),
        ),
    )
    assert (
        type(
            _evaluate_value_function(unsupported_value_function, root, current, selection_meter())
        ).__name__
        == "_Nothing"
    )
    with pytest.raises(TypeError, match="unsupported logical"):
        _evaluate_logical(
            LiteralExpression(value=JsonBoolean(value=True)), root, current, selection_meter()
        )
    with pytest.raises(TypeError, match="comparison operand"):
        _evaluate_operand(cast("FunctionExpression", object()), root, current, selection_meter())
    with pytest.raises(TypeError, match="node-list function"):
        _evaluate_node_argument(
            LiteralExpression(value=JsonNull()), root, current, selection_meter()
        )


def test_value_functions_cover_string_array_object_nothing_and_plural_value() -> None:
    value = JsonArray(
        items=(
            json_object(x=JsonString(value="ab"), many=JsonArray(items=(JsonNull(), JsonNull()))),
            json_object(x=JsonArray(items=(JsonNull(), JsonNull())), many=JsonArray(items=())),
            json_object(x=json_object(a=JsonNull(), b=JsonNull()), many=JsonArray(items=())),
            json_object(x=JsonNull(), many=JsonArray(items=(JsonNull(), JsonNull()))),
        )
    )
    for path in (
        "$[?length(@.x)==2]",
        "$[?value(@.many[*])==null]",
        "$[?count(@.many[*])==2]",
        "$[?$.missing]",
    ):
        result = evaluate_json(value, compile_jsonpath(path), selection_meter())
        assert type(result) is JsonEvaluationReady


def test_json_equality_covers_all_value_families_and_container_mismatches() -> None:
    values = JsonArray(
        items=(
            json_object(a=JsonNull(), b=JsonNull()),
            json_object(a=JsonBoolean(value=True), b=JsonBoolean(value=False)),
            json_object(a=JsonString(value="x"), b=JsonString(value="y")),
            json_object(a=JsonInteger(value=1), b=JsonString(value="1")),
            json_object(
                a=JsonArray(items=(JsonInteger(value=1),)),
                b=JsonArray(items=(JsonInteger(value=1), JsonInteger(value=2))),
            ),
            json_object(
                a=JsonArray(items=(JsonInteger(value=1),)),
                b=JsonArray(items=(JsonInteger(value=1),)),
            ),
            json_object(
                a=JsonArray(items=(JsonInteger(value=1),)),
                b=JsonArray(items=(JsonInteger(value=2),)),
            ),
            json_object(a=json_object(x=JsonNull()), b=json_object()),
            json_object(a=json_object(x=JsonNull()), b=json_object(y=JsonNull())),
            json_object(
                a=JsonDecimal(value=Decimal("1.5")),
                b=JsonInteger(value=1),
            ),
        )
    )
    result = evaluate_json(values, compile_jsonpath("$[?@.a==@.b]"), selection_meter())
    assert type(result) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in result.matches) == ("/0", "/5")


def test_large_numeric_comparison_charges_multiple_complexity_units() -> None:
    huge = 1 << 180
    value = JsonArray(
        items=(
            json_object(a=JsonInteger(value=huge), b=JsonInteger(value=huge)),
            json_object(
                a=JsonDecimal(value=Decimal("1." + "0" * 39 + "1")),
                b=JsonDecimal(value=Decimal("1." + "0" * 39 + "1")),
            ),
        )
    )
    result = evaluate_json(value, compile_jsonpath("$[?@.a==@.b]"), selection_meter())
    assert type(result) is JsonEvaluationReady
    assert len(result.matches) == 2


def test_selected_value_size_rejects_untyped_inputs_and_early_budget_failure() -> None:
    assert selected_value_size(JsonNull(), selection_meter()) == ByteCount(value=4)
    with pytest.raises(TypeError, match="closed SelectedValue"):
        selected_value_size(cast("SelectedValue", object()), selection_meter())
    with pytest.raises(TypeError, match="SelectionMeter"):
        selected_value_size(JsonNull(), cast("SelectionMeter", object()))
    assert (
        type(selected_value_size(HttpFieldTextValue(value="x"), selection_meter(work=0)))
        is SelectionBudgetExceeded
    )


def test_non_json_sizing_propagates_nested_budget_failures() -> None:
    items = HttpFieldItems(items=(MediaTypeTokenValue(value="x"),))
    assert type(selected_value_size(items, selection_meter(work=1))) is SelectionBudgetExceeded
    assert type(selected_value_size(items, selection_meter(work=2))) is SelectionBudgetExceeded

    huge = 10**18
    numerator_failure = Ratio(numerator=huge, denominator=PositiveCount(value=1))
    denominator_failure = Ratio(numerator=1, denominator=PositiveCount(value=huge))
    assert (
        type(selected_value_size(numerator_failure, selection_meter(work=1)))
        is SelectionBudgetExceeded
    )
    assert (
        type(selected_value_size(denominator_failure, selection_meter(work=1)))
        is SelectionBudgetExceeded
    )


def test_media_sizing_propagates_each_nested_failure_and_quotes_escapes() -> None:
    media = MediaType(
        type="a",
        subtype="b",
        parameters=(MediaTypeParameter(name="n", value=MediaTypeTokenValue(value="v")),),
    )
    for work in (0, 2, 3, 4):
        assert type(_media_type_size(media, selection_meter(work=work))) is SelectionBudgetExceeded
    quoted = MediaTypeQuotedValue(value='"\\')
    assert _media_parameter_size(quoted, selection_meter()) == ByteCount(value=6)
    assert (
        type(_media_parameter_size(MediaTypeQuotedValue(value="a b"), selection_meter(work=3)))
        is SelectionBudgetExceeded
    )


def test_json_sizing_propagates_nested_failures_and_all_escape_classes() -> None:
    with pytest.raises(TypeError, match="exact JsonValue"):
        json_value_size(cast("JsonValue", BytesValue(value=b"x")), selection_meter())
    with pytest.raises(TypeError, match="SelectionMeter"):
        json_value_size(JsonNull(), cast("SelectionMeter", object()))
    assert type(json_value_size(JsonNull(), selection_meter(work=0))) is SelectionBudgetExceeded
    assert (
        type(json_value_size(JsonString(value="x"), selection_meter(work=1)))
        is SelectionBudgetExceeded
    )
    assert (
        type(json_value_size(json_object(x=JsonNull()), selection_meter(work=2)))
        is SelectionBudgetExceeded
    )

    mixed = JsonArray(
        items=(
            JsonBoolean(value=True),
            JsonBoolean(value=False),
            JsonInteger(value=0),
            JsonDecimal(value=Decimal("12.3")),
            JsonDecimal(value=Decimal("0.01")),
            JsonString(value='"\\\b\f\n\r\t\u0001é'),
            JsonArray(items=()),
            JsonObject(members=()),
        )
    )
    assert type(json_value_size(mixed, selection_meter())) is ByteCount
    assert _json_string_size("\ud800", selection_meter()) == ByteCount(value=8)
    assert type(_json_string_size("x", selection_meter(work=0))) is SelectionBudgetExceeded
    assert type(_utf8_size("x", selection_meter(work=0))) is SelectionBudgetExceeded


def test_integer_sizing_covers_chunk_loop_failure_zero_and_negative() -> None:
    assert type(_integer_size(10**18, selection_meter(work=0))) is SelectionBudgetExceeded
    assert _integer_size(10**36, selection_meter()) == ByteCount(value=37)
    assert _integer_size(0, selection_meter()) == ByteCount(value=1)
    assert _integer_size(-12, selection_meter()) == ByteCount(value=3)


def test_body_identity_non_json_size_is_fixed() -> None:
    assert selected_value_size(BodyId(value=UUID(int=1)), selection_meter()) == ByteCount(value=36)


def test_parser_contains_decimal_conversion_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_decimal(_token: str) -> Decimal:
        raise InvalidOperation

    monkeypatch.setattr(parser_module, "Decimal", invalid_decimal)
    with pytest.raises(SelectorSyntaxError) as invalid:
        compile_jsonpath("$[?@==1.1]")
    assert invalid.value.code is SelectorSyntaxCode.INVALID_NUMBER

    class NonFiniteExponentDecimal:
        def as_tuple(self) -> SimpleNamespace:
            return SimpleNamespace(exponent="n")

    monkeypatch.setattr(parser_module, "Decimal", lambda _token: NonFiniteExponentDecimal())
    with pytest.raises(SelectorSyntaxError) as non_finite:
        compile_jsonpath("$[?@==1.1]")
    assert non_finite.value.code is SelectorSyntaxCode.TOO_COMPLEX


def test_parser_depth_limit_is_enforced_at_recursive_entry() -> None:
    with pytest.raises(SelectorSyntaxError) as captured:
        compile_jsonpath(
            "$[?length(value(@))==1]",
            limits=SelectorParserLimits(max_depth=1),
        )
    assert captured.value.code is SelectorSyntaxCode.TOO_DEEP
