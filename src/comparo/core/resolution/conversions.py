"""Strict explicit conversions with no implicit type inference."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import cast

from comparo.core.domain.expression_types import ConversionType
from comparo.core.domain.expression_types import FileTrim
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import json_number

_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_DURATION = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ns|us|ms|s|m|h)")
_SIZE = re.compile(r"([0-9]+)(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)")
_DURATION_FACTORS = {
    "ns": Decimal(1),
    "us": Decimal(1_000),
    "ms": Decimal(1_000_000),
    "s": Decimal(1_000_000_000),
    "m": Decimal(60_000_000_000),
    "h": Decimal(3_600_000_000_000),
}
_SIZE_FACTORS = {
    "B": 1,
    "KB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "KiB": 1_024,
    "MiB": 1_048_576,
    "GiB": 1_073_741_824,
    "TiB": 1_099_511_627_776,
}


class ConversionError(ValueError):
    """A payload-free signal that one explicit conversion was invalid."""


def trim_bytes(value: BytesValue, trim: FileTrim) -> BytesValue:
    """Apply explicitly requested ASCII whitespace trimming to raw file bytes."""
    if trim is FileTrim.NONE:
        return value
    if trim is FileTrim.START:
        return BytesValue(value=value.value.lstrip())
    if trim is FileTrim.END:
        return BytesValue(value=value.value.rstrip())
    return BytesValue(value=value.value.strip())


def convert(value: ResolvedValue, target: ConversionType) -> ResolvedValue:
    """Apply one strict conversion and raise only a payload-free failure."""
    try:
        return _convert(value, target)
    except (ArithmeticError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ConversionError("explicit value conversion failed") from exc


def _convert(value: ResolvedValue, target: ConversionType) -> ResolvedValue:
    if target is ConversionType.STRING:
        return _to_string(value)
    if target is ConversionType.BYTES:
        if type(value) is BytesValue:
            return value
        if type(value) is JsonString:
            return BytesValue(value=value.value.encode("utf-8"))
        raise ConversionError("bytes conversion requires text or bytes")
    if target in {ConversionType.INTEGER, ConversionType.NUMBER, ConversionType.BOOLEAN}:
        return _convert_scalar(value, target)
    if target is ConversionType.JSON:
        return _decode_json(_text(value))
    if target is ConversionType.YAML:
        raise ConversionError("YAML conversion requires an injected decoder")
    if target in {ConversionType.DURATION, ConversionType.SIZE}:
        return _convert_unit(value, target)
    raise TypeError("unsupported conversion type")


def _convert_scalar(value: ResolvedValue, target: ConversionType) -> ResolvedValue:
    if target is ConversionType.INTEGER:
        text = _text(value)
        if _INTEGER.fullmatch(text) is None:
            raise ConversionError("integer conversion requires canonical decimal text")
        return JsonInteger(value=int(text))
    if target is ConversionType.NUMBER:
        text = _text(value)
        if _NUMBER.fullmatch(text) is None:
            raise ConversionError("number conversion requires canonical decimal text")
        return json_number(Decimal(text))
    text = _text(value)
    if text not in {"true", "false"}:
        raise ConversionError("boolean conversion requires true or false")
    return JsonBoolean(value=text == "true")


def _convert_unit(value: ResolvedValue, target: ConversionType) -> ResolvedValue:
    if target is ConversionType.DURATION:
        match = _DURATION.fullmatch(_text(value))
        if match is None:
            raise ConversionError("duration conversion requires a supported unit")
        nanoseconds = Decimal(match.group(1)) * _DURATION_FACTORS[match.group(2)]
        integral = nanoseconds.to_integral_exact()
        if integral != nanoseconds:
            raise ConversionError("duration must resolve to whole nanoseconds")
        return Duration(nanoseconds=int(integral))
    match = _SIZE.fullmatch(_text(value))
    if match is None:
        raise ConversionError("size conversion requires a supported unit")
    return ByteCount(value=int(match.group(1)) * _SIZE_FACTORS[match.group(2)])


def _text(value: ResolvedValue) -> str:
    if type(value) is JsonString:
        return value.value
    if type(value) is BytesValue:
        return value.value.decode("utf-8")
    raise ConversionError("conversion requires text or bytes")


def _to_string(value: ResolvedValue) -> JsonString:
    if type(value) is JsonString:
        return value
    if type(value) is BytesValue:
        return JsonString(value=value.value.decode("utf-8"))
    if type(value) is JsonNull:
        return JsonString(value="null")
    if type(value) is JsonBoolean:
        return JsonString(value="true" if value.value else "false")
    if type(value) is JsonInteger:
        return JsonString(value=str(value.value))
    if type(value) is JsonDecimal:
        return JsonString(value=str(value.value))
    if type(value) is Duration:
        return JsonString(value=f"{value.nanoseconds}ns")
    if type(value) is ByteCount:
        return JsonString(value=f"{value.value}B")
    return JsonString(value=_encode_json(cast("JsonValue", value)))


def _decode_json(text: str) -> JsonValue:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, item in items:
            if name in result:
                raise ConversionError("JSON object names must be unique")
            result[name] = item
        return result

    decoded = json.loads(text, parse_float=Decimal, parse_int=int, object_pairs_hook=pairs)
    return _json_value(decoded)


def _json_value(value: object) -> JsonValue:
    if value is None:
        return JsonNull()
    if type(value) is bool:
        return JsonBoolean(value=value)
    if type(value) is int:
        return JsonInteger(value=value)
    if type(value) is Decimal:
        return json_number(value)
    if type(value) is str:
        return JsonString(value=value)
    if type(value) is list:
        return JsonArray(items=tuple(_json_value(item) for item in value))
    if type(value) is dict:
        return JsonObject(
            members=tuple(
                JsonObjectMember(name=cast("str", name), value=_json_value(item))
                for name, item in value.items()
            )
        )
    raise ConversionError("decoded document contains an unsupported value")


def _encode_json(value: JsonValue) -> str:
    if type(value) is JsonNull:
        return "null"
    if type(value) is JsonBoolean:
        return "true" if value.value else "false"
    if type(value) is JsonInteger:
        return str(value.value)
    if type(value) is JsonDecimal:
        return str(value.value)
    if type(value) is JsonString:
        return json.dumps(value.value, ensure_ascii=False)
    if type(value) is JsonArray:
        return "[" + ",".join(_encode_json(item) for item in value.items) + "]"
    return (
        "{"
        + ",".join(
            f"{json.dumps(member.name, ensure_ascii=False)}:{_encode_json(member.value)}"
            for member in value.members
        )
        + "}"
    )
