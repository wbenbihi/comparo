"""Non-regression tests for archive robustness and redaction ordering.

The Report tab replays whatever ``.reports/`` holds, including files a crash
truncated, another version wrote, or a user hand-edited — so the archive reader
must never take the TUI down with it. And the redactor's longest-first masking
is a security property: losing it would leak the tail of any secret that
contains a shorter declared secret.
"""

import json
from pathlib import Path

from comparo.core.archive import list_records
from comparo.core.archive import load_record
from comparo.core.archive import prune
from comparo.core.archive import save_record
from comparo.core.redaction import MASK
from comparo.core.redaction import Redactor
from comparo.core.report_record import Cell
from comparo.core.report_record import Comparison
from comparo.core.report_record import DiffTally
from comparo.core.report_record import Environments
from comparo.core.report_record import EnvRef
from comparo.core.report_record import FieldDiffRecord
from comparo.core.report_record import Invocation
from comparo.core.report_record import OutboundRequest
from comparo.core.report_record import RecordMeta
from comparo.core.report_record import ReportRecord
from comparo.core.report_record import ResponseRecord
from comparo.core.report_record import Side
from comparo.core.report_record import Sides
from comparo.core.report_record import Summary


def _record(record_id: str, created: str) -> ReportRecord:
    baseline = Side(
        request=OutboundRequest(method="GET", url="/users"),
        response=ResponseRecord(status=200, body={"total": 1}),
    )
    candidate = Side(
        request=OutboundRequest(method="GET", url="/users"),
        response=ResponseRecord(status=200, body={"total": 2}),
    )
    cell = Cell(
        request_id="users",
        name="Users",
        variant="locale=fr-FR",
        verdict="drift",
        sides=Sides(baseline, candidate),
        comparison=Comparison(
            verdict="drift",
            same=1,
            drift=1,
            skipped=1,
            fields=[
                FieldDiffRecord("$.total", "drift", "exact", baseline=1, candidate=2),
                FieldDiffRecord("$.ts", "skip", "ignore"),
            ],
        ),
    )
    return ReportRecord(
        kind="diff",
        metadata=RecordMeta(id=record_id, created=created, tool="comparo 0"),
        invocation=Invocation(
            command="comparo diff",
            environments=Environments(EnvRef("Stable", "http://s"), EnvRef("Canary", "http://c")),
            concurrency=2,
        ),
        summary=Summary(gate="FAIL", calls=2, cells=1, diff=DiffTally(same=1, drift=1, skipped=1)),
        cells=[cell],
    )


def test_a_full_record_survives_a_save_load_round_trip(tmp_path: Path) -> None:
    # The saved report is the *only* input to a replay — every field must
    # survive the trip, or the Report tab silently shows less than the run saw.
    record = _record("r1", "2026-07-18T10:00:00Z")
    save_record(tmp_path, record)
    assert load_record(tmp_path / "r1.json") == record


def test_list_records_skips_corrupt_files_instead_of_raising(tmp_path: Path) -> None:
    save_record(tmp_path, _record("good", "2026-07-18T10:00:00Z"))
    (tmp_path / "truncated.json").write_text('{"kind": "diff", "metad', encoding="utf-8")
    (tmp_path / "empty.json").write_text("", encoding="utf-8")
    (tmp_path / "wrong-shape.json").write_text('["not", "a", "record"]', encoding="utf-8")
    records = list_records(tmp_path)
    assert [record.metadata.id for record in records] == ["good"]


def test_list_records_orders_newest_first(tmp_path: Path) -> None:
    save_record(tmp_path, _record("older", "2026-07-17T09:00:00Z"))
    save_record(tmp_path, _record("newer", "2026-07-18T09:00:00Z"))
    assert [record.metadata.id for record in list_records(tmp_path)] == ["newer", "older"]


def test_a_record_from_a_future_schema_still_loads(tmp_path: Path) -> None:
    # Forward tolerance: a record written by a *newer* comparo with extra keys
    # must load (extra keys ignored), so upgrading and downgrading never bricks
    # the Report tab.
    save_record(tmp_path, _record("r1", "2026-07-18T10:00:00Z"))
    raw = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))
    raw["someFutureField"] = {"nested": True}
    raw["cells"][0]["anotherFutureField"] = 7
    (tmp_path / "r1.json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_record(tmp_path / "r1.json")
    assert loaded.metadata.id == "r1"
    assert loaded.cells[0].request_id == "users"


def test_a_saved_record_stamps_its_schema_version(tmp_path: Path) -> None:
    # The archive stamps the format version so a future change is detectable; a
    # freshly written record carries schemaVersion 1 on disk.
    record = _record("r1", "2026-07-18T10:00:00Z")
    assert record.schema_version == 1
    save_record(tmp_path, record)
    raw = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))
    assert raw["schemaVersion"] == 1
    assert load_record(tmp_path / "r1.json").schema_version == 1


def test_a_record_without_a_schema_version_loads_with_the_default(tmp_path: Path) -> None:
    # Backward tolerance: a file missing the version key loads with the default,
    # every other field still intact — an old or hand-edited file never bricks the tab.
    save_record(tmp_path, _record("r1", "2026-07-18T10:00:00Z"))
    raw = json.loads((tmp_path / "r1.json").read_text(encoding="utf-8"))
    del raw["schemaVersion"]
    (tmp_path / "r1.json").write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_record(tmp_path / "r1.json")
    assert loaded.schema_version == 1
    assert loaded.cells[0].request_id == "users"


def test_prune_keeps_only_the_newest_records(tmp_path: Path) -> None:
    # Retention: .reports/ is bounded by pruning to the newest `keep` records by
    # created timestamp; older files are unlinked, newer ones stay.
    for stamp, day in (("r1", 15), ("r2", 16), ("r3", 17), ("r4", 18)):
        save_record(tmp_path, _record(stamp, f"2026-07-{day}T09:00:00Z"))
    prune(tmp_path, keep=2)
    assert {record.metadata.id for record in list_records(tmp_path)} == {"r3", "r4"}
    assert not (tmp_path / "r1.json").exists()
    assert not (tmp_path / "r2.json").exists()


def test_save_record_prunes_when_keep_is_passed(tmp_path: Path) -> None:
    # The optional `keep` on save_record wires in retention: after writing, only
    # the newest `keep` records remain. Omitting it leaves everything in place.
    save_record(tmp_path, _record("r1", "2026-07-15T09:00:00Z"))
    save_record(tmp_path, _record("r2", "2026-07-16T09:00:00Z"))
    save_record(tmp_path, _record("r3", "2026-07-17T09:00:00Z"), keep=2)
    assert {record.metadata.id for record in list_records(tmp_path)} == {"r2", "r3"}
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
