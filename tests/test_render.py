"""Unit tests for the pure render helpers that back the TUI's panels."""

from rich.cells import cell_len
from rich.console import Console

from comparo.core.assertions import AssertionResult
from comparo.core.diff import State
from comparo.core.report_record import FieldDiffRecord
from comparo.tui.render import _assert_count_text
from comparo.tui.render import _assert_tally
from comparo.tui.render import _field_from_record
from comparo.tui.render import _fmt_bytes
from comparo.tui.render import _pad_cells
from comparo.tui.render import _relative_age
from comparo.tui.render import _replay_compare_well
from comparo.tui.render import _report_reading_pane
from comparo.tui.render import _req_short
from comparo.tui.render import _run_label
from comparo.tui.replay import AssertionSummary
from comparo.tui.replay import ReplayCell
from comparo.tui.replay import ReplayRecord


def _plain(renderable: object) -> str:
    console = Console(width=120)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_fmt_bytes() -> None:
    assert _fmt_bytes(None) == "—"
    assert _fmt_bytes(840) == "840 B"
    assert _fmt_bytes(1200) == "1.2 kB"


def test_relative_age() -> None:
    assert _relative_age("not-a-date") == ""  # unparseable → empty, never crash
    assert _relative_age("1970-01-01T00:00:00Z").endswith("d")  # long ago → days


def test_pad_cells_pads_and_clips() -> None:
    assert cell_len(_pad_cells("ab", 6)) == 6  # short is padded to the cell width
    clipped = _pad_cells("a-very-long-request-name", 8)
    assert clipped.endswith("…")
    assert cell_len(clipped) == 8  # long is clipped to the width, ellipsis included


def test_req_short_and_run_label() -> None:
    assert _req_short("request.get-json") == "get-json"
    assert _run_label(None) == "run"
    assert _run_label("7f3a") == "run-7f3a"
    assert _run_label("run-7f3a") == "run-7f3a"  # not double-prefixed


def test_assert_tally_and_count_text() -> None:
    results = [
        AssertionResult("status", "equals", True, "error", "ok"),
        AssertionResult("status", "equals", False, "error", "bad"),
        AssertionResult("latency", "lte", False, "warn", "slow"),
    ]
    assert _assert_tally(results) == (1, 1, 1)
    rendered = _plain(_assert_count_text((1, 1, 1)))
    assert "1 ✓" in rendered
    assert "1 ✗" in rendered
    assert "1 !" in rendered


def test_field_from_record_maps_the_real_state_and_mode() -> None:
    # M-6 core: the replay reconstructs a live FieldDiff from the saved record's
    # real state + mode, never a fabricated "exact".
    drift = _field_from_record(
        FieldDiffRecord("$.total", "drift", "shape", baseline=1, candidate=2, rule="$.total")
    )
    assert drift.state is State.DRIFT
    assert drift.mode == "shape"  # the true mode, not "exact"
    assert drift.baseline == 1
    assert drift.candidate == 2
    assert drift.rule == "$.total"
    skip = _field_from_record(FieldDiffRecord("$.ts", "skip", "ignore"))
    assert skip.state is State.SKIP
    assert skip.mode == "ignore"


def _replay_record(cell: ReplayCell) -> ReplayRecord:
    empty = AssertionSummary(0, 0, 0, [])
    return ReplayRecord(
        id="abc123",
        created="1970-01-01T00:00:00Z",
        kind="diff",
        gate="FAIL",
        calls=2,
        same=3,
        drift=1,
        error=0,
        skipped=1,
        baseline="local",
        candidate="prod",
        execution=None,
        baseline_assertions=empty,
        candidate_assertions=empty,
        requests=[],
        cells=[cell],
    )


def test_report_reading_pane_shows_the_gate_and_counts() -> None:
    rendered = _plain(_report_reading_pane(_replay_record(_cell())))
    assert "gate: FAIL" in rendered
    assert "run-abc123" in rendered


def _cell() -> ReplayCell:
    return ReplayCell(
        request="request.checkout",
        variant="",
        method="POST",
        path="http://x/checkout",
        drift_paths=["$.total"],
        skip_paths=["$.ts"],
        baseline_body={"total": 10, "ts": "a"},
        candidate_body={"total": 12, "ts": "b"},
        status=200,
        latency_ms=42,
        size_bytes=68,
        response_headers={"content-type": "application/json"},
        candidate_status=500,
        candidate_latency_ms=88,
        candidate_size_bytes=70,
        baseline_events=None,
        candidate_events=None,
        fields=[
            FieldDiffRecord("$.total", "drift", "exact", baseline=10, candidate=12),
            FieldDiffRecord("$.ts", "skip", "ignore", rule="$.ts"),
        ],
    )


def test_call_ledger_shows_both_sides_and_the_delta() -> None:
    from comparo.tui.render import _call_ledger

    ledger = _call_ledger(_cell())
    assert ledger is not None
    rendered = _plain(ledger)
    assert "200" in rendered  # baseline status
    assert "500" in rendered  # candidate status
    assert "≠" in rendered  # the statuses differ
    assert "42ms" in rendered
    assert "88ms" in rendered
    assert "+46ms" in rendered  # the latency delta


def test_live_call_ledger_reads_both_executions_and_flags_a_slow_candidate() -> None:
    # The LIVE compare panel's ledger must read metrics off the two executions
    # carried on the cell — not only off a saved record — so a latency regression
    # is visible the moment a diff runs (guards against a replay-only regression).
    from pathlib import Path

    from comparo.core.compare import CellDiff
    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.loader import load_project
    from comparo.core.models import Environment
    from comparo.core.models import Request
    from comparo.tui.render import _live_call_ledger

    loaded = load_project(Path(__file__).parent.parent / "examples" / "sample-project")
    request = next(o for o in loaded.objects.values() if isinstance(o, Request))
    env = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    base_resp = HttpResponse(200, [], b'{"ok":true}', 40.0)
    cand_resp = HttpResponse(200, [], b'{"ok":true,"x":1}', 210.0)
    base = Execution(request, env, "", base_resp)
    cand = Execution(request, env, "", cand_resp)
    cell = CellDiff(request, "", [], baseline=base, candidate=cand)
    ledger = _live_call_ledger(cell)
    assert ledger is not None
    rendered = _plain(ledger)
    assert "40ms" in rendered  # baseline latency, off the execution
    assert "210ms" in rendered  # candidate latency, off the execution
    assert "+170ms" in rendered  # the regression delta


def test_call_ledger_is_none_without_a_candidate_side() -> None:
    # A run has no candidate side, so a baseline-vs-candidate ledger has nothing to show.
    from comparo.tui.render import _call_ledger

    run_cell = ReplayCell(
        request="request.r",
        variant="",
        method="GET",
        path="http://x",
        drift_paths=[],
        skip_paths=[],
        baseline_body={"ok": True},
        candidate_body=None,
        status=200,
        latency_ms=10,
        size_bytes=12,
        response_headers={},
        candidate_status=None,
        candidate_latency_ms=None,
        candidate_size_bytes=None,
        baseline_events=None,
        candidate_events=None,
        fields=[],
    )
    assert _call_ledger(run_cell) is None


def test_replay_compare_well_renders_the_real_field_decisions() -> None:
    # The saved-diff body well replays the true per-field modes from the record's
    # FieldDiffRecords instead of fabricating them (M-6). It must not crash and
    # must surface the drifted path.
    rendered = _plain(_replay_compare_well(_replay_record(_cell()), unified=True, redact=str))
    assert "$.total" in rendered


def test_gate_composition_shows_each_factor_and_the_rollup() -> None:
    from comparo.core.execution import CellOutcome
    from comparo.core.execution import ExecutionResult
    from comparo.tui.render import _gate_composition

    failed = AssertionResult("status", "equals", False, "error", "500 != 200", "status")
    outcome = CellOutcome("request.r", "", [failed], [], diff=None)
    result = ExecutionResult("exec.r", "Base", "Cand", True, True, [outcome])
    rendered = _plain(_gate_composition(result))
    assert "baseline assertions" in rendered
    assert "candidate assertions" in rendered
    assert "diff" in rendered
    assert "∧ gate" in rendered
    assert "FAIL" in rendered  # the failed baseline assertion blocks the gate


def test_event_sequence_marks_each_streamed_event() -> None:
    from comparo.tui.render import _event_sequence

    baseline: list[object] = [{"seq": 1}, {"seq": 2}, {"seq": 3}]
    candidate: list[object] = [{"seq": 1}, {"seq": 99}]  # event 2 differs, event 3 missing
    rendered = _plain(_event_sequence(baseline, candidate, str))
    assert "✓" in rendered  # event 1 matches
    assert "✗" in rendered  # event 2 differs / event 3 missing
    assert "—" in rendered  # candidate is shorter → em dash on the missing row


def test_replay_compare_well_renders_a_streamed_event_sequence() -> None:
    import dataclasses

    cell = dataclasses.replace(
        _cell(),
        baseline_events=[{"seq": 1}, {"seq": 2}],
        candidate_events=[{"seq": 1}, {"seq": 9}],
    )
    rendered = _plain(_replay_compare_well(_replay_record(cell), unified=True, redact=str))
    assert "event sequence" in rendered


def test_stream_body_view_renders_a_numbered_event_sequence_not_a_blob() -> None:
    # d-stream: a streamed response diffs its event SEQUENCE (per-event ✓/✗), never
    # one assembled blob — the eye lands on exactly which event diverged.
    from comparo.tui.render import _stream_body_view

    base: list[object] = [{"seq": 1, "price": 98.4}, {"seq": 2, "price": 98.75}, {"seq": 3}]
    cand: list[object] = [{"seq": 1, "price": 98.4}, {"seq": 2, "price": 98.75}, {"seq": 99}]
    out = _plain(_stream_body_view(base, cand, str))
    assert "event sequence" in out
    assert "✓1" in out  # event 1 matches
    assert "✗3" in out  # event 3 differs
    assert "1 of 3 events drift" in out


def test_running_table_shows_the_plan_per_side_with_assert_tally_and_state() -> None:
    # d-running/e-running: the live run renders as a per-plan table — each cell a
    # row, per-side status/latency (+ assert tally for exec), and a STATE column
    # that names the failing dimension. Queued rows show —, in-flight rows show ….
    from comparo.tui.render import _running_table
    from comparo.tui.render import _RunningRow

    rows = [
        _RunningRow(
            "Price quote",
            "pro",
            "GET /get",
            "done",
            baseline_status=200,
            candidate_status=200,
            baseline_ms=39,
            candidate_ms=92,
            base_pass=4,
            base_fail=0,
            cand_pass=4,
            cand_fail=0,
            drift="taxRate",
            failed=True,
        ),
        _RunningRow(
            "Checkout",
            "",
            "POST /post",
            "done",
            baseline_status=200,
            candidate_status=200,
            baseline_ms=58,
            candidate_ms=280,
            base_pass=2,
            base_fail=0,
            cand_pass=1,
            cand_fail=1,
            failed=True,
        ),
        _RunningRow("Price feed", "", "GET /feed", "queued"),
    ]
    out = _plain(
        _running_table("r", 2, 3, rows, base_name="stable", cand_name="canary", exec_mode=True)
    )
    assert "200 39ms 4/0" in out  # per-side status · latency · assert tally
    assert "200 280ms 1/1" in out  # the slow candidate with a failed assertion
    assert "diff ✗" in out  # a drift-only cell names the diff dimension
    assert "assert ✗" in out  # an assert-failing cell names the assert dimension
    assert "queued" in out  # the not-yet-run cell is a visible row


def test_outbound_header_collapses_to_a_summary_and_expands_to_the_full_diff() -> None:
    # The two-layer compare panel: `o` toggles the OUTBOUND layer between a
    # one-line summary and the full request diff, and a masked secret never leaks.
    from pathlib import Path

    from comparo.core.loader import load_project
    from comparo.core.resolve import ResolvedRequest
    from comparo.tui.render import _environments
    from comparo.tui.render import _outbound_header

    loaded = load_project(Path(__file__).parent.parent / "examples" / "sample-project")
    env_a, env_b = (env.metadata.name for env in _environments(loaded)[:2])
    header: list[tuple[str, object]] = [("Authorization", "Bearer ••••••")]
    a = ResolvedRequest("GET", "http://localhost:8080/x", header, {}, None, [])
    b = ResolvedRequest("GET", "https://prod.example/x", header, {}, None, [])

    collapsed = _plain(_outbound_header(a, b, env_a, env_b, expanded=False))
    assert "OUTBOUND" in collapsed
    assert "differs" in collapsed
    assert "to expand" in collapsed
    assert "localhost:8080" not in collapsed  # collapsed stays a summary, no per-field values

    expanded = _plain(_outbound_header(a, b, env_a, env_b, expanded=True))
    assert "localhost:8080" in expanded  # expanded shows the differing url
    assert "prod.example" in expanded
    assert "to collapse" in expanded
    assert "SECRETVALUE" not in expanded  # the masked token stayed masked

    identical = _plain(_outbound_header(a, a, env_a, env_b, expanded=False))
    assert "identical on both sides" in identical


def test_exec_triplet_summarizes_a_cell() -> None:
    from pathlib import Path

    from rich.text import Text

    from comparo.core.compare import CellDiff
    from comparo.core.diff import FieldDiff
    from comparo.core.execution import CellOutcome
    from comparo.core.loader import load_project
    from comparo.core.models import Request
    from comparo.tui.render import _exec_triplet

    loaded = load_project(Path(__file__).parent.parent / "examples" / "sample-project")
    request = next(o for o in loaded.objects.values() if isinstance(o, Request))
    ok = AssertionResult("status", "equals", True, "error", "ok")
    # A drifted cell: verdict ✗, both sides assert, "1 drift" in the diff column.
    diff_cell = CellDiff(request, "", [FieldDiff("$.total", State.DRIFT, "exact")])
    outcome = CellOutcome("request.r", "", [ok], [ok], diff_cell)
    _label, base_assert, cand_assert, diff, verdict = _exec_triplet(outcome, Text("r"))
    rendered = " ".join(_plain(part) for part in (base_assert, cand_assert, diff, verdict))
    assert "1✓" in rendered  # each side's assertion held
    assert "1 drift" in rendered
    assert "✗ FAIL" in rendered  # the drift fails the cell
    assert "(diff)" in rendered  # ...and the verdict names the failing dimension
