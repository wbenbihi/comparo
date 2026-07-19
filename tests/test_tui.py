"""Tests for the terminal UI, driven headlessly via Textual's test harness."""

import asyncio
import shutil
from pathlib import Path

import pytest
from rich.console import Console
from textual.widgets import ContentSwitcher
from textual.widgets import DataTable
from textual.widgets import Input
from textual.widgets import Static
from textual.widgets import Tree

from comparo.core.assertions import AssertionResult
from comparo.core.compare import CellDiff
from comparo.core.diagnostics import LoadError
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionResult
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.models import Request
from comparo.core.report_record import ReportRecord
from comparo.core.streams import parse_sse
from comparo.tui.app import ComparoApp
from comparo.tui.app import ConfirmModal
from comparo.tui.app import DiffView
from comparo.tui.app import EnvPickerModal
from comparo.tui.app import ErrorView
from comparo.tui.app import ExecutionView
from comparo.tui.app import ExplorerView
from comparo.tui.app import NavBar
from comparo.tui.app import ReportView
from comparo.tui.app import RunView
from comparo.tui.app import SettingsView
from comparo.tui.render import _body_diff_lines
from comparo.tui.render import _edges
from comparo.tui.render import _environments
from comparo.tui.render import _help_body
from comparo.tui.render import _record_detail

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_app_redactor_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    # H-3: the redactor reads every declared secret file, so it must be built once
    # per project and shared across render sites, not rebuilt on each access.
    from comparo.core import redaction

    loaded = load_project(SAMPLE)
    app = ComparoApp(loaded)

    calls = 0
    real = redaction.secret_values

    def counting(project: object) -> set[str]:
        nonlocal calls
        calls += 1
        return real(project)  # type: ignore[arg-type]

    monkeypatch.setattr(redaction, "secret_values", counting)

    first = app.redactor
    second = app.redactor
    assert first is second  # cached_property hands back the same instance
    assert calls == 1  # the secret files are read once, not per redact site


def test_explorer_tree_keeps_focus_at_boot_and_on_return() -> None:
    # KEY-05 (functional): the Explorer's nav keys are tree-scoped, so the tree
    # must hold focus at boot and whenever the Explorer tab is re-entered.
    from textual.widgets import Tree

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            assert getattr(app.focused, "id", None) == "tree"  # boot
            await pilot.press("5")  # another tab (steals focus)
            await pilot.pause()
            await pilot.press("1")  # back to Explorer
            await pilot.pause()
            assert getattr(app.focused, "id", None) == "tree"
            tree = app.query_one("#tree", Tree)
            line = tree.cursor_line
            await pilot.press("down")
            await pilot.pause()
            assert tree.cursor_line != line  # arrows actually move the cursor

    asyncio.run(go())


def test_diff_x_does_not_relaunch_an_in_flight_diff() -> None:
    # KEY-05 (parity with Run): x mid-diff-run must not silently relaunch the worker.
    from comparo.tui.app import DiffView

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            diff = app.query_one(DiffView)
            diff.prime_pair(*[e.metadata.name for e in _environments(loaded)[:2]])
            # The real in-flight state is the RUNNING panel; x there is a no-op.
            diff.query_one("#diff-mode", ContentSwitcher).current = "diff-running"
            diff._done = False
            diff._run_id = "keep99"
            diff.execute()  # x while the diff is actually running
            await pilot.pause()
            assert diff._run_id == "keep99"

    asyncio.run(go())


def test_help_never_lists_a_key_twice() -> None:
    # KEY-05: a key documented in the screen block (even inside a combined token)
    # must not be repeated by the EVERYWHERE globals.
    for screen in ("execution", "execution-cell", "curl", "graph", "report-detail", "matrix"):
        body = _help_body(screen).plain
        assert body.count("esc") == 1, f"{screen} lists esc twice"


def test_diff_footer_swaps_between_prepare_and_results_keys() -> None:
    # KEY-05: the persistent footer must match the active diff state, not mix them.
    from comparo.tui.app import DiffView
    from comparo.tui.tokens import _DIFF_PREPARE_KEYS
    from comparo.tui.tokens import _DIFF_RESULTS_KEYS

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")  # Diff → lands in PREPARE
            await pilot.pause()
            diff = app.query_one(DiffView)
            assert diff.footer_keys() == _DIFF_PREPARE_KEYS
            diff.query_one("#diff-mode", ContentSwitcher).current = "diff-results"
            assert diff.footer_keys() == _DIFF_RESULTS_KEYS
            # PREPARE keys must not advertise the RESULTS-only toggles.
            prepare = {k for k, _ in _DIFF_PREPARE_KEYS}
            assert not ({"r", "v", "i"} & prepare)  # no RESULTS-only keys
            assert {"space", "enter"} <= prepare  # PREPARE-only tree keys present

    asyncio.run(go())


def test_diff_results_landing_focuses_the_drift_table() -> None:
    # KEY-05 (functional): while a diff runs it shows the RUNNING panel (focused so
    # esc cancels), and when results land the drift table takes focus so the RESULTS
    # keys (↑↓/r/v/i/s/esc) are live.
    from comparo.tui.app import DiffView

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            diff = app.query_one(DiffView)
            diff.prime_pair(*[e.metadata.name for e in _environments(loaded)[:2]])
            await pilot.pause()
            diff.execute()  # focus() runs synchronously before the network worker awaits
            await pilot.pause()
            # in flight: the RUNNING panel is shown and focused (esc cancels)
            assert diff.query_one("#diff-mode", ContentSwitcher).current == "diff-running"
            assert app.focused is not None
            assert app.focused.id == "diff-running"
            # when the plan finishes, the drift table takes focus on the RESULTS pane
            diff._finish([])
            await pilot.pause()
            assert diff.query_one("#diff-mode", ContentSwitcher).current == "diff-results"
            assert app.focused is not None
            assert app.focused.id == "drift-table"

    asyncio.run(go())


def test_diff_toggles_and_silence_are_inert_in_prepare() -> None:
    # KEY-05 (hidden keys): r/v/i must not silently mutate state in Diff PREPARE.
    from comparo.tui.app import DiffView

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")  # lands in PREPARE
            await pilot.pause()
            diff = app.query_one(DiffView)
            mode, unified = diff._index_mode, diff._unified
            diff.action_toggle_index()  # r
            diff.action_toggle_view()  # v
            await pilot.pause()
            assert diff._index_mode == mode  # unchanged in PREPARE
            assert diff._unified == unified
            depth = len(app.screen_stack)
            diff.action_silence()  # i — no toast, no modal
            await pilot.pause()
            assert len(app.screen_stack) == depth

    asyncio.run(go())


def test_run_x_does_not_restart_an_in_flight_run() -> None:
    # KEY-05 (destructive): pressing x mid-run must not silently discard the run.
    from comparo.tui.app import RunView

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("2")  # Run
            await pilot.pause()
            run = app.query_one(RunView)
            run.query_one("#run-mode", ContentSwitcher).current = "running"
            run._done = False
            run._run_id = "abc123"
            run.execute()  # x while a run is in flight
            await pilot.pause()
            assert run._run_id == "abc123"  # the live run was not restarted

    asyncio.run(go())


def test_matrix_and_env_keys_are_inert_outside_prepare() -> None:
    # KEY-05: the help scopes m/e to PREPARE, so they must no-op in RESULTS/RUNNING.
    from comparo.tui.app import DiffView
    from comparo.tui.app import RunView

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            diff = app.query_one(DiffView)
            diff.query_one("#diff-mode", ContentSwitcher).current = "diff-results"
            depth = len(app.screen_stack)
            diff.open_case_picker()  # m in RESULTS
            await pilot.pause()
            assert len(app.screen_stack) == depth  # no picker opened
            await pilot.press("2")
            await pilot.pause()
            run = app.query_one(RunView)
            run.query_one("#run-mode", ContentSwitcher).current = "running"
            depth = len(app.screen_stack)
            run.action_pick_env()  # e in RUNNING
            await pilot.pause()
            assert len(app.screen_stack) == depth  # no picker opened

    asyncio.run(go())


def test_instance_footer_omits_the_request_only_curl_key() -> None:
    # KEY-05: 'p' (curl) is inert on an Instance, so its footer must not show it.
    from comparo.tui.tokens import _INSTANCE_KEYS
    from comparo.tui.tokens import _RESOLVE_KEYS

    assert "p" not in {k for k, _ in _INSTANCE_KEYS}
    assert "p" in {k for k, _ in _RESOLVE_KEYS}  # requests keep it
    # Otherwise the two surfaces stay aligned (raw/resolved, filter, graph, help).
    assert {k for k, _ in _INSTANCE_KEYS} == {k for k, _ in _RESOLVE_KEYS} - {"p"}


def test_filter_question_mark_opens_help_instead_of_typing_a_literal() -> None:
    # KEY-04: a focused Input eats printable keys before any binding, so '?' must
    # be intercepted on the Input itself to keep help reachable from the filter.
    from comparo.tui.app import FilterModal
    from comparo.tui.app import HelpModal

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("1")  # Explorer
            await pilot.press("slash")  # open the filter — Input auto-focuses
            await pilot.pause()
            assert isinstance(app.screen, FilterModal)
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)  # '?' opened help …
            filter_modal = app.screen_stack[-2]
            assert isinstance(filter_modal, FilterModal)
            assert filter_modal.query_one("#filter-input", Input).value == ""  # … not typed

    asyncio.run(go())


def test_diff_well_is_a_full_width_rounded_banded_panel() -> None:
    # The git-diff component fills the panel width at ANY size (Rich expand), wraps
    # the purple hunk band + body in one rounded outline, and renders the same well
    # in both the unified and the side-by-side view.
    from rich.console import Console

    from comparo.core.compare import CellDiff
    from comparo.core.diff import diff
    from comparo.tui.render import _diff_body_view

    loaded = load_project(Path(__file__).parent.parent / "examples" / "canary-project")
    request = loaded.objects["request.basic-auth"]
    assert isinstance(request, Request)
    base = {"a": 1, "b": "old"}
    cand = {"a": 1, "b": "new"}
    fields = diff(base, cand, "exact", [])
    drift = next(field for field in fields if field.state is State.DRIFT)
    cell = CellDiff(request, "", fields, None, base, cand)
    group = (drift.path, [(cell, drift)])
    for width in (72, 120):
        for unified in (True, False):
            console = Console(width=width, record=True)
            console.print(_diff_body_view(group, None, unified=unified))
            out = console.export_text()
            corners = ("╭", "╮", "╰", "╯")
            assert all(corner in out for corner in corners)  # a rounded outline
            border = next(line for line in out.splitlines() if "╭" in line)
            assert len(border.rstrip()) == width  # the outline fills the panel width


def test_live_diff_view_masks_a_secret_echoed_into_the_response() -> None:
    # A server that echoes a secret into a drifted field must not print it on the
    # COMPARE panel: the render redacts every displayed value, like the report does.
    from rich.console import Console

    from comparo.core.compare import CellDiff
    from comparo.core.redaction import Redactor
    from comparo.tui.render import _diff_body_view

    secret = "cG9zdG1hbjpwYXNzd29yZA=="  # the canary BASIC_AUTH literal
    loaded = load_project(Path(__file__).parent.parent / "examples" / "canary-project")
    request = loaded.objects["request.basic-auth"]
    assert isinstance(request, Request)
    field = FieldDiff("$.token", State.DRIFT, "exact", f'"{secret}" → "other"')
    cell = CellDiff(request, "", [field], None, {"token": secret}, {"token": "other"})
    group = ("$.token", [(cell, field)])
    redact = Redactor.for_project(loaded).text
    console = Console(width=200)

    def render(renderable: object) -> str:
        with console.capture() as capture:
            console.print(renderable)
        return capture.get()

    unredacted = render(_diff_body_view(group, None, unified=True, names=("Stable", "Canary")))
    masked = render(
        _diff_body_view(group, None, unified=True, names=("Stable", "Canary"), redact=redact)
    )
    assert secret in unredacted  # proves the value really is on screen without redaction
    assert secret not in masked  # … and the redactor removes it


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


def test_execution_screen_renders_outcomes_and_gate() -> None:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    ok = AssertionResult("status", "equals", True, "error", "200 == 200")
    diff = CellDiff(
        request,
        "",
        [FieldDiff("$.a", State.DRIFT, "exact", '"x" → "y"')],
        None,
        {"a": "x"},
        {"a": "y"},
    )
    result = ExecutionResult(
        profile_id="exec.demo",
        baseline="Base",
        candidate="Cand",
        checked_assertions=True,
        checked_diff=True,
        outcomes=[CellOutcome("request.get-json", "", [ok], [ok], diff)],
    )
    profile = loaded.objects["execution.smoke"]
    assert isinstance(profile, ExecutionProfile)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")  # Execution tab
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            # the results overview is the active sub-view of the tab
            assert view._current_view() == "exec-results"
            # the one drifting cell shows in the drift index and fails the gate
            assert view.query_one("#exec-drift-table", DataTable).row_count == 1
            assert view.query_one("#exec-gate").has_class("fail")
            assert not result.passed

    asyncio.run(go())


def test_execution_rerun_clears_the_stale_cell() -> None:
    # M-d: launching a re-run must drop any cell drilled into on the previous run,
    # so a stale CellOutcome can never be rendered against the new result.
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    ok = AssertionResult("status", "equals", True, "error", "200 == 200")
    diff = CellDiff(
        request, "", [FieldDiff("$.a", State.DRIFT, "exact", '"x" → "y"')], None, {"a": "x"}, {}
    )
    outcome = CellOutcome("request.get-json", "", [ok], [ok], diff)
    result = ExecutionResult("exec.demo", "Base", "Cand", True, True, [outcome])
    profile = loaded.objects["execution.smoke"]
    assert isinstance(profile, ExecutionProfile)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")  # Execution tab
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            # drill into the drifting cell (as selecting a drift row would). The
            # explicit type keeps mypy from narrowing _cell to non-None here, which
            # would make the post-launch `is None` assertion look unreachable.
            drilled: CellOutcome | None = outcome
            view._cell = drilled
            view._drifted = [outcome]

            # Stub the worker body so the re-run never touches the network.
            async def _noop(*_args: object, **_kwargs: object) -> None:
                return None

            view._run = _noop  # type: ignore[method-assign]
            # re-run resets state synchronously, before the worker starts
            view.launch(profile)
            assert view._cell is None  # the fix: no stale cell survives a re-run
            assert view._drifted == []
            await pilot.pause()  # let the stubbed worker finish cleanly

    asyncio.run(go())


def test_report_filter_survives_returning_to_the_tab() -> None:
    # M-c/M23: apply_filter matches id/envs/kind/gate/execution, but refresh_screen
    # (fired on re-entering the tab) used to re-filter on id ALONE — silently
    # collapsing a gate/envs filter to nothing. Both must share one predicate.
    from comparo.tui.replay import AssertionSummary
    from comparo.tui.replay import ReplayRecord

    loaded = load_project(SAMPLE)
    empty = AssertionSummary(0, 0, 0, [])

    def _rec(rid: str, gate: str) -> ReplayRecord:
        return ReplayRecord(
            id=rid,
            created="2026-01-01",
            kind="diff",
            gate=gate,
            calls=1,
            same=0,
            drift=1 if gate == "FAIL" else 0,
            error=0,
            skipped=0,
            baseline="local",
            candidate="prod",
            execution=None,
            baseline_assertions=empty,
            candidate_assertions=empty,
            requests=[],
            cells=[],
        )

    records = [_rec("run-a", "FAIL"), _rec("run-b", "PASS")]

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("5")  # Report tab
            await pilot.pause()
            view = app.query_one(ReportView)
            view._load_records = lambda: records  # type: ignore[method-assign]  # bypass empty archive
            view._records = records
            # filter by GATE — the field refresh_screen used to drop
            assert view.apply_filter("fail") == 1
            assert [r.id for r in view._filtered] == ["run-a"]
            # returning to the tab re-runs refresh_screen; the gate filter must hold
            view.refresh_screen()
            assert [r.id for r in view._filtered] == ["run-a"]

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
            # The toggles are RESULTS-only affordances (guarded in PREPARE).
            diff.query_one("#diff-mode", ContentSwitcher).current = "diff-results"
            unified = diff._unified
            diff.action_toggle_view()
            assert diff._unified != unified
            assert diff._index_mode == "fields"
            diff.action_toggle_index()
            assert diff._index_mode == "rules"

    asyncio.run(go())


def test_diff_starts_in_prepare_with_a_selectable_plan() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")  # Diff
            await pilot.pause()
            diff = app.query_one(DiffView)
            # The Diff opens on PREPARE (interactive), not straight into a full replay.
            assert app.query_one("#diff-mode", ContentSwitcher).current == "diff-prepare"
            assert len(diff._plan()) > 0  # everything selected by default

    asyncio.run(go())


def test_diff_prime_pair_sets_or_rejects_environments() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            diff = app.query_one(DiffView)
            envs = _environments(loaded)
            assert diff.prime_pair(envs[0].metadata.name, envs[1].metadata.name)
            assert diff._pair is not None
            assert diff._pair[0].metadata.name == envs[0].metadata.name
            # An unknown env pair (e.g. from an old saved report) is rejected.
            assert not diff.prime_pair("no-such-env", "also-missing")

    asyncio.run(go())


def test_tui_launches_and_builds_tree() -> None:
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            tree = app.query_one("#tree", Tree)
            # the project manifest root leaf, then one foldable branch per non-empty kind
            # (Environments, Requests, Matrices, Schemas, Instances, Diff + Execution Profiles)
            assert len(tree.root.children) == 8
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
    events = parse_sse(stream)
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
            from textual.widgets import ContentSwitcher

            for key, view, expected in (
                ("2", RunView, "run-view"),
                ("3", DiffView, "diff-view"),
                ("4", ExecutionView, "execution-view"),
                ("5", ReportView, "report-view"),
                ("6", SettingsView, "settings-view"),
                ("1", ExplorerView, "explorer-view"),
            ):
                await pilot.press(key)
                await pilot.pause()
                assert app.query_one(view) is not None
                assert app.query_one("#content", ContentSwitcher).current == expected

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
            diff.query_one("#diff-mode", ContentSwitcher).current = "diff-results"  # RESULTS state
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


def test_crash_handler_reports_instead_of_raw_traceback() -> None:
    # ERR-03: an unhandled crash shows a friendly, redacted report (with an issue
    # link), not a raw Textual traceback — unless COMPARO_DEV is set.
    from rich.console import Console

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            captured: list[tuple[object, ...]] = []
            app.panic = lambda *r: captured.append(r)  # type: ignore[method-assign]
            app._handle_exception(ValueError("kaboom"))
            assert captured, "the crash handler did not report"
            console = Console()
            with console.capture() as capture:
                console.print(*captured[0])
            out = capture.get()
            assert "unexpected error" in out
            assert "kaboom" in out
            assert "github.com/wbenbihi/comparo/issues" in out
            app._exception = None  # so run_test teardown doesn't re-raise the injected error

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


def _seed_report(root: Path) -> None:
    import json

    from comparo.core import archive
    from comparo.core.compare import compare_cell
    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.report_builder import record_from_diff
    from comparo.core.resolve import ResolvedRequest
    from comparo.core.resolve import select_environment

    loaded = load_project(root)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    env = select_environment(loaded, "local")

    def execution(trace: str) -> Execution:
        response = HttpResponse(200, [], json.dumps({"headers": {"x-trace": trace}}).encode(), 5.0)
        resolved = ResolvedRequest("GET", "http://x/json", [], {}, None, [])
        return Execution(request, env, "", response, resolved=resolved)

    cell = compare_cell(loaded, execution("a"), execution("b"))
    record = record_from_diff(
        env,
        env,
        [cell],
        record_id="seed01",
        created="2026-01-01",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )
    archive.save_record(app_archive_dir(loaded), record)


def app_archive_dir(loaded: object) -> Path:
    from comparo.core.archive import archive_dir

    manifest = loaded.project  # type: ignore[attr-defined]
    data = manifest.spec.data if manifest else None
    report = manifest.spec.report if manifest else None
    return archive_dir(loaded.root, data, report)  # type: ignore[attr-defined]


def _tui_execution(loaded: object, request_id: str, body: object) -> Execution:
    import json

    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.resolve import ResolvedRequest
    from comparo.core.resolve import select_environment

    request = loaded.objects[request_id]  # type: ignore[attr-defined]
    assert isinstance(request, Request)
    env = select_environment(loaded, "local")  # type: ignore[arg-type]
    response = HttpResponse(200, [], json.dumps(body).encode(), 5.0)
    resolved = ResolvedRequest("GET", "http://x/json", [], {}, None, [])
    return Execution(request, env, "", response, resolved=resolved)


def _tui_run_record(loaded: object, cells: object, *, record_id: str, created: str) -> ReportRecord:
    from comparo.core.report_builder import record_from_run
    from comparo.core.resolve import select_environment

    return record_from_run(
        select_environment(loaded, "local"),  # type: ignore[arg-type]
        cells,  # type: ignore[arg-type]
        record_id=record_id,
        created=created,
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )


def _tui_diff_record(loaded: object, *, record_id: str, created: str) -> ReportRecord:
    from comparo.core.compare import compare_cell
    from comparo.core.report_builder import record_from_diff
    from comparo.core.resolve import select_environment

    env = select_environment(loaded, "local")  # type: ignore[arg-type]
    cell = compare_cell(
        loaded,  # type: ignore[arg-type]
        _tui_execution(loaded, "request.get-json", {"headers": {"x-trace": "a"}}),
        _tui_execution(loaded, "request.get-json", {"headers": {"x-trace": "b"}}),
    )
    return record_from_diff(
        env,
        env,
        [cell],
        record_id=record_id,
        created=created,
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )


def test_execution_transition_screen_shows_live_progress() -> None:
    # EXE-04: the running sub-view tracks the plan progress from engine ticks and
    # renders live inside the tab (no modal), advancing the bar/log as cells finish.
    from comparo.core.execution import ExecutionProgress

    loaded = load_project(SAMPLE)
    profile = next(obj for obj in loaded.objects.values() if isinstance(obj, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")  # Execution tab
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view._profile = profile
            view._show("exec-running")
            view.update_progress(ExecutionProgress("request.get-json", "", 0, 3, done=True))
            await pilot.pause()
            view.update_progress(ExecutionProgress("request.post-json", "", 1, 3, done=False))
            await pilot.pause()
            assert view._total == 3
            assert view._done == 1  # one cell finished
            assert view._current  # a cell is in flight
            assert view._recent  # the finished cell is logged
            # The running content widget was populated without error.
            assert view.query_one("#exec-running-content", Static) is not None

    asyncio.run(go())


def test_execution_open_diff_stays_in_flow_no_tab_jump() -> None:
    # NAV-02: the Execution's 'd' opens a scoped in-flow diff, never the Diff tab.
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    drift = FieldDiff("$.headers.x-trace", State.DRIFT, "exact", '"a" → "b"')
    cell = CellDiff(request, "", [drift])
    outcome = dataclasses_replace_diff(CellOutcome("request.get-json", "", [], [], None), cell)
    result = ExecutionResult("exec.x", "Stable", "Canary", True, True, [outcome])
    profile = next(obj for obj in loaded.objects.values() if isinstance(obj, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            # in-flow diff sub-view, never the Diff tab
            assert view._current_view() == "exec-diff-screen"
            assert app.query_one(NavBar).active == "execution"  # never left the tab
            await pilot.press("escape")
            await pilot.pause()
            assert view._current_view() == "exec-results"  # esc returns to the execution

    asyncio.run(go())


def test_execution_report_opens_in_place_no_tab_jump() -> None:
    # EXE-16 / NAV-02: 'e' exports the run's report without redirecting to the Report tab.
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    drift = FieldDiff("$.headers.x-trace", State.DRIFT, "exact", '"a" → "b"')
    cell = CellDiff(request, "", [drift])
    outcome = dataclasses_replace_diff(CellOutcome("request.get-json", "", [], [], None), cell)
    result = ExecutionResult("exec.x", "Stable", "Canary", True, True, [outcome])
    profile = next(obj for obj in loaded.objects.values() if isinstance(obj, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            # 'e' stays on the results sub-view (no tab jump, no modal)
            assert view._current_view() == "exec-results"
            assert app.query_one(NavBar).active == "execution"

    asyncio.run(go())


def test_execution_enter_drills_into_the_highlighted_cell() -> None:
    # The focused drift table eats 'enter', so the drill fires from row_selected.
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    drift = FieldDiff("$.headers.x-trace", State.DRIFT, "exact", '"a" → "b"')
    cell = CellDiff(request, "", [drift])
    outcome = CellOutcome("request.get-json", "", [], [], None)
    outcome = dataclasses_replace_diff(outcome, cell)
    result = ExecutionResult("exec.x", "Stable", "Canary", True, True, [outcome])
    profile = next(obj for obj in loaded.objects.values() if isinstance(obj, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert view._current_view() == "exec-cell"

    asyncio.run(go())


def dataclasses_replace_diff(outcome: "CellOutcome", cell: "CellDiff") -> "CellOutcome":
    import dataclasses

    return dataclasses.replace(outcome, diff=cell)


def test_execution_launch_lists_profiles_with_a_counted_plan() -> None:
    # EXE-01: opening the Execution tab lands on the launch picker — every profile
    # listed, with the SETUP plan preview counting the cells it will run.
    from textual.widgets import OptionList

    from comparo.tui.render import _exec_setup

    loaded = load_project(SAMPLE)
    profiles = [o for o in loaded.objects.values() if isinstance(o, ExecutionProfile)]

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(104, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            view = app.query_one(ExecutionView)
            assert view._current_view() == "exec-launch"
            options = view.query_one("#exec-profile-list", OptionList)
            assert options.option_count == len(profiles)
            # the SETUP panel counts the exact cells the plan will run
            console = Console()
            with console.capture() as capture:
                console.print(_exec_setup(loaded, profiles[0]))
            assert "plan preview" in capture.get()
            assert "will run" in capture.get()

    asyncio.run(go())


def test_execution_esc_steps_back_one_subview_never_quits() -> None:
    # NAV: esc walks launch ← results ← cell ← diff, one step at a time, in-tab.
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    body_base = {"args": {"taxRate": "0.20"}}
    body_cand = {"args": {"taxRate": "0.25"}}
    drift = FieldDiff("$.args.taxRate", State.DRIFT, "exact", "0.20 → 0.25")
    cell = CellDiff(request, "", [drift], None, body_base, body_cand)
    outcome = CellOutcome("request.get-json", "", [], [], cell)
    result = ExecutionResult("exec.x", "stable", "canary", True, True, [outcome])
    profile = next(o for o in loaded.objects.values() if isinstance(o, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(104, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            await pilot.press("enter")  # → cell
            await pilot.pause()
            await pilot.press("d")  # → in-flow diff
            await pilot.pause()
            assert view._current_view() == "exec-diff-screen"
            await pilot.press("escape")  # back to cell
            await pilot.pause()
            assert view._current_view() == "exec-cell"
            await pilot.press("escape")  # back to results
            await pilot.pause()
            assert view._current_view() == "exec-results"
            await pilot.press("escape")  # back to launch
            await pilot.pause()
            assert view._current_view() == "exec-launch"
            assert app.query_one(NavBar).active == "execution"  # never left the tab

    asyncio.run(go())


def test_execution_cell_v_toggles_unified_and_side_by_side() -> None:
    # EXE: 'v' flips the cell body diff between unified and side-by-side, in place.
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    cell = CellDiff(
        request,
        "",
        [FieldDiff("$.args.taxRate", State.DRIFT, "exact", "0.20 → 0.25")],
        None,
        {"args": {"taxRate": "0.20"}},
        {"args": {"taxRate": "0.25"}},
    )
    outcome = CellOutcome("request.get-json", "", [], [], cell)
    result = ExecutionResult("exec.x", "stable", "canary", True, True, [outcome])
    profile = next(o for o in loaded.objects.values() if isinstance(o, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(104, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert view._unified is True
            await pilot.press("v")
            await pilot.pause()
            assert view._unified is False

    asyncio.run(go())


def test_report_saved_run_replays_the_run_panels(tmp_path: Path) -> None:
    # REP: a saved run reopens with the Run screen's layout (Miller rows + tree),
    # not a flat count, and never jumps to the Run tab.
    from comparo.core import archive
    from comparo.core.assertions import AssertionResult

    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    loaded = load_project(root)
    ok = AssertionResult("status", "equals", True, "error", "200 == 200", "status")
    record = _tui_run_record(
        loaded,
        [
            (_tui_execution(loaded, "request.get-json", {}), [ok]),
            (_tui_execution(loaded, "request.echo-anything", {}), [ok]),
        ],
        record_id="run09",
        created="2026-05-05",
    )
    archive.save_record(app_archive_dir(loaded), record)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("5")  # Report tab
            await pilot.pause()
            report = app.query_one(ReportView)
            assert report.query_one("#report-table", DataTable).row_count == 1
            await pilot.press("enter")
            await pilot.pause()
            # A run has no candidate → replays as the Run screen (rows + tree), in-tab.
            assert report._current_view() == "report-run"
            assert app.query_one(NavBar).active == "report"
            assert report.query_one("#report-req-table", DataTable).row_count == 2

    asyncio.run(go())


def test_report_list_shows_each_rows_kind_and_filters_by_it(tmp_path: Path) -> None:
    # REQ-1/2: every saved row shows its own kind glyph (execution/diff/run), and
    # `/` filters the list by kind (as well as id / envs / gate).
    from comparo.core import archive
    from comparo.core.assertions import AssertionResult

    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    loaded = load_project(root)
    diff_rec = _tui_diff_record(loaded, record_id="dif01", created="2026-02-02")
    ok = AssertionResult("status", "equals", True, "error", "200 == 200", "status")
    run_rec = _tui_run_record(
        loaded,
        [(_tui_execution(loaded, "request.get-json", {}), [ok])],
        record_id="run01",
        created="2026-01-01",
    )
    archive.save_record(app_archive_dir(loaded), diff_rec)
    archive.save_record(app_archive_dir(loaded), run_rec)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()
            report = app.query_one(ReportView)
            table = report.query_one("#report-table", DataTable)
            assert table.row_count == 2
            # each RUN cell carries a leading per-kind glyph — a diamond (◆ execution,
            # ◇ diff, ◇ run); diff and run share the ◇ shape, distinguished by colour.
            glyphs = {table.get_row_at(index)[0].plain[0] for index in range(2)}
            assert glyphs == {"◇"}
            # `/` filters by kind — "run" keeps only the run record (no candidate)
            assert report.apply_filter("run") == 1
            run_only = report._filtered[0]
            assert run_only.candidate is None
            # "diff" keeps only the diff record (has a candidate, no execution name)
            assert report.apply_filter("diff") == 1
            diff_only = report._filtered[0]
            assert diff_only.candidate is not None
            assert diff_only.execution is None

    asyncio.run(go())


def test_pad_cells_aligns_by_display_width_not_len() -> None:
    # REQ-3: the DIFF BREAKDOWN name column pads by terminal cell width so the
    # same/drift/skip bars line up regardless of name length or wide Unicode.
    from rich.cells import cell_len

    from comparo.tui.render import _pad_cells

    assert cell_len(_pad_cells("Checkout", 14)) == 14
    assert cell_len(_pad_cells("Price quote", 14)) == 14
    assert cell_len(_pad_cells("界面测试", 14)) == 14  # wide (2-cell) glyphs still align
    assert cell_len(_pad_cells("a very very long request name", 14)) == 14  # clipped, still 14


def test_diff_save_key_archives_the_finished_diff(tmp_path: Path) -> None:
    # REQ-5: `s` on the Diff RESULTS writes the archive record + toasts (not auto-saved).
    from comparo.core.archive import list_records
    from comparo.core.compare import CellDiff
    from comparo.tui.app import DiffView

    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    loaded = load_project(root)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            diff = app.query_one(DiffView)
            diff.prime_pair(*[e.metadata.name for e in _environments(loaded)[:2]])
            await pilot.pause()
            # land finished results directly (no network), then archive on `s`
            cell = CellDiff(request, "", [FieldDiff("$.x", State.DRIFT, "exact", "a → b")])
            diff._finish([cell])
            await pilot.pause()
            assert not list_records(app_archive_dir(loaded))  # nothing written until s
            await pilot.press("s")
            await pilot.pause()
            assert len(list_records(app_archive_dir(loaded))) == 1  # archived on s

    asyncio.run(go())


def test_execution_save_key_archives_the_run(tmp_path: Path) -> None:
    # REQ-5: `s` on the Execution RESULTS writes the archive record + toasts.
    from comparo.core.archive import list_records

    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    loaded = load_project(root)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    ok = AssertionResult("status", "equals", True, "error", "200 == 200")
    cell = CellDiff(request, "", [FieldDiff("$.a", State.DRIFT, "exact", '"x" → "y"')])
    outcome = CellOutcome("request.get-json", "", [ok], [ok], cell)
    result = ExecutionResult("execution.smoke", "stable", "canary", True, True, [outcome])
    profile = next(o for o in loaded.objects.values() if isinstance(o, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view.show_result(result, profile, None)
            await pilot.pause()
            assert not list_records(app_archive_dir(loaded))  # not archived until s
            await pilot.press("s")
            await pilot.pause()
            assert len(list_records(app_archive_dir(loaded))) == 1  # archived on s
            assert view._record is not None

    asyncio.run(go())


def test_report_enter_opens_deep_dive_in_place_not_the_diff_tab(tmp_path: Path) -> None:
    # NAV self-containment: Report replays a saved diff in-tab, never the Diff tab (REP-11/12).
    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    _seed_report(root)
    loaded = load_project(root)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("5")  # Report tab
            await pilot.pause()
            report = app.query_one(ReportView)
            assert report.query_one("#report-table", DataTable).row_count == 1
            await pilot.press("enter")
            await pilot.pause()
            # The saved diff replays in the Report tab's own diff sub-view, no tab jump.
            assert report._current_view() == "report-diff"
            assert app.query_one(NavBar).active == "report"
            record = report._analyzed
            assert record is not None
            assert record.requests[0].drift_paths == ["$.headers.x-trace"]
            # the drifted field is named in the read-only drift index
            drift_table = report.query_one("#report-drift-table", DataTable)
            assert drift_table.row_count >= 1
            console = Console()
            with console.capture() as capture:
                console.print(_record_detail(record))
            assert "$.headers.x-trace" in capture.get()
            # esc returns to the saved-report list, still in the Report tab.
            await pilot.press("escape")
            await pilot.pause()
            assert report._current_view() == "report-browse"

    asyncio.run(go())


def test_deep_dive_never_shows_drift_as_a_naked_count() -> None:
    # REP-18: a legacy/foreign record with drift>0 but no recorded paths must
    # still be named as unrecorded, never rendered as a bare integer.
    import dataclasses

    from comparo.tui.render import _breakdown_legend
    from comparo.tui.replay import AssertionSummary
    from comparo.tui.replay import ReplayRecord
    from comparo.tui.replay import RequestBreakdown

    empty = AssertionSummary(0, 0, 0, [])
    legacy = ReplayRecord(
        id="old01",
        created="2026-01-01",
        kind="diff",
        gate="FAIL",
        calls=1,
        same=0,
        drift=4,
        error=0,
        skipped=0,
        baseline="a",
        candidate="b",
        execution=None,
        baseline_assertions=empty,
        candidate_assertions=empty,
        requests=[RequestBreakdown("checkout", 0, 4, 0, "drift", [])],
        cells=[],
    )
    console = Console()
    with console.capture() as capture:
        console.print(_record_detail(legacy))
    detail = capture.get()
    assert "not recorded" in detail  # explicit notice, not a naked number
    legend = _breakdown_legend(legacy)
    assert "not recorded" in legend.plain
    # And when paths ARE present, they are named verbatim.
    named = dataclasses.replace(
        legacy, requests=[RequestBreakdown("checkout", 0, 1, 0, "drift", ["$.total"])]
    )
    with console.capture() as capture:
        console.print(_record_detail(named))
    assert "$.total" in capture.get()


def test_run_save_archives_a_report_visible_in_the_report_tab(tmp_path: Path) -> None:
    # RUN-25: a single-environment run archives an assertions report to <data>/.reports.
    from comparo.core.archive import list_records
    from comparo.core.assertions import AssertionResult

    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    loaded = load_project(root)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            from comparo.core.resolve import select_environment

            ok = AssertionResult("status", "equals", True, "error", "200 == 200", "status")
            bad = AssertionResult("status", "equals", False, "error", "500 != 200", "status")
            record = app.save_run_report(
                select_environment(loaded, "local"),
                [
                    (_tui_execution(loaded, "request.get-json", {}), [ok]),
                    (_tui_execution(loaded, "request.echo-anything", {}), [bad]),
                ],
            )
            assert record is not None
            assert record.invocation.environments.candidate is None  # a run has no candidate
            assert record.summary.gate == "FAIL"  # one check failed
            surfaced = list_records(app_archive_dir(loaded))
            assert any(rec.metadata.id == record.metadata.id for rec in surfaced)

    asyncio.run(go())


def test_report_reload_works_when_the_archive_started_empty(tmp_path: Path) -> None:
    # REP-05 hole: with an empty archive the table is unfocused, so 'r' must be
    # dispatched at the app level, not only from the focused table.
    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    loaded = load_project(root)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()
            report = app.query_one(ReportView)
            assert report.query_one("#report-table", DataTable).row_count == 0
            # A run lands on disk after the (empty) screen opened …
            from comparo.core import archive

            record = _tui_diff_record(loaded, record_id="late01", created="2026-03-03")
            archive.save_record(app_archive_dir(loaded), record)
            await pilot.press("r")  # app-level dispatch, no table focus required
            await pilot.pause()
            assert report.query_one("#report-table", DataTable).row_count == 1

    asyncio.run(go())


def test_report_reload_rereads_the_archive_from_disk(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    shutil.copytree(SAMPLE, root)
    _seed_report(root)
    loaded = load_project(root)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()
            report = app.query_one(ReportView)
            assert report.query_one("#report-table", DataTable).row_count == 1
            # A second run lands on disk while the screen is open …
            from comparo.core import archive

            record = _tui_diff_record(loaded, record_id="seed02", created="2026-01-02")
            archive.save_record(app_archive_dir(loaded), record)
            # … and 'r' surfaces it without leaving the screen.
            await pilot.press("r")
            await pilot.pause()
            assert report.query_one("#report-table", DataTable).row_count == 2

    asyncio.run(go())


def test_q_key_always_quits_never_closes_or_goes_back() -> None:
    # Hard UX rule: `q` ALWAYS quits the app — never "close"/"back" on any screen
    # or modal. Back/close is esc / backspace. Scan every screen's BINDINGS.
    import inspect

    from textual.binding import Binding

    from comparo.tui import app as app_module

    offenders: list[tuple[str, str]] = []
    for name, obj in inspect.getmembers(app_module, inspect.isclass):
        for entry in getattr(obj, "BINDINGS", None) or []:
            if isinstance(entry, Binding):
                key, action = entry.key, entry.action
            elif isinstance(entry, tuple):
                key, action = entry[0], entry[1]
            else:
                continue
            keys = [part.strip() for part in str(key).split(",")]
            if "q" in keys and action not in ("quit", "app.quit"):
                offenders.append((name, action))
    assert not offenders, f"`q` must quit, never: {offenders}"


def test_run_detail_focus_carves_sections_and_adds_raw() -> None:
    # RUN-27: the per-cell detail is switchable — each focus shows one facet, and
    # "raw" dumps the unparsed request line + response body verbatim.
    from textual.widgets import Tree

    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.matrix import expand
    from comparo.tui.render import _build_report_tree

    loaded = load_project(SAMPLE)
    request = next(o for o in loaded.objects.values() if isinstance(o, Request))
    env = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    cell = expand(loaded, request)[0]
    resp = HttpResponse(200, [("Content-Type", "application/json")], b'{"ok": true}', 9.0)
    execution = Execution(
        request=request, environment=env, cell_key=cell.key, response=resp, error=None
    )

    def sections(focus: str) -> list[str]:
        tree: Tree[object] = Tree("x")
        _build_report_tree(tree, loaded, env, request, cell, execution, "ok", [], str, focus=focus)
        labels: list[str] = []

        def rec(node: object) -> None:
            label = node.label  # type: ignore[attr-defined]
            labels.append(label.plain if hasattr(label, "plain") else str(label))
            for child in node.children:  # type: ignore[attr-defined]
                rec(child)

        rec(tree.root)
        return [name for name in labels if name in ("REQUEST", "RESPONSE", "RAW REQUEST", "body")]

    assert "REQUEST" in sections("request")
    assert "RESPONSE" not in sections("request")
    assert "RESPONSE" in sections("response")
    assert "REQUEST" not in sections("response")
    assert "RAW REQUEST" in sections("raw")
    # headers mode carries the header nodes but never the bodies
    assert "body" not in sections("headers")


def test_outbound_diff_flags_url_drift_and_never_leaks_a_secret() -> None:
    # DIFF-27: the outbound-request diff surfaces env-config differences (base URL,
    # auth) — and a masked secret compares equal, so it never becomes a false diff.
    import io

    from rich.console import Console

    from comparo.core.resolve import ResolvedRequest
    from comparo.tui.render import _outbound_diff_view

    loaded = load_project(SAMPLE)
    env_a, env_b = _environments(loaded)[:2]

    def render(view: object) -> str:
        console = Console(record=True, width=100, file=io.StringIO())
        console.print(view)
        return console.export_text()

    # Same masked auth header on both, different base URLs.
    header: list[tuple[str, object]] = [("Authorization", "Bearer ••••••")]
    a = ResolvedRequest("GET", "http://localhost:8080/x", header, {}, None, [])
    b = ResolvedRequest("GET", "https://prod.example/x", header, {}, None, [])
    text = render(_outbound_diff_view(a, b, env_a, env_b))
    assert "differs across environments" in text
    assert "localhost:8080" in text
    assert "prod.example" in text
    assert "SECRETVALUE" not in text  # nothing to leak — the token stayed masked

    # Byte-identical outbound → the reassuring "identical" verdict, no warning.
    same = render(_outbound_diff_view(a, a, env_a, env_b))
    assert "identical on both sides" in same
    assert "differs across environments" not in same


def test_nav_tabs_are_reachable_without_shift_on_azerty() -> None:
    # French AZERTY (the user's layout) needs shift for digits, so every nav tab
    # also binds its unshifted character. Tab 6 (Settings) is `-` on a PC keyboard
    # but `§` (section_sign) on an Apple keyboard — bind both so it works on a Mac.
    from textual.binding import Binding

    from comparo.tui.app import ComparoApp

    nav_keys: dict[str, list[str]] = {}
    for entry in ComparoApp.BINDINGS:
        if isinstance(entry, Binding) and entry.action.startswith("screen("):
            nav_keys[entry.action] = entry.key.split(",")

    assert nav_keys, "no nav-screen bindings found"
    settings = nav_keys["screen('settings')"]
    assert "6" in settings  # the shifted digit
    assert "section_sign" in settings  # § — reachable without shift on a MacBook
    # every tab must offer at least one no-shift (non-digit) alternative
    for action, keys in nav_keys.items():
        assert any(not key.isdigit() for key in keys), f"{action} needs a no-shift key"


def test_no_footer_hint_labels_q_as_anything_but_quit() -> None:
    # The hard rule: q ALWAYS quits the app — a footer must never bundle q into a
    # back/close/cancel hint (a user trusting it would lose an unsaved run).
    from comparo.tui import app as app_module

    offenders: list[tuple[str, str, str]] = []
    for name in dir(app_module):
        if not name.endswith("_KEYS"):
            continue
        value = getattr(app_module, name)
        if not isinstance(value, tuple):
            continue
        for entry in value:
            if not (isinstance(entry, tuple) and len(entry) == 2):
                continue
            key, label = entry
            if not (isinstance(key, str) and isinstance(label, str)):
                continue
            tokens = key.replace("/", " ").split()
            if "q" in tokens and "quit" not in label.lower():
                offenders.append((name, key, label))
    assert not offenders, f"q mislabeled as non-quit in footers: {offenders}"


def test_running_counter_reflects_real_verdicts_from_engine_ticks() -> None:
    # H-1: the live counter must reflect the real per-cell verdict the engine
    # reports (ExecutionProgress.ok), not stick at "0 ✓ 0 ✗". The regression was
    # that the app wrote a verdict-less "●" the ✓/✗ tally could never count, and
    # an errored (ok=False) cell was painted green. Drive it end-to-end.
    from rich.console import Console

    from comparo.core.execution import ExecutionProgress

    loaded = load_project(SAMPLE)
    profile = next(obj for obj in loaded.objects.values() if isinstance(obj, ExecutionProfile))

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("4")  # Execution tab
            await pilot.pause()
            view = app.query_one(ExecutionView)
            view._profile = profile
            view._show("exec-running")
            # Two clean passes, one failure (ok=False), then one cell still in flight.
            view.update_progress(
                ExecutionProgress("request.get-json", "", 0, 4, done=True, ok=True)
            )
            view.update_progress(
                ExecutionProgress("request.post-json", "", 1, 4, done=True, ok=True)
            )
            view.update_progress(
                ExecutionProgress("request.echo-anything", "", 2, 4, done=True, ok=False)
            )
            view.update_progress(ExecutionProgress("request.get-json", "", 3, 4, done=False))
            await pilot.pause()

            # The app recorded real verdict glyphs, not a verdict-less "●".
            assert view._plan_glyphs.count("✓") == 2
            assert view._plan_glyphs.count("✗") == 1
            assert "●" not in view._plan_glyphs

            # Those real glyphs drive the live counter (rendered from the same state).
            from comparo.tui.render import _running_body

            body = _running_body(
                "release-gate",
                view._done,
                view._total,
                view._current,
                view._recent,
                view._plan_glyphs,
            )
            console = Console(width=120)
            with console.capture() as capture:
                console.print(body)
            out = capture.get()
            assert "2 ✓" in out
            assert "1 ✗" in out
            assert "0 ✗" not in out  # the stuck-at-zero signature is gone

    asyncio.run(go())


def test_diff_can_rerun_from_results_after_picking_a_new_pair() -> None:
    # H17: after a diff finishes and the user picks a new env (which clears _done
    # while staying on RESULTS), pressing x must re-run — not falsely refuse.
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            diff = app.query_one(DiffView)
            diff.prime_pair(*[e.metadata.name for e in _environments(loaded)[:2]])
            diff.query_one("#diff-mode", ContentSwitcher).current = "diff-results"
            diff._done = False  # what _set_env leaves behind after picking a new pair
            diff._run_id = "old99"
            diff.execute()  # x from RESULTS with a valid pair must re-run
            await pilot.pause()
            # a re-run started: it moved to the RUNNING panel and minted a new id
            assert diff.query_one("#diff-mode", ContentSwitcher).current == "diff-running"
            assert diff._run_id != "old99"

    asyncio.run(go())


def test_a_diff_finishing_on_another_tab_does_not_steal_focus() -> None:
    # H15: if a diff completes while the user has navigated away, _finish must not
    # yank focus to the hidden drift table (their visible tab would go dead).
    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("3")  # Diff
            await pilot.pause()
            diff = app.query_one(DiffView)
            diff.prime_pair(*[e.metadata.name for e in _environments(loaded)[:2]])
            diff.execute()
            await pilot.pause()
            await pilot.press("1")  # navigate to Explorer while the diff runs
            await pilot.pause()
            assert getattr(app.focused, "id", None) == "tree"
            diff._finish([])  # the diff completes in the background
            await pilot.pause()
            # focus stays on the Explorer tree — the background diff didn't steal it
            assert getattr(app.focused, "id", None) == "tree"

    asyncio.run(go())


def test_run_env_is_pinned_and_survives_a_default_env_change() -> None:
    # H14: a run saves/labels against the env it executed on, not whatever the
    # default happens to be later. Pinning _run_env decouples the two.

    from comparo.tui.render import _app_env

    loaded = load_project(SAMPLE)

    async def go() -> None:
        app = ComparoApp(loaded)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            await pilot.press("2")  # Run tab
            await pilot.pause()
            run = app.query_one(RunView)
            envs = _environments(loaded)
            assert len(envs) >= 2
            run._run_env = envs[0]  # what execute() pins at launch
            app.set_default_environment(envs[1])  # user changes the default later
            await pilot.pause()
            assert run._run_env is envs[0]  # the run's env is unchanged...
            assert _app_env(run) is envs[1]  # ...even though the default moved

    asyncio.run(go())
