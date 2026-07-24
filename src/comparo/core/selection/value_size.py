"""Deterministic, budgeted byte sizing for the closed selected-value vocabulary."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeQuotedValue
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.selectors.values import is_selected_value
from comparo.core.domain.values import BytesValue
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


@final
class _ArrayCursor(msgspec.Struct, frozen=True, kw_only=True):
    value: JsonArray
    position: int


@final
class _ObjectCursor(msgspec.Struct, frozen=True, kw_only=True):
    value: JsonObject
    position: int


type _SizeTask = JsonValue | _ArrayCursor | _ObjectCursor
type _Cursor = _ArrayCursor | _ObjectCursor


def selected_value_size(
    value: SelectedValue,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    """Return the deterministic selected representation size without serialization.

    JSON uses compact UTF-8 JSON. Bytes retain their exact byte length. Textual HTTP
    carriers use UTF-8, and typed scalar carriers use their canonical value spelling.
    Every data-dependent scan is preceded by deterministic selection-work accounting.
    """
    if not is_selected_value(value):
        raise TypeError("selected-value sizing requires a closed SelectedValue")
    if type(meter) is not SelectionMeter:
        raise TypeError("selected-value sizing requires a SelectionMeter")
    if is_json_value(value):
        return json_value_size(value, meter)
    if (failure := _charge(meter)) is not None:
        return failure
    return _non_json_value_size(value, meter)


def _non_json_value_size(
    value: SelectedValue,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    if type(value) is BytesValue:
        return ByteCount(value=len(value.value))
    if type(value) is HttpFieldTextValue:
        return _utf8_size(value.value, meter)
    if type(value) is BareQueryFieldValue:
        return ByteCount(value=0)
    if type(value) is HttpFieldItems:
        total = max(0, len(value.items) - 1)
        for item in value.items:
            if (failure := _charge(meter)) is not None:
                return failure
            sized = _utf8_size(item.value, meter)
            if type(sized) is SelectionBudgetExceeded:
                return sized
            total += sized.value
        return ByteCount(value=total)
    return _typed_value_size(value, meter)


def _typed_value_size(
    value: SelectedValue,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    if type(value) is Count:
        return _integer_size(value.value, meter)
    if type(value) is ByteCount:
        return _integer_size(value.value, meter)
    if type(value) is Duration:
        return _integer_size(value.nanoseconds, meter)
    if type(value) is Ratio:
        numerator = _integer_size(value.numerator, meter)
        if type(numerator) is SelectionBudgetExceeded:
            return numerator
        denominator = _integer_size(value.denominator.value, meter)
        if type(denominator) is SelectionBudgetExceeded:
            return denominator
        return ByteCount(value=numerator.value + 1 + denominator.value)
    if type(value) is UtcInstant:
        return _integer_size(value.unix_nanoseconds, meter)
    return _identity_or_media_size(value, meter)


def _identity_or_media_size(
    value: SelectedValue,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    if type(value) is BodyId:
        return ByteCount(value=len(str(value.value)))
    if type(value) is ViewId:
        return ByteCount(value=len(str(value.value)))
    if type(value) is not MediaType:
        raise TypeError("unsupported selected value")  # pragma: no cover
    return _media_type_size(value, meter)


def _media_type_size(
    value: MediaType,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    total = 1
    for component in (value.type, value.subtype):
        sized = _utf8_size(component, meter)
        if type(sized) is SelectionBudgetExceeded:
            return sized
        total += sized.value
    for parameter in value.parameters:
        if (failure := _charge(meter)) is not None:
            return failure
        name_size = _utf8_size(parameter.name, meter)
        if type(name_size) is SelectionBudgetExceeded:
            return name_size
        parameter_size = _media_parameter_size(parameter.value, meter)
        if type(parameter_size) is SelectionBudgetExceeded:
            return parameter_size
        total += 3 + name_size.value + parameter_size.value
    return ByteCount(value=total)


def _media_parameter_size(
    value: MediaTypeTokenValue | MediaTypeQuotedValue,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    sized = _utf8_size(value.value, meter)
    if type(sized) is SelectionBudgetExceeded or type(value) is MediaTypeTokenValue:
        return sized
    escapes = 0
    for character in value.value:
        if (failure := _charge(meter)) is not None:
            return failure
        escapes += int(character in {'"', "\\"})
    return ByteCount(value=sized.value + escapes + 2)


def json_value_size(
    value: JsonValue,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    """Measure compact UTF-8 JSON iteratively, including exact escaping overhead."""
    if not is_json_value(value):
        raise TypeError("JSON sizing requires an exact JsonValue")
    if type(meter) is not SelectionMeter:
        raise TypeError("JSON sizing requires a SelectionMeter")
    total = 0
    stack: list[_SizeTask] = [value]
    while stack:
        task = stack.pop()
        if (failure := _charge(meter)) is not None:
            return failure
        if type(task) is _ArrayCursor:
            _advance_array_cursor(task, stack)
            continue
        if type(task) is _ObjectCursor:
            sized = _advance_object_cursor(task, stack, meter)
            if type(sized) is SelectionBudgetExceeded:
                return sized
            total += sized.value
            continue
        contribution = _json_contribution(task, meter)
        if type(contribution) is SelectionBudgetExceeded:
            return contribution
        sized, cursor = contribution
        total += sized.value
        if cursor is not None:
            stack.append(cursor)
    return ByteCount(value=total)


def _json_contribution(
    value: JsonValue,
    meter: SelectionMeter,
) -> tuple[ByteCount, _Cursor | None] | SelectionBudgetExceeded:
    if type(value) is JsonNull:
        return ByteCount(value=4), None
    if type(value) is JsonBoolean:
        return ByteCount(value=4 if value.value else 5), None
    if type(value) is JsonInteger:
        sized = _integer_size(value.value, meter)
        return sized if type(sized) is SelectionBudgetExceeded else (sized, None)
    if type(value) is JsonDecimal:
        return ByteCount(value=_decimal_size(value)), None
    if type(value) is JsonString:
        sized = _json_string_size(value.value, meter)
        return sized if type(sized) is SelectionBudgetExceeded else (sized, None)
    if type(value) is JsonArray:
        cursor: _Cursor | None = _ArrayCursor(value=value, position=0) if value.items else None
        return ByteCount(value=2 + max(0, len(value.items) - 1)), cursor
    if type(value) is JsonObject:
        cursor = _ObjectCursor(value=value, position=0) if value.members else None
        return ByteCount(value=2 + max(0, len(value.members) - 1)), cursor
    raise TypeError("unsupported JSON payload value")  # pragma: no cover


def _advance_array_cursor(cursor: _ArrayCursor, stack: list[_SizeTask]) -> None:
    next_index = cursor.position + 1
    if next_index < len(cursor.value.items):
        stack.append(_ArrayCursor(value=cursor.value, position=next_index))
    stack.append(cursor.value.items[cursor.position])


def _advance_object_cursor(
    cursor: _ObjectCursor,
    stack: list[_SizeTask],
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    member = cursor.value.members[cursor.position]
    sized = _json_string_size(member.name, meter)
    if type(sized) is SelectionBudgetExceeded:
        return sized
    next_index = cursor.position + 1
    if next_index < len(cursor.value.members):
        stack.append(_ObjectCursor(value=cursor.value, position=next_index))
    stack.append(member.value)
    return ByteCount(value=sized.value + 1)


def _decimal_size(value: JsonDecimal) -> int:
    sign, digits, exponent = value.value.as_tuple()
    exact_exponent = cast("int", exponent)
    point = len(digits) + exact_exponent
    sign_size = int(bool(sign))
    if point > 0:
        return sign_size + len(digits) + 1
    return sign_size + 2 + (-point) + len(digits)


def _integer_size(
    value: int,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    remaining = abs(value)
    digits = 1
    chunk = 1_000_000_000_000_000_000
    while remaining >= chunk:
        if (failure := _charge(meter)) is not None:
            return failure
        remaining //= chunk
        digits += 18
    if remaining:
        digits += len(str(remaining)) - 1
    return ByteCount(value=digits + int(value < 0))


def _json_string_size(
    value: str,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    size = 2
    for character in value:
        if (failure := _charge(meter)) is not None:
            return failure
        codepoint = ord(character)
        if character in {'"', "\\"} or character in "\b\f\n\r\t":
            size += 2
        elif codepoint < 0x20 or 0xD800 <= codepoint <= 0xDFFF:
            size += 6
        else:
            size += len(character.encode("utf-8"))
    return ByteCount(value=size)


def _utf8_size(
    value: str,
    meter: SelectionMeter,
) -> ByteCount | SelectionBudgetExceeded:
    size = 0
    for character in value:
        if (failure := _charge(meter)) is not None:
            return failure
        size += len(character.encode("utf-8"))
    return ByteCount(value=size)


def _charge(meter: SelectionMeter) -> SelectionBudgetExceeded | None:
    exhaustion = meter.charge_work()
    return None if exhaustion is None else SelectionBudgetExceeded(exhaustion=exhaustion)


__all__ = ("json_value_size", "selected_value_size")
