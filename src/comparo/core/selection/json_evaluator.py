"""Pure, budgeted RFC 6901 and RFC 9535 evaluation over immutable JSON values."""

from __future__ import annotations

from typing import Final
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.json_runtime import JsonBudgetExceededError
from comparo.core.selection.json_runtime import JsonNode
from comparo.core.selection.json_runtime import charge_match
from comparo.core.selection.json_runtime import charge_result_bytes
from comparo.core.selection.json_runtime import charge_work
from comparo.core.selection.json_runtime import child_node
from comparo.core.selection.json_runtime import render_json_pointer
from comparo.core.selection.json_runtime import strings_equal
from comparo.core.selection.jsonpath_evaluator import evaluate_jsonpath
from comparo.core.selection.value_size import json_value_size

_SENSITIVE_ACCESS: Final = SensitiveValueAccess(purpose="selector.evaluate.json")
_IREGEXP_UNSUPPORTED: Final = SelectionUnsupported(
    code=DiagnosticCode(value="selection.function.iregexp-unsupported")
)


@final
class JsonEvaluationMatch(msgspec.Struct, frozen=True, kw_only=True):
    """One selected immutable JSON value and its canonical concrete location."""

    value: JsonValue | SensitiveValue[JsonValue]
    pointer: JsonPointer

    def __post_init__(self) -> None:
        """Require exact JSON containment and an RFC 6901 location."""
        if is_sensitive_value(self.value):
            if not _is_json_value_type(self.value.payload_type):
                raise TypeError("sensitive JSON match must contain an exact JSON value")
        elif not is_json_value(self.value):
            raise TypeError("JSON match must contain an exact JSON value")
        if type(self.pointer) is not JsonPointer:
            raise TypeError("JSON match location must be a JsonPointer")


@final
class JsonEvaluationReady(msgspec.Struct, frozen=True, kw_only=True):
    """A complete, ordered RFC nodelist including intentional duplicates."""

    matches: tuple[JsonEvaluationMatch, ...]

    def __post_init__(self) -> None:
        """Require one immutable collection of typed matches."""
        if type(self.matches) is not tuple or any(
            type(match) is not JsonEvaluationMatch for match in self.matches
        ):
            raise TypeError("JSON evaluation matches must be an immutable typed tuple")


type JsonEvaluationResult = JsonEvaluationReady | SelectionBudgetExceeded | SelectionUnsupported
type SelectableJson = JsonValue | SensitiveValue[JsonValue]


def evaluate_json(
    value: SelectableJson,
    path: JsonPointerPath | JsonPathExpression,
    meter: SelectionMeter,
) -> JsonEvaluationResult:
    """Evaluate a compiled JSON selector without I/O, callbacks, or reparsing.

    Result match and byte budgets are charged only for the final nodelist. Work is
    charged for traversal, filtering, function evaluation, comparisons, and exact
    payload-size accounting. Failed charges never append a partial result.
    """
    raw, sensitive = _unwrap_json(value)
    if type(meter) is not SelectionMeter:
        raise TypeError("JSON evaluation requires a SelectionMeter")
    if type(path) is JsonPathExpression and {"match", "search"} & set(
        path.capabilities.function_dependencies
    ):
        return _IREGEXP_UNSUPPORTED
    try:
        if type(path) is JsonPointerPath:
            nodes = _evaluate_pointer(raw, path.pointer, meter)
        elif type(path) is JsonPathExpression:
            root = JsonNode(value=raw, parent=None, token=None)
            nodes = evaluate_jsonpath(root, path.ast, meter)
        else:
            raise TypeError("JSON evaluation requires a compiled JSON path")
        return _finalize(nodes, sensitive, meter)
    except JsonBudgetExceededError as halted:
        return halted.result


def _unwrap_json(value: SelectableJson) -> tuple[JsonValue, SensitiveValue[JsonValue] | None]:
    if is_sensitive_value(value):
        if not _is_json_value_type(value.payload_type):
            raise TypeError("sensitive JSON evaluation input must contain a JsonValue")
        revealed = cast("JsonValue", value.reveal(access=_SENSITIVE_ACCESS))
        return revealed, cast("SensitiveValue[JsonValue]", value)
    if not is_json_value(value):
        raise TypeError("JSON evaluation input must be an exact JsonValue")
    return value, None


def _is_json_value_type(value_type: type[object]) -> bool:
    return value_type in {
        JsonNull,
        JsonBoolean,
        JsonInteger,
        JsonDecimal,
        JsonString,
        JsonArray,
        JsonObject,
    }


def _evaluate_pointer(
    root: JsonValue,
    pointer: JsonPointer,
    meter: SelectionMeter,
) -> tuple[JsonNode, ...]:
    node = JsonNode(value=root, parent=None, token=None)
    if not pointer.value:
        return (node,)
    for encoded in pointer.value[1:].split("/"):
        charge_work(meter)
        token = encoded.replace("~1", "/").replace("~0", "~")
        selected = _pointer_child(node, token, meter)
        if selected is None:
            return ()
        node = selected
    return (node,)


def _pointer_child(node: JsonNode, token: str, meter: SelectionMeter) -> JsonNode | None:
    if type(node.value) is JsonObject:
        for member in node.value.members:
            charge_work(meter)
            if strings_equal(member.name, token, meter):
                return child_node(node, member.name, member.value)
        return None
    if type(node.value) is JsonArray:
        index = _array_token_index(token, len(node.value.items))
        if index is None:
            return None
        charge_work(meter)
        return child_node(node, str(index), node.value.items[index])
    return None


def _array_token_index(array_index: str, length: int) -> int | None:
    if array_index == "0":
        return 0 if length else None
    if (
        not array_index
        or array_index[0] not in "123456789"
        or not array_index.isascii()
        or not array_index.isdigit()
    ):
        return None
    maximum = str(length - 1)
    if len(array_index) > len(maximum) or (
        len(array_index) == len(maximum) and array_index > maximum
    ):
        return None
    return int(array_index)


def _finalize(
    nodes: tuple[JsonNode, ...],
    sensitive: SensitiveValue[JsonValue] | None,
    meter: SelectionMeter,
) -> JsonEvaluationReady:
    matches: list[JsonEvaluationMatch] = []
    for node in nodes:
        charge_match(meter)
        sized = json_value_size(node.value, meter)
        if type(sized) is SelectionBudgetExceeded:
            raise JsonBudgetExceededError(sized)
        charge_result_bytes(meter, sized.value)
        selected: SelectableJson = (
            node.value
            if sensitive is None
            else SensitiveValue(node.value, origins=sensitive.origins)
        )
        matches.append(
            JsonEvaluationMatch(
                value=selected,
                pointer=render_json_pointer(node, meter),
            )
        )
    return JsonEvaluationReady(matches=tuple(matches))


__all__ = (
    "JsonEvaluationMatch",
    "JsonEvaluationReady",
    "JsonEvaluationResult",
    "evaluate_json",
)
