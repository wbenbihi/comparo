"""One status vocabulary for every Comparo TUI surface."""

from __future__ import annotations

from enum import StrEnum
from typing import Final
from typing import final

import msgspec


class SemanticStatus(StrEnum):
    """Meaningful states independent from terminal colors and widgets."""

    SUCCESS = "success"
    ADVISORY = "advisory"
    FAILURE = "failure"
    ERROR = "error"
    RUNNING = "running"
    QUEUED = "queued"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    REDACTED = "redacted"
    NEUTRAL = "neutral"


@final
class SemanticStyle(msgspec.Struct, frozen=True, kw_only=True):
    """Accessible text, glyph, CSS class, and sorting rank for one status."""

    glyph: str
    label: str
    css_class: str
    rank: int

    def __post_init__(self) -> None:
        """Require bounded plain presentation tokens."""
        if any(
            type(value) is not str or not value or len(value) > 32
            for value in (self.glyph, self.label, self.css_class)
        ):
            raise ValueError("semantic style text must be bounded and non-empty")
        if type(self.rank) is not int or self.rank < 0:
            raise ValueError("semantic style rank must be a non-negative integer")


_STYLES: Final[dict[SemanticStatus, SemanticStyle]] = {
    SemanticStatus.SUCCESS: SemanticStyle(
        glyph="+", label="passed", css_class="status-success", rank=6
    ),
    SemanticStatus.ADVISORY: SemanticStyle(
        glyph="~", label="advisory", css_class="status-advisory", rank=3
    ),
    SemanticStatus.FAILURE: SemanticStyle(
        glyph="x", label="failed", css_class="status-failure", rank=0
    ),
    SemanticStatus.ERROR: SemanticStyle(glyph="!", label="error", css_class="status-error", rank=1),
    SemanticStatus.RUNNING: SemanticStyle(
        glyph=">", label="running", css_class="status-running", rank=4
    ),
    SemanticStatus.QUEUED: SemanticStyle(
        glyph=".", label="queued", css_class="status-queued", rank=5
    ),
    SemanticStatus.SKIPPED: SemanticStyle(
        glyph="-", label="skipped", css_class="status-skipped", rank=7
    ),
    SemanticStatus.CANCELLED: SemanticStyle(
        glyph="#", label="cancelled", css_class="status-cancelled", rank=2
    ),
    SemanticStatus.REDACTED: SemanticStyle(
        glyph="*", label="redacted", css_class="status-redacted", rank=8
    ),
    SemanticStatus.NEUTRAL: SemanticStyle(
        glyph="=", label="neutral", css_class="status-neutral", rank=9
    ),
}


def semantic_style(status: SemanticStatus) -> SemanticStyle:
    """Return the sole presentation mapping for a semantic status."""
    if type(status) is not SemanticStatus:
        raise TypeError("semantic style requires SemanticStatus")
    return _STYLES[status]


__all__ = ("SemanticStatus", "SemanticStyle", "semantic_style")
