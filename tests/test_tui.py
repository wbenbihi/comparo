"""Tests for the terminal UI, driven headlessly via Textual's test harness."""

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Static
from textual.widgets import Tree

from comparo.core.diagnostics import LoadError
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.tui.app import ComparoApp
from comparo.tui.app import DiffView
from comparo.tui.app import ErrorView
from comparo.tui.app import ExplorerView
from comparo.tui.app import ReportView
from comparo.tui.app import RunView
from comparo.tui.app import SettingsView
from comparo.tui.app import _edges
from comparo.tui.app import _help_body

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_tui_launches_and_builds_tree() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree", Tree)
            # the project manifest root leaf, then one foldable branch per object kind
            assert len(tree.root.children) == 7
            assert not tree.root.children[0].allow_expand  # project node is a leaf
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


def test_raw_toggle_flips_the_selected_request() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            explorer = app.query_one(ExplorerView)
            assert explorer.raw is False
            explorer.toggle_raw()
            assert explorer.raw is True

    asyncio.run(go())


def test_selecting_an_environment_makes_it_the_default() -> None:
    loaded = load_project(SAMPLE)
    environments = [o for o in loaded.objects.values() if isinstance(o, Environment)]
    other = environments[-1]

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            app.set_default_environment(other)
            explorer = app.query_one(ExplorerView)
            assert explorer.environment is other
            assert app.environment is other

    asyncio.run(go())


def test_help_body_lists_screen_and_global_keys() -> None:
    body = _help_body("explorer").plain
    assert "health" in body  # screen-specific
    assert "quit comparo" in body  # global


def test_run_screen_selection_toggles_cells() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("2")  # Run screen
            await pilot.pause()
            run = app.query_one(RunView)
            full = len(run._plan())
            assert full > 1  # every cell selected by default
            await pilot.press("enter")  # toggle the cursor request out of the run
            assert len(run._plan()) < full

    asyncio.run(go())


def test_number_keys_switch_between_every_screen() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            for key, view in (
                ("2", RunView),
                ("3", DiffView),
                ("4", ReportView),
                ("5", SettingsView),
                ("1", ExplorerView),
            ):
                await pilot.press(key)
                await pilot.pause()
                assert app.query_one(view) is not None

    asyncio.run(go())


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
