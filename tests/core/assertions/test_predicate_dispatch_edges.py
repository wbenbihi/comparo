"""Typed predicate dispatch across supported value and relation variants."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest

from comparo.core.assertions.predicates import PredicateSatisfied
from comparo.core.assertions.predicates import PredicateViolated
from comparo.core.domain.assertions.operators import ContainsMode
from comparo.core.domain.assertions.operators import ContainsOperator
from comparo.core.domain.assertions.operators import CountRelation
from comparo.core.domain.assertions.operators import ExactValueType
from comparo.core.domain.assertions.operators import KeySetOperator
from comparo.core.domain.assertions.operators import KeySetRelation
from comparo.core.domain.assertions.operators import LengthOperator
from comparo.core.domain.assertions.operators import OrderingOperator
from comparo.core.domain.assertions.operators import OrderingRelation
from comparo.core.domain.assertions.operators import StringOperator
from comparo.core.domain.assertions.operators import StringRelation
from comparo.core.domain.assertions.operators import TypeOperator
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.assertions.test_predicates import _evaluate
from tests.core.domain.assertions.test_contracts import _literal


@pytest.mark.parametrize(
    ("value", "operator", "expected"),
    [
        (JsonNull(), TypeOperator(expected=ExactValueType.NULL), PredicateSatisfied),
        (
            JsonBoolean(value=True),
            TypeOperator(expected=ExactValueType.BOOLEAN),
            PredicateSatisfied,
        ),
        (
            JsonDecimal(value=Decimal("1.1")),
            TypeOperator(expected=ExactValueType.NUMBER),
            PredicateSatisfied,
        ),
        (JsonString(value="x"), TypeOperator(expected=ExactValueType.STRING), PredicateSatisfied),
        (JsonArray(items=()), TypeOperator(expected=ExactValueType.ARRAY), PredicateSatisfied),
        (JsonObject(members=()), TypeOperator(expected=ExactValueType.OBJECT), PredicateSatisfied),
        (BytesValue(value=b"x"), TypeOperator(expected=ExactValueType.BYTES), PredicateSatisfied),
        (
            Duration(nanoseconds=1),
            TypeOperator(expected=ExactValueType.DURATION),
            PredicateSatisfied,
        ),
        (ByteCount(value=1), TypeOperator(expected=ExactValueType.SIZE), PredicateSatisfied),
        (
            UtcInstant(unix_nanoseconds=1),
            TypeOperator(expected=ExactValueType.INSTANT),
            PredicateSatisfied,
        ),
        (JsonInteger(value=1), TypeOperator(expected=ExactValueType.STRING), PredicateViolated),
        (
            JsonInteger(value=1),
            OrderingOperator(
                relation=OrderingRelation.LESS_OR_EQUAL, expected=_literal(JsonInteger(value=1))
            ),
            PredicateSatisfied,
        ),
        (
            JsonInteger(value=1),
            OrderingOperator(
                relation=OrderingRelation.GREATER_OR_EQUAL, expected=_literal(JsonInteger(value=1))
            ),
            PredicateSatisfied,
        ),
        (
            JsonString(value="abcdef"),
            StringOperator(
                relation=StringRelation.NOT_CONTAINS, expected=_literal(JsonString(value="z"))
            ),
            PredicateSatisfied,
        ),
        (
            JsonString(value="abcdef"),
            StringOperator(
                relation=StringRelation.ENDS_WITH, expected=_literal(JsonString(value="def"))
            ),
            PredicateSatisfied,
        ),
        (
            JsonString(value="abc"),
            ContainsOperator(
                mode=ContainsMode.ALL,
                expected=(_literal(JsonString(value="a")), _literal(JsonString(value="b"))),
            ),
            PredicateSatisfied,
        ),
        (
            JsonObject(members=(JsonObjectMember(name="a", value=JsonInteger(value=1)),)),
            ContainsOperator(
                mode=ContainsMode.ANY,
                expected=(_literal(JsonString(value="x")), _literal(JsonString(value="a"))),
            ),
            PredicateSatisfied,
        ),
        (
            JsonObject(members=(JsonObjectMember(name="a", value=JsonInteger(value=1)),)),
            KeySetOperator(relation=KeySetRelation.HAS_ALL, keys=("a",)),
            PredicateSatisfied,
        ),
        (
            JsonString(value="ab"),
            LengthOperator(relation=CountRelation.AT_LEAST, expected=Count(value=2)),
            PredicateSatisfied,
        ),
        (
            JsonObject(members=()),
            LengthOperator(relation=CountRelation.AT_MOST, expected=Count(value=0)),
            PredicateSatisfied,
        ),
        (
            HttpFieldItems(items=()),
            LengthOperator(relation=CountRelation.EQUALS, expected=Count(value=0)),
            PredicateSatisfied,
        ),
    ],
)
def test_additional_predicate_dispatch_branches(
    value: object, operator: object, expected: type[object]
) -> None:
    assert type(_evaluate(cast("object", value), cast("object", operator))) is expected
