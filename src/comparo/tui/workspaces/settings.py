"""Thin Settings composition limited to terminal-application preferences."""

from __future__ import annotations

from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical

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
        self._preferences = preferences

    @override
    def compose(self) -> ComposeResult:
        """Mount the reusable TUI-only settings panel."""
        yield StatePanel(
            TuiOnlySettingsPanel(self._preferences),
            mode=StatePanelMode.CONTENT,
        )

    def show_preferences(self, preferences: TuiPreferences) -> None:
        """Update the existing settings component without replacing this workspace."""
        if type(preferences) is not TuiPreferences:
            raise TypeError("Settings workspace requires TuiPreferences")
        self._preferences = preferences
        if self.is_mounted:
            self.query_one(TuiOnlySettingsPanel).show_preferences(preferences)


__all__ = ("SettingsWorkspace",)
