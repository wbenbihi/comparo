"""Project a saved :class:`ReportRecord` into the flat shape the Report tab reads.

The persisted artifact is the nested :class:`~comparo.core.report_record.ReportRecord`
(request + response per side, structured field diffs, per-side assertions). The
Report-tab replay wants a flatter per-record / per-request / per-cell view — the
gate line, the assertion roll-ups, the per-request breakdown, and each cell's
drift/skip paths with the two response bodies. This module is that read-model:
one :func:`project` call turns a record into a :class:`ReplayRecord` the render
helpers consume, so the replay never re-derives it and never fabricates a field
mode (it carries the real :class:`FieldDiffRecord`s — the M-6 fix).
"""

import dataclasses

from comparo.core.report_record import AssertionRecord
from comparo.core.report_record import Cell
from comparo.core.report_record import FieldDiffRecord
from comparo.core.report_record import ReportRecord


@dataclasses.dataclass(frozen=True, slots=True)
class AssertionLine:
    """One assertion in a saved roll-up — a label, its state, and the detail."""

    label: str
    state: str  # "pass" | "warn" | "fail"
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class AssertionSummary:
    """A per-environment assertion roll-up: the counts and the per-rule lines."""

    passed: int
    failed: int
    warned: int
    lines: list[AssertionLine]


@dataclasses.dataclass(frozen=True, slots=True)
class RequestBreakdown:
    """A per-request rollup across a record's cells — counts, verdict, drift paths."""

    request: str
    same: int
    drift: int
    skip: int
    verdict: str  # "pass" | "fail" | "drift" | "error"
    drift_paths: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class ReplayCell:
    """One saved cell, flattened for the diff replay's body well and detail tree."""

    request: str
    variant: str
    method: str
    path: str
    drift_paths: list[str]
    skip_paths: list[str]
    baseline_body: object
    candidate_body: object
    status: int | None
    latency_ms: int | None
    size_bytes: int | None
    response_headers: dict[str, str]
    #: The real per-field diffs (state/mode/baseline/candidate) so the body well
    #: renders the true profile decision instead of fabricating ``exact``.
    fields: list[FieldDiffRecord]


@dataclasses.dataclass(frozen=True, slots=True)
class ReplayRecord:
    """A saved report flattened for the Report tab — the read-model over ReportRecord."""

    id: str
    created: str
    kind: str  # "run" | "diff" | "execution"
    gate: str
    calls: int
    same: int
    drift: int
    error: int
    skipped: int
    baseline: str
    candidate: str | None
    execution: str | None  # the profile title, for an execution record
    baseline_assertions: AssertionSummary
    candidate_assertions: AssertionSummary
    requests: list[RequestBreakdown]
    cells: list[ReplayCell]


def _assertion_summary(rows: list[list[AssertionRecord]]) -> AssertionSummary:
    passed = failed = warned = 0
    lines: list[AssertionLine] = []
    for row in rows:
        for result in row:
            if result.ok:
                state = "pass"
                passed += 1
            elif result.severity == "warn":
                state = "warn"
                warned += 1
            else:
                state = "fail"
                failed += 1
            label = f"{result.target} {result.op}".strip()
            lines.append(AssertionLine(label, state, result.detail or ""))
    return AssertionSummary(passed, failed, warned, lines)


def _replay_cell(cell: Cell) -> ReplayCell:
    fields = cell.comparison.fields if cell.comparison is not None else []
    drift_paths = [field.path for field in fields if field.state == "drift"]
    skip_paths = [field.path for field in fields if field.state == "skip"]
    request = cell.sides.baseline.request
    response = cell.sides.baseline.response
    headers = dict(response.headers) if response is not None else {}
    return ReplayCell(
        request=cell.request_id,
        variant=cell.variant,
        method=request.method,
        path=request.url,
        drift_paths=drift_paths,
        skip_paths=skip_paths,
        baseline_body=response.body if response is not None else None,
        candidate_body=(
            cell.sides.candidate.response.body
            if cell.sides.candidate is not None and cell.sides.candidate.response is not None
            else None
        ),
        status=response.status if response is not None else None,
        latency_ms=round(response.latency_ms) if response is not None else None,
        size_bytes=response.size_bytes if response is not None else None,
        response_headers=headers,
        fields=list(fields),
    )


def _breakdown(cells: list[Cell]) -> list[RequestBreakdown]:
    """Group cells by request id into a per-request rollup, in first-seen order."""
    order: list[str] = []
    same: dict[str, int] = {}
    drift: dict[str, int] = {}
    skip: dict[str, int] = {}
    verdict: dict[str, str] = {}
    paths: dict[str, list[str]] = {}
    for cell in cells:
        name = cell.request_id
        if name not in same:
            order.append(name)
            same[name] = drift[name] = skip[name] = 0
            verdict[name] = "pass"
            paths[name] = []
        drifted = (
            [f.path for f in cell.comparison.fields if f.state == "drift"]
            if cell.comparison
            else []
        )
        skipped = (
            sum(1 for f in cell.comparison.fields if f.state == "skip") if cell.comparison else 0
        )
        if cell.verdict in ("drift", "same", "pass"):
            if drifted:
                drift[name] += 1
            else:
                same[name] += 1
        skip[name] += skipped
        paths[name].extend(p for p in drifted if p not in paths[name])
        verdict[name] = _worse_verdict(verdict[name], cell.verdict)
    return [
        RequestBreakdown(name, same[name], drift[name], skip[name], verdict[name], paths[name])
        for name in order
    ]


def _worse_verdict(current: str, cell: str) -> str:
    order = {"pass": 0, "same": 0, "skip": 0, "fail": 2, "drift": 2, "error": 3}
    winner = cell if order.get(cell, 0) >= order.get(current, 0) else current
    # Normalise the display verdict domain to pass/fail/drift/error.
    return {"same": "pass", "skip": "pass"}.get(winner, winner)


def project(record: ReportRecord) -> ReplayRecord:
    """Flatten *record* into the Report tab's read-model."""
    summary = record.summary
    diff = summary.diff
    environments = record.invocation.environments
    baseline_rows = [c.sides.baseline.assertions or [] for c in record.cells]
    candidate_rows = [
        (c.sides.candidate.assertions or []) if c.sides.candidate is not None else []
        for c in record.cells
    ]
    return ReplayRecord(
        id=record.metadata.id,
        created=record.metadata.created,
        kind=record.kind,
        gate=summary.gate,
        calls=summary.calls,
        same=diff.same if diff is not None else 0,
        drift=diff.drift if diff is not None else 0,
        error=diff.error if diff is not None else 0,
        skipped=diff.skipped if diff is not None else 0,
        baseline=environments.baseline.name,
        candidate=environments.candidate.name if environments.candidate is not None else None,
        execution=record.metadata.title if record.kind == "execution" else None,
        baseline_assertions=_assertion_summary(baseline_rows),
        candidate_assertions=_assertion_summary(candidate_rows),
        requests=_breakdown(record.cells),
        cells=[_replay_cell(cell) for cell in record.cells],
    )
