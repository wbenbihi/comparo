"""The saved-report archive round-trips a record and projects a per-request breakdown."""

import json
from pathlib import Path

from comparo.core.archive import list_records
from comparo.core.archive import load_record
from comparo.core.archive import save_record
from comparo.core.assertions import AssertionResult
from comparo.core.compare import CellDiff
from comparo.core.compare import compare_cell
from comparo.core.execute import Execution
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.report_builder import record_from_diff
from comparo.core.report_builder import record_from_run
from comparo.core.report_record import ReportRecord
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import select_environment
from comparo.tui.replay import project

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def _bits() -> tuple[LoadedProject, Environment, Request]:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    return loaded, env, request


def _execution(request: Request, env: Environment, body: dict[str, object]) -> Execution:
    response = HttpResponse(200, [], json.dumps(body).encode(), 5.0)
    resolved = ResolvedRequest("GET", "http://localhost:8080/json", [], {}, None, [])
    return Execution(request, env, "", response, resolved=resolved)


def _diff(cells: list[CellDiff], env: Environment, record_id: str = "r1") -> ReportRecord:
    return record_from_diff(
        env,
        env,
        cells,
        record_id=record_id,
        created="now",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )


def test_breakdown_names_each_drifted_field() -> None:
    loaded, env, request = _bits()
    cell = compare_cell(
        loaded,
        _execution(request, env, {"token": "a", "expiry": "x", "stable": "s"}),
        _execution(request, env, {"token": "b", "expiry": "y", "stable": "s"}),
    )
    replay = project(_diff([cell], env))
    breakdown = replay.requests[0]
    assert breakdown.verdict == "drift"
    assert set(breakdown.drift_paths) == {"$.token", "$.expiry"}


def test_breakdown_dedupes_drift_paths_across_cells() -> None:
    loaded, env, request = _bits()
    cells = [
        compare_cell(
            loaded,
            _execution(request, env, {"token": "a"}),
            _execution(request, env, {"token": "b"}),
        )
        for _ in range(2)
    ]
    replay = project(_diff(cells, env))
    breakdown = replay.requests[0]
    assert breakdown.drift == 2
    assert breakdown.drift_paths == ["$.token"]  # deduped across the two cells


def test_a_record_survives_a_save_load_round_trip(tmp_path: Path) -> None:
    loaded, env, request = _bits()
    cell = compare_cell(
        loaded, _execution(request, env, {"token": "a"}), _execution(request, env, {"token": "b"})
    )
    record = _diff([cell], env, record_id="r3")
    save_record(tmp_path, record)
    (reloaded,) = list_records(tmp_path)
    assert reloaded == record  # msgspec round-trips the whole record
    assert load_record(tmp_path / "r3.json").cells[0].comparison is not None
    assert project(reloaded).requests[0].drift_paths == ["$.token"]


def test_run_record_is_an_assertions_only_report() -> None:
    _, env, request = _bits()
    ok = AssertionResult("status", "equals", True, "error", "200 == 200", "status")
    bad = AssertionResult("status", "equals", False, "error", "500 != 200", "status")
    cells = [
        (_execution(request, env, {"a": 1}), [ok]),
        (_execution(request, env, {"a": 1}), [bad]),
    ]
    record = record_from_run(
        env,
        cells,
        record_id="run01",
        created="now",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )
    assert record.kind == "run"
    assert record.invocation.environments.candidate is None  # a run has no candidate
    assert record.summary.gate == "FAIL"  # the second cell's assertion failed
    replay = project(record)
    assert replay.baseline_assertions.passed == 1
    assert replay.baseline_assertions.failed == 1


def test_run_replay_scopes_checks_to_each_request_not_the_whole_record() -> None:
    # Regression: the Run detail's "checks" section must show only the SELECTED
    # request's assertions. The record-wide roll-up leaked every request's checks
    # into every request's detail (hundreds of "status equals 200").
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    req_a = loaded.objects["request.get-json"]
    req_b = loaded.objects["request.echo-anything"]
    assert isinstance(req_a, Request)
    assert isinstance(req_b, Request)
    ok = AssertionResult("status", "equals", True, "error", "200 == 200", "status")
    latency = AssertionResult("latency", "lte", True, "error", "5 < 100", "latency")
    cells = [
        (_execution(req_a, env, {"a": 1}), [ok]),
        (_execution(req_b, env, {"b": 2}), [ok, latency]),
    ]
    record = record_from_run(
        env,
        cells,
        record_id="run",
        created="now",
        tool="c",
        project=None,
        concurrency=1,
        redact=str,
    )
    replay = project(record)
    by_request = {cell.request: cell for cell in replay.cells}
    # Each cell carries only its own request's checks, not the record-wide three.
    assert [line.label for line in by_request["request.get-json"].assertions] == ["status equals"]
    assert sorted(line.label for line in by_request["request.echo-anything"].assertions) == [
        "latency lte",
        "status equals",
    ]
    # The record-wide roll-up still holds all three (it feeds the summary counts only).
    assert len(replay.baseline_assertions.lines) == 3


def test_no_drift_leaves_paths_empty() -> None:
    loaded, env, request = _bits()
    cell = compare_cell(
        loaded, _execution(request, env, {"stable": "s"}), _execution(request, env, {"stable": "s"})
    )
    replay = project(_diff([cell], env))
    assert replay.requests[0].drift_paths == []
    assert replay.requests[0].verdict == "pass"
