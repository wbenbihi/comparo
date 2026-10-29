"""Launch workspace interactivity — scope math, topology, profiles, run board."""

from __future__ import annotations

import asyncio

from comparo.tuibis import fixtures_launch
from comparo.tuibis.app import ComparoApp
from comparo.tuibis.screens.launch import LaunchWorkspace


def _launch(app: ComparoApp) -> LaunchWorkspace:
    workspace = app._workspaces["LAUNCH"]
    assert isinstance(workspace, LaunchWorkspace)
    return workspace


def test_scope_toggle_updates_math_and_admit_guard() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            workspace = _launch(app)
            scope = workspace._scope
            assert scope.scope_selected_count == 4
            assert "10 cells" in scope.math_text()[1]
            assert "20 calls" in scope.math_text()[1]
            await pilot.press("space")  # cursor starts on req:quote — deselect it
            await pilot.pause()
            assert scope.scope_selected_count == 3
            assert "3 requests" in scope.math_text()[1]
            assert "7 cells" in scope.math_text()[1]
            await pilot.press("n")
            await pilot.pause()
            assert scope.scope_selected_count == 0
            assert "nothing selected" in scope.math_text()[4]
            await pilot.press("p")
            await pilot.pause()
            assert workspace._stage == "scope"  # admit refused with empty scope
            await pilot.press("a")
            await pilot.pause()
            assert scope.scope_selected_count == 6
            assert "12 cells" in scope.math_text()[1]
            await pilot.press("p")
            await pilot.pause()
            assert workspace._stage == "plan"

    asyncio.run(scenario())


def test_scope_candidate_toggle_flips_to_assertions_only() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            scope = _launch(app)._scope
            assert bool(scope.candidate_on)
            assert "DIFF" in scope.math_text()[0]
            await pilot.press("c")
            await pilot.pause()
            assert not bool(scope.candidate_on)
            assert "assertions-only" in scope.math_text()[0]
            assert "0 comparisons" in scope.math_text()[1]
            assert "10 calls" in scope.math_text()[1]
            await pilot.press("c")
            await pilot.pause()
            assert scope.candidate_on
            assert "DIFF" in scope.math_text()[0]
            assert "10 comparisons" in scope.math_text()[1]

    asyncio.run(scenario())


def test_scope_filter_narrows_list() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            scope = _launch(app)._scope
            assert scope._scope_list.cursor_key == "req:quote"
            await pilot.press("slash")
            await pilot.pause()
            await pilot.press("c", "h", "e", "c")
            await pilot.pause()
            assert scope._scope_list.cursor_key == "req:checkout"

    asyncio.run(scenario())


def test_scope_profile_activation_prefills_selection() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            scope = _launch(app)._scope
            for _ in range(7):  # 5 remaining requests, then the two profile rows
                await pilot.press("down")
            await pilot.pause()
            assert scope._scope_list.cursor_key == "profile:smoke-run"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert scope.selected_keys == fixtures_launch.PROFILES["profile:smoke-run"]
            assert scope.scope_selected_count == 2
            assert "prefilled by smoke-run" in scope.math_text()[3]
            assert "4 cells" in scope.math_text()[1]
            assert "8 calls" in scope.math_text()[1]

    asyncio.run(scenario())


def test_run_board_cursor_drives_selected_call_panel() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.press("p")
            await pilot.press("x")
            await pilot.pause()
            workspace = _launch(app)
            assert workspace._stage == "run"
            run = workspace._run
            assert run.selected_call_key == "call:1"
            assert "3/3 held" in "".join(run.detail_text())
            await pilot.press("down")
            await pilot.pause()
            assert run.selected_call_key == "call:2"
            assert "streaming" in "".join(run.detail_text())
            await pilot.press("down")
            await pilot.pause()
            assert run.selected_call_key == "call:3"
            assert "outbound drift" in "".join(run.detail_text())

    asyncio.run(scenario())


def test_run_fails_only_and_cancelling_states() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.press("p")
            await pilot.press("x")
            await pilot.pause()
            run = _launch(app)._run
            assert run.board_mode == "all"
            await pilot.press("f")
            await pilot.pause()
            assert run.board_mode == "fails"
            assert run.selected_call_key == "call:3"
            await pilot.press("f")
            await pilot.pause()
            assert run.board_mode == "all"
            await pilot.press("a")
            await pilot.pause()
            assert bool(run.cancelling)
            assert run.board_mode == "cancel"
            assert run._header_panel.has_class("tone-warning")
            await pilot.press("a")
            await pilot.pause()
            assert not run.cancelling
            assert run.board_mode == "all"
            assert not run._header_panel.has_class("tone-warning")

    asyncio.run(scenario())
