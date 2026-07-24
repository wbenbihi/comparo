"""Exercise strict sensitivity, sizing, and canonical diff value semantics."""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.value_support import canonical_key
from comparo.core.diffs.value_support import contain
from comparo.core.diffs.value_support import json_text_size
from comparo.core.diffs.value_support import merge_taint_origins
from comparo.core.diffs.value_support import require_work
from comparo.core.diffs.value_support import reveal
from comparo.core.diffs.value_support import value_size
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.values import BareQueryFieldValue
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
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.diffs.factories import meter

_ORIGIN = TaintOriginId(value="diff-value-test")


def test_reveal_and_contain_preserve_sensitive_lineage() -> None:
    value = JsonString(value="secret")
    assert contain(value, ()) is value
    sealed = contain(value, (_ORIGIN,))
    assert type(sealed) is SensitiveValue
    assert reveal(sealed) == value
    assert reveal(value) is value


def test_require_work_raises_exact_budget_exhaustion() -> None:
    with pytest.raises(DiffHaltError) as caught:
        require_work(meter(work=0))
    assert caught.value.exhaustion is not None


def test_taint_origin_union_is_stable_unique_and_strictly_metered() -> None:
    second = TaintOriginId(value="diff-value-second")
    assert merge_taint_origins(meter(), (_ORIGIN, second), (_ORIGIN,)) == (
        _ORIGIN,
        second,
    )
    with pytest.raises(DiffHaltError) as caught:
        merge_taint_origins(meter(work=1), (_ORIGIN, second))
    assert caught.value.exhaustion is not None
    assert caught.value.code is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (BytesValue(value=b"abc"), 3),
        (HttpFieldTextValue(value="é"), 2),
        (BareQueryFieldValue(), 0),
        (
            HttpFieldItems(
                items=(
                    MediaTypeTokenValue(value="x"),
                    MediaTypeTokenValue(value="yz"),
                )
            ),
            4,
        ),
        (HttpFieldItems(items=()), 0),
        (ByteCount(value=12), 2),
        (Count(value=3), 1),
        (Duration(nanoseconds=45), 2),
        (Ratio(numerator=1, denominator=PositiveCount(value=2)), 3),
        (UtcInstant(unix_nanoseconds=-1), 2),
        (BodyId(value=UUID(int=1)), 36),
        (ViewId(value=UUID(int=2)), 36),
        (
            MediaType(
                type="application",
                subtype="json",
                parameters=(
                    MediaTypeParameter(
                        name="charset",
                        value=MediaTypeTokenValue(value="utf-8"),
                    ),
                ),
            ),
            len("application/json;charset=utf-8"),
        ),
    ],
)
def test_value_size_covers_every_non_json_carrier(
    value: SelectedValue,
    expected: int,
) -> None:
    assert value_size(value, meter()) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (JsonNull(), 4),
        (JsonBoolean(value=True), 4),
        (JsonBoolean(value=False), 5),
        (JsonInteger(value=-12), 3),
        (JsonDecimal(value=Decimal("1.25")), 4),
        (JsonString(value='a"\\\n'), 13),
        (JsonString(value="é"), 4),
        (JsonArray(items=(JsonInteger(value=1), JsonNull())), 8),
        (JsonArray(items=()), 2),
        (JsonObject(members=()), 2),
        (
            JsonObject(members=(JsonObjectMember(name='a"', value=JsonBoolean(value=True)),)),
            12,
        ),
    ],
)
def test_json_value_size_is_deterministic_and_escape_aware(
    value: SelectedValue,
    expected: int,
) -> None:
    assert value_size(value, meter()) == expected


def test_value_size_rejects_values_outside_the_closed_union() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        value_size(cast("SelectedValue", object()), meter())


def test_canonical_key_normalizes_json_numbers_and_object_member_order() -> None:
    first = JsonObject(
        members=(
            JsonObjectMember(name="b", value=JsonArray(items=(JsonInteger(value=2),))),
            JsonObjectMember(name="a", value=JsonDecimal(value=Decimal("1.5"))),
        )
    )
    second = JsonObject(
        members=(
            JsonObjectMember(name="a", value=JsonDecimal(value=Decimal("1.5"))),
            JsonObjectMember(name="b", value=JsonArray(items=(JsonInteger(value=2),))),
        )
    )
    assert canonical_key(first, meter()) == canonical_key(second, meter())


@pytest.mark.parametrize(
    "value",
    [
        JsonNull(),
        JsonBoolean(value=True),
        JsonInteger(value=1),
        JsonDecimal(value=Decimal("1.5")),
        JsonString(value="x"),
        BytesValue(value=b"x"),
        Count(value=1),
    ],
)
def test_canonical_key_has_strict_scalar_type_identity(value: SelectedValue) -> None:
    key = canonical_key(value, meter())
    assert type(key) is tuple
    assert key


def test_canonical_key_fails_closed_at_the_maximum_depth() -> None:
    value: SelectedValue = JsonNull()
    for _ in range(258):
        value = JsonArray(items=(cast("JsonNull | JsonArray", value),))
    with pytest.raises(DiffHaltError) as caught:
        canonical_key(value, meter())
    assert caught.value.code is not None
    assert caught.value.code.value == "diff.value.depth-unsupported"


def test_canonical_key_and_text_sizing_honor_work_budget() -> None:
    with pytest.raises(DiffHaltError):
        canonical_key(JsonString(value="x"), meter(work=1))
    with pytest.raises(DiffHaltError):
        value_size(JsonString(value="x"), meter(work=1))
    with pytest.raises(DiffHaltError):
        canonical_key(JsonInteger(value=10**10_000), meter(work=10))
    with pytest.raises(DiffHaltError):
        canonical_key(
            JsonArray(items=tuple(JsonInteger(value=index) for index in range(1_000))),
            meter(work=10),
        )


def test_decimal_sizing_keeps_large_exponents_in_compact_scientific_form() -> None:
    assert value_size(JsonDecimal(value=Decimal("1E-100000")), meter(work=100)) == len("1E-100000")


@pytest.mark.parametrize("repetitions", [-1, True])
def test_json_text_size_rejects_invalid_repetitions(repetitions: int) -> None:
    with pytest.raises(ValueError, match="repetitions"):
        json_text_size("x", meter(), repetitions=repetitions)
