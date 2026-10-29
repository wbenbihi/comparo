"""Small layout components: styled text rows and section rules."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class TextRow(Static):
    """One line assembled from fragment builders."""

    def __init__(self, *parts: Text, id: str | None = None, classes: str | None = None) -> None:
        """Initialize the component from its view-model inputs."""
        content = Text()
        for part in parts:
            content.append_text(part)
        super().__init__(content, id=id, classes=classes)

    def update_parts(self, *parts: Text) -> None:
        """Replace the row content."""
        content = Text()
        for part in parts:
            content.append_text(part)
        self.update(content)


class Rule(Static):
    """A thin horizontal separator inside a panel."""

    def __init__(self) -> None:
        """Initialize the component from its view-model inputs."""
        super().__init__("", classes="section-rule")


class SplitRow(Static):
    """A row with left-aligned and right-aligned fragments."""

    DEFAULT_CSS = ""

    def __init__(self, left: Text, right: Text, classes: str | None = None) -> None:
        """Initialize the component from its view-model inputs."""
        self._left = left
        self._right = right
        super().__init__(left, classes=f"split-row {classes or ''}".strip())

    def on_resize(self) -> None:
        """Re-balance the left/right split to the new width."""
        self._refresh_line()

    def set_left(self, left: Text) -> None:
        """Replace the left fragment and re-balance."""
        self._left = left
        self._refresh_line()

    def _refresh_line(self) -> None:
        width = self.size.width
        gap = width - self._left.cell_len - self._right.cell_len
        content = self._left.copy()
        content.append(" " * max(gap, 1))
        content.append_text(self._right)
        self.update(content)
