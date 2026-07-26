"""Framework-neutral terminal preference contracts."""

from __future__ import annotations

import msgspec
import pytest

from comparo.tui.commands import CommandId
from comparo.tui.commands import KeymapOverride
from comparo.tui.design import COMPARO_DARK_THEME
from comparo.tui.design import ThemeId
from comparo.tui.navigation.routes import WorkspaceId
from comparo.tui.state.models import DEFAULT_TUI_PREFERENCES
from comparo.tui.state.models import TuiPreferences


def test_preferences_are_tui_only_immutable_and_typed() -> None:
    preferences = TuiPreferences(
        theme=COMPARO_DARK_THEME.theme_id,
        default_workspace=WorkspaceId.COMPARE,
        dense=True,
        mouse=False,
        keymap=(KeymapOverride(command_id=CommandId.APP_PALETTE, keys=("ctrl+k",)),),
    )
    assert preferences.default_workspace is WorkspaceId.COMPARE
    assert TuiPreferences() == DEFAULT_TUI_PREFERENCES
    for field, value in (
        ("theme", "dark"),
        ("default_workspace", "compare"),
        ("dense", 1),
        ("mouse", 1),
        ("glyphs", "auto"),
        ("reduced_motion", 1),
    ):
        with pytest.raises(TypeError):
            msgspec.structs.replace(preferences, **{field: value})
    with pytest.raises(ValueError, match="theme ID"):
        ThemeId(value="Not Portable")
    with pytest.raises(ValueError, match="at most once"):
        msgspec.structs.replace(preferences, keymap=preferences.keymap * 2)
