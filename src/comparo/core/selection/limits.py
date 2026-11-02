"""Explicit resource ceilings for pure selector compilation."""

from __future__ import annotations

from typing import final

import msgspec


@final
class SelectorParserLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-overridable defenses against pathological authored expressions."""

    max_codepoints: int = 65_536
    max_depth: int = 128
    max_segments: int = 4_096
    max_selectors: int = 8_192
    max_expressions: int = 8_192
    max_integer_digits: int = 256

    def __post_init__(self) -> None:
        """Require exact positive ceilings."""
        values = (
            self.max_codepoints,
            self.max_depth,
            self.max_segments,
            self.max_selectors,
            self.max_expressions,
            self.max_integer_digits,
        )
        if any(type(value) is not int for value in values):
            raise TypeError("selector parser limits must be integers")
        if any(value < 1 for value in values):
            raise ValueError("selector parser limits must be positive")


DEFAULT_SELECTOR_PARSER_LIMITS = SelectorParserLimits()
