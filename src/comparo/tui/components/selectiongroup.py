"""Labeled selection groups with explicit parent/child indentation."""

from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget

from comparo.tui.components.typography import Caption


class SelectionGroup(Vertical):
    """Separate a field's options from subordinate choices revealed by that field."""

    DEFAULT_CSS = """
    SelectionGroup { height: auto; margin-bottom: 1; }
    SelectionGroup > .selection-title { height: auto; color: $foreground; text-style: bold; }
    SelectionGroup.dependent {
        margin-left: 2; padding-left: 1; border-left: solid $comparo-border;
    }
    """

    def __init__(
        self, title: str, *children: Widget, dependent: bool = False, id: str | None = None
    ) -> None:
        """Retain the semantic group label and its existing library controls."""
        super().__init__(id=id, classes="dependent" if dependent else "")
        self.title = title
        self.contents = children

    @override
    def compose(self) -> ComposeResult:
        """Render one explicit label before the grouped content."""
        yield Caption(self.title, classes="selection-title", markup=False)
        yield from self.contents
