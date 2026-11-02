"""Sensitivity-preserving value access, sizing, and canonical identities."""

from __future__ import annotations

from decimal import Decimal
from typing import Final
from typing import cast

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
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

_ACCESS: Final = SensitiveValueAccess(purpose="diff.evaluate.value")
_MAX_CANONICAL_DEPTH: Final = 256
_DEPTH_UNSUPPORTED: Final = DiagnosticCode(value="diff.value.depth-unsupported")


def reveal(value: SelectedValue | SensitiveValue[SelectedValue]) -> SelectedValue:
    """Reveal only within this private pure semantic kernel."""
    if is_sensitive_value(value):
        sensitive = cast("SensitiveValue[SelectedValue]", value)
        return sensitive.reveal(access=_ACCESS)
    return value  # type: ignore[return-value]


def contain(
    value: SelectedValue,
    origins: tuple[TaintOriginId, ...],
) -> SelectedValue | SensitiveValue[SelectedValue]:
    """Seal every derived result carrying any sensitive lineage."""
    return SensitiveValue(value, origins=origins) if origins else value


def require_work(meter: DiffMeter, amount: int = 1) -> None:
    """Stop before performing unbudgeted data-dependent work."""
    if (failure := meter.charge_work(amount)) is not None:
        raise DiffHaltError(exhaustion=failure)


def require_derived_capacity(meter: DiffMeter, size: int) -> None:
    """Fail before allocating a derived payload that cannot be retained."""
    if (failure := meter.derived_exhaustion(size)) is not None:
        raise DiffHaltError(exhaustion=failure)


def require_sort_work(meter: DiffMeter, count: int) -> None:
    """Charge a conservative comparison-sort upper shape before sorting."""
    require_work(meter, max(1, count * max(1, count.bit_length())))


def merge_taint_origins(
    meter: DiffMeter,
    *groups: tuple[TaintOriginId, ...],
) -> tuple[TaintOriginId, ...]:
    """Meter and stably deduplicate every consumed, domain-bounded origin ID."""
    seen: set[TaintOriginId] = set()
    output: list[TaintOriginId] = []
    for group in groups:
        for origin in group:
            require_work(meter)
            if origin not in seen:
                seen.add(origin)
                output.append(origin)
    return tuple(output)


def json_text_size(value: str, meter: DiffMeter, *, repetitions: int = 1) -> int:
    """Measure JSON string content and charge every represented repetition."""
    if type(repetitions) is not int or repetitions < 0:
        raise ValueError("JSON text repetitions must be a non-negative integer")
    total = 0
    for character in value:
        require_work(meter, max(1, repetitions))
        codepoint = ord(character)
        width = (
            2
            if character in {'"', "\\"} and codepoint >= 0x20
            else 6
            if codepoint < 0x20
            else len(character.encode("utf-8"))
        )
        total += width * repetitions
    return total


def value_size(value: SelectedValue, meter: DiffMeter) -> int:  # noqa: C901
    """Compute a deterministic bounded representation size without serialization."""
    require_work(meter)
    if is_json_value(value):
        return _json_size(value, meter)
    if type(value) is BytesValue:
        return len(value.value)
    if type(value) is HttpFieldTextValue:
        return _text_size(value.value, meter)
    if type(value) is BareQueryFieldValue:
        return 0
    if type(value) is HttpFieldItems:
        return sum(_text_size(item.value, meter) for item in value.items) + max(
            0, len(value.items) - 1
        )
    if type(value) is ByteCount:
        return _integer_text_size(value.value, meter)
    if type(value) is Count:
        return _integer_text_size(value.value, meter)
    if type(value) is Duration:
        return _integer_text_size(value.nanoseconds, meter)
    if type(value) is Ratio:
        return (
            _integer_text_size(value.numerator, meter)
            + 1
            + _integer_text_size(value.denominator.value, meter)
        )
    if type(value) is UtcInstant:
        return _integer_text_size(value.unix_nanoseconds, meter)
    if type(value) in {BodyId, ViewId}:
        return 36
    if type(value) is MediaType:
        return _text_size(_media_text(value), meter)
    raise TypeError("unsupported derived value")


def canonical_key(value: SelectedValue, meter: DiffMeter) -> tuple[object, ...]:  # noqa: C901
    """Build a bounded strict identity without recursive Python evaluation."""
    stack: list[tuple[SelectedValue, int, bool]] = [(value, 0, False)]
    completed: dict[int, tuple[object, ...]] = {}
    while stack:
        current, depth, expanded = stack.pop()
        if depth > _MAX_CANONICAL_DEPTH:
            raise DiffHaltError(code=_DEPTH_UNSUPPORTED)
        if expanded:
            if type(current) is JsonArray:
                completed[id(current)] = (
                    "array",
                    tuple(completed[id(item)] for item in current.items),
                )
            else:
                object_value = cast("JsonObject", current)
                require_work(meter, max(1, len(object_value.members)))
                require_sort_work(meter, len(object_value.members))
                members = tuple(
                    sorted(
                        (
                            (_charged_text(member.name, meter), completed[id(member.value)])
                            for member in object_value.members
                        ),
                        key=lambda item: item[0],
                    )
                )
                completed[id(object_value)] = ("object", members)
            continue
        require_work(meter)
        if type(current) is JsonNull:
            completed[id(current)] = ("null",)
        elif type(current) is JsonBoolean:
            completed[id(current)] = ("boolean", current.value)
        elif type(current) is JsonInteger:
            _integer_text_size(current.value, meter)
            completed[id(current)] = ("number", Decimal(current.value))
        elif type(current) is JsonDecimal:
            completed[id(current)] = ("number", current.value)
        elif type(current) is JsonString:
            _text_size(current.value, meter)
            completed[id(current)] = ("string", current.value)
        elif type(current) is JsonArray:
            require_work(meter, max(1, len(current.items)))
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in reversed(current.items))
        elif type(current) is JsonObject:
            require_work(meter, max(1, len(current.members)))
            stack.append((current, depth, True))
            stack.extend((member.value, depth + 1, False) for member in reversed(current.members))
        elif type(current) is BytesValue:
            require_work(meter, max(1, len(current.value)))
            completed[id(current)] = ("bytes", current.value)
        else:
            completed[id(current)] = (type(current).__qualname__, current)
    return completed[id(value)]


def _json_size(root: JsonValue, meter: DiffMeter) -> int:
    total = 0
    stack: list[JsonValue] = [root]
    while stack:
        require_work(meter)
        value = stack.pop()
        if type(value) is JsonNull:
            total += 4
        elif type(value) is JsonBoolean:
            total += 4 if value.value else 5
        elif type(value) is JsonInteger:
            total += _integer_text_size(value.value, meter)
        elif type(value) is JsonDecimal:
            total += _decimal_text_size(value.value, meter)
        elif type(value) is JsonString:
            total += 2 + _json_text_size(value.value, meter)
        elif type(value) is JsonArray:
            total += 2 + max(0, len(value.items) - 1)
            require_work(meter, max(1, len(value.items)))
            stack.extend(reversed(value.items))
        else:
            total += 2 + max(0, len(value.members) - 1)
            require_work(meter, max(1, len(value.members)))
            for member in reversed(value.members):
                total += 3 + _json_text_size(member.name, meter)
                stack.append(member.value)
    return total


def _text_size(value: str, meter: DiffMeter) -> int:
    total = 0
    for character in value:
        require_work(meter)
        total += len(character.encode("utf-8"))
    return total


def _json_text_size(value: str, meter: DiffMeter) -> int:
    return json_text_size(value, meter)


def _charged_text(value: str, meter: DiffMeter) -> str:
    _text_size(value, meter)
    return value


def _integer_text_size(value: int, meter: DiffMeter) -> int:
    """Measure arbitrary-size signed integers without converting them to text."""
    rough_digits = max(1, (abs(value).bit_length() * 30_103) // 100_000 + 1)
    require_work(meter, rough_digits)
    digits = _integer_digits(abs(value))
    return digits + (1 if value < 0 else 0)


def _integer_digits(value: int) -> int:
    if value == 0:
        return 1
    estimate = (value.bit_length() * 30_103) // 100_000 + 1
    return estimate - 1 if value < 10 ** (estimate - 1) else estimate


def _decimal_text_size(value: Decimal, meter: DiffMeter) -> int:
    """Measure Decimal's canonical text form without allocating exponent-sized text."""
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast("int", raw_exponent)
    digit_count = max(1, len(digits))
    adjusted = digit_count + exponent - 1
    require_work(meter, digit_count + _integer_digits(abs(exponent)))
    sign_size = int(bool(sign))
    if exponent > 0 or adjusted < -6:
        coefficient_size = digit_count + int(digit_count > 1)
        return sign_size + coefficient_size + 2 + _integer_digits(abs(adjusted))
    if adjusted >= 0:
        return sign_size + digit_count + int(exponent < 0)
    return sign_size + 2 + (-adjusted - 1) + digit_count


def _media_text(value: MediaType) -> str:
    parameters = "".join(
        f";{parameter.name}={parameter.value.value}" for parameter in value.parameters
    )
    return f"{value.type}/{value.subtype}{parameters}"


__all__ = (
    "canonical_key",
    "contain",
    "json_text_size",
    "merge_taint_origins",
    "require_derived_capacity",
    "require_sort_work",
    "require_work",
    "reveal",
    "value_size",
)
