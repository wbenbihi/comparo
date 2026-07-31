from __future__ import annotations

from textual.app import App
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static

from comparo.tui4.states import AppState
from comparo.tui4.types import ComparoScreen


class NavTab(Static):
    """Navigation Tab."""

    class Selected(Message):
        """Tab Selection Message."""

        def __init__(self, tab: ComparoScreen):
            """Init."""
            self.tab = tab
            super().__init__()

    def __init__(self, tab: ComparoScreen, index: int | None = None):
        """Init."""
        self.tab = tab
        self.index = index
        super().__init__(self.display_name, classes="nav-tab")

    @property
    def display_name(self) -> str:
        """Text display in TUI."""
        if self.index is not None:
            return f"{self.index}. {self.tab.value.upper()}"
        return f"{self.tab.value.upper()}"

    def on_click(self) -> None:
        """On Click callback."""
        self.post_message(self.Selected(self.tab))


class NavBar(Horizontal):
    """Navigation Bar."""

    def compose(self):
        """Render NavBar."""
        for idx, tab in enumerate(ComparoScreen, start=1):
            yield NavTab(tab=tab, index=idx)

    def update_active_tab(self, active: ComparoScreen):
        """Update active tab."""
        # Update Visual
        for tab in self.query(NavTab):
            tab.set_class(tab.tab == active, "active")

    def on_mount(self) -> None:
        """On Mount."""
        self.app: App[None]
        app = self.app
        self.watch(app, "state", self._on_state)

    def _on_state(self, state: AppState) -> None:
        """Callback on state update."""
        self.update_active_tab(state.active_screen)


class NavHelper(Horizontal):
    """Navigation Helpers."""

    def compose(self):
        """Helpers."""
        yield Static("env: DEV")
        yield Static("vX.X.X")
