"""Reports and Settings behaviors — Textual Pilot journeys."""

from __future__ import annotations

import asyncio

from comparo.tuibis.app import ComparoApp
from comparo.tuibis.screens.reports import ReportsWorkspace
from comparo.tuibis.screens.settings import SettingsWorkspace
from comparo.tuibis.widgets.cursor_list import CursorList
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.panel import Panel
from comparo.tuibis.widgets.sections import TextRow


def test_archive_cursor_updates_preview() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            archive = app.query_one("#reports-archive-list", CursorList)
            preview = app.query_one("#reports-preview", Panel)
            assert archive.cursor_key == "run:74ecaa"
            assert "74ecaa" in str(preview.border_title)
            first_row = str(preview.query(TextRow).first().render())
            assert "FAILED" in first_row
            archive.focus()
            await pilot.press("down")
            await pilot.pause()
            assert archive.cursor_key == "run:74d1b3"
            assert "74d1b3" in str(preview.border_title)
            first_row = str(preview.query(TextRow).first().render())
            assert "PASSED" in first_row
            await pilot.press("down", "down")
            await pilot.pause()
            assert archive.cursor_key == "run:748f21"
            assert "748f21" in str(preview.border_title)
            rows = "".join(str(row.render()) for row in preview.query(TextRow))
            assert "transport error" in rows
            assert "open analysis" not in rows
            assert "copy run id" in rows

    asyncio.run(scenario())


def test_filter_narrows_archive_and_moves_cursor() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            archive = app.query_one("#reports-archive-list", CursorList)
            preview = app.query_one("#reports-preview", Panel)
            archive.focus()
            await pilot.press("slash")
            await pilot.pause()
            await pilot.press("s", "m", "o", "k", "e")
            await pilot.pause()
            visible_runs = [
                item.key
                for row, item in zip(archive.query(TextRow), archive._items, strict=True)
                if row.display and not item.header
            ]
            assert visible_runs == ["run:74d1b3"]
            assert archive.cursor_key == "run:74d1b3"
            assert "74d1b3" in str(preview.border_title)

    asyncio.run(scenario())


def test_enter_opens_replay_and_escape_returns() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            workspace = app._workspaces["REPORTS"]
            assert isinstance(workspace, ReportsWorkspace)
            archive = app.query_one("#reports-archive-list", CursorList)
            archive.focus()
            assert bool(workspace._browsing)
            await pilot.press("enter")
            await pilot.pause()
            assert not bool(workspace._browsing)
            await pilot.press("escape")
            await pilot.pause()
            assert workspace._browsing

    asyncio.run(scenario())


def test_settings_nav_switches_detail_host() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            host = app.query_one("#settings-detail", InspectorHost)
            assert host.current == "sec:appearance"
            await pilot.press("down")
            await pilot.pause()
            assert host.current == "sec:keyboard"
            await pilot.press("down", "down", "down")
            await pilot.pause()
            assert host.current == "sec:updates"
            await pilot.press("up", "up", "up", "up")
            await pilot.pause()
            assert host.current == "sec:appearance"

    asyncio.run(scenario())


def test_theme_toggle_flips_app_theme() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            assert app.theme == "comparo-dark"
            await pilot.press("t")
            await pilot.pause()
            assert app.theme == "comparo-light"
            workspace = app._workspaces["SETTINGS"]
            assert isinstance(workspace, SettingsWorkspace)
            appearance = str(workspace._appearance_block.query(TextRow)[1].render())
            assert "(•) comparo light" in appearance
            assert "( ) comparo dark" in appearance
            await pilot.press("t")
            await pilot.pause()
            assert app.theme == "comparo-dark"
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert app.theme == "comparo-dark"

    asyncio.run(scenario())
