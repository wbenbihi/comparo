"""Build a versioned :class:`ReportRecord` from a run, diff, or execution result.

The one result→record pipeline: it reads the in-memory objects the engine keeps
(each cell's two :class:`Execution`s — the exact request sent and the full
response received — plus the structured diff and assertions) and projects them
into the serializable :mod:`comparo.core.report_record` shape.

**Redaction happens here, unconditionally.** The in-memory objects hold real
secret values (a resolved ``Authorization`` header, a body a server echoed a
secret into); every value that reaches the record is masked first — url, headers,
query, body, cookies, JSON paths, names, and error text — via the project
``redact`` callable, :func:`redact_tree`, and :func:`mask_credential_header`. An
``auth`` value is *always* the mask glyph. This is the never-leak floor; the
``comparo doctor`` ``saved-reports-v1`` canary sink proves it at runtime.
"""

import json
from collections.abc import Callable
from typing import Literal

from comparo.core.assertions import AssertionResult
from comparo.core.assertions import passed as assertions_pass
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionResult
from comparo.core.http import HttpResponse
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.redaction import MASK
from comparo.core.redaction import mask_credential_header
from comparo.core.redaction import redact_tree
from comparo.core.report import diff_gate
from comparo.core.report import execution_gate
from comparo.core.report import run_gate
from comparo.core.report_record import AssertionRecord
from comparo.core.report_record import AssertTally
from comparo.core.report_record import AuthRecord
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
from comparo.core.report_record import Selection
from comparo.core.report_record import Side
from comparo.core.report_record import Sides
from comparo.core.report_record import Summary

Redact = Callable[[str], str]


# ── envelope ──────────────────────────────────────────────────────────────────


def _meta(
    record_id: str, created: str, tool: str, project: str | None, title: str | None, redact: Redact
) -> RecordMeta:
    return RecordMeta(
        id=record_id,
        created=created,
        tool=tool,
        project=redact(project) if project else None,
        title=redact(title) if title else None,
    )


def _env_ref(environment: Environment, redact: Redact) -> EnvRef:
    return EnvRef(
        name=redact(environment.metadata.name),
        base_url=redact(environment.spec.base_url),
        id=environment.metadata.id,
    )


def _redact_selection(selection: Selection | None, redact: Redact) -> Selection | None:
    """A tag or request id can equal a declared secret value — mask both, the floor."""
    if selection is None:
        return None
    return Selection(
        tags=[redact(tag) for tag in selection.tags] if selection.tags is not None else None,
        requests=[redact(name) for name in selection.requests]
        if selection.requests is not None
        else None,
    )


# ── request / response serialization (the redaction crux) ─────────────────────


def _body_type(body_type: str, has_body: bool) -> Literal["json", "form", "raw"] | None:
    if not has_body:
        return None
    match body_type:
        case "form":
            return "form"
        case "raw":
            return "raw"
        case _:
            return "json"


def _auth_record(auth: object) -> AuthRecord | None:
    """Project a resolved auth block to a masked record — the value is never real."""
    if not isinstance(auth, dict):
        return None
    if "basic" in auth:
        return AuthRecord(scheme="basic", value=MASK)
    if "bearer" in auth:
        return AuthRecord(scheme="bearer", value=MASK)
    return None


def _gate(value: str) -> Literal["PASS", "FAIL", "ERROR"]:
    match value:
        case "PASS":
            return "PASS"
        case "ERROR":
            return "ERROR"
        case _:
            return "FAIL"


def _outbound(execution: Execution, redact: Redact) -> OutboundRequest:
    """The exact request sent (masked), or the declared shape if resolution failed."""
    resolved = execution.resolved
    if resolved is None:
        http = execution.request.spec.request
        return OutboundRequest(method=http.method, url=redact(http.endpoint))
    return OutboundRequest(
        method=resolved.method,
        url=redact(resolved.url),
        # Credential-bearing headers (authorization, cookie, x-api-key…) are masked
        # by name — the resolved request carries the *real* value — then any declared
        # secret elsewhere is value-redacted.
        # An unset optional (``${VAR?}``) resolved to None was not sent, so it is
        # omitted here too — never serialized as the literal "None".
        headers=[
            (redact(str(key)), redact(mask_credential_header(str(key), str(value))))
            for key, value in resolved.headers
            if value is not None
        ],
        query={
            redact(str(key)): redact_tree(value, redact)
            for key, value in resolved.query.items()
            if value is not None
        },
        body=redact_tree(resolved.body, redact),
        body_type=_body_type(resolved.body_type, resolved.body is not None),
        auth=_auth_record(resolved.auth),
        cookies={
            redact(str(key)): redact_tree(value, redact)
            for key, value in (resolved.cookies or {}).items()
            if value is not None
        },
        streaming=resolved.streaming,
    )


def _response(response: HttpResponse | None, redact: Redact) -> ResponseRecord | None:
    if response is None:
        return None
    events = (
        [redact_tree(event, redact) for event in response.events]
        if response.events is not None
        else None
    )
    # A streamed response is replayed from its event sequence; a normal one from its
    # parsed body. A non-JSON body is dropped (bodyText is opt-in, off by default).
    body = None if events is not None else _json_body(response.body, redact)
    return ResponseRecord(
        status=response.status,
        headers=[
            (redact(str(key)), redact(mask_credential_header(str(key), str(value))))
            for key, value in response.headers
        ],
        latency_ms=round(response.elapsed_ms, 1),
        size_bytes=len(response.body),
        body=body,
        events=events,
    )


def _json_body(body: bytes, redact: Redact) -> object:
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None  # non-JSON — bodyText carries it, opt-in (default off)
    return redact_tree(parsed, redact)


def _assertion(result: AssertionResult, redact: Redact) -> AssertionRecord:
    severity: Literal["error", "warn"] = "warn" if result.severity == "warn" else "error"
    return AssertionRecord(
        target=redact(result.target),
        op=result.op,
        ok=result.ok,
        severity=severity,
        expected=redact_tree(result.expected, redact),
        actual=redact_tree(result.actual, redact),
        detail=redact(result.detail) if result.detail else None,
    )


def _mode(mode: str) -> Literal["exact", "ignore", "shape", "type", "tolerance"]:
    match mode:
        case "ignore" | "shape" | "type" | "tolerance" as known:
            return known
        case _:
            return "exact"


def _field(field: FieldDiff, redact: Redact) -> FieldDiffRecord | None:
    """A non-same field as a record; ``None`` for a same field (omitted, compact)."""
    if field.state is State.SAME:
        return None
    state: Literal["drift", "skip"] = "drift" if field.state is State.DRIFT else "skip"
    return FieldDiffRecord(
        path=redact(field.path),
        state=state,
        mode=_mode(field.mode),
        baseline=redact_tree(field.baseline, redact),
        candidate=redact_tree(field.candidate, redact),
        # The governing rule's declared path. The catch-all serializes as null —
        # the wire meaning of "no rule: the default mode governed" — while profile,
        # inline, and synthetic (e.g. built-in volatile-header ignores) rules keep
        # their path so a skip stays attributable. The full RuleRef (origin,
        # profile id, tallies) lands with the rule-inventory schema pass.
        rule=redact(field.rule.path)
        if field.rule is not None and field.rule.origin != "default"
        else None,
    )


def _comparison(diff: CellDiff, redact: Redact) -> Comparison:
    same, drift, skip = _field_tally(diff.fields)
    verdict: Literal["same", "drift", "error"] = (
        "error" if diff.error is not None else ("drift" if diff.drifted else "same")
    )
    fields = [record for field in diff.fields if (record := _field(field, redact)) is not None]
    return Comparison(verdict=verdict, same=same, drift=drift, skipped=skip, fields=fields)


def _side(execution: Execution, assertions: list[AssertionResult] | None, redact: Redact) -> Side:
    return Side(
        request=_outbound(execution, redact),
        response=_response(execution.response, redact),
        assertions=[_assertion(result, redact) for result in assertions]
        if assertions is not None
        else None,
        error=redact(execution.error) if execution.error else None,
    )


# ── tallies ───────────────────────────────────────────────────────────────────


def _field_tally(fields: list[FieldDiff]) -> tuple[int, int, int]:
    same = sum(1 for field in fields if field.state is State.SAME)
    drift = sum(1 for field in fields if field.state is State.DRIFT)
    skip = sum(1 for field in fields if field.state is State.SKIP)
    return same, drift, skip


def _assert_tally(per_cell: list[list[AssertionResult]]) -> AssertTally:
    passed = failed = warned = not_asserted = 0
    for results in per_cell:
        if not results:
            not_asserted += 1
            continue
        for result in results:
            if result.ok:
                passed += 1
            elif result.severity == "warn":
                warned += 1
            else:
                failed += 1
    return AssertTally(passed=passed, failed=failed, warned=warned, not_asserted=not_asserted)


def _judged_failures(execution: Execution | None, results: list[AssertionResult]) -> int:
    """Error-severity breaks judged against a real response.

    Every rule on a response-less side auto-fails with "no response" — those were
    never evaluated, so they count toward the cell's error, never toward the
    gate's broken rules (an errored cell must not drag an errors-only run to FAIL).
    """
    if execution is None or execution.response is None:
        return 0
    return sum(1 for result in results if not result.ok and result.severity == "error")


def _calls(executions: list[Execution | None]) -> int:
    """How many HTTP calls actually went out — a side that failed to resolve made none."""
    return sum(
        1 for execution in executions if execution is not None and execution.resolved is not None
    )


# ── per-kind builders ─────────────────────────────────────────────────────────


def record_from_diff(
    baseline: Environment,
    candidate: Environment,
    diffs: list[CellDiff],
    *,
    record_id: str,
    created: str,
    tool: str,
    project: str | None,
    concurrency: int,
    redact: Redact,
    selection: Selection | None = None,
) -> ReportRecord:
    """Build a ``diff`` record from the paired cell diffs."""
    cells = [_diff_cell(diff, redact) for diff in diffs]
    field_same = field_drift = field_skip = 0
    for diff in diffs:
        same, drift, skip = _field_tally(diff.fields)
        field_same += same
        field_drift += drift
        field_skip += skip
    drift_cells = sum(1 for diff in diffs if diff.drifted)
    error_cells = sum(1 for diff in diffs if diff.error is not None)
    calls = sum(_calls([diff.baseline, diff.candidate]) for diff in diffs)
    summary = Summary(
        gate=_gate(diff_gate(len(diffs), drift_cells, error_cells)),
        calls=calls,
        cells=len(diffs),
        diff=DiffTally(same=field_same, drift=field_drift, error=error_cells, skipped=field_skip),
    )
    invocation = Invocation(
        command=redact(
            f"comparo diff --baseline {baseline.metadata.name} "
            f"--candidate {candidate.metadata.name}"
        ),
        environments=Environments(_env_ref(baseline, redact), _env_ref(candidate, redact)),
        concurrency=concurrency,
        selection=_redact_selection(selection, redact),
    )
    return ReportRecord(
        kind="diff",
        metadata=_meta(record_id, created, tool, project, None, redact),
        invocation=invocation,
        summary=summary,
        cells=cells,
    )


def _diff_cell(diff: CellDiff, redact: Redact) -> Cell:
    baseline = diff.baseline
    request = diff.request
    verdict: Literal["same", "drift", "error"] = (
        "error" if diff.error is not None else ("drift" if diff.drifted else "same")
    )
    sides = Sides(
        baseline=_side(baseline, None, redact) if baseline is not None else _empty_side(redact),
        candidate=_side(diff.candidate, None, redact) if diff.candidate is not None else None,
    )
    return Cell(
        request_id=redact(request.metadata.id or request.metadata.name),
        name=redact(request.metadata.name),
        variant=redact(diff.cell_key),
        verdict=verdict,
        sides=sides,
        comparison=_comparison(diff, redact),
    )


def _execution_environments(result: ExecutionResult, redact: Redact) -> Environments:
    """Derive the env refs from the outcomes' executions (they hold the real envs).

    Falls back to the result's env *names* (baseUrl unknown) when a run produced no
    outcomes, so an empty execution still records what it targeted.
    """
    baseline: EnvRef | None = None
    candidate: EnvRef | None = None
    for outcome in result.outcomes:
        if baseline is None and outcome.baseline is not None:
            baseline = _env_ref(outcome.baseline.environment, redact)
        if candidate is None and outcome.candidate is not None:
            candidate = _env_ref(outcome.candidate.environment, redact)
    if baseline is None:
        baseline = EnvRef(name=redact(result.baseline), base_url="")
    if candidate is None and result.candidate is not None:
        candidate = EnvRef(name=redact(result.candidate), base_url="")
    return Environments(baseline, candidate)


def record_from_execution(
    profile: ExecutionProfile,
    result: ExecutionResult,
    *,
    record_id: str,
    created: str,
    tool: str,
    project: str | None,
    concurrency: int,
    redact: Redact,
    selection: Selection | None = None,
) -> ReportRecord:
    """Build an ``execution`` record — assertions on both sides, plus the diff."""
    cells = [_exec_cell(outcome, redact) for outcome in result.outcomes]
    field_same = field_drift = field_skip = 0
    for outcome in result.outcomes:
        if outcome.diff is not None:
            same, drift, skip = _field_tally(outcome.diff.fields)
            field_same += same
            field_drift += drift
            field_skip += skip
    calls = sum(_calls([outcome.baseline, outcome.candidate]) for outcome in result.outcomes)
    tally = _assert_tally(
        [outcome.baseline_assertions + outcome.candidate_assertions for outcome in result.outcomes]
    )
    broke = sum(
        _judged_failures(outcome.baseline, outcome.baseline_assertions)
        + _judged_failures(outcome.candidate, outcome.candidate_assertions)
        for outcome in result.outcomes
    )
    gate = _gate(execution_gate(result.drift, broke, result.errors, len(result.outcomes)))
    summary = Summary(
        gate=gate,
        calls=calls,
        cells=len(result.outcomes),
        diff=DiffTally(same=field_same, drift=field_drift, error=result.errors, skipped=field_skip),
        assertions=tally,
    )
    invocation = Invocation(
        command=redact(f"comparo exec {profile.metadata.id or profile.metadata.name}"),
        environments=_execution_environments(result, redact),
        concurrency=concurrency,
        selection=_redact_selection(selection, redact),
        profile=redact(profile.metadata.id) if profile.metadata.id else None,
    )
    return ReportRecord(
        kind="execution",
        metadata=_meta(record_id, created, tool, project, redact(profile.metadata.name), redact),
        invocation=invocation,
        summary=summary,
        cells=cells,
    )


def _exec_cell(outcome: CellOutcome, redact: Redact) -> Cell:
    baseline = outcome.baseline
    name = baseline.request.metadata.name if baseline is not None else outcome.request_id
    sides = Sides(
        baseline=_side(baseline, outcome.baseline_assertions, redact)
        if baseline is not None
        else _empty_side(redact),
        candidate=_side(outcome.candidate, outcome.candidate_assertions, redact)
        if outcome.candidate is not None
        else None,
    )
    return Cell(
        request_id=redact(outcome.request_id),
        name=redact(name),
        variant=redact(outcome.cell_key),
        verdict=_exec_verdict(outcome),
        sides=sides,
        comparison=_comparison(outcome.diff, redact) if outcome.diff is not None else None,
    )


def _exec_verdict(outcome: CellOutcome) -> Literal["same", "drift", "error", "pass", "fail"]:
    if outcome.error is not None:
        return "error"
    if outcome.diff is not None and outcome.diff.drifted:
        return "drift"
    if not (
        assertions_pass(outcome.baseline_assertions)
        and assertions_pass(outcome.candidate_assertions)
    ):
        return "fail"
    if outcome.baseline_assertions or outcome.candidate_assertions:
        return "pass"
    return "same"


def record_from_run(
    environment: Environment,
    cells: list[tuple[Execution, list[AssertionResult]]],
    *,
    record_id: str,
    created: str,
    tool: str,
    project: str | None,
    concurrency: int,
    redact: Redact,
    selection: Selection | None = None,
) -> ReportRecord:
    """Build a ``run`` record — one side per cell, assertions, no comparison."""
    records = [_run_cell(execution, assertions, redact) for execution, assertions in cells]
    tally = _assert_tally([assertions for _, assertions in cells])
    error_cells = sum(1 for execution, _ in cells if execution.response is None)
    failed = sum(_judged_failures(execution, results) for execution, results in cells)
    gate = _gate(run_gate(failed, error_cells, len(cells)))
    summary = Summary(
        gate=gate,
        calls=_calls([execution for execution, _ in cells]),
        cells=len(cells),
        assertions=tally,
    )
    invocation = Invocation(
        command=redact(f"comparo run --env {environment.metadata.name}"),
        environments=Environments(_env_ref(environment, redact)),
        concurrency=concurrency,
        selection=_redact_selection(selection, redact),
    )
    return ReportRecord(
        kind="run",
        metadata=_meta(record_id, created, tool, project, None, redact),
        invocation=invocation,
        summary=summary,
        cells=records,
    )


def _run_cell(execution: Execution, assertions: list[AssertionResult], redact: Redact) -> Cell:
    request = execution.request
    verdict: Literal["pass", "fail", "error"] = (
        "error"
        if execution.response is None
        else ("pass" if assertions_pass(assertions) else "fail")
    )
    return Cell(
        request_id=redact(request.metadata.id or request.metadata.name),
        name=redact(request.metadata.name),
        variant=redact(execution.cell_key),
        verdict=verdict,
        sides=Sides(baseline=_side(execution, assertions, redact)),
    )


def _empty_side(redact: Redact) -> Side:
    """A placeholder side when a cell somehow has no baseline execution (defensive)."""
    return Side(request=OutboundRequest(method="", url=""), error="no execution recorded")
