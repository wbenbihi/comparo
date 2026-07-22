"""Tests for the execution planner — assert both envs, diff, and gate."""

import asyncio
import json
from pathlib import Path

import msgspec

from comparo.core.assertions import AssertionResult
from comparo.core.assertions import AssertRef
from comparo.core.execute import Execution
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionResult
from comparo.core.execution import run_execution
from comparo.core.http import HttpResponse
from comparo.core.http import TimeoutBudget
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.models import ExecutionProfileSpec
from comparo.core.models import Meta
from comparo.core.models import Request
from comparo.core.report_builder import record_from_execution
from comparo.core.report_record import ReportRecord
from comparo.core.resolve import ResolvedRequest

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"
_PROFILE = ExecutionProfile(
    api_version="comparo/v1", metadata=Meta(name="Run", id="exec.run"), spec=ExecutionProfileSpec()
)


def _probe_execution() -> Execution:
    loaded = load_project(SAMPLE)
    request = next(o for o in loaded.objects.values() if isinstance(o, Request))
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    response = HttpResponse(200, [], b"{}", 5.0)
    resolved = ResolvedRequest("GET", "http://x/probe", [], {}, None, [])
    return Execution(request, environment, "", response, resolved=resolved)


def _exec_record(outcome: CellOutcome, redact: object = str) -> ReportRecord:
    result = ExecutionResult("exec.run", "Base", "Cand", True, True, [outcome])
    return record_from_execution(
        _PROFILE,
        result,
        record_id="r",
        created="t",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=redact,  # type: ignore[arg-type]
    )


_ENV = """apiVersion: comparo/v1
kind: Environment
metadata:
  name: {name}
  id: environment.{id}
spec:
  baseUrl: https://{id}.test
"""

_EXEC = """apiVersion: comparo/v1
kind: ExecutionProfile
metadata:
  name: Run
  id: exec.run
spec:
  environments:
    baseline: environment.base
    candidate: environment.cand
  check:
    assertions: true
    diff: true
"""


class _EnvEchoClient:
    """Returns a body that differs by which environment (host) is called."""

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        env = "base" if "base.test" in request.url else "cand"
        body = json.dumps({"env": env, "shared": "x"}).encode()
        return HttpResponse(200, [("content-type", "application/json")], body, 5.0)

    async def aclose(self) -> None:
        return None


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def _project(root: Path, *, status: int = 200, matrix: bool = False) -> None:
    _write(root, "base.yaml", _ENV.format(name="Base", id="base"))
    _write(root, "cand.yaml", _ENV.format(name="Cand", id="cand"))
    _write(root, "exec.yaml", _EXEC)
    probe = (
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Probe\n  id: request.probe\n  tags:\n    - smoke\n"
        "spec:\n"
    )
    if matrix:
        probe += "  matrix:\n    - $use: matrix.tiers\n"
    probe += f"  request:\n    method: GET\n    endpoint: /get\n  response:\n    status: {status}\n"
    _write(root, "probe.yaml", probe)
    if matrix:
        _write(
            root,
            "tiers.yaml",
            "apiVersion: comparo/v1\nkind: Matrix\n"
            "metadata:\n  name: Tiers\n  id: matrix.tiers\n"
            "spec:\n  target: request.query\n  values:\n"
            "    - tier: free\n    - tier: pro\n",
        )


def _run(root: Path) -> ExecutionResult:
    loaded = load_project(root)
    profile = loaded.objects["exec.run"]
    assert isinstance(profile, ExecutionProfile)
    return asyncio.run(run_execution(loaded, profile, _EnvEchoClient()))


def test_empty_execution_fails_closed() -> None:
    # An execution that verified nothing must never report a green gate.
    result = ExecutionResult("exec.x", "Base", "Cand", True, True, outcomes=[])
    assert not result.passed
    assert result.drift == 0
    assert result.errors == 0


def test_a_request_whose_matrix_expands_to_zero_cells_fails_closed(tmp_path: Path) -> None:
    # A selected request with an empty matrix verified nothing → error → gate FAIL.
    _project(tmp_path, matrix=True)
    tiers = tmp_path / "tiers.yaml"
    tiers.write_text(
        "apiVersion: comparo/v1\nkind: Matrix\n"
        "metadata:\n  name: Tiers\n  id: matrix.tiers\n"
        "spec:\n  target: request.query\n  values: []\n",  # empty matrix
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.outcomes  # the request is recorded, not silently dropped
    assert result.errors >= 1
    assert not result.passed


def test_execution_reports_live_progress(tmp_path: Path) -> None:
    # EXE-04: run_execution emits three phases per cell — a queued seed tick
    # (started=False) for the whole plan up front, then a start (started=True,
    # done=False) and a done (done=True) tick per cell — so a UI can render
    # progress over the plan as a table. The total is known from the first tick.
    from comparo.core.execution import ExecutionProgress

    _project(tmp_path, matrix=True)  # 2 matrix cells (tier free / pro)
    loaded = load_project(tmp_path)
    profile = loaded.objects["exec.run"]
    assert isinstance(profile, ExecutionProfile)
    events: list[ExecutionProgress] = []
    asyncio.run(run_execution(loaded, profile, _EnvEchoClient(), on_progress=events.append))
    assert all(e.total == 2 for e in events)  # total known from the first tick
    queued = [e for e in events if not e.started]
    started = [e for e in events if e.started and not e.done]
    finished = [e for e in events if e.done]
    assert sorted(e.index for e in queued) == [0, 1]  # a queued seed per plan cell
    assert sorted(e.index for e in started) == [0, 1]  # each cell goes in flight
    assert sorted(e.index for e in finished) == [0, 1]  # each cell finishes
    # The queued seed ticks precede any start/done tick (the whole plan is seeded first).
    assert events[0].started is False
    assert events[1].started is False
    assert {e.request_id for e in events} == {"request.probe"}


def test_execution_asserts_both_envs_and_diffs(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _run(tmp_path)
    assert result.baseline == "Base"
    assert result.candidate == "Cand"
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    # the status==200 sugar holds on both environments
    assert all(a.ok for a in outcome.baseline_assertions)
    assert all(a.ok for a in outcome.candidate_assertions)
    # $.env differs between the two -> drift -> gate fails
    assert outcome.diff is not None
    assert outcome.diff.drifted
    assert not result.passed
    assert result.drift == 1
    # Both executions are threaded onto the outcome so a report can serialize each
    # side's request+response even when no diff is computed.
    assert outcome.baseline is not None
    assert outcome.candidate is not None
    assert outcome.baseline.response is not None
    assert outcome.candidate.response is not None


def test_execution_assertion_failure_fails_the_gate(tmp_path: Path) -> None:
    _project(tmp_path, status=201)  # server answers 200
    result = _run(tmp_path)
    outcome = result.outcomes[0]
    assert not all(a.ok for a in outcome.baseline_assertions)
    assert not result.passed


def test_execution_record_gate_matches_the_execution_gate(tmp_path: Path) -> None:
    # M-b: comparo exec --report must produce an artifact whose pass/fail is the
    # execution gate, not a diff-only gate.
    _project(tmp_path)
    loaded = load_project(tmp_path)
    profile = loaded.objects["exec.run"]
    assert isinstance(profile, ExecutionProfile)
    result = asyncio.run(run_execution(loaded, profile, _EnvEchoClient()))
    record = record_from_execution(
        profile,
        result,
        record_id="r",
        created="t",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )
    assert (record.summary.gate == "PASS") == result.passed
    assert len(record.cells) == len(result.outcomes)


def test_execution_record_flags_an_assertion_only_failure() -> None:
    # A cell that failed only its assertions (no drift) is still a failure — the
    # record must not show a green gate, and the cell verdict is "fail".
    failed = AssertionResult("status", "equals", False, "error", "200 == 201", "status == 201")
    warn = AssertionResult("latency", "lte", False, "warn", "slow", "latency <= 1ms")
    outcome = CellOutcome(
        "request.probe", "", [failed, warn], [], diff=None, baseline=_probe_execution()
    )
    record = _exec_record(outcome)
    assert record.summary.gate == "FAIL"
    assert record.summary.assertions is not None
    assert record.summary.assertions.failed == 1  # the warn does not count as a failure
    cell = record.cells[0]
    assert cell.verdict == "fail"


def test_execution_record_reads_error_when_errors_are_the_only_failure() -> None:
    # A dead side's assertions auto-fail with "no response" — never judged, so
    # they must not drag an errors-only execution to FAIL.
    live = _probe_execution()
    dead = Execution(live.request, live.environment, "", None, "ConnectError: boom", resolved=None)
    never_judged = AssertionResult(
        "status", "equals", False, "error", "no response", expected=200, actual=None
    )
    outcome = CellOutcome(
        "request.probe",
        "",
        [],
        [never_judged],
        diff=None,
        error="candidate: ConnectError: boom",
        baseline=live,
        candidate=dead,
    )
    record = _exec_record(outcome)
    assert record.summary.gate == "ERROR"
    assert record.cells[0].verdict == "error"


def test_execution_record_fail_outranks_an_errored_cell() -> None:
    live = _probe_execution()
    dead = Execution(live.request, live.environment, "", None, "ConnectError: boom", resolved=None)
    broke = AssertionResult(
        "status", "equals", False, "error", "500 != 200", expected=200, actual=500
    )
    judged = CellOutcome("request.probe", "", [broke], [], diff=None, baseline=live)
    errored = CellOutcome(
        "request.probe",
        "b",
        [],
        [],
        diff=None,
        error="candidate: ConnectError: boom",
        baseline=live,
        candidate=dead,
    )
    result = ExecutionResult("exec.run", "Base", "Cand", True, True, [judged, errored])
    record = record_from_execution(
        _PROFILE,
        result,
        record_id="r",
        created="t",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )
    assert record.summary.gate == "FAIL"  # the judged broken rule outranks the errored cell


def test_execution_record_masks_a_secret_in_an_assertion_detail() -> None:
    # A server can echo a secret into a failed assertion's detail/value; the
    # redactor passed to the builder must mask it before it reaches the record.
    failed = AssertionResult(
        "body:$.token",
        "equals",
        False,
        "error",
        "s3cr3t == x",
        "token == x",
        expected="x",
        actual="s3cr3t",
    )
    outcome = CellOutcome("request.probe", "", [failed], [], diff=None, baseline=_probe_execution())
    record = _exec_record(outcome, redact=lambda text: text.replace("s3cr3t", "••••••"))
    assert "s3cr3t" not in msgspec.json.encode(record).decode()


def test_execution_inline_diff_profile_composes(tmp_path: Path) -> None:
    _project(tmp_path)
    # An inline diff profile (a list of one) that ignores the field which differs
    # between the two envs — so the cell no longer drifts.
    probe = (
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Probe\n  id: request.probe\n"
        "spec:\n"
        "  request:\n    method: GET\n    endpoint: /get\n"
        "  response:\n    status: 200\n"
        "    diff:\n"
        "      - default: exact\n"
        "        rules:\n"
        "          - path: $.env\n            mode: ignore\n"
    )
    _write(tmp_path, "probe.yaml", probe)
    result = _run(tmp_path)
    outcome = result.outcomes[0]
    assert outcome.diff is not None
    assert not outcome.diff.drifted  # $.env ignored by the inline profile
    assert result.passed


def test_execution_matrix_scope_limits_cells(tmp_path: Path) -> None:
    _project(tmp_path, matrix=True)
    # scope the tiers matrix down to just `free`
    exec_scoped = _EXEC + ("  matrix:\n    matrix.tiers:\n      include:\n        - tier: free\n")
    _write(tmp_path, "exec.yaml", exec_scoped)
    result = _run(tmp_path)
    assert len(result.outcomes) == 1
    assert "tier=free" in result.outcomes[0].cell_key


def test_execution_tallies_count_cells_not_phantom_sides() -> None:
    # A baseline-only execution must not count a phantom candidate side as
    # not-asserted, and a rule judged on BOTH sides of one cell counts once.
    held = AssertionResult(
        "status",
        "equals",
        True,
        "error",
        "200 == 200",
        "status == 200",
        expected=200,
        actual=200,
        ref=AssertRef("status", "equals", "error", "status == 200", "inline", request="request.r"),
    )
    both_sides = CellOutcome(
        "request.probe",
        "",
        [held],
        [held],
        diff=None,
        baseline=_probe_execution(),
        candidate=_probe_execution(),
    )
    record = _exec_record(both_sides)
    assert record.summary.assertions is not None
    assert record.summary.assertions.not_asserted == 0
    assert record.rules is not None
    (rule,) = record.rules.assertions
    assert rule.outcomes.held == 1  # one CELL, not two sides

    baseline_only = CellOutcome(
        "request.probe", "", [held], [], diff=None, baseline=_probe_execution()
    )
    record = _exec_record(baseline_only)
    assert record.summary.assertions is not None
    assert record.summary.assertions.passed == 1
    assert record.summary.assertions.not_asserted == 0  # the cell WAS asserted


def test_advisory_marks_passed_cells_only() -> None:
    # A failed cell with a broken warn is a failure, never additionally advisory.
    broke = AssertionResult("status", "equals", False, "error", "500", "status == 200")
    warn = AssertionResult("latency", "lte", False, "warn", "slow", "latency <= 1ms")
    failed_cell = CellOutcome(
        "request.probe", "", [broke, warn], [], diff=None, baseline=_probe_execution()
    )
    record = _exec_record(failed_cell)
    assert record.cells[0].verdict == "fail"
    assert record.cells[0].advisory is False
    assert record.summary.cell_verdicts is not None
    assert record.summary.cell_verdicts.advisory == 0

    passed_cell = CellOutcome(
        "request.probe", "", [warn], [], diff=None, baseline=_probe_execution()
    )
    record = _exec_record(passed_cell)
    assert record.cells[0].verdict == "pass"
    assert record.cells[0].advisory is True  # the ~ marker: green with a warning
