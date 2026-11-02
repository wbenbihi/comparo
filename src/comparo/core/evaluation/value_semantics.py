"""Neutral strict value semantics shared by assertion and diff evaluators."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import TypeGuard
from typing import cast

from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import is_selected_value
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import is_json_value

type ExactNumber = int | Decimal
type OrderedScalar = Decimal | Fraction | int


def semantic_equal(left: SelectedValue, right: SelectedValue) -> bool:
    """Compare values without conversion or representation-order accidents.

    JSON object member order is insignificant, JSON array order is significant,
    and JSON integers and decimals share one exact mathematical number domain.
    Non-JSON carriers remain type-distinct even if their payloads look alike.
    """
    _require_selected(left, label="left")
    _require_selected(right, label="right")
    if is_json_value(left) and is_json_value(right):
        return _json_equal(left, right)
    return type(left) is type(right) and left == right


def _json_equal(left: JsonValue, right: JsonValue) -> bool:
    pending: list[tuple[JsonValue, JsonValue]] = [(left, right)]
    while pending:
        current_left, current_right = pending.pop()
        if not _json_step(current_left, current_right, pending):
            return False
    return True


def _json_step(
    left: JsonValue,
    right: JsonValue,
    pending: list[tuple[JsonValue, JsonValue]],
) -> bool:
    if _is_json_number(left) and _is_json_number(right):
        return numeric_value(left) == numeric_value(right)
    if type(left) is not type(right):
        return False
    if type(left) is JsonNull:
        return True
    if type(left) in {JsonBoolean, JsonString}:
        left_scalar = cast("JsonBoolean | JsonString", left)
        right_scalar = cast("JsonBoolean | JsonString", right)
        return left_scalar.value == right_scalar.value
    if type(left) is JsonArray:
        left_array = left
        right_array = cast("JsonArray", right)
        if len(left_array.items) != len(right_array.items):
            return False
        pending.extend(zip(left_array.items, right_array.items, strict=True))
        return True
    if type(left) is JsonObject:
        return _queue_object_members(
            left,
            cast("JsonObject", right),
            pending,
        )
    raise TypeError("unsupported JSON value in strict equality")  # pragma: no cover


def _queue_object_members(
    left: JsonObject,
    right: JsonObject,
    pending: list[tuple[JsonValue, JsonValue]],
) -> bool:
    if len(left.members) != len(right.members):
        return False
    right_members = {member.name: member.value for member in right.members}
    if any(member.name not in right_members for member in left.members):
        return False
    pending.extend((member.value, right_members[member.name]) for member in left.members)
    return True


def semantic_compare(left: SelectedValue, right: SelectedValue) -> int | None:
    """Return strict ordering for compatible scalar domains, otherwise ``None``."""
    _require_selected(left, label="left")
    _require_selected(right, label="right")
    if _is_json_number(left) and _is_json_number(right):
        return _compare(numeric_value(left), numeric_value(right))
    if type(left) is not type(right):
        return None
    if type(left) is Duration:
        return _compare(
            left.nanoseconds,
            cast("Duration", right).nanoseconds,
        )
    if type(left) in {ByteCount, Count}:
        left_count = cast("ByteCount | Count", left)
        right_count = cast("ByteCount | Count", right)
        return _compare(left_count.value, right_count.value)
    if type(left) is Ratio:
        left_ratio = left
        right_ratio = cast("Ratio", right)
        return _compare(
            Fraction(left_ratio.numerator, left_ratio.denominator.value),
            Fraction(right_ratio.numerator, right_ratio.denominator.value),
        )
    if type(left) is UtcInstant:
        return _compare(
            left.unix_nanoseconds,
            cast("UtcInstant", right).unix_nanoseconds,
        )
    return None


def numeric_value(value: object) -> Decimal:
    """Return the exact decimal value of a JSON number without float conversion."""
    if type(value) is JsonInteger:
        return Decimal(value.value)
    if type(value) is JsonDecimal:
        return value.value
    raise TypeError("numeric semantics require a JSON integer or decimal")


def text_value(value: object) -> str | None:
    """Expose text for explicit string operations, not structural equality."""
    if type(value) in {JsonString, HttpFieldTextValue}:
        return cast("JsonString | HttpFieldTextValue", value).value
    return None


def collection_values(value: object) -> tuple[object, ...] | None:
    """Return explicit collection members supported by collection operations."""
    if type(value) is JsonArray:
        return value.items
    if type(value) is HttpFieldItems:
        return value.items
    return None


def _is_json_number(value: object) -> TypeGuard[JsonInteger | JsonDecimal]:
    return type(value) in {JsonInteger, JsonDecimal}


def _compare(left: OrderedScalar, right: OrderedScalar) -> int:
    return (left > right) - (left < right)


def _require_selected(value: object, *, label: str) -> None:
    if not is_selected_value(value):
        raise TypeError(f"{label} value must be a SelectedValue")


__all__ = (
    "collection_values",
    "numeric_value",
    "semantic_compare",
    "semantic_equal",
    "text_value",
)
