"""First-class, non-throwing authored path compilation outcomes."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import PathDialect
from comparo.core.selection.compile import compile_jsonpath
from comparo.core.selection.errors import SelectorSyntaxCode
from comparo.core.selection.errors import SelectorSyntaxError
from comparo.core.selection.limits import DEFAULT_SELECTOR_PARSER_LIMITS
from comparo.core.selection.limits import SelectorParserLimits
from comparo.core.selection.pointer import compile_json_pointer


@final
class PathCompileReady(msgspec.Struct, frozen=True, kw_only=True):
    """A successfully compiled path retaining its exact source dialect."""

    dialect: PathDialect
    path: JsonPointerPath | JsonPathExpression

    def __post_init__(self) -> None:
        """Require the path variant implied by the dialect."""
        expected = {
            PathDialect.JSON_POINTER: JsonPointerPath,
            PathDialect.JSON_PATH: JsonPathExpression,
        }.get(self.dialect)
        if expected is None or type(self.path) is not expected:
            raise ValueError("path compile result contradicts its dialect")


@final
class PathCompileFailure(msgspec.Struct, frozen=True, kw_only=True):
    """A safe authored failure distinct from runtime selection outcomes."""

    dialect: PathDialect
    code: SelectorSyntaxCode
    offset: Count

    def __post_init__(self) -> None:
        """Require a supported path dialect and structured syntax metadata."""
        if self.dialect not in {PathDialect.JSON_POINTER, PathDialect.JSON_PATH}:
            raise ValueError("path compile failure requires a JSON path dialect")
        if type(self.code) is not SelectorSyntaxCode:
            raise TypeError("path compile failure code must be a SelectorSyntaxCode")
        if type(self.offset) is not Count:
            raise TypeError("path compile failure offset must be a Count")


type PathCompileResult = PathCompileReady | PathCompileFailure


def try_compile_jsonpath(
    text: str,
    *,
    limits: SelectorParserLimits = DEFAULT_SELECTOR_PARSER_LIMITS,
) -> PathCompileResult:
    """Compile JSONPath text as an explicit success-or-failure result."""
    try:
        path = compile_jsonpath(text, limits=limits)
    except SelectorSyntaxError as error:
        return _failure(PathDialect.JSON_PATH, error)
    return PathCompileReady(dialect=PathDialect.JSON_PATH, path=path)


def try_compile_json_pointer(
    text: str,
    *,
    limits: SelectorParserLimits = DEFAULT_SELECTOR_PARSER_LIMITS,
) -> PathCompileResult:
    """Compile JSON Pointer text as an explicit success-or-failure result."""
    try:
        path = compile_json_pointer(text, limits=limits)
    except SelectorSyntaxError as error:
        return _failure(PathDialect.JSON_POINTER, error)
    return PathCompileReady(dialect=PathDialect.JSON_POINTER, path=path)


def _failure(dialect: PathDialect, error: SelectorSyntaxError) -> PathCompileFailure:
    return PathCompileFailure(dialect=dialect, code=error.code, offset=Count(value=error.offset))
