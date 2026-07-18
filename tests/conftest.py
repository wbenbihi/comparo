"""Shared fixtures: keep every test hermetic.

The TUI reads (and, in the Settings flows, writes) the user config at
``$COMPARO_CONFIG_HOME``/``~/.config/comparo``. Without isolation the suite
inherits the developer's real preferences — a changed default tab or theme
would flip TUI assertions — and settings tests would overwrite the real file.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path / "user-config"))
