"""The saved-run archive records which fields drifted, so Report can deep-dive."""

from pathlib import Path

from comparo.core.archive import list_records
from comparo.core.archive import load_record
from comparo.core.archive import record_from_diff
from comparo.core.archive import save_record
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.loader import load_project
from comparo.core.models import Request

SAMPLE = Path(__file__).parent.parent / "examples" / "canary-project"


def _request() -> Request:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.basic-auth"]
    assert isinstance(request, Request)
    return request


def test_breakdown_names_each_drifted_field() -> None:
    request = _request()
    fields = [
        FieldDiff("$.token", State.DRIFT, "exact", '"a" → "b"'),
        FieldDiff("$.expiry", State.DRIFT, "exact", '"x" → "y"'),
        FieldDiff("$.stable", State.SAME, "exact", ""),
    ]
    cell = CellDiff(request, "", fields)
    record = record_from_diff("Stable", "Canary", [cell], run_id="r1", created="now")
    breakdown = record.requests[0]
    assert breakdown.verdict == "drift"
    assert breakdown.drift_paths == ["$.token", "$.expiry"]


def test_breakdown_dedupes_drift_paths_across_cells() -> None:
    request = _request()
    field = FieldDiff("$.token", State.DRIFT, "exact", '"a" → "b"')
    cells = [CellDiff(request, "case-1", [field]), CellDiff(request, "case-2", [field])]
    record = record_from_diff("Stable", "Canary", cells, run_id="r2", created="now")
    breakdown = record.requests[0]
    assert breakdown.drift == 2
    assert breakdown.drift_paths == ["$.token"]


def test_drift_paths_survive_a_save_load_round_trip(tmp_path: Path) -> None:
    request = _request()
    field = FieldDiff("$.token", State.DRIFT, "exact", '"a" → "b"')
    cell = CellDiff(request, "", [field])
    record = record_from_diff("Stable", "Canary", [cell], run_id="r3", created="now")
    save_record(tmp_path, record)
    (reloaded,) = list_records(tmp_path)
    assert reloaded.requests[0].drift_paths == ["$.token"]
    assert load_record(tmp_path / "r3.json").requests[0].drift_paths == ["$.token"]


def test_record_from_run_is_an_assertions_only_report() -> None:
    from comparo.core.archive import record_from_run
    from comparo.core.assertions import AssertionResult

    ok = AssertionResult("status", "", True, "error", "200 == 200", "status")
    bad = AssertionResult("status", "", False, "error", "500 ≠ 200", "status")
    cells = [("request.alpha", [ok]), ("request.beta", [bad])]
    record = record_from_run("Local", cells, run_id="run01", created="now")
    assert record.baseline == "Local"
    assert record.candidate is None  # a run has no candidate / no diff
    assert record.gate == "FAIL"  # beta's status assertion failed
    assert record.drift == 0
    assert record.skipped == 0
    assert record.baseline_assertions.passed == 1
    assert record.baseline_assertions.failed == 1
    verdicts = {row.request: row.verdict for row in record.requests}
    assert verdicts["alpha"] == "pass"
    assert verdicts["beta"] == "fail"


def test_record_from_run_passes_when_every_check_holds() -> None:
    from comparo.core.archive import record_from_run
    from comparo.core.assertions import AssertionResult

    ok = AssertionResult("status", "", True, "error", "200 == 200", "status")
    record = record_from_run("Local", [("request.a", [ok])], run_id="run02", created="now")
    assert record.gate == "PASS"


def test_no_drift_leaves_paths_empty() -> None:
    request = _request()
    cell = CellDiff(request, "", [FieldDiff("$.stable", State.SAME, "exact", "")])
    record = record_from_diff("Stable", "Canary", [cell], run_id="r4", created="now")
    assert record.requests[0].drift_paths == []
    assert record.requests[0].verdict == "pass"
