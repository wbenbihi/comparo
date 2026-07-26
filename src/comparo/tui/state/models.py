"""Immutable presentation preferences owned only by the terminal frontend."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.tui.commands.keymap import KeymapOverride
from comparo.tui.commands.keymap import textual_keymap
from comparo.tui.design.themes import COMPARO_DARK_THEME
from comparo.tui.design.themes import ThemeId
from comparo.tui.navigation.routes import WorkspaceId


@final
class TuiPreferences(msgspec.Struct, frozen=True, kw_only=True):
    """Presentation preferences that never alter project execution semantics."""

    theme: ThemeId = COMPARO_DARK_THEME.theme_id
    default_workspace: WorkspaceId = WorkspaceId.EXPLORER
    dense: bool = False
    mouse: bool = True
    keymap: tuple[KeymapOverride, ...] = ()

    def __post_init__(self) -> None:
        """Reject project-facing or untyped preference values."""
        if type(self.theme) is not ThemeId:
            raise TypeError("TUI theme preference must be typed")
        if type(self.default_workspace) is not WorkspaceId:
            raise TypeError("TUI default workspace must be typed")
        if type(self.dense) is not bool or type(self.mouse) is not bool:
            raise TypeError("TUI boolean preferences must be bool")
        textual_keymap(self.keymap)


DEFAULT_TUI_PREFERENCES = TuiPreferences()


__all__ = ("DEFAULT_TUI_PREFERENCES", "TuiPreferences")
