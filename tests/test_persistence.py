"""Non-regression tests for archive robustness and redaction ordering.

The Report tab replays whatever ``.reports/`` holds, including files a crash
truncated, another version wrote, or a user hand-edited — so the archive reader
must never take the TUI down with it. And the redactor's longest-first masking
is a security property: losing it would leak the tail of any secret that
contains a shorter declared secret.
"""

import dataclasses
import json
from pathlib import Path

from comparo.core.archive import ARCHIVE_VERSION
from comparo.core.archive import AssertionLine
from comparo.core.archive import AssertionSummary
from comparo.core.archive import CellRecord
from comparo.core.archive import ReportRecord
from comparo.core.archive import RequestBreakdown
from comparo.core.archive import list_records
from comparo.core.archive import load_record
from comparo.core.archive import prune
from comparo.core.archive import save_record
from comparo.core.redaction import MASK
from comparo.core.redaction import Redactor


def _record(run_id: str, created: str) -> ReportRecord:
    return ReportRecord(
        id=run_id,
        created=created,
        execution="execution.gate",
        baseline="Stable",
        candidate="Canary",
        gate="FAIL",
        calls=2,
        same=1,
        drift=1,
        error=0,
        skipped=3,
        baseline_assertions=AssertionSummary(
            2, 1, 0, [AssertionLine("status == 200", "fail", "x")]
        ),
        candidate_assertions=AssertionSummary(3, 0, 0, []),
        requests=[RequestBreakdown("users", 4, 1, 3, "drift", ["$.total"])],
        cells=[
            CellRecord(
                request="users",
                variant="locale=fr-FR",
                method="GET",
                path="/users",
                drift_paths=["$.total"],
                skip_paths=["$.ts"],
                baseline_body={"total": 1},
                candidate_body={"total": 2},
                status=200,
                latency_ms=12,
                size_bytes=64,
                response_headers={"content-type": "application/json"},
            )
        ],
    )


def test_a_full_record_survives_a_save_load_round_trip(tmp_path: Path) -> None:
    # The saved report is the *only* input to a replay — every field must
    # survive the trip, or the Report tab silently shows less than the run saw.
    record = _record("r1", "2026-07-18T10:00:00Z")
    save_record(tmp_path, record)
    loaded = load_record(tmp_path / "r1.json")
    assert dataclasses.asdict(loaded) == dataclasses.asdict(record)


def test_list_records_skips_corrupt_files_instead_of_raising(tmp_path: Path) -> None:
    save_record(tmp_path, _record("good", "2026-07-18T10:00:00Z"))
    (tmp_path / "truncated.json").write_text('{"id": "t", "created": "20', encoding="utf-8")
    (tmp_path / "empty.json").write_text("", encoding="utf-8")
    (tmp_path / "wrong-shape.json").write_text('["not", "a", "record"]', encoding="utf-8")
    records = list_records(tmp_path)
    assert [record.id for record in records] == ["good"]


def test_list_records_orders_newest_first(tmp_path: Path) -> None:
    save_record(tmp_path, _record("older", "2026-07-17T09:00:00Z"))
    save_record(tmp_path, _record("newer", "2026-07-18T09:00:00Z"))
    assert [record.id for record in list_records(tmp_path)] == ["newer", "older"]


def test_a_record_from_a_future_schema_still_loads(tmp_path: Path) -> None:
    # Forward tolerance: a record written by a *newer* comparo with extra keys
    # must load (extra keys ignored), so upgrading and downgrading never bricks
    # the Report tab.
    save_record(tmp_path, _record("r1", "2026-07-18T10:00:00Z"))
    raw = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))
    raw["some_future_field"] = {"nested": True}
    raw["cells"][0]["another_future_field"] = 7
    (tmp_path / "r1.json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_record(tmp_path / "r1.json")
    assert loaded.id == "r1"
    assert loaded.cells[0].request == "users"


def test_a_saved_record_stamps_and_round_trips_its_version(tmp_path: Path) -> None:
    # The archive stamps a schema version so a future format change is
    # detectable; a freshly written record carries the current ARCHIVE_VERSION
    # on disk and reads it back unchanged.
    record = _record("r1", "2026-07-18T10:00:00Z")
    assert record.version == ARCHIVE_VERSION
    save_record(tmp_path, record)
    raw = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))
    assert raw["version"] == ARCHIVE_VERSION
    assert load_record(tmp_path / "r1.json").version == ARCHIVE_VERSION


def test_a_legacy_record_without_a_version_loads_as_version_zero(tmp_path: Path) -> None:
    # Backward tolerance: a file written before the version field existed lacks
    # the key and must load as version 0, with every other field still intact.
    save_record(tmp_path, _record("r1", "2026-07-18T10:00:00Z"))
    raw = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))
    del raw["version"]
    (tmp_path / "r1.json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_record(tmp_path / "r1.json")
    assert loaded.version == 0
    assert loaded.id == "r1"
    assert loaded.cells[0].request == "users"


def test_prune_keeps_only_the_newest_records(tmp_path: Path) -> None:
    # Retention: .reports/ is bounded by pruning to the newest `keep` records by
    # created timestamp; older files are unlinked, newer ones stay.
    save_record(tmp_path, _record("r1", "2026-07-15T09:00:00Z"))
    save_record(tmp_path, _record("r2", "2026-07-16T09:00:00Z"))
    save_record(tmp_path, _record("r3", "2026-07-17T09:00:00Z"))
    save_record(tmp_path, _record("r4", "2026-07-18T09:00:00Z"))
    prune(tmp_path, keep=2)
    assert {record.id for record in list_records(tmp_path)} == {"r3", "r4"}
    assert not (tmp_path / "r1.json").exists()
    assert not (tmp_path / "r2.json").exists()


def test_save_record_prunes_when_keep_is_passed(tmp_path: Path) -> None:
    # The optional `keep` on save_record wires in retention: after writing, only
    # the newest `keep` records remain. Omitting it leaves everything in place.
    save_record(tmp_path, _record("r1", "2026-07-15T09:00:00Z"))
    save_record(tmp_path, _record("r2", "2026-07-16T09:00:00Z"))
    save_record(tmp_path, _record("r3", "2026-07-17T09:00:00Z"), keep=2)
    assert {record.id for record in list_records(tmp_path)} == {"r2", "r3"}
    assert not (tmp_path / "r1.json").exists()


def test_redaction_masks_the_longer_secret_whole_when_one_contains_another() -> None:
    # Longest-first is load-bearing: masking "abc" before "abcdef" would leave
    # "def" — the tail of the longer secret — in the output.
    redactor = Redactor(tuple(sorted({"abc", "abcdef"}, key=len, reverse=True)))
    assert redactor.text("token=abcdef suffix=abc") == f"token={MASK} suffix={MASK}"
    assert "def" not in redactor.text("abcdef")


def test_redactor_for_project_orders_values_longest_first() -> None:
    # Pins the constructor's ordering itself, so a refactor that drops the
    # sort key fails here rather than in a leaked report.
    values = ("longest-secret-value", "short")
    assert Redactor(values).values == tuple(sorted(values, key=len, reverse=True))


def test_manifest_load_separates_root_from_data_dir_and_archive_does_not_nest(
    tmp_path: Path,
) -> None:
    # A manifest with a non-'.' data dir must give root=project dir, data_dir=<data>,
    # and the archive must land at <data>/.reports, not <data>/<data>/.reports.
    from comparo.core.archive import archive_dir
    from comparo.core.loader import load_project

    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\nmetadata: {name: P, id: project.p}\n"
        "spec: {data: .comparo}\n",
        encoding="utf-8",
    )
    data = tmp_path / ".comparo"
    data.mkdir()
    (data / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\nmetadata: {name: E, id: environment.e}\n"
        "spec: {baseUrl: 'http://h'}\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path / "comparo.yaml")
    assert loaded.root == tmp_path
    assert loaded.data_dir == data
    resolved = archive_dir(loaded.root, loaded.project.spec.data, None)  # type: ignore[union-attr]
    assert resolved == data / ".reports"
