"""Cover JSONPath grammar, type, and bounded-work failures."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.jsonpath import AndExpression
from comparo.core.domain.selectors.jsonpath import ChildSegment
from comparo.core.domain.selectors.jsonpath import FilterExpression
from comparo.core.domain.selectors.jsonpath import FilterSelector
from comparo.core.domain.selectors.jsonpath import JsonPathAst
from comparo.core.domain.selectors.jsonpath import LiteralExpression
from comparo.core.domain.selectors.jsonpath import NotExpression
from comparo.core.domain.selectors.paths import PathDialect
from comparo.core.domain.values import JsonBoolean
from comparo.core.selection import PathCompileFailure
from comparo.core.selection import PathCompileReady
from comparo.core.selection import SelectorParserLimits
from comparo.core.selection import SelectorSyntaxCode
from comparo.core.selection import SelectorSyntaxError
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath
from comparo.core.selection.render import canonical_jsonpath


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("", SelectorSyntaxCode.EXPECTED_ROOT),
        ("@.a", SelectorSyntaxCode.EXPECTED_ROOT),
        ("$.", SelectorSyntaxCode.EXPECTED_SELECTOR),
        ("$..", SelectorSyntaxCode.EXPECTED_SELECTOR),
        ("$[]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[+]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[01]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[-]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$['unterminated]", SelectorSyntaxCode.EXPECTED_SYNTAX),
        ("$['\\q']", SelectorSyntaxCode.INVALID_ESCAPE),
        ("$['\\u12xz']", SelectorSyntaxCode.INVALID_ESCAPE),
        ("$['\\uDC00']", SelectorSyntaxCode.INVALID_UNICODE),
        ("$['\\uD800']", SelectorSyntaxCode.INVALID_UNICODE),
        ("$['\\uD800\\u0041']", SelectorSyntaxCode.INVALID_UNICODE),
        ("$['line\nfeed']", SelectorSyntaxCode.INVALID_UNICODE),
        ("$[?unknown(@)]", SelectorSyntaxCode.INVALID_FUNCTION),
        ("$[?true]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?length(@)]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?count(1)>0]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?value(@, @)]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?@[*] == 1]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?@.a ==]", SelectorSyntaxCode.INVALID_FUNCTION),
        ("$[?@.a !=]", SelectorSyntaxCode.INVALID_FUNCTION),
        ("$[?!=@]", SelectorSyntaxCode.INVALID_EXPRESSION),
        ("$[?count()]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?1 == @[*]]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?!true]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?@ && true]", SelectorSyntaxCode.INVALID_TYPE),
        ("$[?@ == 01]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[?@ == -]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[?@ == 1.]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[?@ == 1e]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[?@ == truex]", SelectorSyntaxCode.INVALID_FUNCTION),
        ("$['\\", SelectorSyntaxCode.INVALID_ESCAPE),
        ("$['\ud800']", SelectorSyntaxCode.INVALID_UNICODE),
        ("$[?@.a] trailing", SelectorSyntaxCode.TRAILING_INPUT),
        ("$ .a", SelectorSyntaxCode.TRAILING_INPUT),
        ("$[9007199254740992]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[-9007199254740992]", SelectorSyntaxCode.INVALID_NUMBER),
        ("$[?@ == 1e256]", SelectorSyntaxCode.TOO_COMPLEX),
    ],
)
def test_invalid_jsonpaths_return_structured_safe_failures(
    path: str,
    code: SelectorSyntaxCode,
) -> None:
    with pytest.raises(SelectorSyntaxError) as captured:
        compile_jsonpath(path)

    assert captured.value.code is code
    assert captured.value.offset >= 0
    if path:
        assert path not in str(captured.value)
        assert path not in repr(captured.value)


def test_compilers_reject_wrong_boundary_types_without_echoing_them() -> None:
    with pytest.raises(SelectorSyntaxError, match=r"syntax\.type"):
        compile_jsonpath(cast("str", 42))
    with pytest.raises(SelectorSyntaxError, match=r"syntax\.type"):
        compile_json_pointer(cast("str", b"/a"))
    with pytest.raises(TypeError, match="SelectorParserLimits"):
        compile_jsonpath("$", limits=cast("SelectorParserLimits", object()))
    with pytest.raises(TypeError, match="SelectorParserLimits"):
        compile_json_pointer("", limits=cast("SelectorParserLimits", object()))


def test_parser_limits_reject_depth_work_and_numeric_amplification() -> None:
    limits = SelectorParserLimits(
        max_codepoints=1_000,
        max_depth=2,
        max_segments=2,
        max_selectors=2,
        max_expressions=2,
        max_integer_digits=3,
    )
    for path, code in (
        ("$[?!!!@]", SelectorSyntaxCode.TOO_DEEP),
        ("$[?@==1||@==2]", SelectorSyntaxCode.TOO_COMPLEX),
        ("$.a.b.c", SelectorSyntaxCode.TOO_COMPLEX),
        ("$['a','b','c']", SelectorSyntaxCode.TOO_COMPLEX),
        ("$[1234]", SelectorSyntaxCode.TOO_COMPLEX),
        ("$[?@==1e999]", SelectorSyntaxCode.TOO_COMPLEX),
        ("$[?@==1e256]", SelectorSyntaxCode.TOO_COMPLEX),
    ):
        with pytest.raises(SelectorSyntaxError) as captured:
            compile_jsonpath(path, limits=limits)
        assert captured.value.code is code

    depth_limits = SelectorParserLimits(
        max_codepoints=1_000,
        max_depth=2,
        max_segments=20,
        max_selectors=20,
        max_expressions=20,
        max_integer_digits=20,
    )
    with pytest.raises(SelectorSyntaxError) as captured:
        compile_jsonpath("$[?@&&@&&@]", limits=depth_limits)
    assert captured.value.code is SelectorSyntaxCode.TOO_DEEP

    segment_limits = SelectorParserLimits(
        max_codepoints=1_000,
        max_depth=20,
        max_segments=1,
        max_selectors=20,
        max_expressions=20,
        max_integer_digits=20,
    )
    with pytest.raises(SelectorSyntaxError) as captured:
        compile_jsonpath("$.a.b", limits=segment_limits)
    assert captured.value.code is SelectorSyntaxCode.TOO_COMPLEX


def test_direct_ast_rendering_rejects_pathological_recursion() -> None:
    expression: FilterExpression = LiteralExpression(value=JsonBoolean(value=True))
    for _ in range(130):
        expression = NotExpression(operand=expression)
    ast = JsonPathAst(
        segments=(),
    )
    assert canonical_jsonpath(ast) == "$"

    # Build a deep predicate without involving authored text; the renderer must still fail safely.
    deep: FilterExpression = expression
    for _ in range(2):
        deep = AndExpression(left=deep, right=LiteralExpression(value=JsonBoolean(value=True)))
    with pytest.raises(ValueError, match=r"jsonpath\.ast\.too-deep"):
        canonical_jsonpath(
            JsonPathAst(segments=(ChildSegment(selectors=(FilterSelector(predicate=deep),)),))
        )


def test_limit_and_error_objects_validate_direct_construction() -> None:
    with pytest.raises(TypeError, match="integers"):
        SelectorParserLimits(max_depth=cast("int", True))
    with pytest.raises(ValueError, match="positive"):
        SelectorParserLimits(max_depth=0)
    with pytest.raises(TypeError, match="SelectorSyntaxCode"):
        SelectorSyntaxError(cast("SelectorSyntaxCode", "bad"), 0)
    with pytest.raises(ValueError, match="non-negative"):
        SelectorSyntaxError(SelectorSyntaxCode.INVALID_TYPE, -1)


def test_non_throwing_result_objects_reject_forged_metadata() -> None:
    with pytest.raises(ValueError, match="contradicts"):
        PathCompileReady(
            dialect=PathDialect.JSON_POINTER,
            path=compile_jsonpath("$"),
        )
    with pytest.raises(ValueError, match="JSON path dialect"):
        PathCompileFailure(
            dialect=PathDialect.STATIC_FIELDS,
            code=SelectorSyntaxCode.INVALID_TYPE,
            offset=Count(value=0),
        )
    with pytest.raises(TypeError, match="SelectorSyntaxCode"):
        PathCompileFailure(
            dialect=PathDialect.JSON_PATH,
            code=cast("SelectorSyntaxCode", "bad"),
            offset=Count(value=0),
        )
    with pytest.raises(TypeError, match="Count"):
        PathCompileFailure(
            dialect=PathDialect.JSON_PATH,
            code=SelectorSyntaxCode.INVALID_TYPE,
            offset=cast("Count", 0),
        )
