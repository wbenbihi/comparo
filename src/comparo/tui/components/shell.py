"""Persistent terminal-native product bar shared by every workspace."""

from __future__ import annotations

from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label
from textual.widgets import Static
from textual.widgets import Tab
from textual.widgets import Tabs

from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.tui.navigation import WorkspaceId
from comparo.tui.presentation import safe_terminal_text


@final
class ComparoShellBar(Horizontal):
    """Brand, workspace navigation, and current project context in one row."""

    def __init__(
        self,
        active: WorkspaceId,
        result: ProjectWorkspaceResult | None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the persistent shell context."""
        super().__init__(id=id, classes=classes)
        self._active = active
        self._result = result

    @override
    def compose(self) -> ComposeResult:
        """Compose the native workspace Tabs once."""
        yield Label("comparo", id="shell-brand")
        yield Tabs(
            *(Tab(item.value.title(), id=f"tab-{item.value}") for item in WorkspaceId),
            active=f"tab-{self._active.value}",
            id="workspace-tabs",
        )
        yield Static(_context_text(self._result), id="shell-context", markup=False)

    def show_project(self, result: ProjectWorkspaceResult | None) -> None:
        """Update project context without rebuilding workspace navigation."""
        self._result = result
        if self.is_mounted:
            self.query_one("#shell-context", Static).update(_context_text(result))


def _context_text(result: ProjectWorkspaceResult | None) -> str:
    if result is None:
        return "Project loading…  ·  Ctrl+P Commands"
    snapshot = result.snapshot
    if snapshot is None:
        return "Project unavailable  ·  Ctrl+P Commands"
    project = snapshot.project
    return safe_terminal_text(
        f"Project {project.name or project.coordinate.resource_id}  ·  Ctrl+P Commands",
        maximum_characters=180,
    )


__all__ = ("ComparoShellBar",)
