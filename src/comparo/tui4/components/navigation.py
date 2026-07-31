"""Top-level workspace navigation widgets."""

from __future__ import annotations

from typing import override

from textual.app import App
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static

from comparo.tui4.states import AppState
from comparo.tui4.types import ComparoScreen


class NavTab(Static):
    """Navigation Tab."""

    class Selected(Message):
        """Tab Selection Message."""

        def __init__(self, tab: ComparoScreen) -> None:
            """Initialize a selection event for one workspace."""
            self.tab = tab
            super().__init__()

    def __init__(self, tab: ComparoScreen, index: int | None = None) -> None:
        """Initialize a navigation tab."""
        self.tab = tab
        self.index = index
        super().__init__(self.display_name, classes="nav-tab")

    @property
    def display_name(self) -> str:
        """Text display in TUI."""
        if self.index is not None:
            return f"{self.index}. {self.tab.value.upper()}"
        return self.tab.value.upper()

    def on_click(self) -> None:
        """Emit a semantic selection when the tab is clicked."""
        self.post_message(self.Selected(self.tab))


class NavBar(Horizontal):
    """Navigation Bar."""

    @override
    def compose(self) -> ComposeResult:
        """Render NavBar."""
        for idx, tab in enumerate(ComparoScreen, start=1):
            yield NavTab(tab=tab, index=idx)

    def update_active_tab(self, active: ComparoScreen) -> None:
        """Update active tab."""
        # Update Visual
        for tab in self.query(NavTab):
            tab.set_class(tab.tab == active, "active")

    def on_mount(self) -> None:
        """Observe application state after the navigation bar is mounted."""
        self.app: App[None]
        app = self.app
        self.watch(app, "state", self._on_state)

    def _on_state(self, state: AppState) -> None:
        """Reflect application state in the active tab class."""
        self.update_active_tab(state.active_screen)


class NavHelper(Horizontal):
    """Navigation Helpers."""

    @override
    def compose(self) -> ComposeResult:
        """Helpers."""
        yield Static("env: DEV")
        yield Static("vX.X.X")
