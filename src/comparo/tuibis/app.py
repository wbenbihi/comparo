"""The application shell — chrome plus workspace switching, nothing else."""

from __future__ import annotations

from pathlib import PurePath
from typing import ClassVar
from typing import override

from textual.app import App
from textual.app import ComposeResult
from textual.binding import Binding

from comparo.tuibis import fixtures
from comparo.tuibis.screens.explore import KEY_HINTS as EXPLORE_HINTS
from comparo.tuibis.screens.explore import ExploreWorkspace
from comparo.tuibis.screens.launch import LaunchWorkspace
from comparo.tuibis.screens.reports import ReportsWorkspace
from comparo.tuibis.screens.settings import SettingsWorkspace
from comparo.tuibis.theme import COMPARO_DARK
from comparo.tuibis.theme import COMPARO_LIGHT
from comparo.tuibis.widgets import FooterBar
from comparo.tuibis.widgets import TopBar
from comparo.tuibis.widgets import WorkspaceTabs
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.project_tree import ProjectTree


class ComparoApp(App[int]):
    """Four workspaces behind one chrome; keys route to the active one."""

    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = "styles.tcss"

    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("1", "workspace('EXPLORE')", "Explore", show=False),
        Binding("2", "workspace('LAUNCH')", "Launch", show=False),
        Binding("3", "workspace('REPORTS')", "Reports", show=False),
        Binding("4", "workspace('SETTINGS')", "Settings", show=False),
        Binding("p", "command('admit')", "Admit plan", show=False),
        Binding("x", "command('execute')", "Run", show=False),
        Binding("i", "command('index')", "Next index", show=False),
        Binding("z", "command('dive-response')", "Deep dive", show=False),
        Binding("d", "command('dive-sent')", "Sent diff", show=False),
        Binding("o", "command('open')", "Open", show=False),
        Binding("escape", "command('back')", "Back", show=False),
        Binding("]", "command('advance')", "Next stage", show=False),
        Binding("[", "command('back')", "Previous stage", show=False),
    ]

    def __init__(self) -> None:
        """Build fixtures and register the theme before CSS parsing."""
        super().__init__()
        self._project = fixtures.project()
        self.register_theme(COMPARO_DARK)
        self.register_theme(COMPARO_LIGHT)
        self.theme = "comparo-dark"
        self._workspaces: dict[str, object] = {}

    @override
    def compose(self) -> ComposeResult:
        """Yield the chrome and the workspace host."""
        explore = ExploreWorkspace(self._project)
        launch = LaunchWorkspace()
        reports = ReportsWorkspace()
        settings = SettingsWorkspace()
        self._workspaces = {
            "EXPLORE": explore,
            "LAUNCH": launch,
            "REPORTS": reports,
            "SETTINGS": settings,
        }
        yield TopBar(
            self._project.name,
            self._project.baseline,
            self._project.candidate,
        )
        yield WorkspaceTabs("EXPLORE")
        self._host = InspectorHost(
            {
                "EXPLORE": explore,
                "LAUNCH": launch,
                "REPORTS": reports,
                "SETTINGS": settings,
            },
            initial="EXPLORE",
        )
        self._host.add_class("workspace-host")
        yield self._host
        yield FooterBar(EXPLORE_HINTS)

    def _refresh_hints(self) -> None:
        workspace = self._workspaces[self._host.current]
        hints = getattr(workspace, "hints", EXPLORE_HINTS)
        self.query_one(FooterBar).set_hints(tuple(hints))

    def on_mount(self) -> None:
        """Give the tree initial focus so arrows work immediately."""
        self.query_one(ProjectTree).focus()

    def _focus_workspace(self, name: str) -> None:
        shown = self._workspaces[name]
        for widget in shown.query("*"):  # type: ignore[attr-defined]
            if widget.can_focus and widget.display:
                widget.focus()
                return
        self.set_focus(None)

    def action_workspace(self, name: str) -> None:
        """Switch the visible workspace, its chrome, and focus."""
        self.query_one(WorkspaceTabs).set_active(name)
        self._host.show(name)
        self._focus_workspace(name)
        self._refresh_hints()

    def action_command(self, command: str) -> None:
        """Route a semantic command to the active workspace."""
        workspace = self._workspaces[self._host.current]
        handler = getattr(workspace, "handle_command", None)
        if handler is not None and handler(command):
            self._refresh_hints()
