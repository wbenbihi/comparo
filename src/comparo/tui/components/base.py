"""Reusable state and responsive containers for every workspace."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Resize
from textual.widget import Widget
from textual.widgets import LoadingIndicator
from textual.widgets import Static

from comparo.tui.design import LayoutClass
from comparo.tui.design import layout_class
from comparo.tui.presentation import safe_terminal_text

COMPONENT_STYLESHEET = Path(__file__).with_name("comparo.tcss")


class StatePanelMode(StrEnum):
    """The four consistent data-loading states used by TUI surfaces."""

    LOADING = "loading"
    EMPTY = "empty"
    ERROR = "error"
    CONTENT = "content"


@final
class StatePanel(Vertical):
    """One shared loading, empty, error, or content presentation shell."""

    def __init__(
        self,
        *content: Widget,
        mode: StatePanelMode = StatePanelMode.CONTENT,
        message: str = "",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a state shell around caller-owned content widgets."""
        super().__init__(id=id, classes=classes)
        if type(mode) is not StatePanelMode:
            raise TypeError("state panel mode must be typed")
        self._content = content
        self._mode = mode
        self._message = safe_terminal_text(message)

    @property
    def mode(self) -> StatePanelMode:
        """Return the current semantic state."""
        return self._mode

    @override
    def compose(self) -> ComposeResult:
        """Compose all states once so transitions never duplicate child widgets."""
        yield LoadingIndicator(classes="state-loading-marker")
        yield Static(self._message, classes="state-message", markup=False)
        with Vertical(classes="state-content"):
            yield from self._content

    def on_mount(self) -> None:
        """Apply the initial state after all owned widgets exist."""
        self._apply_state()

    def show_state(self, mode: StatePanelMode, message: str = "") -> None:
        """Switch state while preserving the mounted content component instances."""
        if type(mode) is not StatePanelMode:
            raise TypeError("state panel mode must be typed")
        self._mode = mode
        self._message = safe_terminal_text(message)
        if self.is_mounted:
            self._apply_state()

    def _apply_state(self) -> None:
        loading = self.query_one(".state-loading-marker", LoadingIndicator)
        message = self.query_one(".state-message", Static)
        content = self.query_one(".state-content", Vertical)
        loading.display = self._mode is StatePanelMode.LOADING
        message.display = bool(self._message) and self._mode in {
            StatePanelMode.LOADING,
            StatePanelMode.EMPTY,
            StatePanelMode.ERROR,
        }
        content.display = self._mode in {StatePanelMode.CONTENT, StatePanelMode.ERROR}
        message.update(self._message)
        for candidate in StatePanelMode:
            self.remove_class(f"state-{candidate.value}")
        self.add_class(f"state-{self._mode.value}")


class ResponsiveWidget(Vertical):
    """Apply the canonical width class without component-local breakpoints."""

    _layout_class: LayoutClass | None = None

    @property
    def layout_class(self) -> LayoutClass | None:
        """Return the last canonical layout classification."""
        return self._layout_class

    def on_mount(self) -> None:
        """Classify the mounted width before the first stable render."""
        self._apply_width(self.size.width)

    def on_resize(self, event: Resize) -> None:
        """Reclassify through the shared breakpoint policy."""
        self._apply_width(event.size.width)

    def _apply_width(self, width: int) -> None:
        selected = layout_class(width)
        if selected is self._layout_class:
            return
        for candidate in LayoutClass:
            self.remove_class(f"layout-{candidate.value}")
        self.add_class(f"layout-{selected.value}")
        self._layout_class = selected


__all__ = (
    "COMPONENT_STYLESHEET",
    "ResponsiveWidget",
    "StatePanel",
    "StatePanelMode",
)
