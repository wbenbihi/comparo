"""Separator — horizontal rule with an optional label."""

from typing import override

from rich.align import AlignMethod
from rich.console import RenderableType
from rich.rule import Rule as RichRule
from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget


class Separator(Widget):
    """One-line horizontal rule, optionally labelled."""

    label: reactive[str | None] = reactive(None)

    def __init__(
        self,
        label: str | None = None,
        *,
        align: AlignMethod = "center",
        characters: str = "─",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the separator's label, alignment, and rule character."""
        super().__init__(id=id, classes=classes)
        self._align: AlignMethod = align
        self._characters = characters
        self.set_reactive(Separator.label, label)

    @override
    def render(self) -> RenderableType:
        """One rule line, label inset when present."""
        if self.label:
            return RichRule(
                Text(self.label),
                characters=self._characters,
                align=self._align,
                style=self.rich_style,
            )
        return RichRule(characters=self._characters, style=self.rich_style)
