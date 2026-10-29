"""Titled rounded panel — the structural primitive of every screen."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widget import Widget

from comparo.tuibis.viewmodels import EntityKind
from comparo.tuibis.widgets.fragments import GLYPHS


class Panel(Vertical):
    """A ``╭─ TITLE ─╮`` region.

    The focused panel gets the accent border (see styles.tcss
    ``Panel:focus-within``); key hints render in the bottom border, the way
    the validated prototype did. ``tone`` switches the resting border color
    for warning/danger panels.
    """

    def __init__(
        self,
        *children: Widget,
        title: str,
        glyph: EntityKind | None = None,
        hint: str = "",
        tone: str = "neutral",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the component from its view-model inputs."""
        super().__init__(*children, id=id, classes=classes)
        prefix = f"{GLYPHS[glyph]} " if glyph else ""
        self.border_title = f"{prefix}{title}"
        if hint:
            self.border_subtitle = hint
        if tone != "neutral":
            self.add_class(f"tone-{tone}")
