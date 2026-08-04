"""Textual application shell for the experimental Comparo interface."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from pathlib import PurePath
from typing import ClassVar
from typing import override

from textual.app import App
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.reactive import reactive
from textual.widgets import ContentSwitcher
from textual.widgets import Footer
from textual.widgets import Static

from comparo.application.plan_inspection_identity import ResourceCoordinate

from .backend import TuiBackend
from .bindings import application_bindings
from .components.header import AppHeader
from .components.navigation import NavTab
from .states import AppState
from .states import default_app_state
from .theme import COMPARO_DARK
from .types import ComparoScreen
from .workspaces import WORKSPACES
from .workspaces.explorer import ExplorerWorkspace


class ComparoApp(App[None]):
    """Own the terminal application's backend and active workspace."""

    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = [
        "styles/main.tcss",
        "styles/environment.tcss",
        "styles/request.tcss",
    ]
    BINDINGS: ClassVar[list[BindingType]] = application_bindings()
    state: reactive[AppState] = reactive(default_app_state, init=False)

    def __init__(self, config: Path) -> None:
        """Initialize the application for a project path."""
        super().__init__()
        self.register_theme(COMPARO_DARK)
        self.theme = "comparo-dark"
        self.backend = TuiBackend(config)

    async def on_unmount(self) -> None:
        """Drain the backend before the process exits."""
        result = await self.backend.shutdown()
        if result is not None and result.diagnostics:
            self.log(f"backend shutdown diagnostics: {[d.code for d in result.diagnostics]}")

    @override
    def compose(self) -> ComposeResult:
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
            self.state = replace(self.state, active_screen=ComparoScreen(ws))

    def select_execution_environment(self, environment: ResourceCoordinate) -> None:
        """Set the shared execution Environment used by contextual inspections."""
        if environment.kind != "Environment":
            raise ValueError("execution context must identify an Environment")
        self.state = replace(self.state, execution_environment=environment)

    def on_nav_tab_selected(self, message: NavTab.Selected) -> None:
        """Switch workspace from the navbar."""
        self.action_workspace(message.tab.value)

    def watch_state(self) -> None:
        """Reflect state into the main switcher."""
        self.query_one("#main", ContentSwitcher).current = self.state.active_screen
        for workspace in self.query(ExplorerWorkspace):
            workspace.execution_environment = self.state.execution_environment
