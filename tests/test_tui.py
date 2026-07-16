"""Tests for the terminal UI, driven headlessly via Textual's test harness."""

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Static
from textual.widgets import Tree

from comparo.core.diagnostics import LoadError
from comparo.core.loader import load_project
from comparo.tui.app import ComparoApp
from comparo.tui.app import ErrorView
from comparo.tui.app import ExplorerView
from comparo.tui.app import _edges

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_tui_launches_and_builds_tree() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree", Tree)
            assert len(tree.root.children) == 6  # one foldable branch per object kind
            assert app.query_one("#detail-content", Static) is not None
            assert app.query_one("#context-content", Static) is not None

    asyncio.run(go())


def test_filter_narrows_the_tree() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            explorer = app.query_one(ExplorerView)
            all_visible = explorer.apply_filter("")
            matched = explorer.apply_filter("request")  # matches the Request kind
            assert 0 < matched < all_visible
            # An empty branch is dropped while filtering.
            tree = app.query_one("#tree", Tree)
            assert len(tree.root.children) < 6

    asyncio.run(go())


def test_graph_edges_link_requests_to_their_objects() -> None:
    loaded = load_project(SAMPLE)
    relations = {relation for _, relation, _ in _edges(loaded)}
    assert relations  # the sample wires requests to matrices/schemas/profiles


def test_error_screen_replaces_the_explorer(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: staging\nspec:\n  baseUrl: https://example.test\n",
        encoding="utf-8",
    )
    with pytest.raises(LoadError) as caught:
        load_project(broken)

    async def go() -> None:
        app = ComparoApp.from_error(caught.value)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            assert app.query_one(ErrorView) is not None
            assert not app.query("#tree")  # no Explorer tree in error mode

    asyncio.run(go())
