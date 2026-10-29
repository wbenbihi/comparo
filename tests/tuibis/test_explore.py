"""Explore workspace interactivity — filter, variants, body walk, matrix, graph."""

from __future__ import annotations

import asyncio

from comparo.tuibis.app import ComparoApp
from comparo.tuibis.screens.explore import ExploreWorkspace
from comparo.tuibis.widgets.cursor_list import CursorList
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.inspectors import RequestInspector
from comparo.tuibis.widgets.panel import Panel
from comparo.tuibis.widgets.project_tree import ProjectTree


def _leaf_keys(tree: ProjectTree) -> list[str]:
    keys: list[str] = []
    for group in tree.root.children:
        keys.extend(child.data for child in group.children if child.data is not None)
    return keys


async def _highlight(pilot, host: InspectorHost, key: str) -> None:  # type: ignore[no-untyped-def]
    for _ in range(20):
        await pilot.press("down")
        await pilot.pause()
        if host.current == key:
            return
    raise AssertionError(f"never reached {key}; ended on {host.current}")


def test_filter_narrows_tree_and_escape_clears() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            tree = app.query_one(ProjectTree)
            full_count = len(_leaf_keys(tree))
            await pilot.press("slash")
            await pilot.pause()
            assert not isinstance(app.focused, ProjectTree)
            for char in "checkout":
                await pilot.press(char)
            await pilot.pause()
            assert _leaf_keys(tree) == ["req:checkout"]
            labels = [str(node.label) for node in tree.root.children]
            assert any("REQUESTS" in label and "1 match" in label for label in labels)
            await pilot.press("escape")
            await pilot.pause()
            assert len(_leaf_keys(tree)) == full_count
            assert isinstance(app.focused, ProjectTree)

    asyncio.run(scenario())


def test_v_toggles_authored_and_resolved() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            host = app.query_one("#explore-inspectors", InspectorHost)
            panel = app.query_one("#inspector-panel", Panel)
            assert host.current == "req:quote"
            await pilot.press("v")
            await pilot.pause()
            assert host.current == "req:quote:authored"
            assert "authored" in str(panel.border_title)
            await pilot.press("v")
            await pilot.pause()
            assert host.current == "req:quote"
            assert "resolved" in str(panel.border_title)

    asyncio.run(scenario())


def test_body_walk_updates_explainer() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            host = app.query_one("#explore-inspectors", InspectorHost)
            await _highlight(pilot, host, "req:checkout")
            workspace = app.query_one(ExploreWorkspace)
            inspector = workspace._inspectors["req:checkout"]
            assert isinstance(inspector, RequestInspector)
            body = app.query_one("#body-walk", CursorList)
            body.focus()
            await pilot.pause()
            assert body.cursor_key == "body:2"
            assert "$.quote_id" in inspector.explanation_text
            await pilot.press("down")
            await pilot.pause()
            assert body.cursor_key == "body:3"
            assert "$.customer.id" in inspector.explanation_text

    asyncio.run(scenario())


def test_g_opens_graph_and_escape_returns() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            host = app.query_one("#explore-inspectors", InspectorHost)
            await pilot.press("g")
            await pilot.pause()
            assert host.current == "graph:req:quote"
            await pilot.press("escape")
            await pilot.pause()
            assert host.current == "req:quote"

    asyncio.run(scenario())


def test_graph_node_activation_jumps_to_resource() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            host = app.query_one("#explore-inspectors", InspectorHost)
            await pilot.press("g")
            await pilot.pause()
            assert host.current == "graph:req:quote"
            await pilot.press("enter")  # cursor starts on the Plans node
            await pilot.pause()
            assert host.current == "mat:plans"

    asyncio.run(scenario())


def test_matrix_space_narrows_a_case() -> None:
    async def scenario() -> None:
        app = ComparoApp()
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause()
            host = app.query_one("#explore-inspectors", InspectorHost)
            await _highlight(pilot, host, "mat:plans")
            panel = app.query_one("#inspector-panel", Panel)
            assert "⊞ Plans" in str(panel.border_title)
            cases = app.query_one("#matrix-cases", CursorList)
            cases.focus()
            await pilot.pause()
            before = cases.selected_keys
            assert "cell:basic-USD" in before
            await pilot.press("space")
            await pilot.pause()
            assert cases.selected_keys == before - {"cell:basic-USD"}

    asyncio.run(scenario())
