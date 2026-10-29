"""Reports and Settings journeys."""

from __future__ import annotations

import asyncio
from importlib import import_module

from comparo.tui3.app import ComparoTui3
from comparo.tui3.screens.reports import ReportsScreen
from comparo.tui3.screens.settings import SettingsScreen


def _has_analyze() -> bool:
    """Whether the shared analyze contract has landed."""
    try:
        module = import_module("comparo.tui3.screens.analyze")
    except ImportError:
        return False
    return hasattr(module, "AnalyzeView")


def _plains(container: object) -> str:
    """Concatenated plain text of every TextRow under a widget."""
    from comparo.tui3.widgets import TextRow

    assert hasattr(container, "query")  # noqa: S101
    return " ".join(str(row.render()) for row in container.query(TextRow))


def test_reports_cursor_updates_summary() -> None:
    """Moving the archive cursor re-renders the summary panel."""

    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            reports = app.query_one(ReportsScreen)
            assert reports._sel == "8c3e11"  # noqa: S101
            assert "root cause" in _plains(reports._summary)  # noqa: S101
            await pilot.press("down")
            await pilot.pause()
            assert reports._sel == "7b21aa"  # noqa: S101
            assert "clean" in _plains(reports._summary)  # noqa: S101
            assert "7b21aa" in reports.crumb()  # noqa: S101

    asyncio.run(scenario())


def test_reports_replay_roundtrip() -> None:
    """Enter opens the analyze replay (when available) and esc returns to browse."""

    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            reports = app.query_one(ReportsScreen)
            await pilot.press("enter")
            await pilot.pause()
            if _has_analyze():
                assert reports._mode == "replay"  # noqa: S101
                assert reports._replay is not None  # noqa: S101
                assert "replay 8c3e11" in reports.crumb()  # noqa: S101
                await pilot.press("escape")
                await pilot.pause()
            assert reports._mode == "browse"  # noqa: S101
            assert reports._replay is None  # noqa: S101
            assert reports._browse.display is True  # noqa: S101

    asyncio.run(scenario())


def test_settings_cursor_switches_category() -> None:
    """Moving the category cursor swaps the content panel."""

    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.press("4")
            await pilot.pause()
            settings = app.query_one(SettingsScreen)
            assert settings._cat == "appearance"  # noqa: S101
            assert "PREVIEW" in _plains(settings._content)  # noqa: S101
            await pilot.press("down")
            await pilot.pause()
            assert settings._cat == "keyboard"  # noqa: S101
            assert "quit — always, everywhere" in _plains(settings._content)  # noqa: S101

    asyncio.run(scenario())


def test_settings_theme_toggle_flips_app_theme() -> None:
    """The t key really switches the registered app theme back and forth."""

    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.press("4")
            await pilot.pause()
            assert app.theme == "tui3-dark"  # noqa: S101
            await pilot.press("t")
            await pilot.pause()
            assert app.theme == "tui3-light"  # noqa: S101
            await pilot.press("t")
            await pilot.pause()
            assert app.theme == "tui3-dark"  # noqa: S101

    asyncio.run(scenario())
