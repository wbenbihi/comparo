"""A browsable archive of saved report records under ``<data>/.reports/``.

Each saved run is one JSON file named by its short id — a whole
:class:`~comparo.core.report_record.ReportRecord` (the same versioned artifact the
CI reporters project from), so the Report tab can replay a past run in full detail
without re-executing it. The record is built by
:mod:`comparo.core.report_builder`; this module only reads and writes it.

The core stays clock-free: callers pass the record (whose ``metadata.created`` is
an ISO timestamp); the front-end computes the relative age at render time.
"""

import json
from pathlib import Path

import msgspec

from comparo.core.report_record import ReportRecord


def archive_dir(root: Path, data: str | None, report_config: object) -> Path:
    """Resolve ``<data>/.reports`` — ``spec.report.dir`` overrides ``.reports``."""
    base = root / (data or ".")
    name = ".reports"
    configured = getattr(report_config, "dir", None)
    if isinstance(configured, str) and configured:
        name = configured
    return base / name


def save_record(directory: Path, record: ReportRecord, keep: int | None = None) -> Path:
    """Write *record* to ``<directory>/<id>.json``, creating the directory.

    When *keep* is not ``None``, prune the archive to the newest *keep* records
    after writing (see :func:`prune`); the default leaves every older record.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.metadata.id}.json"
    document = json.dumps(msgspec.to_builtins(record), indent=2, ensure_ascii=False)
    path.write_text(document, encoding="utf-8")
    if keep is not None:
        prune(directory, keep)
    return path


def prune(directory: Path, keep: int) -> None:
    """Delete all but the newest *keep* records in *directory*, by created time.

    Records are ordered newest-first by ``metadata.created`` (reusing
    :func:`list_records`); every record past the first *keep* has its
    ``<id>.json`` file unlinked. A non-positive *keep* removes every loadable
    record. Corrupt files that :func:`list_records` skips are left untouched.
    """
    kept = max(keep, 0)
    for record in list_records(directory)[kept:]:
        try:
            (directory / f"{record.metadata.id}.json").unlink()
        except OSError:
            continue


def list_records(directory: Path) -> list[ReportRecord]:
    """Every saved record in *directory*, newest first; unreadable files are skipped."""
    if not directory.is_dir():
        return []
    records: list[ReportRecord] = []
    for path in directory.glob("*.json"):
        try:
            records.append(load_record(path))
        except (OSError, msgspec.DecodeError, msgspec.ValidationError):
            continue
    records.sort(key=lambda record: record.metadata.created, reverse=True)
    return records


def load_record(path: Path) -> ReportRecord:
    """Read a single saved record from *path* (raises if it does not decode)."""
    return msgspec.json.decode(path.read_bytes(), type=ReportRecord)
