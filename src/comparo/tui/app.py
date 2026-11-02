"""Textual application shell for the Comparo terminal interface."""

from __future__ import annotations

import asyncio
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

from comparo.application.plan_inspection_identity import ResourceCoordinate

from .backend import TuiBackend
from .bindings import application_bindings
from .components.header import AppHeader
from .components.navigation import NavTab
from .components.results import ResultsView
from .contracts import TuiLaunchRequest
from .preferences import Preferences
from .preferences import load_preferences
from .preferences import preferences_path
from .preferences import save_preferences
from .states import AppState
from .states import default_app_state
from .theme import COMPARO_DARK
from .theme import COMPARO_LIGHT
from .types import ComparoScreen
from .workspaces import WORKSPACES
from .workspaces.explorer import ExplorerWorkspace
from .workspaces.launch import LaunchWorkspace


class ComparoApp(App[None]):
    """Own the terminal application's backend and active workspace."""

    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = [
        "styles/main.tcss",
        "styles/environment.tcss",
        "styles/request.tcss",
        "styles/resource_types.tcss",
        "styles/profile_resources.tcss",
        "styles/launch.tcss",
        "styles/reports.tcss",
        "styles/settings.tcss",
    ]
    BINDINGS: ClassVar[list[BindingType]] = application_bindings()
    state: reactive[AppState] = reactive(default_app_state, init=False)

    def __init__(
        self, config: Path | TuiLaunchRequest, *, preference_path: Path | None = None
    ) -> None:
        """Initialize the application for a project path."""
        super().__init__()
        self.preference_path = preference_path or preferences_path()
        self.preferences, self.preference_notice = load_preferences(self.preference_path)
        self.register_theme(COMPARO_DARK)
        self.register_theme(COMPARO_LIGHT)
        self.theme = self.preferences.theme
        self.set_keymap(self.preferences.keymap)
        self.set_reactive(
            ComparoApp.state,
            replace(self.state, active_screen=ComparoScreen(self.preferences.workspace)),
        )
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
        with ContentSwitcher(initial=self.state.active_screen, id="main"):
            for tab in ComparoScreen:
                yield WORKSPACES[tab]()
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

    def on_mount(self) -> None:
        """Apply layout preferences after the screen exists."""
        self.screen.set_class(self.preferences.density == "compact", "density-compact")
        if self.preference_notice:
            self.notify(self.preference_notice, severity="warning")

    async def persist_preferences(self, preferences: Preferences) -> bool:
        """Persist before activation so failed writes never discard the active settings."""
        try:
            await asyncio.to_thread(save_preferences, self.preference_path, preferences)
        except OSError:
            return False
        if not self.is_running:
            return True
        self.preferences = preferences
        self.preference_notice = ""
        self.set_keymap(preferences.keymap)
        self.screen.set_class(preferences.density == "compact", "density-compact")
        for tab in self.query(NavTab):
            tab.update(tab.display_name)
        for results in self.query(ResultsView):
            results.refresh_shortcuts()
        self.query_one(LaunchWorkspace)._update_controls()
        self.refresh(layout=True)
        return True
