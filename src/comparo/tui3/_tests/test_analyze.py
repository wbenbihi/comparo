"""Analyze view journeys."""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
from pathlib import Path
from pathlib import PurePath
from typing import ClassVar
from typing import override

from textual.app import App
from textual.widgets import Static

from comparo.tui3.screens.analyze import AnalyzeView
from comparo.tui3.screens.analyze import _OutboundCallout
from comparo.tui3.theme import TUI3_DARK


class _Harness(App[None]):
    """Minimal host that mounts one AnalyzeView."""

    CSS_PATH: ClassVar[str | PurePath | list[str | PurePath] | None] = (
        Path(__file__).resolve().parent.parent / "styles.tcss"
    )

    def __init__(self, *, replay: bool = False) -> None:
        """Build the view under test."""
        super().__init__()
        self.register_theme(TUI3_DARK)
        self.theme = "tui3-dark"
        self.view = AnalyzeView(replay=replay)

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield the view."""
        yield self.view

    def on_mount(self) -> None:
        """Focus like the hosting workspaces do."""
        self.view.focus_default()


def _detail_text(view: AnalyzeView) -> str:
    return "\n".join(str(w.render()) for w in view._detail.query(Static))


def test_cursor_moves_detail() -> None:
    async def scenario() -> None:
        app = _Harness()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            assert app.view._sel["cells"] == "quote-basic"
            assert "basic" in _detail_text(app.view)
            await pilot.press("down")
            await pilot.pause()
            assert app.view._sel["cells"] == "quote-pro"
            assert "pro" in _detail_text(app.view)

    asyncio.run(scenario())


def test_t_cycles_index() -> None:
    async def scenario() -> None:
        app = _Harness()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            assert app.view.route_key("t")
            await pilot.pause()
            assert app.view._index == "fields"
            assert app.view._lists["fields"].display
            assert not app.view._lists["cells"].display
            assert "$.price" in _detail_text(app.view)
            assert app.view.route_key("t")
            await pilot.pause()
            assert app.view._index == "rules"
            assert "$.headers.*" in _detail_text(app.view)
            assert app.view.route_key("t")
            await pilot.pause()
            assert app.view._index == "cells"

    asyncio.run(scenario())


def test_brackets_cycle_cell_facets() -> None:
    async def scenario() -> None:
        app = _Harness()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            assert app.view.route_key("right_square_bracket")
            await pilot.pause()
            assert app.view._facet == "request"
            assert "outbound DIFFERS" in _detail_text(app.view)
            assert app.view.route_key("left_square_bracket")
            await pilot.pause()
            assert app.view._facet == "overview"
            app.view.route_key("t")
            await pilot.pause()
            assert not app.view.route_key("right_square_bracket")

    asyncio.run(scenario())


def test_v_flips_diff_view() -> None:
    async def scenario() -> None:
        app = _Harness()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            assert app.view.route_key("v")
            await pilot.pause()
            assert app.view._view == "side"
            assert app.view.route_key("v")
            await pilot.pause()
            assert app.view._view == "unified"

    asyncio.run(scenario())


def test_o_folds_outbound_callout() -> None:
    async def scenario() -> None:
        app = _Harness()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            app.view.route_key("t")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert app.view._sel["fields"] == "taxRate"
            assert len(app.view.query(_OutboundCallout)) == 1
            assert app.view.route_key("o")
            await pilot.pause()
            assert len(app.view.query(_OutboundCallout)) == 0

    asyncio.run(scenario())


def test_replay_shows_pill_and_crumb() -> None:
    async def scenario() -> None:
        app = _Harness(replay=True)
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            summary = app.view._summary
            assert "replay 8c3e11" in summary._left.plain
            assert "read-only · esc back to reports" in summary._right.plain
            assert app.view.crumb_suffix() == "replay 8c3e11"

    asyncio.run(scenario())


def test_live_summary_and_crumb() -> None:
    async def scenario() -> None:
        app = _Harness()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            summary = app.view._summary
            assert "replay" not in summary._left.plain
            assert "saved · .runs/8c3e11" in summary._right.plain
            assert app.view.crumb_suffix() == "results · 8c3e11"

    asyncio.run(scenario())
