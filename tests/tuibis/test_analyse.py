"""Analyse interactivity — cursor-bound panels, facet cycling, dives, replay."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from comparo.tuibis.app import ComparoApp
from comparo.tuibis.screens.launch import LaunchWorkspace
from comparo.tuibis.screens.reports import ReportsWorkspace
from comparo.tuibis.widgets.analyse_view import AnalyseView
from comparo.tuibis.widgets.lines import LineBlock
from comparo.tuibis.widgets.sections import TextRow

if TYPE_CHECKING:
    from textual.pilot import Pilot


def _launch_analyse(app: ComparoApp) -> AnalyseView:
    workspace = app._workspaces["LAUNCH"]
    assert isinstance(workspace, LaunchWorkspace)
    return workspace._analyse


def _replay_analyse(app: ComparoApp) -> AnalyseView:
    workspace = app._workspaces["REPORTS"]
    assert isinstance(workspace, ReportsWorkspace)
    return workspace._replay


async def _goto_analyse(pilot: Pilot[int]) -> None:
    await pilot.press("2")
    await pilot.press("p")
    await pilot.press("x")
    await pilot.press("right_square_bracket")
    await pilot.pause()


def _block_text(block: LineBlock) -> str:
    return "\n".join(str(row.content) for row in block.query(TextRow))


def test_requests_cursor_rebinds_detail_and_inspect() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await _goto_analyse(pilot)
            view = _launch_analyse(app)
            detail_panel = view._detail_panels["requests"]
            detail_block = view._detail_blocks["requests"]
            inspect_block = view._inspect_blocks["requests"]
            assert view._cursor_keys["requests"] == "req:checkout"
            assert "Checkout" in str(detail_panel.border_title)
            before_detail = _block_text(detail_block)
            before_inspect = _block_text(inspect_block)
            assert "SENT NOT 1:1" in before_detail
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["requests"] == "req:orders"
            assert "List orders" in str(detail_panel.border_title)
            after_detail = _block_text(detail_block)
            assert after_detail != before_detail
            assert "SENT 1:1" in after_detail
            assert "Upstream timeout" in _block_text(inspect_block)
            assert _block_text(inspect_block) != before_inspect
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["requests"] == "req:quote"
            assert "Price quote" in str(detail_panel.border_title)
            assert "drift 2.7%" in _block_text(inspect_block)
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["requests"] == "req:product"
            assert "all assertions held" in _block_text(inspect_block)
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["requests"] == "req:product"

    asyncio.run(scenario())


def test_index_cycle_then_issue_cursor() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await _goto_analyse(pilot)
            view = _launch_analyse(app)
            await pilot.press("i")
            await pilot.pause()
            assert view._active == "issues"
            assert str(view._detail_panels["issues"].border_title) == "ISSUE — 1 of 3"
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["issues"] == "issue:2"
            assert str(view._detail_panels["issues"].border_title) == "ISSUE — 2 of 3"
            detail = _block_text(view._detail_blocks["issues"])
            assert "price-decimal" in detail
            assert "separator dropped" in _block_text(view._inspect_blocks["issues"])
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["issues"] == "issue:3"
            assert "latency-slo" in _block_text(view._detail_blocks["issues"])

    asyncio.run(scenario())


def test_s_cycles_inspect_facets() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await _goto_analyse(pilot)
            view = _launch_analyse(app)
            inspect_block = view._inspect_blocks["requests"]
            assert view._facets["requests"] == "RESPONSE"
            evidence = _block_text(inspect_block)
            await pilot.press("s")
            await pilot.pause()
            assert view._facets["requests"] == "HEADERS"
            headers_text = _block_text(inspect_block)
            assert headers_text != evidence
            assert "content-length" in headers_text
            await pilot.press("s")
            await pilot.pause()
            assert view._facets["requests"] == "EVENTS"
            assert "message.start" in _block_text(inspect_block)
            await pilot.press("s")
            await pilot.pause()
            assert view._facets["requests"] == "RAW"
            assert "raw candidate body" in _block_text(inspect_block)
            await pilot.press("s")
            await pilot.pause()
            assert view._facets["requests"] == "ALL"
            assert "status-2xx expected 200-299" in _block_text(inspect_block)
            await pilot.press("s")
            await pilot.pause()
            assert view._facets["requests"] == "REQUEST"
            assert "what was actually sent" in _block_text(inspect_block)

    asyncio.run(scenario())


def test_facet_follows_selection_variants() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await _goto_analyse(pilot)
            view = _launch_analyse(app)
            inspect_block = view._inspect_blocks["requests"]
            for _ in range(5):
                await pilot.press("s")
            await pilot.pause()
            assert view._facets["requests"] == "REQUEST"
            assert "POST /v1/checkout" in _block_text(inspect_block)
            await pilot.press("down")
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["requests"] == "req:quote"
            assert "GET /v1/quote" in _block_text(inspect_block)

    asyncio.run(scenario())


def test_dive_and_back_keep_list_interactive() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await _goto_analyse(pilot)
            view = _launch_analyse(app)
            await pilot.press("z")
            await pilot.pause()
            assert bool(view._diving)
            assert view._host.current == "dive:response"
            await pilot.press("escape")
            await pilot.pause()
            assert not view._diving
            assert view._host.current == "requests"
            await pilot.press("down")
            await pilot.pause()
            assert view._cursor_keys["requests"] == "req:orders"

    asyncio.run(scenario())


def test_replay_analyse_renders_and_stays_interactive() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.press("o")
            await pilot.pause()
            replay = _replay_analyse(app)
            assert replay._lists["requests"].is_mounted
            assert replay._cursor_keys["requests"] == "req:checkout"
            await pilot.press("down")
            await pilot.pause()
            assert replay._cursor_keys["requests"] == "req:orders"
            assert "List orders" in str(replay._detail_panels["requests"].border_title)
            await pilot.press("i")
            await pilot.pause()
            assert replay._active == "issues"
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(scenario())
