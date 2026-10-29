"""The tui3 shell — navrow with tabs, workspace host, footer keys."""

from __future__ import annotations

from pathlib import PurePath
from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import App
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.events import Key
from textual.widgets import Static

from comparo.tui3.screens.base import WorkspaceScreen
from comparo.tui3.screens.explorer import ExplorerScreen
from comparo.tui3.screens.launch import LaunchScreen
from comparo.tui3.screens.reports import ReportsScreen
from comparo.tui3.screens.settings import SettingsScreen
from comparo.tui3.theme import TUI3_DARK
from comparo.tui3.theme import TUI3_LIGHT
from comparo.tui3.theme import set_palette
from comparo.tui3.theme import style

WORKSPACES = ("explorer", "launch", "reports", "settings")


class _Tab(Static):
    """One clickable workspace tab."""

    def __init__(self, name: str, index: int) -> None:
        """Store the workspace name."""
        super().__init__(f" {index} {name.upper()} ", classes="nav-tab")
        self.ws = name

    def on_click(self) -> None:
        """Switch workspace."""
        app = self.app
        if isinstance(app, ComparoTui3):
            app.switch_workspace(self.ws)


class ComparoTui3(App[int]):
    """Four workspaces behind the v0 chrome."""

    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = "styles.tcss"
    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("1", "workspace('explorer')", "Explorer", show=False),
        Binding("2", "workspace('launch')", "Launch", show=False),
        Binding("3", "workspace('reports')", "Reports", show=False),
        Binding("4", "workspace('settings')", "Settings", show=False),
    ]

    def __init__(self) -> None:
        """Register themes and build the workspaces."""
        super().__init__()
        self._light = False
        self.register_theme(TUI3_DARK)
        self.register_theme(TUI3_LIGHT)
        self.theme = "tui3-dark"
        self._screens: dict[str, WorkspaceScreen] = {
            "explorer": ExplorerScreen(),
            "launch": LaunchScreen(),
            "reports": ReportsScreen(),
            "settings": SettingsScreen(),
        }
        self._active = "explorer"

    @override
    def compose(self) -> ComposeResult:
        """Chrome + workspace host."""
        yield Horizontal(
            Static(Text("comparo", style=style("acc.b")), classes="logo"),
            *(_Tab(name, index) for index, name in enumerate(WORKSPACES, 1)),
            Static("", classes="nav-crumb", id="nav-crumb"),
            Static("", classes="nav-tier", id="nav-tier"),
            classes="navrow",
        )
        yield Vertical(*self._screens.values(), id="body")
        yield Static("", classes="footrow", id="footrow")

    def on_mount(self) -> None:
        """Show explorer, focus its tree."""
        self._apply_active()

    def watch_theme(self, theme_name: str) -> None:
        """Swap the fragment palette and rebuild workspaces on theme change."""
        light = theme_name == "tui3-light"
        set_palette(light)
        if self.is_running and light != self._light:
            self._light = light
            self.call_after_refresh(self._rebuild_workspaces)
        else:
            self._light = light

    async def _rebuild_workspaces(self) -> None:
        """Recreate every workspace so baked fragments pick up the palette."""
        body = self.query_one("#body", Vertical)
        await body.remove_children()
        self._screens = {
            "explorer": ExplorerScreen(),
            "launch": LaunchScreen(),
            "reports": ReportsScreen(),
            "settings": SettingsScreen(),
        }
        await body.mount_all(self._screens.values())
        self.query_one(".logo", Static).update(Text("comparo", style=style("acc.b")))
        self._apply_active()

    # ------------------------------------------------------------ workspace

    def switch_workspace(self, name: str) -> None:
        """Public switch used by tabs and keys."""
        self._active = name
        self._apply_active()

    def _apply_active(self) -> None:
        for name, screen in self._screens.items():
            screen.display = name == self._active
        for tab in self.query(_Tab):
            tab.set_class(tab.ws == self._active, "-on")
        self.refresh_chrome()
        self._screens[self._active].focus_default()

    def refresh_chrome(self) -> None:
        """Update crumb, tier, and footer from the active screen."""
        screen = self._screens[self._active]
        crumb = self.query_one("#nav-crumb", Static)
        crumb.update(Text(screen.crumb(), style=style("dim")))
        tier = self.query_one("#nav-tier", Static)
        tier.update(Text(screen.tier(), style=style("acc")))
        foot = Text(" ")
        for key, action in screen.hints():
            foot.append(key, style=style("b"))
            foot.append(f" {action}   ", style=style("dim"))
        self.query_one("#footrow", Static).update(foot)

    def action_workspace(self, name: str) -> None:
        """Key 1-4."""
        self.switch_workspace(name)

    @property
    def active_workspace(self) -> str:
        """Name of the visible workspace."""
        return self._active

    # ----------------------------------------------------------------- keys

    def on_key(self, event: Key) -> None:
        """Route unclaimed keys to the active workspace."""
        focused = self.focused
        if focused is not None and focused.__class__.__name__ == "FilterInput":
            return
        screen = self._screens[self._active]
        if screen.on_ws_key(event.key):
            event.stop()
            self.refresh_chrome()
