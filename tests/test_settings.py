"""Tests for the app-level Settings tab, the self-check, and the version check."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from comparo.adapters import updates as updates_adapter
from comparo.adapters import userconfig
from comparo.cli.app import app as cli_app
from comparo.core.loader import load_project
from comparo.tui.app import ComparoApp
from comparo.tui.app import DiffView
from comparo.tui.app import SettingsView

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"
runner = CliRunner()


def test_settings_exposes_the_nine_app_sections() -> None:
    keys = [key for key, _ in SettingsView.SECTIONS]
    assert keys == [
        "about",
        "project",
        "security",
        "appearance",
        "keybindings",
        "updates",
        "plugins",
        "engine",
        "behavior",
    ]


def test_selfcheck_masks_every_sink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(112, 40)) as pilot:
            await pilot.pause()
            await pilot.press("6")  # Settings
            await pilot.press("down", "down")  # → Security & Redaction
            await pilot.pause()
            settings = app.query_one(SettingsView)
            await pilot.press("t")  # run the self-check
            for _ in range(40):
                await pilot.pause(0.1)
                if settings._selfcheck is not None:
                    break
            assert settings._selfcheck is not None
            assert len(settings._selfcheck) == 9
            assert all(ok for _, _, ok in settings._selfcheck)

    asyncio.run(go())


def test_toggling_update_check_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))
    # The toggle triggers an immediate check — stub the network so the test is offline.

    async def _no_network(_current: str, **_: object) -> None:
        return None

    monkeypatch.setattr(updates_adapter, "check_latest", _no_network)
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(112, 40)) as pilot:
            await pilot.pause()
            assert app.user_config.update_check is False  # opt-in default
            await pilot.press("6")
            await pilot.press("down", "down", "down", "down", "down")  # → Updates & Privacy
            await pilot.pause()
            await pilot.press("enter")  # toggle it on
            await pilot.pause()
            # a fresh read — the toggle reassigns app.user_config, which mypy can't
            # see through the key press (so it would otherwise narrow to the default).
            toggled = app.user_config
            assert toggled.update_check is True
            assert userconfig.load().update_check is True  # persisted to disk

    asyncio.run(go())


def test_toggling_diff_layout_applies_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(112, 40)) as pilot:
            await pilot.pause()
            await pilot.press("6")
            await pilot.press("down", "down", "down")  # → Appearance
            await pilot.pause()
            await pilot.press("enter")  # unified → side-by-side
            await pilot.pause()
            assert app.user_config.diff_view == "side-by-side"
            assert app.query_one(DiffView)._unified is False
            assert userconfig.load().diff_view == "side-by-side"

    asyncio.run(go())


def test_version_check_records_and_would_toast_a_newer_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))

    async def _newer(_current: str, **_: object) -> str:
        return "999.0.0"

    monkeypatch.setattr(updates_adapter, "check_latest", _newer)
    loaded = load_project(SAMPLE)
    toasts: list[str] = []

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda *a, **k: toasts.append(str(a[0])))
            await app._version_check(force=True)
            assert app.user_config.update_latest_seen == "999.0.0"
            assert userconfig.load().update_latest_seen == "999.0.0"
            assert any("999.0.0" in message for message in toasts)  # toasted

    asyncio.run(go())


def test_confirm_on_quit_never_stacks_a_second_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # q always quits — even with confirm-on-quit on, a second q inside the prompt
    # exits directly rather than stacking another dialog (re-entrancy guard).
    from comparo.tui.app import ConfirmModal

    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.user_config = app.user_config.with_(confirm_quit=True)
            exits: list[int] = []
            monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(1))
            app.action_quit()  # first q → one confirm dialog
            await pilot.pause()
            assert sum(isinstance(s, ConfirmModal) for s in app.screen_stack) == 1
            assert app._quitting is True
            app.action_quit()  # second q (inside the prompt) → exits, no second dialog
            await pilot.pause()
            assert exits  # it exited
            assert sum(isinstance(s, ConfirmModal) for s in app.screen_stack) == 1

    asyncio.run(go())


def test_doctor_cli_reports_every_sink_masked() -> None:
    result = runner.invoke(cli_app, ["doctor"])
    assert result.exit_code == 0
    assert "9/9 sinks masked the canary" in result.stdout
