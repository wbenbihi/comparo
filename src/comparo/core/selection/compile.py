"""Public pure selector path compilers."""

from __future__ import annotations

from comparo.core.domain.selectors.jsonpath_derivation import JsonPathAstError
from comparo.core.domain.selectors.jsonpath_derivation import JsonPathAstErrorCode
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.selection.errors import SelectorSyntaxCode
from comparo.core.selection.errors import SelectorSyntaxError
from comparo.core.selection.limits import DEFAULT_SELECTOR_PARSER_LIMITS
from comparo.core.selection.limits import SelectorParserLimits
from comparo.core.selection.parser import JsonPathParser
from comparo.core.selection.pointer import compile_json_pointer


def compile_jsonpath(
    text: str,
    *,
    limits: SelectorParserLimits = DEFAULT_SELECTOR_PARSER_LIMITS,
) -> JsonPathExpression:
    """Compile authored RFC 9535 text to its canonical executable domain path."""
    if type(limits) is not SelectorParserLimits:
        raise TypeError("JSONPath compilation requires SelectorParserLimits")
    if type(text) is not str:
        raise SelectorSyntaxError(SelectorSyntaxCode.TYPE, 0)
    if len(text) > limits.max_codepoints:
        raise SelectorSyntaxError(SelectorSyntaxCode.TOO_LONG, limits.max_codepoints)
    ast = JsonPathParser(text, limits).parse()
    try:
        return JsonPathExpression(ast=ast)
    except JsonPathAstError as error:
        code = (
            SelectorSyntaxCode.TOO_DEEP
            if error.code is JsonPathAstErrorCode.TOO_DEEP
            else SelectorSyntaxCode.INVALID_NUMBER
            if error.code is JsonPathAstErrorCode.INVALID_NUMBER
            else SelectorSyntaxCode.INVALID_TYPE
        )
        raise SelectorSyntaxError(code, 0) from None


__all__ = ("compile_json_pointer", "compile_jsonpath")
