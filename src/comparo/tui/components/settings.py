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
from textual.widgets import Collapsible
from textual.widgets import Label
from textual.widgets import Select
from textual.widgets import Static
from textual.widgets import Switch

from comparo.tui.commands import COMMAND_CATALOG
from comparo.tui.commands import resolved_primary_key
from comparo.tui.design import BUILTIN_THEMES
from comparo.tui.design import SYSTEM_THEME
from comparo.tui.design import ThemeId
from comparo.tui.navigation import WorkspaceId
from comparo.tui.state import GlyphPreference
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
        yield Label("APPEARANCE", classes="settings-section-title")
        yield _setting_row(
            "Theme",
            Select(
                (
                    ("System default", SYSTEM_THEME),
                    *((item.label, item.theme_id) for item in BUILTIN_THEMES),
                ),
                value=self._preferences.theme,
                allow_blank=False,
                id="tui-setting-theme",
            ),
        )
        yield _setting_row(
            "Dense layout",
            Switch(value=self._preferences.dense, id="tui-setting-dense"),
        )
        yield Label("INTERACTION", classes="settings-section-title")
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
        yield Label("ACCESSIBILITY", classes="settings-section-title")
        yield _setting_row(
            "Glyphs",
            Select(
                tuple((item.value.title(), item) for item in GlyphPreference),
                value=self._preferences.glyphs,
                allow_blank=False,
                id="tui-setting-glyphs",
            ),
        )
        yield _setting_row(
            "Reduced motion",
            Switch(
                value=self._preferences.reduced_motion,
                id="tui-setting-reduced-motion",
            ),
        )
        yield Label("KEYBOARD", classes="settings-section-title")
        yield Collapsible(
            Static(_keymap_summary(self._preferences), markup=False),
            title="Resolved command bindings",
            collapsed=True,
            id="tui-setting-keymap-summary",
        )

    def current_preferences(self) -> TuiPreferences:
        """Return the exact current local settings control values."""
        if not self.is_mounted:
            return self._preferences
        return TuiPreferences(
            theme=cast("ThemeId", self.query_one("#tui-setting-theme", Select).value),
            default_workspace=cast(
                "WorkspaceId",
                self.query_one("#tui-setting-workspace", Select).value,
            ),
            dense=self.query_one("#tui-setting-dense", Switch).value,
            mouse=self.query_one("#tui-setting-mouse", Switch).value,
            glyphs=cast(
                "GlyphPreference",
                self.query_one("#tui-setting-glyphs", Select).value,
            ),
            reduced_motion=self.query_one("#tui-setting-reduced-motion", Switch).value,
            keymap=self._preferences.keymap,
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
        self.query_one("#tui-setting-glyphs", Select).value = preferences.glyphs
        self.query_one("#tui-setting-reduced-motion", Switch).value = preferences.reduced_motion

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


def _keymap_summary(preferences: TuiPreferences) -> str:
    return "\n".join(
        f"{resolved_primary_key(command.command_id, preferences.keymap):<12} {command.label}"
        for command in COMMAND_CATALOG
    )


__all__ = ("TuiOnlySettingsPanel",)
