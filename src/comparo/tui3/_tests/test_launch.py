"""Launch journeys."""

from __future__ import annotations

import asyncio

from textual.widgets import Static

from comparo.tui3.app import ComparoTui3
from comparo.tui3.screens.launch import LaunchScreen


def test_scope_toggle_updates_math() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            launch = app.query_one(LaunchScreen)
            assert launch._stage == "scope"  # noqa: S101
            assert "= 12 calls" in str(launch._math_row.content)  # noqa: S101
            await pilot.press("space")  # cursor starts on Price quote — toggle it off
            await pilot.pause()
            assert launch._scope.on["quote"] is False  # noqa: S101
            assert "= 6 calls" in str(launch._math_row.content)  # noqa: S101
            assert "3 requests" in str(launch._math_row.content)  # noqa: S101

    asyncio.run(scenario())


def test_candidate_toggle_flips_tier_and_math() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            launch = app.query_one(LaunchScreen)
            assert launch.tier() == "DIFF"  # noqa: S101
            await pilot.press("c")
            await pilot.pause()
            assert launch.tier() == "RUN"  # noqa: S101
            assert str(app.query_one("#nav-tier", Static).content) == "RUN"  # noqa: S101
            assert "RUN assertions-only" in str(launch._math_row.content)  # noqa: S101
            await pilot.press("c")
            await pilot.pause()
            assert launch.tier() == "DIFF"  # noqa: S101
            assert "⇄ DIFF" in str(launch._math_row.content)  # noqa: S101

    asyncio.run(scenario())


def test_blocked_run_toasts_and_stays() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            launch = app.query_one(LaunchScreen)
            await pilot.press("x")  # checkout is write-capable and writes are not approved
            await pilot.pause()
            assert launch._stage == "scope"  # noqa: S101
            assert any("blocked" in n.message for n in app._notifications)  # noqa: S101

    asyncio.run(scenario())


def test_plan_roundtrip() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            launch = app.query_one(LaunchScreen)
            await pilot.press("p")
            await pilot.pause()
            assert launch._stage == "plan"  # noqa: S101
            assert launch.crumb() == "launch · plan"  # noqa: S101
            await pilot.press("escape")
            await pilot.pause()
            assert launch._stage == "scope"  # noqa: S101

    asyncio.run(scenario())


def test_run_flow_lands_on_results_and_esc_returns() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            launch = app.query_one(LaunchScreen)
            await pilot.press("w")  # approve writes
            await pilot.pause()
            assert launch._scope.writes == "approved"  # noqa: S101
            await pilot.press("x")
            await pilot.pause()
            assert launch._stage in ("run", "results")  # noqa: S101
            for _ in range(300):
                await pilot.pause(0.02)
                if launch._stage == "results":
                    break
            assert launch._stage == "results"  # noqa: S101
            await pilot.press("escape")
            await pilot.pause()
            assert launch._stage == "scope"  # noqa: S101

    old_tick, old_delay = LaunchScreen.RUN_TICK, LaunchScreen.RESULTS_DELAY
    LaunchScreen.RUN_TICK = 0.02
    LaunchScreen.RESULTS_DELAY = 0.02
    try:
        asyncio.run(scenario())
    finally:
        LaunchScreen.RUN_TICK = old_tick
        LaunchScreen.RESULTS_DELAY = old_delay


def test_run_cancel_marks_skipped() -> None:
    async def scenario() -> None:
        app = ComparoTui3()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            launch = app.query_one(LaunchScreen)
            await pilot.press("w")
            await pilot.press("x")
            await pilot.pause()
            assert launch._stage == "run"  # noqa: S101
            await pilot.press("escape")
            await pilot.pause()
            assert launch._stage == "run"  # noqa: S101
            assert launch._run_cancelled is True  # noqa: S101
            await pilot.press("escape")
            await pilot.pause()
            assert launch._stage == "scope"  # noqa: S101

    asyncio.run(scenario())
