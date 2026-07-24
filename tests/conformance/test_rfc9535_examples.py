"""Pinned JSONPath examples from RFC 9535, Section 1.5, Table 2.

Reference: https://www.rfc-editor.org/rfc/rfc9535#section-1.5
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.core.selection.json_evaluator_factories import json_object
from tests.core.selection.json_evaluator_factories import selection_meter

from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.selection import compile_jsonpath
from comparo.core.selection.json_evaluator import JsonEvaluationReady
from comparo.core.selection.json_evaluator import evaluate_json


def _bookstore() -> JsonObject:
    return json_object(
        store=json_object(
            book=JsonArray(
                items=(
                    json_object(
                        category=JsonString(value="reference"),
                        author=JsonString(value="Nigel Rees"),
                        title=JsonString(value="Sayings of the Century"),
                        price=JsonDecimal(value=Decimal("8.95")),
                    ),
                    json_object(
                        category=JsonString(value="fiction"),
                        author=JsonString(value="Evelyn Waugh"),
                        title=JsonString(value="Sword of Honour"),
                        price=JsonDecimal(value=Decimal("12.99")),
                    ),
                    json_object(
                        category=JsonString(value="fiction"),
                        author=JsonString(value="Herman Melville"),
                        title=JsonString(value="Moby Dick"),
                        isbn=JsonString(value="0-553-21311-3"),
                        price=JsonDecimal(value=Decimal("8.99")),
                    ),
                    json_object(
                        category=JsonString(value="fiction"),
                        author=JsonString(value="J. R. R. Tolkien"),
                        title=JsonString(value="The Lord of the Rings"),
                        isbn=JsonString(value="0-395-19395-8"),
                        price=JsonDecimal(value=Decimal("22.99")),
                    ),
                )
            ),
            bicycle=json_object(
                color=JsonString(value="red"),
                price=JsonInteger(value=399),
            ),
        )
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (
            "$.store.book[*].author",
            (
                "/store/book/0/author",
                "/store/book/1/author",
                "/store/book/2/author",
                "/store/book/3/author",
            ),
        ),
        (
            "$..author",
            (
                "/store/book/0/author",
                "/store/book/1/author",
                "/store/book/2/author",
                "/store/book/3/author",
            ),
        ),
        ("$..book[2]", ("/store/book/2",)),
        ("$..book[2].publisher", ()),
        ("$..book[-1]", ("/store/book/3",)),
        ("$..book[0,1]", ("/store/book/0", "/store/book/1")),
        ("$..book[:2]", ("/store/book/0", "/store/book/1")),
        ("$..book[?@.isbn]", ("/store/book/2", "/store/book/3")),
        ("$..book[?@.price<10]", ("/store/book/0", "/store/book/2")),
    ],
)
def test_rfc9535_bookstore_examples(
    expression: str,
    expected: tuple[str, ...],
) -> None:
    result = evaluate_json(_bookstore(), compile_jsonpath(expression), selection_meter())

    assert type(result) is JsonEvaluationReady
    assert tuple(match.pointer.value for match in result.matches) == expected
