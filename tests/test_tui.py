"""Tests for the terminal UI, driven headlessly via Textual's test harness."""

import asyncio
import shutil
from pathlib import Path

import pytest
from textual.widgets import Static
from textual.widgets import Tree

from comparo.core.compare import CellDiff
from comparo.core.diagnostics import LoadError
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.tui.app import ComparoApp
from comparo.tui.app import ConfirmModal
from comparo.tui.app import DiffView
from comparo.tui.app import EnvPickerModal
from comparo.tui.app import ErrorView
from comparo.tui.app import ExplorerView
from comparo.tui.app import ReportView
from comparo.tui.app import RunView
from comparo.tui.app import SettingsView
from comparo.tui.app import _body_diff_lines
from comparo.tui.app import _edges
from comparo.tui.app import _environments
from comparo.tui.app import _help_body
from comparo.tui.app import _parse_sse

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_body_diff_lines_marks_drift_skip_and_same() -> None:
    base = {"args": {"a": "1", "b": "2"}, "headers": {"h": "x"}}
    cand = {"args": {"a": "1", "b": "3"}, "headers": {"h": "y"}}
    states = {
        "$.args.a": FieldDiff("$.args.a", State.SAME, "exact"),
        "$.args.b": FieldDiff("$.args.b", State.DRIFT, "exact", '"2" → "3"'),
        "$.headers": FieldDiff("$.headers", State.SKIP, "ignore"),
    }
    lines = _body_diff_lines(base, cand, states)
    # the drifted leaf carries both sides under the drift state
    assert any(state == "drift" and '"2"' in left for _, left, _, state, _ in lines)
    assert any(state == "drift" and '"3"' in right for _, _, right, state, _ in lines)
    # the same leaf is not drift
    assert any(state == "same" and '"a": "1"' in left for _, left, _, state, _ in lines)
    # the ignored container collapses to a single skip line, not recursed into
    assert any(state == "skip" and "headers" in left for _, left, _, state, _ in lines)
    assert not any('"h"' in left for _, left, _, _, _ in lines)


def test_diff_candidate_picker_updates_the_pair() -> None:
    loaded = load_project(SAMPLE)  # has local + prod environments
    environments = _environments(loaded)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")  # Diff
            await pilot.pause()
            diff = app.query_one(DiffView)
            diff.action_pick_candidate()
            await pilot.pause()
            assert isinstance(app.screen, EnvPickerModal)
            await pilot.press("enter")  # choose the first (highlighted) environment
            await pilot.pause()
            assert diff._pair is not None
            assert diff._pair[1].metadata.id == environments[0].metadata.id

    asyncio.run(go())


def test_diff_toggles_flip_view_and_index() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            diff = app.query_one(DiffView)
            unified = diff._unified
            diff.action_toggle_view()
            assert diff._unified != unified
            assert diff._index_mode == "fields"
            diff.action_toggle_index()
            assert diff._index_mode == "rules"

    asyncio.run(go())


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


def test_parse_sse_splits_events_and_joins_multiline_data() -> None:
    stream = 'event: message\ndata: {"n": 1}\n\nid: 2\ndata: hello\ndata: world\n\n'
    events = _parse_sse(stream)
    assert len(events) == 2
    assert events[0]["event"] == "message"
    assert events[0]["data"] == '{"n": 1}'
    assert events[1]["id"] == "2"
    assert events[1]["data"] == "hello\nworld"


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


def test_silencing_a_drift_is_gated_by_a_confirmation(tmp_path: Path) -> None:
    # Copy the sample so the triage write lands in a throwaway tree, not the repo.
    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    loaded = load_project(root)
    request = loaded.objects["request.get-json"]  # -> diff.strict -> diff/strict.yaml
    assert isinstance(request, Request)
    profile_file = root / "diff" / "strict.yaml"
    before = profile_file.read_text(encoding="utf-8")

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")  # Diff screen — sets the pair
            await pilot.pause()
            diff = app.query_one(DiffView)
            # Inject a synthetic drift so we never touch the network.
            drift = FieldDiff("$.headers.x-trace", State.DRIFT, "exact", '"a" → "b"')
            diff._cells = [CellDiff(request, "", [drift])]
            diff._done = True
            diff._regroup()
            diff._populate_drift()
            await pilot.pause()

            diff.action_silence()  # opens the confirmation — must NOT write yet
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            assert profile_file.read_text(encoding="utf-8") == before

            await pilot.press("n")  # decline — still no write
            await pilot.pause()
            assert profile_file.read_text(encoding="utf-8") == before

            diff.action_silence()
            await pilot.pause()
            await pilot.press("y")  # confirm — now it writes
            await pilot.pause()
            after = profile_file.read_text(encoding="utf-8")
            assert after != before
            assert "$.headers.x-trace" in after

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
