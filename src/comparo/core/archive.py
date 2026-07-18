"""A browsable archive of saved run reports under ``<data>/.reports/``.

Each saved run is one JSON file named by its short id. The archive is what the
Report tab browses: a compact, self-contained summary of a diff or execution —
its gate, counts, per-environment assertion roll-up, and per-request drift
breakdown — enough to read a past run at a glance without re-executing it.

The core stays clock-free: callers pass the run id and an ISO timestamp; the
front-end computes the relative age at render time.
"""

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path

from comparo.core.assertions import AssertionResult
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execution import ExecutionResult
from comparo.core.redaction import mask_credential_header
from comparo.core.report import diff_gate


@dataclasses.dataclass(frozen=True, slots=True)
class AssertionLine:
    """One aggregated assertion rule in a saved report — label, state, detail."""

    label: str
    state: str  # "pass" | "warn" | "fail"
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class AssertionSummary:
    """A per-environment roll-up of assertion results."""

    passed: int
    failed: int
    warned: int
    lines: list[AssertionLine]


@dataclasses.dataclass(frozen=True, slots=True)
class RequestBreakdown:
    """The per-request field tally in a saved report."""

    request: str
    same: int
    drift: int
    skip: int
    verdict: str  # "pass" | "fail" | "drift" | "error"
    #: The drifted field paths for this request, for the in-place deep-dive.
    drift_paths: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class CellRecord:
    """One saved request cell — enough to replay its body diff and detail tree.

    Every string, key, path and body value is redacted before it reaches this
    record, so a saved report never writes a declared secret to disk.
    """

    request: str  # redacted short request name
    variant: str  # redacted matrix cell key ("" for a no-matrix cell)
    method: str
    path: str  # redacted endpoint
    drift_paths: list[str] = dataclasses.field(default_factory=list)  # redacted
    skip_paths: list[str] = dataclasses.field(default_factory=list)  # redacted
    baseline_body: object = None  # redacted parsed body (JSON-serializable)
    candidate_body: object = None  # redacted parsed body
    status: int | None = None
    latency_ms: int | None = None
    size_bytes: int | None = None
    response_headers: dict[str, str] = dataclasses.field(default_factory=dict)  # redacted


@dataclasses.dataclass(frozen=True, slots=True)
class ReportRecord:
    """A saved run: gate, counts, assertion roll-ups, and per-request breakdown."""

    id: str
    created: str  # ISO 8601
    execution: str | None
    baseline: str
    candidate: str | None
    gate: str  # "PASS" | "FAIL" | "ERROR"
    calls: int
    same: int
    drift: int
    error: int
    skipped: int
    baseline_assertions: AssertionSummary
    candidate_assertions: AssertionSummary
    requests: list[RequestBreakdown]
    #: Per-cell detail for a faithful replay (body diff, metrics, detail tree).
    cells: list[CellRecord] = dataclasses.field(default_factory=list)


# ── building records ──────────────────────────────────────────────────────────


def _short(request_id: str) -> str:
    return request_id.split(".", 1)[-1]


def _result_state(result: AssertionResult) -> str:
    if result.ok:
        return "pass"
    return "warn" if result.severity == "warn" else "fail"


_WORSE = {"pass": 0, "warn": 1, "fail": 2}


@dataclasses.dataclass
class _Group:
    """Mutable accumulator for one assertion rule while summarizing."""

    requests: list[str]
    state: str
    bad: str | None


def _summarize(
    pairs: list[tuple[str, AssertionResult]], redact: Callable[[str], str] = str
) -> AssertionSummary:
    """Aggregate (request, result) pairs into per-rule lines and totals.

    ``redact`` masks known secret values in the offending-value detail before it
    is persisted or rendered — a server can echo a secret into a field an
    assertion reports on.
    """
    passed = failed = warned = 0
    order: list[str] = []
    groups: dict[str, _Group] = {}
    for request, result in pairs:
        state = _result_state(result)
        if state == "pass":
            passed += 1
        elif state == "warn":
            warned += 1
        else:
            failed += 1
        group = groups.get(result.label)
        if group is None:
            group = _Group([], "pass", None)
            groups[result.label] = group
            order.append(result.label)
        short = redact(_short(request))
        if short not in group.requests:
            group.requests.append(short)
        if _WORSE[state] > _WORSE[group.state]:
            group.state = state
        if state != "pass" and group.bad is None:
            group.bad = f"{short} {redact(result.detail)}"
    lines: list[AssertionLine] = []
    for label in order:
        group = groups[label]
        if group.state == "pass":
            count = len(group.requests)
            detail = f"{count} request" if count == 1 else f"{count} requests"
        else:
            detail = group.bad if group.bad is not None else ", ".join(group.requests)
        # Redact the label too: a rule's label embeds its asserted value
        # (``authorization contains <value>``), so a secret literal in the
        # assertion would otherwise be persisted / rendered verbatim.
        lines.append(AssertionLine(redact(label), group.state, detail))
    return AssertionSummary(passed, failed, warned, lines)


def _redact_body(value: object, redact: Callable[[str], str]) -> object:
    """Recursively mask secrets in a parsed body — keys and string values alike.

    Mirrors ``export._redact_value``: a server can echo a secret as a JSON *key*
    as well as a value, so both are redacted before the body is written to disk.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact(str(key)): _redact_body(item, redact) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_body(item, redact) for item in value]
    return value


def _cell_record(
    request_id: str, diff: CellDiff | None, redact: Callable[[str], str] = str
) -> CellRecord:
    """Build one redacted, replayable cell record from a diff outcome."""
    request = redact(_short(request_id))
    if diff is None:
        return CellRecord(request=request, variant="", method="", path="")
    outbound = diff.request.spec.request
    return CellRecord(
        request=request,
        variant=redact(diff.cell_key),
        method=outbound.method,
        path=redact(outbound.endpoint),
        drift_paths=[redact(f.path) for f in diff.fields if f.state is State.DRIFT],
        skip_paths=[redact(f.path) for f in diff.fields if f.state is State.SKIP],
        baseline_body=_redact_body(diff.baseline_body, redact),
        candidate_body=_redact_body(diff.candidate_body, redact),
        status=diff.status,
        latency_ms=diff.latency_ms,
        size_bytes=diff.size_bytes,
        response_headers={
            redact(str(key)): redact(mask_credential_header(str(key), str(value)))
            for key, value in diff.response_headers
        },
    )


def _field_tally(fields: list[FieldDiff]) -> tuple[int, int, int]:
    same = sum(1 for field in fields if field.state is State.SAME)
    drift = sum(1 for field in fields if field.state is State.DRIFT)
    skip = sum(1 for field in fields if field.state is State.SKIP)
    return same, drift, skip


def _breakdown(
    cells: list[tuple[str, CellDiff | None, str | None]],
    assertion_failed: frozenset[str] = frozenset(),
    redact: Callable[[str], str] = str,
) -> list[RequestBreakdown]:
    """Per-request field tally from (request_id, diff, error) triples.

    ``assertion_failed`` names requests with a failing error-severity assertion;
    such a request is never recorded as ``pass`` even when it does not drift, so
    the archive can't show a green verdict for a run that failed a check.
    ``redact`` masks a secret a server may have echoed as a JSON key (a field path).
    """
    order: list[str] = []
    same: dict[str, int] = {}
    drift: dict[str, int] = {}
    skip: dict[str, int] = {}
    verdict: dict[str, str] = {}
    paths: dict[str, list[str]] = {}
    for request_id, diff, error in cells:
        # Redact the request name too: on the vanishing chance an id equals a
        # declared secret, the whole-value backstop masks it (a no-op otherwise).
        name = redact(_short(request_id))
        if name not in verdict:
            order.append(name)
            same[name] = drift[name] = skip[name] = 0
            verdict[name] = "fail" if name in assertion_failed else "pass"
            paths[name] = []
        if error is not None:
            verdict[name] = "error"
            continue
        if diff is not None:
            cell_same, cell_drift, cell_skip = _field_tally(diff.fields)
            same[name] += cell_same
            drift[name] += cell_drift
            skip[name] += cell_skip
            for field in diff.drifts:
                path = redact(field.path)
                if path not in paths[name]:
                    paths[name].append(path)
            if cell_drift and verdict[name] != "error":
                verdict[name] = "drift"
    return [RequestBreakdown(n, same[n], drift[n], skip[n], verdict[n], paths[n]) for n in order]


def record_from_execution(
    result: ExecutionResult,
    *,
    run_id: str,
    created: str,
    name: str | None,
    redact: Callable[[str], str] = str,
) -> ReportRecord:
    """Build a saveable record from an execution result (secret-redacted)."""
    baseline_pairs = [
        (outcome.request_id, assertion)
        for outcome in result.outcomes
        for assertion in outcome.baseline_assertions
    ]
    candidate_pairs = [
        (outcome.request_id, assertion)
        for outcome in result.outcomes
        for assertion in outcome.candidate_assertions
    ]
    cells = [(outcome.request_id, outcome.diff, outcome.error) for outcome in result.outcomes]
    assertion_failed = frozenset(
        _short(outcome.request_id)
        for outcome in result.outcomes
        if any(_result_state(r) == "fail" for r in outcome.baseline_assertions)
        or any(_result_state(r) == "fail" for r in outcome.candidate_assertions)
    )
    calls = len(result.outcomes)
    errors = result.errors
    drift = result.drift
    skipped = sum(
        _field_tally(outcome.diff.fields)[2] for outcome in result.outcomes if outcome.diff
    )
    same = calls - drift - errors
    gate = "ERROR" if errors else ("PASS" if result.passed else "FAIL")
    return ReportRecord(
        id=run_id,
        created=created,
        execution=redact(name) if name is not None else None,
        baseline=redact(result.baseline),
        candidate=redact(result.candidate) if result.candidate is not None else None,
        gate=gate,
        calls=calls,
        same=same,
        drift=drift,
        error=errors,
        skipped=skipped,
        baseline_assertions=_summarize(baseline_pairs, redact),
        candidate_assertions=_summarize(candidate_pairs, redact),
        requests=_breakdown(cells, assertion_failed, redact),
        cells=[
            _cell_record(outcome.request_id, outcome.diff, redact) for outcome in result.outcomes
        ],
    )


def record_from_diff(
    baseline: str,
    candidate: str,
    diffs: list[CellDiff],
    *,
    run_id: str,
    created: str,
    redact: Callable[[str], str] = str,
) -> ReportRecord:
    """Build a saveable record from an ad-hoc diff run (no assertions)."""
    cells: list[tuple[str, CellDiff | None, str | None]] = [
        (cell.request.metadata.id or cell.request.metadata.name, cell, cell.error) for cell in diffs
    ]
    calls = len(diffs)
    errors = sum(1 for cell in diffs if cell.error is not None)
    drift = sum(1 for cell in diffs if cell.drifted)
    skipped = sum(cell.skipped for cell in diffs)
    same = calls - drift - errors
    gate = diff_gate(calls, drift, errors)
    empty = AssertionSummary(0, 0, 0, [])
    return ReportRecord(
        id=run_id,
        created=created,
        execution=None,
        baseline=redact(baseline),
        candidate=redact(candidate),
        gate=gate,
        calls=calls,
        same=same,
        drift=drift,
        error=errors,
        skipped=skipped,
        baseline_assertions=empty,
        candidate_assertions=empty,
        requests=_breakdown(cells, redact=redact),
        cells=[
            _cell_record(cell.request.metadata.id or cell.request.metadata.name, cell, redact)
            for cell in diffs
        ],
    )


def record_from_run(
    environment: str,
    cells: list[tuple[str, list[AssertionResult]]],
    *,
    run_id: str,
    created: str,
    redact: Callable[[str], str] = str,
) -> ReportRecord:
    """Build a saveable record from a single-environment run — assertions, no diff.

    A run executes requests against one environment and checks assertions; it has
    no candidate and no diff, so the record is an assertions roll-up on the one
    environment. Its gate fails when any error-severity assertion fails.
    """
    pairs = [(request_id, result) for request_id, results in cells for result in results]
    summary = _summarize(pairs, redact)
    assertion_failed = frozenset(
        _short(request_id)
        for request_id, results in cells
        if any(_result_state(result) == "fail" for result in results)
    )
    diff_cells: list[tuple[str, CellDiff | None, str | None]] = [
        (request_id, None, None) for request_id, _ in cells
    ]
    empty = AssertionSummary(0, 0, 0, [])
    return ReportRecord(
        id=run_id,
        created=created,
        execution=None,
        baseline=redact(environment),
        candidate=None,
        gate="PASS" if summary.failed == 0 else "FAIL",
        calls=len(cells),
        same=0,
        drift=0,
        error=0,
        skipped=0,
        baseline_assertions=summary,
        candidate_assertions=empty,
        requests=_breakdown(diff_cells, assertion_failed, redact),
    )


# ── archive I/O ───────────────────────────────────────────────────────────────


def archive_dir(root: Path, data: str | None, report_config: object) -> Path:
    """Resolve ``<data>/.reports`` — ``spec.report.dir`` overrides ``.reports``."""
    base = root / (data or ".")
    name = ".reports"
    configured = getattr(report_config, "dir", None)
    if isinstance(configured, str) and configured:
        name = configured
    return base / name


def save_record(directory: Path, record: ReportRecord) -> Path:
    """Write *record* to ``<directory>/<id>.json``, creating the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.id}.json"
    path.write_text(json.dumps(_to_dict(record), indent=2), encoding="utf-8")
    return path


def list_records(directory: Path) -> list[ReportRecord]:
    """Every saved record in *directory*, newest first; bad files are skipped."""
    if not directory.is_dir():
        return []
    records: list[ReportRecord] = []
    for path in directory.glob("*.json"):
        try:
            records.append(load_record(path))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    records.sort(key=lambda record: record.created, reverse=True)
    return records


def load_record(path: Path) -> ReportRecord:
    """Read a single saved record from *path*."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        message = f"not a report record: {path}"
        raise ValueError(message)
    return _from_dict(data)


def _to_dict(record: ReportRecord) -> dict[str, object]:
    return dataclasses.asdict(record)


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _as_paths(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _summary_from(raw: object) -> AssertionSummary:
    data = raw if isinstance(raw, dict) else {}
    raw_lines = data.get("lines")
    lines = [
        AssertionLine(
            str(line.get("label", "")), str(line.get("state", "")), str(line.get("detail", ""))
        )
        for line in (raw_lines if isinstance(raw_lines, list) else [])
        if isinstance(line, dict)
    ]
    return AssertionSummary(
        _as_int(data.get("passed")), _as_int(data.get("failed")), _as_int(data.get("warned")), lines
    )


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    return _as_int(value)


def _str_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _cell_from(raw: object) -> CellRecord:
    data = raw if isinstance(raw, dict) else {}
    return CellRecord(
        request=str(data.get("request", "")),
        variant=str(data.get("variant", "")),
        method=str(data.get("method", "")),
        path=str(data.get("path", "")),
        drift_paths=_as_paths(data.get("drift_paths")),
        skip_paths=_as_paths(data.get("skip_paths")),
        baseline_body=data.get("baseline_body"),
        candidate_body=data.get("candidate_body"),
        status=_int_or_none(data.get("status")),
        latency_ms=_int_or_none(data.get("latency_ms")),
        size_bytes=_int_or_none(data.get("size_bytes")),
        response_headers=_str_map(data.get("response_headers")),
    )


def _from_dict(data: dict[str, object]) -> ReportRecord:
    raw_requests = data.get("requests")
    requests = [
        RequestBreakdown(
            str(row.get("request", "")),
            _as_int(row.get("same")),
            _as_int(row.get("drift")),
            _as_int(row.get("skip")),
            str(row.get("verdict", "pass")),
            _as_paths(row.get("drift_paths")),
        )
        for row in (raw_requests if isinstance(raw_requests, list) else [])
        if isinstance(row, dict)
    ]
    raw_cells = data.get("cells")
    cells = [_cell_from(row) for row in (raw_cells if isinstance(raw_cells, list) else [])]
    candidate = data.get("candidate")
    execution = data.get("execution")
    return ReportRecord(
        id=str(data["id"]),
        created=str(data["created"]),
        execution=str(execution) if isinstance(execution, str) else None,
        baseline=str(data.get("baseline", "")),
        candidate=str(candidate) if isinstance(candidate, str) else None,
        gate=str(data.get("gate", "")),
        calls=_as_int(data.get("calls")),
        same=_as_int(data.get("same")),
        drift=_as_int(data.get("drift")),
        error=_as_int(data.get("error")),
        skipped=_as_int(data.get("skipped")),
        baseline_assertions=_summary_from(data.get("baseline_assertions")),
        candidate_assertions=_summary_from(data.get("candidate_assertions")),
        requests=requests,
        cells=cells,
    )
