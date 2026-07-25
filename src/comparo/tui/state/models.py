"""Immutable presentation preferences owned only by the terminal frontend."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.tui.navigation.routes import WorkspaceId


class ThemePreference(StrEnum):
    """TUI-only theme preference."""

    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"


@final
class TuiPreferences(msgspec.Struct, frozen=True, kw_only=True):
    """Presentation preferences that never alter project execution semantics."""

    theme: ThemePreference = ThemePreference.SYSTEM
    default_workspace: WorkspaceId = WorkspaceId.EXPLORER
    dense: bool = False
    mouse: bool = True

    def __post_init__(self) -> None:
        """Reject project-facing or untyped preference values."""
        if type(self.theme) is not ThemePreference:
            raise TypeError("TUI theme preference must be typed")
        if type(self.default_workspace) is not WorkspaceId:
            raise TypeError("TUI default workspace must be typed")
        if type(self.dense) is not bool or type(self.mouse) is not bool:
            raise TypeError("TUI boolean preferences must be bool")


DEFAULT_TUI_PREFERENCES = TuiPreferences()


__all__ = ("DEFAULT_TUI_PREFERENCES", "ThemePreference", "TuiPreferences")
