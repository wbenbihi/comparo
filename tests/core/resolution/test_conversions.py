"""Strict conversion vocabulary and failure behavior."""

from decimal import Decimal
from typing import cast

import pytest

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
from comparo.core.domain.values import ResolvedValue
from comparo.core.resolution.conversions import ConversionError
from comparo.core.resolution.conversions import convert
from comparo.core.resolution.conversions import trim_bytes


@pytest.mark.parametrize(
    ("trim", "expected"),
    [
        (FileTrim.NONE, b" x \n"),
        (FileTrim.START, b"x \n"),
        (FileTrim.END, b" x"),
        (FileTrim.BOTH, b"x"),
    ],
)
def test_file_trimming_is_explicit(trim: FileTrim, expected: bytes) -> None:
    assert trim_bytes(BytesValue(value=b" x \n"), trim) == BytesValue(value=expected)


@pytest.mark.parametrize(
    ("value", "target", "expected"),
    [
        (JsonString(value="text"), ConversionType.STRING, JsonString(value="text")),
        (BytesValue(value=b"text"), ConversionType.STRING, JsonString(value="text")),
        (JsonNull(), ConversionType.STRING, JsonString(value="null")),
        (JsonBoolean(value=True), ConversionType.STRING, JsonString(value="true")),
        (JsonBoolean(value=False), ConversionType.STRING, JsonString(value="false")),
        (JsonInteger(value=12), ConversionType.STRING, JsonString(value="12")),
        (
            JsonDecimal(value=Decimal("1.25")),
            ConversionType.STRING,
            JsonString(value="1.25"),
        ),
        (Duration(nanoseconds=12), ConversionType.STRING, JsonString(value="12ns")),
        (ByteCount(value=12), ConversionType.STRING, JsonString(value="12B")),
        (JsonString(value="text"), ConversionType.BYTES, BytesValue(value=b"text")),
        (BytesValue(value=b"text"), ConversionType.BYTES, BytesValue(value=b"text")),
        (JsonString(value="-12"), ConversionType.INTEGER, JsonInteger(value=-12)),
        (JsonString(value="12"), ConversionType.NUMBER, JsonInteger(value=12)),
        (
            JsonString(value="1.25e2"),
            ConversionType.NUMBER,
            JsonInteger(value=125),
        ),
        (JsonString(value="true"), ConversionType.BOOLEAN, JsonBoolean(value=True)),
        (BytesValue(value=b"false"), ConversionType.BOOLEAN, JsonBoolean(value=False)),
        (JsonString(value="1.5ms"), ConversionType.DURATION, Duration(nanoseconds=1_500_000)),
        (JsonString(value="2KiB"), ConversionType.SIZE, ByteCount(value=2_048)),
    ],
)
def test_scalar_conversions_are_strict_and_exact(
    value: ResolvedValue,
    target: ConversionType,
    expected: ResolvedValue,
) -> None:
    assert convert(value, target) == expected


def test_json_conversion_preserves_exact_values_and_duplicate_rejection() -> None:
    result = convert(
        JsonString(
            value='{"nothing":null,"flag":true,"integer":2,"decimal":1.25,'
            '"text":"é","array":[false]}'
        ),
        ConversionType.JSON,
    )
    assert result == JsonObject(
        members=(
            JsonObjectMember(name="nothing", value=JsonNull()),
            JsonObjectMember(name="flag", value=JsonBoolean(value=True)),
            JsonObjectMember(name="integer", value=JsonInteger(value=2)),
            JsonObjectMember(name="decimal", value=JsonDecimal(value=Decimal("1.25"))),
            JsonObjectMember(name="text", value=JsonString(value="é")),
            JsonObjectMember(
                name="array",
                value=JsonArray(items=(JsonBoolean(value=False),)),
            ),
        )
    )
    assert convert(BytesValue(value=b'"bytes"'), ConversionType.JSON) == JsonString(value="bytes")
    with pytest.raises(ConversionError, match="explicit value conversion failed"):
        convert(JsonString(value='{"same":1,"same":2}'), ConversionType.JSON)


def test_structured_string_encoding_is_canonical_and_decimal_exact() -> None:
    value = JsonObject(
        members=(
            JsonObjectMember(
                name="array",
                value=JsonArray(
                    items=(
                        JsonNull(),
                        JsonBoolean(value=False),
                        JsonInteger(value=2),
                        JsonDecimal(value=Decimal("1.0000000000000000001")),
                        JsonString(value="é"),
                    )
                ),
            ),
        )
    )
    assert convert(value, ConversionType.STRING) == JsonString(
        value='{"array":[null,false,2,1.0000000000000000001,"é"]}'
    )


@pytest.mark.parametrize(
    ("value", "target"),
    [
        (JsonInteger(value=1), ConversionType.BYTES),
        (JsonString(value="01"), ConversionType.INTEGER),
        (JsonString(value="+1"), ConversionType.NUMBER),
        (JsonString(value="TRUE"), ConversionType.BOOLEAN),
        (JsonArray(items=()), ConversionType.INTEGER),
        (JsonString(value="bad"), ConversionType.JSON),
        (JsonString(value="value"), ConversionType.YAML),
        (JsonString(value="1day"), ConversionType.DURATION),
        (JsonString(value="0.0000000001s"), ConversionType.DURATION),
        (JsonString(value="-1B"), ConversionType.SIZE),
        (BytesValue(value=b"\xff"), ConversionType.STRING),
    ],
)
def test_invalid_conversions_have_one_payload_free_error(
    value: ResolvedValue,
    target: ConversionType,
) -> None:
    with pytest.raises(ConversionError, match="explicit value conversion failed"):
        convert(value, target)


def test_unknown_runtime_conversion_value_fails_closed() -> None:
    with pytest.raises(ConversionError, match="explicit value conversion failed"):
        convert(JsonString(value="1"), cast("ConversionType", object()))
