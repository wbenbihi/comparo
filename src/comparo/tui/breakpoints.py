"""The single responsive-width policy shared by every TUI component."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec


class LayoutClass(StrEnum):
    """Closed application layout classes."""

    COMPACT = "compact"
    STANDARD = "standard"
    WIDE = "wide"


@final
class BreakpointPolicy(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-visible responsive thresholds without component-local breakpoints."""

    standard_at: int = 88
    wide_at: int = 140

    def __post_init__(self) -> None:
        """Require ordered positive exact widths."""
        if type(self.standard_at) is not int or type(self.wide_at) is not int:
            raise TypeError("TUI breakpoints must be exact integers")
        if self.standard_at < 1 or self.wide_at <= self.standard_at:
            raise ValueError("TUI breakpoints must be positive and strictly ordered")

    def classify(self, width: int) -> LayoutClass:
        """Classify one terminal width through the canonical thresholds."""
        if type(width) is not int or width < 0:
            raise ValueError("terminal width must be a non-negative exact integer")
        if width < self.standard_at:
            return LayoutClass.COMPACT
        if width < self.wide_at:
            return LayoutClass.STANDARD
        return LayoutClass.WIDE


DEFAULT_BREAKPOINT_POLICY = BreakpointPolicy()


def layout_class(width: int) -> LayoutClass:
    """Classify a width through Comparo's default responsive policy."""
    return DEFAULT_BREAKPOINT_POLICY.classify(width)


__all__ = (
    "DEFAULT_BREAKPOINT_POLICY",
    "BreakpointPolicy",
    "LayoutClass",
    "layout_class",
)
