"""Terminal-native badges for states, metadata, and live status."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import override

from rich.console import RenderableType
from rich.text import Text
from textual.widgets import Static

from comparo.tui4.theme import style


class BadgeTone(StrEnum):
    """Semantic badge colors independent from any concrete theme palette."""

    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    BASELINE = "baseline"
    CANDIDATE = "candidate"


class BadgeEmphasis(StrEnum):
    """Terminal-native treatments for different information roles."""

    SOLID = "solid"
    QUIET = "quiet"
    STATUS = "status"


@dataclass(frozen=True)
class BadgeSpec:
    """Immutable badge content and semantics shared by inline and widget rendering."""

    label: str
    tone: BadgeTone = BadgeTone.NEUTRAL
    emphasis: BadgeEmphasis = BadgeEmphasis.QUIET

    def __post_init__(self) -> None:
        """Reject labels that cannot remain a compact single-line terminal element."""
        invalid_length = not self.label or len(self.label) > 32
        invalid_spacing = self.label != self.label.strip() or not self.label.isprintable()
        if invalid_length or invalid_spacing:
            raise ValueError("badge label must be 1-32 printable characters without outer spaces")


def render_badge(spec: BadgeSpec) -> Text:
    """Render a badge without markup parsing or font-dependent faux curves."""
    tone = spec.tone.value
    if spec.emphasis is BadgeEmphasis.SOLID:
        return Text(f" {spec.label.upper()} ", style(f"badge.solid.{tone}"))
    if spec.emphasis is BadgeEmphasis.STATUS:
        result = Text("● ", style(f"badge.quiet.{tone}"))
        result.append(spec.label, style("badge.status.label"))
        return result
    result = Text("[", style("badge.quiet.frame"))
    result.append(spec.label, style(f"badge.quiet.{tone}"))
    result.append("]", style("badge.quiet.frame"))
    return result


def badge_text(
    label: str,
    *,
    tone: BadgeTone = BadgeTone.NEUTRAL,
    emphasis: BadgeEmphasis = BadgeEmphasis.QUIET,
) -> Text:
    """Convenience renderer for inline tree, table, and text composition."""
    return render_badge(BadgeSpec(label=label, tone=tone, emphasis=emphasis))


class Badge(Static):
    """Native Textual badge backed by the same contract as inline fragments."""

    def __init__(
        self,
        label: str,
        *,
        tone: BadgeTone = BadgeTone.NEUTRAL,
        emphasis: BadgeEmphasis = BadgeEmphasis.QUIET,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize one immutable terminal badge."""
        super().__init__(id=id, classes=classes)
        self.spec = BadgeSpec(label=label, tone=tone, emphasis=emphasis)

    @override
    def render(self) -> RenderableType:
        """Render using the shared inline badge grammar."""
        return render_badge(self.spec)
