"""The v0 panel: rounded hairline border, optional highlighted variant."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widget import Widget


class Panel(VerticalScroll):
    """A bordered panel; ``hi=True`` uses the raised background."""

    def __init__(
        self, *children: Widget, hi: bool = False, id: str | None = None, classes: str | None = None
    ) -> None:
        """Compose the panel with its children."""
        cls = f"t3-panel {'-hi' if hi else ''} {classes or ''}".strip()
        super().__init__(*children, id=id, classes=cls)
