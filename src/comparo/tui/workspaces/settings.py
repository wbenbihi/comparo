"""Thin Settings composition limited to terminal-application preferences."""

from __future__ import annotations

from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Label
from textual.widgets import Static

from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode
from comparo.tui.components import TuiOnlySettingsPanel
from comparo.tui.state import TuiPreferences


@final
class SettingsWorkspace(Vertical):
    """Compose only TUI appearance and interaction preferences."""

    def __init__(
        self,
        preferences: TuiPreferences,
        *,
        id: str | None = None,
        classes: str | None = "workspace",
    ) -> None:
        """Initialize Settings from local TUI preferences only."""
        super().__init__(id=id, classes=classes)
        self.can_focus = True
        self._preferences = preferences

    @override
    def compose(self) -> ComposeResult:
        """Mount the reusable TUI-only settings panel."""
        with Vertical(classes="settings-page"):
            with (
                Horizontal(classes="workspace-heading"),
                Vertical(classes="workspace-heading-copy"),
            ):
                yield Label("Settings", classes="workspace-title")
                yield Static(
                    "Terminal appearance, interaction, and accessibility only.",
                    classes="workspace-subtitle",
                    markup=False,
                )
            yield StatePanel(
                TuiOnlySettingsPanel(self._preferences),
                mode=StatePanelMode.CONTENT,
                classes="settings-surface",
            )

    def show_preferences(self, preferences: TuiPreferences) -> None:
        """Update the existing settings component without replacing this workspace."""
        if type(preferences) is not TuiPreferences:
            raise TypeError("Settings workspace requires TuiPreferences")
        self._preferences = preferences
        if self.is_mounted:
            self.query_one(TuiOnlySettingsPanel).show_preferences(preferences)


__all__ = ("SettingsWorkspace",)
