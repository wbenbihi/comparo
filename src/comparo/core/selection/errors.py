"""Structured, value-free selector compilation failures."""

from __future__ import annotations

from typing import final
from typing import override

from comparo.core.domain.enums import DomainStrEnum


class SelectorSyntaxCode(DomainStrEnum):
    """Stable parser failure categories safe for logs and reports."""

    TYPE = "selector.syntax.type"
    TOO_LONG = "selector.syntax.too-long"
    TOO_DEEP = "selector.syntax.too-deep"
    TOO_COMPLEX = "selector.syntax.too-complex"
    EXPECTED_ROOT = "selector.syntax.expected-root"
    EXPECTED_SELECTOR = "selector.syntax.expected-selector"
    EXPECTED_SYNTAX = "selector.syntax.expected-syntax"
    INVALID_ESCAPE = "selector.syntax.invalid-escape"
    INVALID_UNICODE = "selector.syntax.invalid-unicode"
    INVALID_NUMBER = "selector.syntax.invalid-number"
    INVALID_SLICE = "selector.syntax.invalid-slice"
    INVALID_FUNCTION = "selector.syntax.invalid-function"
    INVALID_EXPRESSION = "selector.syntax.invalid-expression"
    INVALID_TYPE = "selector.syntax.invalid-type"
    TRAILING_INPUT = "selector.syntax.trailing-input"


@final
class SelectorSyntaxError(ValueError):
    """A syntax failure that never retains or renders authored input."""

    __slots__ = ("code", "offset")

    code: SelectorSyntaxCode
    offset: int

    def __init__(self, code: SelectorSyntaxCode, offset: int) -> None:
        """Store only a stable code and structural character offset."""
        if type(code) is not SelectorSyntaxCode:
            raise TypeError("selector syntax error code must be a SelectorSyntaxCode")
        if type(offset) is not int or offset < 0:
            raise ValueError("selector syntax error offset must be non-negative")
        self.code = code
        self.offset = offset
        super().__init__(code.value, offset)

    @override
    def __str__(self) -> str:
        """Render only safe structural metadata."""
        return f"{self.code.value} at offset {self.offset}"
