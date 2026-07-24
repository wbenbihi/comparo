"""Exercise strict JSON Pointer and RFC 9535 JSONPath compilation."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.selectors.jsonpath import ChildSegment
from comparo.core.domain.selectors.jsonpath import DescendantSegment
from comparo.core.domain.selectors.jsonpath import FilterSelector
from comparo.core.domain.selectors.jsonpath import IndexSelector
from comparo.core.domain.selectors.jsonpath import NameSelector
from comparo.core.domain.selectors.jsonpath import SliceSelector
from comparo.core.domain.selectors.jsonpath import WildcardSelector
from comparo.core.domain.selectors.paths import PathDialect
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.selection import PathCompileFailure
from comparo.core.selection import PathCompileReady
from comparo.core.selection import SelectorParserLimits
from comparo.core.selection import SelectorSyntaxCode
from comparo.core.selection import SelectorSyntaxError
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath
from comparo.core.selection import try_compile_json_pointer
from comparo.core.selection import try_compile_jsonpath


@pytest.mark.parametrize(
    ("authored", "canonical"),
    [
        ("$", "$"),
        ("$.store.book[*].author", '$["store"]["book"][*]["author"]'),
        ("$['a', 0, 1:9:0]", '$["a",0,1:9:0]'),
        ("$..book", '$..["book"]'),
        ("$..['price']", '$..["price"]'),
        ("$['a\\'b']", '$["a\'b"]'),
        ('$["a\\nb"]', '$["a\\nb"]'),
        ('$["\\uD834\\uDD1E"]', '$["𝄞"]'),
        ('$["quote\\"slash\\\\"]', '$["quote\\"slash\\\\"]'),
        ('$["\\b\\f\\n\\r\\t\\u0000"]', '$["\\b\\f\\n\\r\\t\\u0000"]'),
        ("$[-0]", "$[0]"),
        ("$[::-1]", "$[::-1]"),
        ("$[1:2]", "$[1:2]"),
        ("$[?@==null]", "$[?@==null]"),
        ("$[?@==1.5]", "$[?@==1.5]"),
        ("$[?@==1.2e-10]", "$[?@==1.2e-10]"),
    ],
)
def test_jsonpath_uses_one_bracket_normalized_canonical_form(
    authored: str,
    canonical: str,
) -> None:
    assert compile_jsonpath(authored).canonical == canonical


def test_jsonpath_ast_preserves_selector_and_segment_order() -> None:
    compiled = compile_jsonpath("$['name',2,1:8:2,*]..value")
    first, second = compiled.ast.segments

    assert type(first) is ChildSegment
    assert tuple(type(selector) for selector in first.selectors) == (
        NameSelector,
        IndexSelector,
        SliceSelector,
        WildcardSelector,
    )
    assert type(second) is DescendantSegment
    assert second.selectors == (NameSelector(name="value"),)


def test_equivalent_jsonpaths_share_fingerprint_and_capabilities() -> None:
    shorthand = compile_jsonpath("$.store.book[*]")
    normalized = compile_jsonpath('$["store"]["book"][*]')

    assert shorthand.canonical == normalized.canonical
    assert shorthand.fingerprint == normalized.fingerprint
    assert shorthand.capabilities == normalized.capabilities
    assert shorthand.fingerprint != compile_jsonpath("$.store.books[*]").fingerprint


@pytest.mark.parametrize(
    ("path", "cardinality", "access", "duplicates", "descendant", "filtered"),
    [
        ("$", SelectorCardinality.EXACTLY_ONE, SelectionAccess.INCREMENTAL, False, False, False),
        (
            "$.a[0]",
            SelectorCardinality.ZERO_OR_ONE,
            SelectionAccess.INCREMENTAL,
            False,
            False,
            False,
        ),
        (
            "$[-1]",
            SelectorCardinality.ZERO_OR_ONE,
            SelectionAccess.INDEXED,
            False,
            False,
            False,
        ),
        (
            "$['a','a']",
            SelectorCardinality.ZERO_OR_MORE,
            SelectionAccess.INCREMENTAL,
            True,
            False,
            False,
        ),
        (
            "$..*",
            SelectorCardinality.ZERO_OR_MORE,
            SelectionAccess.INCREMENTAL,
            False,
            True,
            False,
        ),
        (
            "$[?@.active]",
            SelectorCardinality.ZERO_OR_MORE,
            SelectionAccess.MATERIALIZED,
            False,
            False,
            True,
        ),
    ],
)
def test_jsonpath_capabilities_are_derived_from_ast(
    path: str,
    cardinality: SelectorCardinality,
    access: SelectionAccess,
    duplicates: bool,
    descendant: bool,
    filtered: bool,
) -> None:
    capabilities = compile_jsonpath(path).capabilities

    assert capabilities.cardinality is cardinality
    assert capabilities.access is access
    assert capabilities.may_duplicate is duplicates
    assert capabilities.uses_descendant is descendant
    assert capabilities.uses_filter is filtered


def test_standard_functions_are_typed_and_reported_in_first_use_order() -> None:
    compiled = compile_jsonpath(
        "$[?length(@.name)>=3 && count(@.items[*])>0 "
        '&& match(@.name,"a") && search(@.name,"b") && value(@.enabled)==true]'
    )
    predicate = cast("FilterSelector", compiled.ast.segments[0].selectors[0]).predicate

    assert predicate is not None
    assert compiled.capabilities.function_dependencies == (
        "length",
        "count",
        "match",
        "search",
        "value",
    )


@pytest.mark.parametrize(
    "path",
    [
        "$[?@.present]",
        "$[?!@.missing]",
        "$[?@.a && @.b]",
        "$[?(@.a || @.b) && !(@.c)]",
        "$[?@.falseValue]",
        "$[?@.nullValue]",
        "$[?$.catalog.enabled == true]",
    ],
)
def test_rfc_filter_existence_and_logical_expressions_compile(path: str) -> None:
    assert compile_jsonpath(path).capabilities.uses_filter


def test_json_pointer_is_strict_and_does_not_decode_tokens() -> None:
    root = compile_json_pointer("")
    pointer = compile_json_pointer("/a~1b/~0key/0/é")

    assert root.pointer.value == ""
    assert pointer.pointer.value == "/a~1b/~0key/0/é"


@pytest.mark.parametrize(
    ("pointer", "code"),
    [
        ("#fragment", SelectorSyntaxCode.EXPECTED_ROOT),
        ("relative", SelectorSyntaxCode.EXPECTED_ROOT),
        ("/~", SelectorSyntaxCode.INVALID_ESCAPE),
        ("/~2", SelectorSyntaxCode.INVALID_ESCAPE),
        ("/\ud800", SelectorSyntaxCode.INVALID_UNICODE),
    ],
)
def test_json_pointer_rejects_non_rfc_input(pointer: str, code: SelectorSyntaxCode) -> None:
    with pytest.raises(SelectorSyntaxError) as captured:
        compile_json_pointer(pointer)
    assert captured.value.code is code


def test_pointer_and_path_compilers_enforce_explicit_limits() -> None:
    limits = SelectorParserLimits(
        max_codepoints=10,
        max_depth=2,
        max_segments=2,
        max_selectors=2,
        max_expressions=2,
        max_integer_digits=2,
    )
    with pytest.raises(SelectorSyntaxError, match="too-long"):
        compile_jsonpath("$.abcdefghij", limits=limits)
    with pytest.raises(SelectorSyntaxError, match="too-complex"):
        compile_json_pointer("/a/b/c", limits=limits)
    with pytest.raises(SelectorSyntaxError, match="too-long"):
        compile_json_pointer("/abcdefghij", limits=limits)


def test_non_throwing_compile_results_keep_dialect_and_safe_failure() -> None:
    ready = try_compile_jsonpath("$.value")
    failure = try_compile_json_pointer("not-a-pointer")
    pointer_ready = try_compile_json_pointer("/value")
    path_failure = try_compile_jsonpath("not-a-path")

    assert type(ready) is PathCompileReady
    assert ready.dialect is PathDialect.JSON_PATH
    assert ready.path == compile_jsonpath("$.value")
    assert type(failure) is PathCompileFailure
    assert failure.dialect is PathDialect.JSON_POINTER
    assert failure.code is SelectorSyntaxCode.EXPECTED_ROOT
    assert failure.offset.value == 0
    assert type(pointer_ready) is PathCompileReady
    assert pointer_ready.dialect is PathDialect.JSON_POINTER
    assert type(path_failure) is PathCompileFailure
    assert path_failure.dialect is PathDialect.JSON_PATH
