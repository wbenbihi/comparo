"""Build a versioned :class:`ReportRecord` from a run, diff, or execution result.

The one result→record pipeline: it reads the in-memory objects the engine keeps
(each cell's two :class:`Execution`s — the exact request sent and the full
response received — plus the structured diff and assertions) and projects them
into the serializable :mod:`comparo.core.report_record` shape, including the
record-level rule inventories every cell references by id.

**Redaction happens here, unconditionally.** The in-memory objects hold real
secret values (a resolved ``Authorization`` header, a body a server echoed a
secret into); every value that reaches the record is masked first — url, headers,
query, body, cookies, JSON paths, names, labels, and error text — via the project
``redact`` callable, :func:`redact_tree`, and :func:`mask_credential_header`. An
``auth`` value is *always* the mask glyph, ``bodyText`` is redacted before it is
truncated, and a binary body's hex head is dropped entirely when the redactor
would touch its text view. This is the never-leak floor; the ``comparo doctor``
``saved-reports-v1`` canary sink proves it at runtime.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Literal

from comparo.core.assertions import AssertionResult
from comparo.core.assertions import AssertRef
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import RuleRef
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionResult
from comparo.core.http import HttpResponse
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.outbound import outbound_diffs
from comparo.core.redaction import MASK
from comparo.core.redaction import binary_is_clean
from comparo.core.redaction import decoded_text
from comparo.core.redaction import mask_credential_header
from comparo.core.redaction import redact_tree
from comparo.core.report import diff_gate
from comparo.core.report import execution_gate
from comparo.core.report import run_gate
from comparo.core.report_record import AssertionRecord
from comparo.core.report_record import AssertRuleRecord
from comparo.core.report_record import AssertTally
from comparo.core.report_record import AuthRecord
from comparo.core.report_record import Cell
from comparo.core.report_record import CellTally
from comparo.core.report_record import Comparison
from comparo.core.report_record import DiffRuleRecord
from comparo.core.report_record import Environments
from comparo.core.report_record import EnvRef
from comparo.core.report_record import FieldDiffRecord
from comparo.core.report_record import FieldTally
from comparo.core.report_record import Invocation
from comparo.core.report_record import NotRunCell
from comparo.core.report_record import OutboundDiffRecord
from comparo.core.report_record import OutboundRequest
from comparo.core.report_record import RecordMeta
from comparo.core.report_record import ReportRecord
from comparo.core.report_record import RequestComparison
from comparo.core.report_record import ResponseRecord
from comparo.core.report_record import Rules
from comparo.core.report_record import RuleTally
from comparo.core.report_record import Selection
from comparo.core.report_record import Side
from comparo.core.report_record import Sides
from comparo.core.report_record import Summary
from comparo.core.report_record import TrailRecord

Redact = Callable[[str], str]

#: Cap on serialized non-JSON body text, applied AFTER redaction (a secret's
#: prefix must never survive a cut). ``sizeBytes`` always carries the true size.
_TEXT_CAP = 512_000
#: How many raw bytes the binary hex head may carry.
_HEAD_BYTES = 1024


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


def _not_run(entries: list[tuple[str, str, str]] | None, redact: Redact) -> list[NotRunCell]:
    """The ⊘ roster — ``(request_id, name, variant)`` tuples, redacted."""
    if not entries:
        return []
    return [
        NotRunCell(request_id=redact(rid), name=redact(name), variant=redact(variant))
        for rid, name, variant in entries
    ]


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
        trail=[
            TrailRecord(
                path=redact(str(entry.path)),
                origin=entry.origin.value,
                detail=redact(str(entry.detail)),
            )
            for entry in resolved.trail
        ],
    )


def _response(response: HttpResponse | None, redact: Redact) -> ResponseRecord | None:
    if response is None:
        return None
    events = (
        [redact_tree(event, redact) for event in response.events]
        if response.events is not None
        else None
    )
    body: object = None
    body_text: str | None = None
    truncated = False
    sha256: str | None = None
    body_head: str | None = None
    if events is None and response.body:
        parsed = _parse_json(response.body)
        if parsed is not _NOT_JSON:
            body = redact_tree(parsed, redact)
        else:
            text = decoded_text(response.body)
            if text is not None:
                # Redact BEFORE truncating — a secret's prefix must never survive.
                masked = redact(text)
                if len(masked) > _TEXT_CAP:
                    body_text, truncated = masked[:_TEXT_CAP], True
                else:
                    body_text = masked
            elif binary_is_clean(response.body, redact):
                # Binary: an honest digest, never mojibake. Both the digest and
                # the hex head are dropped when the redactor would touch ANY text
                # view of the WHOLE body — hex must not become a side channel
                # around the mask, and a digest of secret-bearing bytes would be
                # an offline verification oracle. Fail closed on both.
                sha256 = hashlib.sha256(response.body).hexdigest()
                body_head = response.body[:_HEAD_BYTES].hex()
    return ResponseRecord(
        status=response.status,
        headers=[
            (redact(str(key)), redact(mask_credential_header(str(key), str(value))))
            for key, value in response.headers
        ],
        latency_ms=round(response.elapsed_ms, 1),
        size_bytes=len(response.body),
        http_version=response.http_version,
        reason_phrase=redact(response.reason_phrase),
        body=body,
        events=events,
        body_text=body_text,
        body_truncated=truncated,
        sha256=sha256,
        body_head=body_head,
    )


_NOT_JSON = object()


def _parse_json(body: bytes) -> object:
    try:
        return json.loads(body)
    except (ValueError, TypeError, RecursionError):
        # Non-JSON or a pathologically deep body is treated as not-JSON (raw bytes).
        return _NOT_JSON


# ── rule inventories ──────────────────────────────────────────────────────────

#: A diff rule as written — stable across compositions (RuleRef.index is not).
_DiffKey = tuple[str, str | None, str, str, float | None, str | None]


def _diff_key(ref: RuleRef) -> _DiffKey:
    return (ref.origin, ref.profile, ref.path, ref.mode, ref.tolerance, ref.array_length)


def _diff_inventory(
    cells: list[CellDiff], redact: Redact
) -> tuple[list[DiffRuleRecord], dict[_DiffKey, str]]:
    """Fold every cell's rule outcomes into one inventory with per-rule tallies."""
    order: list[tuple[_DiffKey, RuleRef]] = []
    tallies: dict[_DiffKey, dict[str, int]] = {}
    for cell in cells:
        for outcome in cell.rule_outcomes:
            key = _diff_key(outcome.ref)
            if key not in tallies:
                tallies[key] = {}
                order.append((key, outcome.ref))
            counts = tallies[key]
            counts[outcome.outcome] = counts.get(outcome.outcome, 0) + 1
    ids = {key: f"d{index}" for index, (key, _) in enumerate(order)}
    records = [
        DiffRuleRecord(
            id=ids[key],
            path=redact(ref.path),
            mode=ref.mode,
            origin=ref.origin,
            profile=redact(ref.profile) if ref.profile else None,
            array_length=_array_length(ref.array_length),
            tolerance=ref.tolerance,
            outcomes=RuleTally(
                broke=tallies[key].get("broke", 0),
                held=tallies[key].get("held", 0),
                silenced=tallies[key].get("silenced", 0),
                absent=tallies[key].get("absent", 0),
                error=tallies[key].get("error", 0),
            ),
        )
        for key, ref in order
    ]
    return records, ids


def _array_length(value: str | None) -> Literal["exact", "tolerant"] | None:
    match value:
        case "exact":
            return "exact"
        case "tolerant":
            return "tolerant"
        case _:
            return None


#: One cell's evaluated sides — each entry a real side (never a phantom).
_CellSides = list[tuple[Execution | None, list[AssertionResult]]]


def _assert_inventory(
    cells: list[_CellSides], redact: Redact
) -> tuple[list[AssertRuleRecord], dict[AssertRef, str]]:
    """Fold every evaluated result into one assertion-rule inventory.

    AssertRef indices are block-relative (stable across compositions), so the
    whole ref is the written identity. Tallies count CELLS, matching the diff
    inventory: a rule judged on both sides of one cell counts once, worst
    outcome winning (broke > held > error/unjudged). ``expected`` records the
    first observed declared value (a matrix-templated expectation varies).
    """
    order: list[AssertRef] = []
    tallies: dict[AssertRef, RuleTally] = {}
    expected: dict[AssertRef, object] = {}
    for sides in cells:
        per_cell: dict[AssertRef, tuple[str, str]] = {}  # ref -> (outcome, severity)
        for execution, results in sides:
            judged = execution is not None and execution.response is not None
            for result in results:
                ref = result.ref
                if ref is None:
                    continue
                if ref not in tallies:
                    tallies[ref] = RuleTally()
                    order.append(ref)
                    expected[ref] = result.expected
                outcome = "error" if not judged else ("held" if result.ok else "broke")
                rank = {"broke": 2, "held": 1, "error": 0}
                current = per_cell.get(ref)
                if current is None or rank[outcome] > rank[current[0]]:
                    per_cell[ref] = (outcome, result.severity)
        for ref, (outcome, rule_severity) in per_cell.items():
            tally = tallies[ref]
            if outcome == "error":
                tally.error += 1
            elif outcome == "held":
                tally.held += 1
                if rule_severity == "warn":
                    tally.warn_held += 1
            else:
                tally.broke += 1
                if rule_severity == "warn":
                    tally.warn_broke += 1
    ids = {ref: f"a{index}" for index, ref in enumerate(order)}
    records = []
    for ref in order:
        severity: Literal["error", "warn"] = "warn" if ref.severity == "warn" else "error"
        records.append(
            AssertRuleRecord(
                id=ids[ref],
                target=redact(ref.target),
                op=ref.op,
                severity=severity,
                label=redact(ref.label),
                origin="profile" if ref.profile else "inline",
                profile=redact(ref.profile) if ref.profile else None,
                request=redact(ref.request) if ref.request else None,
                expected=redact_tree(expected[ref], redact),
                outcomes=tallies[ref],
            )
        )
    return records, ids


# ── field / assertion / comparison serialization ──────────────────────────────


def _assertion(
    result: AssertionResult,
    redact: Redact,
    *,
    judged: bool,
    rule_ids: dict[AssertRef, str] | None = None,
) -> AssertionRecord:
    severity: Literal["error", "warn"] = "warn" if result.severity == "warn" else "error"
    if not judged:
        outcome: Literal["held", "broke", "error"] = "error"
    elif result.ok:
        outcome = "held"
    else:
        outcome = "broke"
    return AssertionRecord(
        target=redact(result.target),
        op=result.op,
        ok=result.ok,
        severity=severity,
        label=redact(result.label) if result.label else "",
        rule_id=rule_ids.get(result.ref) if rule_ids is not None and result.ref else None,
        outcome=outcome,
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


def _field(
    field: FieldDiff, redact: Redact, rule_ids: dict[_DiffKey, str] | None
) -> FieldDiffRecord:
    """One field as a record — ``same`` entries carry the path and rule only."""
    if field.state is State.SAME:
        state: Literal["same", "drift", "skip"] = "same"
    elif field.state is State.DRIFT:
        state = "drift"
    else:
        state = "skip"
    rule_id = None
    if field.rule is not None and rule_ids is not None:
        rule_id = rule_ids.get(_diff_key(field.rule))
    if state == "same":
        return FieldDiffRecord(
            path=redact(field.path), state=state, mode=_mode(field.mode), rule_id=rule_id
        )
    return FieldDiffRecord(
        path=redact(field.path),
        state=state,
        mode=_mode(field.mode),
        baseline=redact_tree(field.baseline, redact),
        candidate=redact_tree(field.candidate, redact),
        rule_id=rule_id,
    )


def _comparison(
    diff: CellDiff, redact: Redact, rule_ids: dict[_DiffKey, str] | None = None
) -> Comparison:
    same, drift, skip = _field_tally(diff.fields)
    verdict: Literal["same", "drift", "error"] = (
        "error" if diff.error is not None else ("drift" if diff.drifted else "same")
    )
    profiles: list[str] = []
    default_mode: str | None = None
    for outcome in diff.rule_outcomes:
        ref = outcome.ref
        if ref.origin == "profile" and ref.profile and redact(ref.profile) not in profiles:
            profiles.append(redact(ref.profile))
        if ref.origin == "default" and ref.path == "$":
            default_mode = ref.mode
    return Comparison(
        verdict=verdict,
        same=same,
        drift=drift,
        skipped=skip,
        fields=[_field(field, redact, rule_ids) for field in diff.fields],
        profiles=profiles,
        default_mode=default_mode,
    )


def _request_comparison(
    baseline: Execution | None, candidate: Execution | None, redact: Redact
) -> RequestComparison | None:
    """The outbound layer — did we send the same request to both sides?"""
    if baseline is None or candidate is None:
        return None
    if baseline.resolved is None or candidate.resolved is None:
        return None
    entries = outbound_diffs(baseline.resolved, candidate.resolved, redact=redact)
    return RequestComparison(
        verdict="drift" if entries else "same",
        fields=[
            OutboundDiffRecord(
                label=entry.label,
                baseline=entry.baseline,
                candidate=entry.candidate,
                source=entry.source,
            )
            for entry in entries
        ],
    )


def _side(
    execution: Execution,
    assertions: list[AssertionResult] | None,
    redact: Redact,
    rule_ids: dict[AssertRef, str] | None = None,
) -> Side:
    judged = execution.response is not None
    return Side(
        request=_outbound(execution, redact),
        response=_response(execution.response, redact),
        assertions=[
            _assertion(result, redact, judged=judged, rule_ids=rule_ids) for result in assertions
        ]
        if assertions is not None
        else None,
        error=redact(execution.error) if execution.error else None,
        attempts=execution.attempts,
        retry_policy=execution.retry_policy,
    )


# ── tallies ───────────────────────────────────────────────────────────────────


def _field_tally(fields: list[FieldDiff]) -> tuple[int, int, int]:
    same = sum(1 for field in fields if field.state is State.SAME)
    drift = sum(1 for field in fields if field.state is State.DRIFT)
    skip = sum(1 for field in fields if field.state is State.SKIP)
    return same, drift, skip


def _assert_tally(cells: list[_CellSides]) -> AssertTally:
    """Row counts across all judged sides.

    ``notAsserted`` counts CELLS (one unit for run and execution alike) whose
    every real side carried no rules.
    """
    passed = failed = warned = not_asserted = unjudged = 0
    for sides in cells:
        if not any(results for _, results in sides):
            not_asserted += 1
            continue
        for execution, results in sides:
            judged = execution is not None and execution.response is not None
            for result in results:
                if not judged:
                    unjudged += 1
                elif result.ok:
                    passed += 1
                elif result.severity == "warn":
                    warned += 1
                else:
                    failed += 1
    return AssertTally(
        passed=passed, failed=failed, warned=warned, not_asserted=not_asserted, unjudged=unjudged
    )


def _cell_tally(cells: list[Cell], not_run: int = 0) -> CellTally:
    return CellTally(
        passed=sum(1 for cell in cells if cell.verdict == "pass"),
        failed=sum(1 for cell in cells if cell.verdict == "fail"),
        errors=sum(1 for cell in cells if cell.verdict == "error"),
        not_run=not_run,
        advisory=sum(1 for cell in cells if cell.advisory),
    )


def _judged_failures(execution: Execution | None, results: list[AssertionResult]) -> int:
    """Error-severity breaks judged against a real response.

    Every rule on a response-less side auto-fails with "no response" — those were
    never evaluated, so they count toward the cell's error, never toward the
    gate's broken rules (an errored cell must not drag an errors-only run to FAIL).
    """
    if execution is None or execution.response is None:
        return 0
    return sum(1 for result in results if not result.ok and result.severity == "error")


def _advisory(execution: Execution | None, results: list[AssertionResult]) -> bool:
    """Whether a judged warn rule broke — gate-neutral, surfaced as ``~``."""
    if execution is None or execution.response is None:
        return False
    return any(not result.ok and result.severity == "warn" for result in results)


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
    not_run: list[tuple[str, str, str]] | None = None,
) -> ReportRecord:
    """Build a ``diff`` record from the paired cell diffs."""
    inventory, rule_ids = _diff_inventory(diffs, redact)
    cells = [_diff_cell(diff, redact, rule_ids) for diff in diffs]
    field_same = field_drift = field_skip = 0
    for diff in diffs:
        same, drift, skip = _field_tally(diff.fields)
        field_same += same
        field_drift += drift
        field_skip += skip
    drift_cells = sum(1 for diff in diffs if diff.drifted)
    error_cells = sum(1 for diff in diffs if diff.error is not None)
    calls = sum(_calls([diff.baseline, diff.candidate]) for diff in diffs)
    roster = _not_run(not_run, redact)
    summary = Summary(
        gate=_gate(diff_gate(len(diffs), drift_cells, error_cells)),
        calls=calls,
        cells=len(diffs),
        fields=FieldTally(same=field_same, drift=field_drift, skipped=field_skip),
        cell_verdicts=_cell_tally(cells, not_run=len(roster)),
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
        rules=Rules(diff=inventory),
        cells=cells,
        not_run=roster,
    )


def _diff_cell(diff: CellDiff, redact: Redact, rule_ids: dict[_DiffKey, str]) -> Cell:
    baseline = diff.baseline
    request = diff.request
    verdict: Literal["pass", "fail", "error"] = (
        "error" if diff.error is not None else ("fail" if diff.drifted else "pass")
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
        error=redact(diff.error) if diff.error else None,
        comparison=_comparison(diff, redact, rule_ids),
        request_comparison=_request_comparison(diff.baseline, diff.candidate, redact),
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
    not_run: list[tuple[str, str, str]] | None = None,
) -> ReportRecord:
    """Build an ``execution`` record — assertions on both sides, plus the diff."""
    cell_diffs = [outcome.diff for outcome in result.outcomes if outcome.diff is not None]
    diff_inventory, diff_ids = _diff_inventory(cell_diffs, redact)
    assert_cells: list[_CellSides] = []
    for outcome in result.outcomes:
        sides: _CellSides = [(outcome.baseline, outcome.baseline_assertions)]
        if outcome.candidate is not None:
            sides.append((outcome.candidate, outcome.candidate_assertions))
        assert_cells.append(sides)
    assert_inventory, assert_ids = _assert_inventory(assert_cells, redact)
    cells = [_exec_cell(outcome, redact, diff_ids, assert_ids) for outcome in result.outcomes]
    field_same = field_drift = field_skip = 0
    for cell_diff in cell_diffs:
        same, drift, skip = _field_tally(cell_diff.fields)
        field_same += same
        field_drift += drift
        field_skip += skip
    calls = sum(_calls([outcome.baseline, outcome.candidate]) for outcome in result.outcomes)
    tally = _assert_tally(assert_cells)
    broke = sum(
        _judged_failures(outcome.baseline, outcome.baseline_assertions)
        + _judged_failures(outcome.candidate, outcome.candidate_assertions)
        for outcome in result.outcomes
    )
    gate = _gate(execution_gate(result.drift, broke, result.errors, len(result.outcomes)))
    roster = _not_run(not_run, redact)
    summary = Summary(
        gate=gate,
        calls=calls,
        cells=len(result.outcomes),
        fields=FieldTally(same=field_same, drift=field_drift, skipped=field_skip),
        cell_verdicts=_cell_tally(cells, not_run=len(roster)),
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
        rules=Rules(diff=diff_inventory, assertions=assert_inventory),
        cells=cells,
        not_run=roster,
    )


def _exec_cell(
    outcome: CellOutcome,
    redact: Redact,
    diff_ids: dict[_DiffKey, str],
    assert_ids: dict[AssertRef, str],
) -> Cell:
    baseline = outcome.baseline
    name = baseline.request.metadata.name if baseline is not None else outcome.request_id
    sides = Sides(
        baseline=_side(baseline, outcome.baseline_assertions, redact, assert_ids)
        if baseline is not None
        else _empty_side(redact),
        candidate=_side(outcome.candidate, outcome.candidate_assertions, redact, assert_ids)
        if outcome.candidate is not None
        else None,
    )
    verdict = _exec_verdict(outcome)
    return Cell(
        request_id=redact(outcome.request_id),
        name=redact(name),
        variant=redact(outcome.cell_key),
        verdict=verdict,
        sides=sides,
        # Advisory means PASSED with a broken warn — a failed/errored cell is
        # never additionally advisory (the ~ marker marks green cells only).
        advisory=verdict == "pass"
        and (
            _advisory(outcome.baseline, outcome.baseline_assertions)
            or _advisory(outcome.candidate, outcome.candidate_assertions)
        ),
        error=redact(outcome.error) if outcome.error else None,
        comparison=_comparison(outcome.diff, redact, diff_ids)
        if outcome.diff is not None
        else None,
        request_comparison=_request_comparison(outcome.baseline, outcome.candidate, redact),
    )


def _exec_verdict(outcome: CellOutcome) -> Literal["pass", "fail", "error"]:
    if outcome.error is not None:
        return "error"
    if outcome.diff is not None and outcome.diff.drifted:
        return "fail"
    if _judged_failures(outcome.baseline, outcome.baseline_assertions) or _judged_failures(
        outcome.candidate, outcome.candidate_assertions
    ):
        return "fail"
    return "pass"


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
    not_run: list[tuple[str, str, str]] | None = None,
) -> ReportRecord:
    """Build a ``run`` record — one side per cell, assertions, no comparison."""
    groups: list[_CellSides] = [[(execution, results)] for execution, results in cells]
    inventory, rule_ids = _assert_inventory(groups, redact)
    records = [_run_cell(execution, results, redact, rule_ids) for execution, results in cells]
    tally = _assert_tally(groups)
    error_cells = sum(1 for execution, _ in cells if execution.response is None)
    failed = sum(_judged_failures(execution, results) for execution, results in cells)
    gate = _gate(run_gate(failed, error_cells, len(cells)))
    roster = _not_run(not_run, redact)
    summary = Summary(
        gate=gate,
        calls=_calls([execution for execution, _ in cells]),
        cells=len(cells),
        cell_verdicts=_cell_tally(records, not_run=len(roster)),
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
        rules=Rules(assertions=inventory),
        cells=records,
        not_run=roster,
    )


def _run_cell(
    execution: Execution,
    assertions: list[AssertionResult],
    redact: Redact,
    rule_ids: dict[AssertRef, str],
) -> Cell:
    request = execution.request
    verdict: Literal["pass", "fail", "error"] = (
        "error"
        if execution.response is None
        else ("fail" if _judged_failures(execution, assertions) else "pass")
    )
    return Cell(
        request_id=redact(request.metadata.id or request.metadata.name),
        name=redact(request.metadata.name),
        variant=redact(execution.cell_key),
        verdict=verdict,
        sides=Sides(baseline=_side(execution, assertions, redact, rule_ids)),
        advisory=verdict == "pass" and _advisory(execution, assertions),
    )


def _empty_side(redact: Redact) -> Side:
    """A placeholder side when a cell somehow has no baseline execution (defensive)."""
    return Side(request=OutboundRequest(method="", url=""), error="no execution recorded")
