"""Exercise immutable JSONPath AST and exact derived-metadata boundaries."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from comparo.core.domain.selectors.jsonpath import AndExpression
from comparo.core.domain.selectors.jsonpath import ChildSegment
from comparo.core.domain.selectors.jsonpath import ComparisonExpression
from comparo.core.domain.selectors.jsonpath import ComparisonOperator
from comparo.core.domain.selectors.jsonpath import FilterExpression
from comparo.core.domain.selectors.jsonpath import FilterSelector
from comparo.core.domain.selectors.jsonpath import FunctionExpression
from comparo.core.domain.selectors.jsonpath import IndexSelector
from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath import LiteralExpression
from comparo.core.domain.selectors.jsonpath import NameSelector
from comparo.core.domain.selectors.jsonpath import NotExpression
from comparo.core.domain.selectors.jsonpath import OrExpression
from comparo.core.domain.selectors.jsonpath import QueryExpression
from comparo.core.domain.selectors.jsonpath import QueryOrigin
from comparo.core.domain.selectors.jsonpath import SliceSelector
from comparo.core.domain.selectors.jsonpath import StandardFunction
from comparo.core.domain.selectors.jsonpath import WildcardSelector
from comparo.core.domain.selectors.jsonpath import is_filter_expression
from comparo.core.domain.selectors.jsonpath_derivation import JsonPathAstError
from comparo.core.domain.selectors.jsonpath_derivation import canonical_jsonpath
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonString
from comparo.core.selection import compile_jsonpath
from comparo.core.selection.semantics import validate_and_derive_capabilities


def _literal() -> LiteralExpression:
    return LiteralExpression(value=JsonBoolean(value=True))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: NameSelector(name=cast("str", 1)),
        lambda: NameSelector(name="\ud800"),
        lambda: IndexSelector(index=cast("int", True)),
        lambda: SliceSelector(start=cast("int", "0")),
        lambda: LiteralExpression(value=cast("JsonBoolean", JsonArray(items=()))),
        lambda: LiteralExpression(value=JsonString(value="\ud800")),
        lambda: FilterSelector(predicate=cast("FilterExpression", object())),
        lambda: ChildSegment(selectors=cast("tuple[WildcardSelector, ...]", [])),
        lambda: QueryExpression(origin=cast("QueryOrigin", "root"), segments=()),
        lambda: QueryExpression(
            origin=QueryOrigin.ROOT, segments=cast("tuple[ChildSegment, ...]", [])
        ),
        lambda: FunctionExpression(function=cast("StandardFunction", "length"), arguments=()),
        lambda: FunctionExpression(
            function=StandardFunction.LENGTH, arguments=cast("tuple[FilterExpression, ...]", [])
        ),
        lambda: ComparisonExpression(
            left=cast("FilterExpression", object()),
            operator=ComparisonOperator.EQUAL,
            right=_literal(),
        ),
        lambda: ComparisonExpression(
            left=_literal(), operator=cast("ComparisonOperator", "=="), right=_literal()
        ),
        lambda: NotExpression(operand=cast("FilterExpression", object())),
        lambda: AndExpression(left=_literal(), right=cast("FilterExpression", object())),
        lambda: JsonPathAst(segments=cast("tuple[ChildSegment, ...]", [])),
        lambda: JsonPathAst(segments=cast("tuple[ChildSegment, ...]", (object(),))),
    ],
)
def test_ast_nodes_reject_mutable_or_untyped_components(factory: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        cast("Callable[[], object]", factory)()


def test_segments_require_nonempty_supported_selector_tuples() -> None:
    with pytest.raises(ValueError, match="supported selectors"):
        ChildSegment(selectors=())
    with pytest.raises(ValueError, match="supported selectors"):
        ChildSegment(selectors=cast("tuple[WildcardSelector, ...]", (object(),)))


def test_closed_expression_guard_and_valid_logical_nodes() -> None:
    literal = _literal()
    comparison = ComparisonExpression(
        left=literal,
        operator=ComparisonOperator.EQUAL,
        right=literal,
    )
    assert is_filter_expression(comparison)
    assert not is_filter_expression(object())
    assert (
        type(OrExpression(left=comparison, right=NotExpression(operand=comparison))) is OrExpression
    )


@pytest.mark.parametrize(
    "selector",
    [
        IndexSelector(index=9_007_199_254_740_992),
        SliceSelector(stop=-9_007_199_254_740_992),
    ],
)
def test_expression_rejects_non_interoperable_direct_ast_indexes(selector: object) -> None:
    ast = JsonPathAst(segments=(ChildSegment(selectors=(cast("WildcardSelector", selector),)),))
    with pytest.raises(JsonPathAstError, match="invalid-number"):
        JsonPathExpression(ast=ast)


def test_derived_metadata_cannot_be_supplied_or_forged() -> None:
    expression = JsonPathExpression(
        ast=JsonPathAst(segments=(ChildSegment(selectors=(NameSelector(name="value"),)),))
    )

    assert expression.canonical == '$["value"]'
    assert expression.fingerprint == expression.fingerprint
    assert expression.capabilities == expression.capabilities
    assert validate_and_derive_capabilities(expression.ast) == expression.capabilities
    with pytest.raises(TypeError):
        JsonPathExpression(  # type: ignore[call-arg]
            ast=expression.ast,
            canonical="$",
        )

    with pytest.raises(TypeError, match="JsonPathAst"):
        canonical_jsonpath(cast("JsonPathAst", object()))


def test_nested_query_ast_still_validates_interoperable_indexes() -> None:
    nested = QueryExpression(
        origin=QueryOrigin.CURRENT,
        segments=(ChildSegment(selectors=(IndexSelector(index=9_007_199_254_740_992),)),),
    )
    predicate = ComparisonExpression(
        left=FunctionExpression(function=StandardFunction.VALUE, arguments=(nested,)),
        operator=ComparisonOperator.EQUAL,
        right=_literal(),
    )
    ast = JsonPathAst(segments=(ChildSegment(selectors=(FilterSelector(predicate=predicate),)),))

    with pytest.raises(JsonPathAstError, match="invalid-number"):
        JsonPathExpression(ast=ast)


def test_nested_filter_functions_are_included_in_dependencies() -> None:
    expression = compile_jsonpath('$[?count(@[?match(@,"x")])>0]')

    assert expression.capabilities.function_dependencies == ("count", "match")
