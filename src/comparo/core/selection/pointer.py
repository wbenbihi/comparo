"""Strict RFC 6901 JSON Pointer compilation."""

from __future__ import annotations

from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.selection.errors import SelectorSyntaxCode
from comparo.core.selection.errors import SelectorSyntaxError
from comparo.core.selection.limits import DEFAULT_SELECTOR_PARSER_LIMITS
from comparo.core.selection.limits import SelectorParserLimits


def compile_json_pointer(
    text: str,
    *,
    limits: SelectorParserLimits = DEFAULT_SELECTOR_PARSER_LIMITS,
) -> JsonPointerPath:
    """Compile one RFC 6901 pointer without URI-fragment or implicit decoding."""
    if type(limits) is not SelectorParserLimits:
        raise TypeError("JSON Pointer compilation requires SelectorParserLimits")
    if type(text) is not str:
        raise SelectorSyntaxError(SelectorSyntaxCode.TYPE, 0)
    if len(text) > limits.max_codepoints:
        raise SelectorSyntaxError(SelectorSyntaxCode.TOO_LONG, limits.max_codepoints)
    if text and not text.startswith("/"):
        raise SelectorSyntaxError(SelectorSyntaxCode.EXPECTED_ROOT, 0)
    _validate_pointer_characters(text, limits)
    return JsonPointerPath(pointer=JsonPointer(value=text))


def _validate_pointer_characters(text: str, limits: SelectorParserLimits) -> None:
    index = 0
    segments = 0
    while index < len(text):
        character = text[index]
        if character == "/":
            segments += 1
            if segments > limits.max_segments:
                raise SelectorSyntaxError(SelectorSyntaxCode.TOO_COMPLEX, index)
        if _is_surrogate(character):
            raise SelectorSyntaxError(SelectorSyntaxCode.INVALID_UNICODE, index)
        if character == "~":
            if index + 1 == len(text) or text[index + 1] not in "01":
                raise SelectorSyntaxError(SelectorSyntaxCode.INVALID_ESCAPE, index)
            index += 1
        index += 1


def _is_surrogate(character: str) -> bool:
    return 0xD800 <= ord(character) <= 0xDFFF
