"""Tests for the terminal UI, driven headlessly via Textual's test harness."""

import asyncio
from pathlib import Path

from textual.widgets import Static
from textual.widgets import Tree

from comparo.core.loader import load_project
from comparo.tui.app import ComparoApp

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_tui_launches_and_populates_tree() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree", Tree)
            assert len(tree.root.children) > 0
            assert app.query_one("#detail-content", Static) is not None

    asyncio.run(go())
