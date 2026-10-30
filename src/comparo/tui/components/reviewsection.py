"""Aligned review rows shared by immutable planning checkpoints."""

from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widget import Widget

from comparo.tui.components.typography import Caption


class ReviewSection(Horizontal):
    """A numbered label beside the effective facts, separated by one divider."""

    DEFAULT_CSS = """
    ReviewSection { height: auto; border-top: solid $comparo-border; padding: 0 1; }
    ReviewSection > Caption { width: 25; height: auto; color: $foreground; }
    ReviewSection > Vertical { width: 1fr; height: auto; }
    ReviewSection > Vertical > Caption { height: auto; }
    """

    def __init__(self, title: str, *contents: Widget) -> None:
        """Retain the row label and ordinary shared display components."""
        super().__init__()
        self.title = title
        self.contents = contents

    @override
    def compose(self) -> ComposeResult:
        """Keep the section label aligned independently of multiline facts."""
        yield Caption(self.title, markup=False)
        yield Vertical(*self.contents)
