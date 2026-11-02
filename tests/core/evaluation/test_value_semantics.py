"""Tests for neutral strict evaluator value semantics."""

from decimal import Decimal

import pytest

from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.evaluation.value_semantics import collection_values
from comparo.core.evaluation.value_semantics import numeric_value
from comparo.core.evaluation.value_semantics import semantic_compare
from comparo.core.evaluation.value_semantics import semantic_equal
from comparo.core.evaluation.value_semantics import text_value


def _object(*members: tuple[str, object]) -> JsonObject:
    return JsonObject(
        members=tuple(JsonObjectMember(name=name, value=value) for name, value in members)  # type: ignore[arg-type]
    )


def test_structural_equality_ignores_object_order_but_not_array_order() -> None:
    left = _object(
        ("a", JsonInteger(value=1)),
        ("nested", JsonArray(items=(JsonString(value="x"), JsonBoolean(value=True)))),
    )
    reordered = _object(
        ("nested", JsonArray(items=(JsonString(value="x"), JsonBoolean(value=True)))),
        ("a", JsonInteger(value=1)),
    )
    changed_array = _object(
        ("a", JsonInteger(value=1)),
        ("nested", JsonArray(items=(JsonBoolean(value=True), JsonString(value="x")))),
    )

    assert semantic_equal(left, reordered)
    assert not semantic_equal(left, changed_array)


def test_structural_equality_is_strict_for_types_and_members() -> None:
    assert semantic_equal(JsonNull(), JsonNull())
    assert semantic_equal(JsonDecimal(value=Decimal("1.5")), JsonDecimal(value=Decimal("1.5")))
    assert not semantic_equal(JsonString(value="a"), JsonString(value="b"))
    assert not semantic_equal(JsonBoolean(value=True), JsonInteger(value=1))
    assert not semantic_equal(
        JsonArray(items=(JsonInteger(value=1),)),
        JsonArray(items=()),
    )
    assert not semantic_equal(
        _object(("a", JsonInteger(value=1))),
        JsonObject(members=()),
    )
    assert not semantic_equal(
        _object(("a", JsonInteger(value=1))),
        _object(("b", JsonInteger(value=1))),
    )
    assert not semantic_equal(JsonString(value="x"), HttpFieldTextValue(value="x"))
    with pytest.raises(TypeError):
        semantic_equal(object(), JsonNull())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (JsonInteger(value=1), JsonDecimal(value=Decimal("1.5")), -1),
        (Duration(nanoseconds=2), Duration(nanoseconds=1), 1),
        (ByteCount(value=3), ByteCount(value=3), 0),
        (Count(value=1), Count(value=2), -1),
        (
            Ratio(numerator=1, denominator=PositiveCount(value=2)),
            Ratio(numerator=2, denominator=PositiveCount(value=3)),
            -1,
        ),
        (UtcInstant(unix_nanoseconds=2), UtcInstant(unix_nanoseconds=1), 1),
        (JsonString(value="a"), JsonString(value="b"), None),
        (Count(value=1), JsonInteger(value=1), None),
    ],
)
def test_semantic_compare_supports_only_explicit_ordered_domains(
    left: object,
    right: object,
    expected: int | None,
) -> None:
    assert semantic_compare(left, right) == expected  # type: ignore[arg-type]


def test_text_numeric_and_collection_accessors_are_explicit() -> None:
    token = MediaTypeTokenValue(value="gzip")
    items = HttpFieldItems(items=(token,))

    assert numeric_value(JsonInteger(value=7)) == Decimal(7)
    assert text_value(JsonString(value="json")) == "json"
    assert text_value(HttpFieldTextValue(value="http")) == "http"
    assert text_value(JsonInteger(value=1)) is None
    assert collection_values(JsonArray(items=(JsonInteger(value=1),))) == (JsonInteger(value=1),)
    assert collection_values(items) == (token,)
    assert collection_values(JsonNull()) is None
    with pytest.raises(TypeError):
        numeric_value(JsonString(value="1"))
