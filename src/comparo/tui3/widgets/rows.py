"""Row primitives: plain rows, split rows, and the v0 panel header."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from comparo.tui3.theme import style


class TextRow(Static):
    """One rendered line."""

    def __init__(self, content: Text, *, id: str | None = None, classes: str | None = None) -> None:
        """Render the given fragment run."""
        super().__init__(content, id=id, classes=classes)


class SplitRow(Static):
    """Left- and right-aligned fragments on one line."""

    def __init__(self, left: Text, right: Text, classes: str | None = None) -> None:
        """Store both sides; balancing happens on resize."""
        self._left = left
        self._right = right
        super().__init__(left, classes=f"split-row {classes or ''}".strip())

    def set_left(self, left: Text) -> None:
        """Replace the left side and re-balance."""
        self._left = left
        self._refresh_line()

    def set_right(self, right: Text) -> None:
        """Replace the right side and re-balance."""
        self._right = right
        self._refresh_line()

    def on_resize(self) -> None:
        """Re-balance to the new width."""
        self._refresh_line()

    def _refresh_line(self) -> None:
        width = self.size.width
        gap = width - self._left.cell_len - self._right.cell_len
        content = self._left.copy()
        content.append(" " * max(gap, 1))
        content.append_text(self._right)
        self.update(content)


class PanelHeader(SplitRow):
    """The v0 ``.ph`` row: dim uppercase left, dim hint right."""

    def __init__(self, left: str | Text, right: str | Text = "") -> None:
        """Style both sides in the panel-header grammar."""
        lt = Text(left.upper(), style=style("ph")) if isinstance(left, str) else left
        rt = Text(right, style=style("dim")) if isinstance(right, str) else right
        super().__init__(lt, rt, classes="panel-header")
