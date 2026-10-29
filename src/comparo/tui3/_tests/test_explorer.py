"""Explorer journeys."""

from __future__ import annotations

import asyncio

from comparo.tui3.app import ComparoTui3
from comparo.tui3.screens.explorer import ExplorerScreen


def test_cursor_updates_detail_for_every_leaf() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            explorer = app.query_one(ExplorerScreen)
            seen = set()
            for _ in range(20):
                await pilot.press("down")
                seen.add(explorer._sel)
            assert any(k.startswith("env-") for k in seen)  # noqa: S101
            assert any(k.startswith("prof-") for k in seen)  # noqa: S101
            assert any(k.startswith("val-") for k in seen)  # noqa: S101

    asyncio.run(scenario())


def test_facet_cycle_and_fold() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            explorer = app.query_one(ExplorerScreen)
            await pilot.press("right_square_bracket")
            assert explorer._facet == "resolved"  # noqa: S101
            await pilot.press("left_square_bracket")
            assert explorer._facet == "overview"  # noqa: S101

    asyncio.run(scenario())
