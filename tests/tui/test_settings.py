"""User-owned preferences, keyboard remapping, and truthful header context."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from tests.tui.test_launch_workspace import _wait
from textual.widgets import Button
from textual.widgets import Static

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.tui.app import ComparoApp
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.menu import Menu
from comparo.tui.components.selectfield import SelectField
from comparo.tui.contracts import TuiLaunchRequest
from comparo.tui.launch import run_tui
from comparo.tui.preferences import Preferences
from comparo.tui.preferences import load_preferences
from comparo.tui.preferences import save_preferences
from comparo.tui.types import ComparoScreen
from comparo.tui.workspaces.settings import SettingsWorkspace


def test_preferences_validate_conflicts_and_preserve_invalid_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="conflicts"):
        Preferences(keymap={"workspace.launch": "f2", "analyze.next": "f2"})
    with pytest.raises(ValueError, match="Use the default"):
        Preferences(keymap={"launch.run": "enter"})
    path = tmp_path / "tui.json"
    path.write_text('{"secret-canary": true}')
    preferences, notice = load_preferences(path)
    assert preferences == Preferences()
    assert notice
    assert "secret-canary" not in notice
    assert path.read_text() == '{"secret-canary": true}'
    selected = Preferences(theme="comparo-light", mouse=False, keymap={"workspace.settings": "f4"})
    save_preferences(path, selected)
    assert load_preferences(path) == (selected, "")


@pytest.mark.parametrize("size", [(80, 30), (120, 40), (160, 52)])
def test_settings_persist_without_replacing_run_state(
    tmp_path: Path, size: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)

    async def scenario() -> None:
        app = ComparoApp(Path("examples/quickstart"), preference_path=tmp_path / "prefs.json")
        async with app.run_test(size=size) as pilot:
            await pilot.press("4")
            assert app.state.active_screen is ComparoScreen.SETTINGS
            settings = app.query_one(SettingsWorkspace)
            sections = settings.query_one("#settings-sections", Menu)
            assert sections.items[0].id == "general"
            await _wait(pilot, lambda: bool(settings.query_one(KeyValueList).entries))
            entries = settings.query_one(KeyValueList).entries
            assert [field.key for field in entries] == [
                "Version",
                "Author",
                "Repository",
                "Source",
                "License",
                "Build SHA",
            ]
            assert str(entries[1].value) == "Walid Benbihi"
            assert str(entries[-1].value) != "Not recorded"
            sections.focus()
            await pilot.press("home", "enter")
            assert sections.has_focus
            controls = app.backend.execution_settings
            selected = replace(
                app.preferences,
                density="compact",
                workspace="REPORTS",
                keymap={"workspace.settings": "f4"},
            )
            assert await app.persist_preferences(selected)
            assert app.screen.has_class("density-compact")
            assert app.backend.execution_settings is controls
            await pilot.press("1", "f4")
            assert app.state.active_screen is ComparoScreen.SETTINGS
            app.select_execution_environment(
                ResourceCoordinate(
                    namespace="local",
                    kind="Environment",
                    resource_id="candidate",
                    fingerprint="a" * 64,
                )
            )
            await pilot.pause()
            assert "local/candidate" in str(app.query_one("#header-context", Static).render())
            assert "X.X.X" not in str(app.query_one("#header-version", Static).render())
            assert settings.region.width > 0
            app.save_screenshot(f"settings-{size[0]}.svg", path=str(tmp_path))
        restarted = ComparoApp(Path("examples/quickstart"), preference_path=tmp_path / "prefs.json")
        assert restarted.state.active_screen is ComparoScreen.REPORTS
        assert restarted.preferences == selected

    asyncio.run(scenario())


def test_settings_editor_applies_draft_and_light_theme_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)

    async def scenario() -> None:
        path = tmp_path / "prefs.json"
        app = ComparoApp(Path("examples/quickstart"), preference_path=path)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.press("4")
            settings = app.query_one(SettingsWorkspace)
            theme = settings.query_one("#setting-theme", SelectField)
            theme.focus()
            await pilot.press("enter", "down", "enter")
            assert settings.draft.theme == "comparo-light"
            assert app.preferences.theme == "comparo-dark"
            assert not path.exists()
            next(b for b in settings.query(Button) if b.name == "save").press()
            await _wait(pilot, lambda: path.exists())
            assert app.preferences.theme == "comparo-light"
            assert app.theme == "comparo-dark"
        restarted = ComparoApp(Path("examples/quickstart"), preference_path=path)
        async with restarted.run_test(size=(120, 40)) as pilot:
            assert restarted.theme == "comparo-light"
            await pilot.press("4")
            restarted.save_screenshot("settings-light.svg", path=str(tmp_path))

    asyncio.run(scenario())


def test_mouse_preference_is_passed_to_terminal_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "prefs.json"
    save_preferences(path, Preferences(mouse=False))
    app = ComparoApp(Path("examples/quickstart"), preference_path=path)
    calls: list[bool] = []
    monkeypatch.setattr(app, "run", lambda *, mouse: calls.append(mouse))
    assert run_tui(TuiLaunchRequest(), app_factory=lambda request: app) == 0
    assert calls == [False]
