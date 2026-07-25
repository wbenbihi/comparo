"""Functional terminal-application-only settings controls."""

from __future__ import annotations

from typing import cast
from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label
from textual.widgets import Select
from textual.widgets import Switch

from comparo.tui.navigation import WorkspaceId
from comparo.tui.state import ThemePreference
from comparo.tui.state import TuiPreferences


@final
class TuiOnlySettingsPanel(Vertical):
    """Edit TUI interaction and appearance preferences, never project settings."""

    @final
    class PreferencesChanged(Message):
        """The immutable local TUI preferences changed."""

        def __init__(self, preferences: TuiPreferences) -> None:
            """Retain one typed local preference snapshot."""
            super().__init__()
            self.preferences = preferences

    def __init__(
        self,
        preferences: TuiPreferences,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize controls from framework-neutral TUI preferences."""
        super().__init__(id=id, classes=classes)
        if type(preferences) is not TuiPreferences:
            raise TypeError("TUI settings panel requires TuiPreferences")
        self._preferences = preferences

    @override
    def compose(self) -> ComposeResult:
        """Compose closed theme/workspace selects and Boolean switches."""
        yield _setting_row(
            "Theme",
            Select(
                tuple((item.value.title(), item) for item in ThemePreference),
                value=self._preferences.theme,
                allow_blank=False,
                id="tui-setting-theme",
            ),
        )
        yield _setting_row(
            "Dense layout",
            Switch(value=self._preferences.dense, id="tui-setting-dense"),
        )
        yield _setting_row(
            "Mouse",
            Switch(value=self._preferences.mouse, id="tui-setting-mouse"),
        )
        yield _setting_row(
            "Default workspace",
            Select(
                tuple((item.value.title(), item) for item in WorkspaceId),
                value=self._preferences.default_workspace,
                allow_blank=False,
                id="tui-setting-workspace",
            ),
        )

    def current_preferences(self) -> TuiPreferences:
        """Return the exact current local settings control values."""
        if not self.is_mounted:
            return self._preferences
        return TuiPreferences(
            theme=cast("ThemePreference", self.query_one("#tui-setting-theme", Select).value),
            default_workspace=cast(
                "WorkspaceId",
                self.query_one("#tui-setting-workspace", Select).value,
            ),
            dense=self.query_one("#tui-setting-dense", Switch).value,
            mouse=self.query_one("#tui-setting-mouse", Switch).value,
        )

    def show_preferences(self, preferences: TuiPreferences) -> None:
        """Update existing controls without replacing the settings panel."""
        if type(preferences) is not TuiPreferences:
            raise TypeError("TUI settings panel requires TuiPreferences")
        self._preferences = preferences
        if not self.is_mounted:
            return
        self.query_one("#tui-setting-theme", Select).value = preferences.theme
        self.query_one("#tui-setting-workspace", Select).value = preferences.default_workspace
        self.query_one("#tui-setting-dense", Switch).value = preferences.dense
        self.query_one("#tui-setting-mouse", Switch).value = preferences.mouse

    @on(Select.Changed)
    @on(Switch.Changed)
    def publish_preferences(self) -> None:
        """Publish edits while leaving persistence to the app/controller."""
        self._preferences = self.current_preferences()
        self.post_message(self.PreferencesChanged(self._preferences))


def _setting_row(label: str, control: Select[object] | Switch) -> Horizontal:
    return Horizontal(
        Label(label, classes="settings-label", markup=False),
        control,
        classes="settings-row",
    )


__all__ = ("TuiOnlySettingsPanel",)
