"""Framework-neutral terminal preference contracts."""

from __future__ import annotations

import msgspec
import pytest

from comparo.tui.navigation.routes import WorkspaceId
from comparo.tui.state.models import DEFAULT_TUI_PREFERENCES
from comparo.tui.state.models import ThemePreference
from comparo.tui.state.models import TuiPreferences


def test_preferences_are_tui_only_immutable_and_typed() -> None:
    preferences = TuiPreferences(
        theme=ThemePreference.DARK,
        default_workspace=WorkspaceId.COMPARE,
        dense=True,
        mouse=False,
    )
    assert preferences.default_workspace is WorkspaceId.COMPARE
    assert TuiPreferences() == DEFAULT_TUI_PREFERENCES
    for field, value in (
        ("theme", "dark"),
        ("default_workspace", "compare"),
        ("dense", 1),
        ("mouse", 1),
    ):
        with pytest.raises(TypeError):
            msgspec.structs.replace(preferences, **{field: value})
