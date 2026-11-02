"""Budgeted RFC 9535 nodelist traversal and filter expression semantics."""

from __future__ import annotations

from typing import Final
from typing import TypeGuard
from typing import cast
from typing import final

from comparo.core.domain.selectors.jsonpath import AndExpression
from comparo.core.domain.selectors.jsonpath import ChildSegment
from comparo.core.domain.selectors.jsonpath import ComparisonExpression
from comparo.core.domain.selectors.jsonpath import ComparisonOperator
from comparo.core.domain.selectors.jsonpath import DescendantSegment
from comparo.core.domain.selectors.jsonpath import FilterExpression
from comparo.core.domain.selectors.jsonpath import FilterSelector
from comparo.core.domain.selectors.jsonpath import FunctionExpression
from comparo.core.domain.selectors.jsonpath import IndexSelector
from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath import LiteralExpression
from comparo.core.domain.selectors.jsonpath import NameSelector
from comparo.core.domain.selectors.jsonpath import NotExpression
from comparo.core.domain.selectors.jsonpath import OrExpression
from comparo.core.domain.selectors.jsonpath import QueryExpression
from comparo.core.domain.selectors.jsonpath import QueryOrigin
from comparo.core.domain.selectors.jsonpath import SegmentSelector
from comparo.core.domain.selectors.jsonpath import SliceSelector
from comparo.core.domain.selectors.jsonpath import StandardFunction
from comparo.core.domain.selectors.jsonpath import WildcardSelector
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.json_runtime import JsonNode
from comparo.core.selection.json_runtime import charge_work
from comparo.core.selection.json_runtime import child_node
from comparo.core.selection.json_runtime import compare_strings
from comparo.core.selection.json_runtime import iter_children
from comparo.core.selection.json_runtime import strings_equal


@final
class _Nothing:
    __slots__ = ()


_NOTHING: Final = _Nothing()
type _ComparableValue = JsonValue | _Nothing


def evaluate_jsonpath(
    root: JsonNode,
    ast: JsonPathAst,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    """Evaluate a compiled root-addressed JSONPath AST to an ordered nodelist."""
    return _evaluate_segments((root,), ast.segments, root, meter)


def _evaluate_segments(
    initial: tuple[JsonNode, ...],
    segments: tuple[ChildSegment | DescendantSegment, ...],
    root: JsonNode,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    nodes = initial
    for segment in segments:
        nodes = (
            _apply_child_segment(nodes, segment, root, meter)
            if type(segment) is ChildSegment
            else _apply_descendant_segment(nodes, segment, root, meter)
        )
        if not nodes:
            break
    return nodes


def _apply_child_segment(
    nodes: tuple[JsonNode, ...],
    segment: ChildSegment,
    root: JsonNode,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    output: list[JsonNode] = []
    for node in nodes:
        for selector in segment.selectors:
            charge_work(meter)
            output.extend(_apply_selector(node, selector, root, meter))
    return tuple(output)


def _apply_descendant_segment(
    nodes: tuple[JsonNode, ...],
    segment: DescendantSegment,
    root: JsonNode,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    output: list[JsonNode] = []
    for input_node in nodes:
        stack = [input_node]
        while stack:
            node = stack.pop()
            charge_work(meter)
            for selector in segment.selectors:
                charge_work(meter)
                output.extend(_apply_selector(node, selector, root, meter))
            stack.extend(iter_children(node, meter, reverse=True))
    return tuple(output)


def _apply_selector(
    node: JsonNode,
    selector: SegmentSelector,
    root: JsonNode,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    if type(selector) is NameSelector:
        return _select_name(node, selector.name, meter)
    if type(selector) is IndexSelector:
        return _select_index(node, selector.index, meter)
    if type(selector) is SliceSelector:
        return _select_slice(node, selector, meter)
    if type(selector) is WildcardSelector:
        return _select_wildcard(node, meter)
    if type(selector) is FilterSelector:
        return _select_filter(node, selector.predicate, root, meter)
    raise TypeError("unsupported JSONPath selector")  # pragma: no cover


def _select_name(node: JsonNode, name: str, meter: SelectionMeter) -> tuple[JsonNode, ...]:
    if type(node.value) is not JsonObject:
        return ()
    for member in node.value.members:
        charge_work(meter)
        if strings_equal(member.name, name, meter):
            return (child_node(node, member.name, member.value),)
    return ()


def _select_index(node: JsonNode, authored: int, meter: SelectionMeter) -> tuple[JsonNode, ...]:
    if type(node.value) is not JsonArray:
        return ()
    index = authored if authored >= 0 else len(node.value.items) + authored
    if index < 0 or index >= len(node.value.items):
        return ()
    charge_work(meter)
    return (child_node(node, str(index), node.value.items[index]),)


def _select_slice(
    node: JsonNode,
    selector: SliceSelector,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    if type(node.value) is not JsonArray or selector.step == 0:
        return ()
    start, stop, step = slice(selector.start, selector.stop, selector.step).indices(
        len(node.value.items)
    )
    output: list[JsonNode] = []
    for index in range(start, stop, step):
        charge_work(meter)
        output.append(child_node(node, str(index), node.value.items[index]))
    return tuple(output)


def _select_wildcard(node: JsonNode, meter: SelectionMeter) -> tuple[JsonNode, ...]:
    return tuple(iter_children(node, meter))


def _select_filter(
    node: JsonNode,
    predicate: FilterExpression,
    root: JsonNode,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    return tuple(
        child
        for child in iter_children(node, meter)
        if _evaluate_logical(predicate, root, child, meter)
    )


def _evaluate_logical(
    expression: FilterExpression,
    root: JsonNode,
    current: JsonNode,
    meter: SelectionMeter,
) -> bool:
    charge_work(meter)
    if type(expression) is QueryExpression:
        return bool(_evaluate_query(expression, root, current, meter))
    if type(expression) is ComparisonExpression:
        return _evaluate_comparison(expression, root, current, meter)
    if type(expression) is NotExpression:
        return not _evaluate_logical(expression.operand, root, current, meter)
    if type(expression) is AndExpression:
        return _evaluate_logical(expression.left, root, current, meter) and _evaluate_logical(
            expression.right, root, current, meter
        )
    if type(expression) is OrExpression:
        return _evaluate_logical(expression.left, root, current, meter) or _evaluate_logical(
            expression.right, root, current, meter
        )
    if type(expression) is FunctionExpression:
        return False
    raise TypeError("unsupported logical JSONPath expression")


def _evaluate_comparison(
    expression: ComparisonExpression,
    root: JsonNode,
    current: JsonNode,
    meter: SelectionMeter,
) -> bool:
    left = _evaluate_operand(expression.left, root, current, meter)
    right = _evaluate_operand(expression.right, root, current, meter)
    if expression.operator is ComparisonOperator.EQUAL:
        return _equal(left, right, meter)
    if expression.operator is ComparisonOperator.NOT_EQUAL:
        return not _equal(left, right, meter)
    if expression.operator in {
        ComparisonOperator.LESS_THAN_OR_EQUAL,
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
    } and _equal(left, right, meter):
        return True
    ordering = _compare_order(left, right, meter)
    if ordering is None:
        return False
    if expression.operator is ComparisonOperator.LESS_THAN:
        return ordering < 0
    if expression.operator is ComparisonOperator.LESS_THAN_OR_EQUAL:
        return ordering <= 0
    if expression.operator is ComparisonOperator.GREATER_THAN:
        return ordering > 0
    return ordering >= 0


def _evaluate_operand(
    expression: FilterExpression,
    root: JsonNode,
    current: JsonNode,
    meter: SelectionMeter,
) -> _ComparableValue:
    charge_work(meter)
    if type(expression) is LiteralExpression:
        return expression.value
    if type(expression) is QueryExpression:
        nodes = _evaluate_query(expression, root, current, meter)
        return nodes[0].value if len(nodes) == 1 else _NOTHING
    if type(expression) is FunctionExpression:
        return _evaluate_value_function(expression, root, current, meter)
    raise TypeError("comparison operand is not a value expression")


def _evaluate_value_function(
    expression: FunctionExpression,
    root: JsonNode,
    current: JsonNode,
    meter: SelectionMeter,
) -> _ComparableValue:
    charge_work(meter)
    argument = expression.arguments[0]
    if expression.function is StandardFunction.COUNT:
        return JsonInteger(value=len(_evaluate_node_argument(argument, root, current, meter)))
    if expression.function is StandardFunction.VALUE:
        nodes = _evaluate_node_argument(argument, root, current, meter)
        return nodes[0].value if len(nodes) == 1 else _NOTHING
    if expression.function is StandardFunction.LENGTH:
        value = _evaluate_operand(argument, root, current, meter)
        if type(value) is JsonString:
            return JsonInteger(value=len(value.value))
        if type(value) is JsonArray:
            return JsonInteger(value=len(value.items))
        if type(value) is JsonObject:
            return JsonInteger(value=len(value.members))
    return _NOTHING


def _evaluate_node_argument(
    expression: FilterExpression,
    root: JsonNode,
    current: JsonNode,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    if type(expression) is not QueryExpression:
        raise TypeError("node-list function requires a query expression")
    return _evaluate_query(expression, root, current, meter)


def _evaluate_query(
    expression: QueryExpression,
    root: JsonNode,
    current: JsonNode,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    origin = root if expression.origin is QueryOrigin.ROOT else current
    return _evaluate_segments((origin,), expression.segments, root, meter)


def _equal(left: _ComparableValue, right: _ComparableValue, meter: SelectionMeter) -> bool:
    stack: list[tuple[_ComparableValue, _ComparableValue]] = [(left, right)]
    while stack:
        first, second = stack.pop()
        charge_work(meter)
        if not _compare_equal_pair(first, second, stack, meter):
            return False
    return True


def _compare_equal_pair(
    first: _ComparableValue,
    second: _ComparableValue,
    stack: list[tuple[_ComparableValue, _ComparableValue]],
    meter: SelectionMeter,
) -> bool:
    if type(first) is _Nothing or type(second) is _Nothing:
        return type(first) is type(second)
    if _is_number(first) and _is_number(second):
        return _compare_numbers(first, second, meter) == 0
    if type(first) is not type(second):
        return False
    if type(first) is JsonNull:
        return True
    if type(first) is JsonBoolean:
        return first.value == cast("JsonBoolean", second).value
    if type(first) is JsonString:
        return strings_equal(first.value, cast("JsonString", second).value, meter)
    if type(first) is JsonArray:
        return _queue_equal_array(first, cast("JsonArray", second), stack, meter)
    if type(first) is JsonObject:
        return _queue_equal_object(first, cast("JsonObject", second), stack, meter)
    raise TypeError("unsupported JSON equality value")  # pragma: no cover


def _queue_equal_array(
    first: JsonArray,
    second: JsonArray,
    stack: list[tuple[_ComparableValue, _ComparableValue]],
    meter: SelectionMeter,
) -> bool:
    if len(first.items) != len(second.items):
        return False
    for pair in zip(reversed(first.items), reversed(second.items), strict=True):
        charge_work(meter)
        stack.append(pair)
    return True


def _queue_equal_object(
    first: JsonObject,
    second: JsonObject,
    stack: list[tuple[_ComparableValue, _ComparableValue]],
    meter: SelectionMeter,
) -> bool:
    if len(first.members) != len(second.members):
        return False
    for member in first.members:
        found: JsonValue | None = None
        for candidate in second.members:
            charge_work(meter)
            if strings_equal(member.name, candidate.name, meter):
                found = candidate.value
                break
        if found is None:
            return False
        stack.append((member.value, found))
    return True


def _compare_order(
    left: _ComparableValue,
    right: _ComparableValue,
    meter: SelectionMeter,
) -> int | None:
    if _is_number(left) and _is_number(right):
        return _compare_numbers(left, right, meter)
    if type(left) is JsonString and type(right) is JsonString:
        return compare_strings(left.value, right.value, meter)
    return None


def _is_number(value: object) -> TypeGuard[JsonInteger | JsonDecimal]:
    return type(value) in {JsonInteger, JsonDecimal}


def _compare_numbers(
    left: JsonInteger | JsonDecimal,
    right: JsonInteger | JsonDecimal,
    meter: SelectionMeter,
) -> int:
    _charge_number_complexity(left, meter)
    _charge_number_complexity(right, meter)
    left_value = left.value
    right_value = right.value
    return (left_value > right_value) - (left_value < right_value)


def _charge_number_complexity(
    value: JsonInteger | JsonDecimal,
    meter: SelectionMeter,
) -> None:
    units = (
        max(1, (abs(value.value).bit_length() + 59) // 60)
        if type(value) is JsonInteger
        else max(1, (len(value.value.as_tuple().digits) + 17) // 18)
    )
    for _unit in range(units):
        charge_work(meter)


__all__ = ("evaluate_jsonpath",)
