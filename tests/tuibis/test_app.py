"""Shell and Explore behaviors — Textual Pilot journeys."""

from __future__ import annotations

import asyncio

from comparo.tuibis.app import ComparoApp
from comparo.tuibis.screens.launch import LaunchWorkspace
from comparo.tuibis.screens.reports import ReportsWorkspace
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.project_tree import ProjectTree


def test_app_boots_into_explore() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            assert app.query_one("#project-panel") is not None
            assert app.query_one("#inspector-panel") is not None
            assert app.query_one("#explore-inspectors", InspectorHost).current == "req:quote"

    asyncio.run(scenario())


def test_tree_highlight_switches_inspector_instantly() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            app.query_one(ProjectTree).focus()
            await pilot.pause()
            for _ in range(6):
                await pilot.press("down")
            await pilot.pause()
            assert app.query_one("#explore-inspectors", InspectorHost).current == "req:checkout"

    asyncio.run(scenario())


def test_q_always_quits() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()

    asyncio.run(scenario())


def test_launch_stage_flow_and_analyse() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            workspace = app._workspaces["LAUNCH"]
            assert isinstance(workspace, LaunchWorkspace)
            assert workspace._stage == "scope"
            await pilot.press("p")
            assert workspace._stage == "plan"
            await pilot.press("x")
            assert workspace._stage == "run"
            await pilot.press("right_square_bracket")
            assert workspace._stage == "analyse"
            analyse = workspace._analyse
            assert analyse._active == "requests"
            await pilot.press("i")
            assert analyse._active == "issues"
            await pilot.press("z")
            assert bool(analyse._diving)
            await pilot.press("escape")
            assert not analyse._diving
            assert workspace._stage == "analyse"
            await pilot.press("escape")
            assert workspace._stage == "run"

    asyncio.run(scenario())


def test_reports_replay_mounts_analyse_read_only() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            workspace = app._workspaces["REPORTS"]
            assert isinstance(workspace, ReportsWorkspace)
            assert bool(workspace._browsing)
            await pilot.press("o")
            assert not bool(workspace._browsing)
            await pilot.press("escape")
            assert workspace._browsing

    asyncio.run(scenario())
