from __future__ import annotations

from typing import Any
from typing import ClassVar
from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.binding import BindingType
from textual.reactive import reactive
from textual.widgets import Footer
from textual.widgets import Static
from textual.widgets import ContentSwitcher

from .components.header import AppHeader
from .components.navigation import NavTab
from .helpers import get_screen_bindings
from .helpers import get_state
from .states import AppState
from .types import ComparoScreen
from .workspaces import WORKSPACES
from .theme import COMPARO_DARK
from .backend import TuiBackend


class ComparoApp(App[None]):
    """Main Comparo Application."""

    CSS_PATH = "styles/main.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        *get_screen_bindings(),
    ]
    state: reactive[AppState] = reactive(get_state, init=False)

    def __init__(self, config: Path):
        """Init the TUI App."""
        super().__init__()
        self.register_theme(COMPARO_DARK)
        self.theme = "comparo-dark"
        self.backend = TuiBackend(config)

    async def on_unmount(self) -> None:
        """Drain the backend before the process exits."""
        result = await self.backend.shutdown()
        if result is not None and result.diagnostics:
            self.log(f"backend shutdown diagnostics: {[d.code for d in result.diagnostics]}")

    def compose(self):
        """Render the main app."""
        yield AppHeader()
        with ContentSwitcher(initial=ComparoScreen.EXPLORE, id="main"):
            for tab in ComparoScreen:
                workspace = WORKSPACES.get(tab)
                if workspace is not None:
                    yield workspace()
                else:
                    yield Static(tab.value, id=tab, classes="Workspace")
        yield Footer()

    def action_workspace(self, ws: str) -> None:
        """Switch workspace — string entry point for key bindings."""
        if ws in ComparoScreen:
            self.update_state(active_screen=ComparoScreen(ws))

    def on_nav_tab_selected(self, message: NavTab.Selected) -> None:
        """Switch workspace from the navbar."""
        self.action_workspace(message.tab.value)

    def update_state(self, **changes: Any) -> None:
        """Update the state of the application."""
        for key, value in changes.items():
            setattr(self.state, key, value)
        self.mutate_reactive(ComparoApp.state)

    def watch_state(self) -> None:
        """Reflect state into the main switcher."""
        self.query_one("#main", ContentSwitcher).current = self.state.active_screen

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Check if an action may run."""
        return True
