"""Run an ``ExecutionProfile``: assert both environments, then diff them.

The planner resolves a profile to a concrete plan — which requests, which matrix
cells, which environments — then executes each cell against the baseline (and the
candidate, when one is set), runs the request's assertions on each, and diffs the
pair. It only orchestrates the execute / assertion / diff sinks; it holds no
comparison logic of its own.
"""

import asyncio
import dataclasses
from collections.abc import Callable

from comparo.core.assertions import AssertionResult
from comparo.core.assertions import evaluate_rules
from comparo.core.assertions import passed as assertions_pass
from comparo.core.assertions import profiles_to_rules
from comparo.core.assertions import request_response_rules
from comparo.core.compare import CellDiff
from comparo.core.compare import compare_cell
from comparo.core.execute import Execution
from comparo.core.execute import execute_request
from comparo.core.execute import run_settings
from comparo.core.http import HttpClient
from comparo.core.loader import LoadedProject
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import expand
from comparo.core.models import AssertionRule
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.models import MatrixScope
from comparo.core.models import Request
from comparo.core.resolve import select_environment


@dataclasses.dataclass(frozen=True, slots=True)
class CellOutcome:
    """One request cell's outcome under an execution: assertions and diff."""

    request_id: str
    cell_key: str
    baseline_assertions: list[AssertionResult]
    candidate_assertions: list[AssertionResult]
    diff: CellDiff | None
    error: str | None = None
    #: The two executions this cell ran — the exact request sent and the full
    #: response received, per side — kept even when no diff was computed, so the
    #: v1 report builder can serialize both sides. In-memory only (they hold live
    #: secrets); redacted at build time. ``candidate`` is ``None`` for a
    #: baseline-only execution.
    baseline: Execution | None = None
    candidate: Execution | None = None

    @property
    def ok(self) -> bool:
        """Whether this cell passed — no error, assertions hold, no drift."""
        if self.error is not None:
            return False
        if not assertions_pass(self.baseline_assertions):
            return False
        if not assertions_pass(self.candidate_assertions):
            return False
        return not (self.diff is not None and self.diff.drifted)


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The complete outcome of an execution — assertions on both envs, and the diff."""

    profile_id: str
    baseline: str
    candidate: str | None
    checked_assertions: bool
    checked_diff: bool
    outcomes: list[CellOutcome]

    @property
    def passed(self) -> bool:
        """Whether every cell passed — the execution gate.

        Fails closed on an empty run: a profile whose selection or matrix scope
        matched nothing verified nothing, so it must not report a green gate.
        """
        return bool(self.outcomes) and all(outcome.ok for outcome in self.outcomes)

    @property
    def drift(self) -> int:
        """How many cells drifted."""
        return sum(1 for o in self.outcomes if o.diff is not None and o.diff.drifted)

    @property
    def errors(self) -> int:
        """How many cells failed to execute."""
        return sum(1 for o in self.outcomes if o.error is not None)


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionProgress:
    """A live progress tick emitted as an execution works through its plan.

    Three phases per cell so a UI can render progress *over the whole plan* as a
    table: a ``started=False`` seed tick for every cell before the run (queued),
    a ``started=True, done=False`` tick when the cell goes in flight, and a
    ``done=True`` tick when it finishes with both sides' metrics.
    """

    request_id: str
    cell_key: str
    index: int  # 0-based position of this cell in the plan
    total: int  # total cells in the plan (known before the run starts)
    done: bool
    started: bool = True  # False = a queued seed tick (cell not yet in flight)
    method: str = ""  # the cell's HTTP method (e.g. GET), for the live row
    path: str = ""  # the cell's endpoint path, for the live row
    status: int | None = None  # baseline response status, once finished
    candidate_status: int | None = None  # candidate response status, once finished
    baseline_ms: int | None = None  # baseline latency, once finished
    candidate_ms: int | None = None  # candidate latency, once finished
    baseline_pass: int = 0  # baseline assertions passed, once finished
    baseline_fail: int = 0  # baseline assertions failed, once finished
    candidate_pass: int = 0  # candidate assertions passed, once finished
    candidate_fail: int = 0  # candidate assertions failed, once finished
    drift: str = ""  # the first drifted field path, once finished (else "")
    ok: bool = True  # the cell's verdict once finished — no error, assertions hold, no drift


def _tally(results: list[AssertionResult]) -> tuple[int, int, int]:
    """Count (passed, failed, warned) over a side's assertion results."""
    passed = failed = warned = 0
    for result in results:
        if result.ok:
            passed += 1
        elif result.severity == "warn":
            warned += 1
        else:
            failed += 1
    return passed, failed, warned


def _build_plan(
    project: LoadedProject,
    profile: ExecutionProfile,
    *,
    do_assert: bool,
    scopes: dict[str, MatrixScope],
) -> tuple[list[tuple[Request, MatrixCell, list[AssertionRule]]], list[Request]]:
    """Expand the profile's selected requests into a flat cell plan.

    Returns the ``(plan, empty)`` pair: one entry per ``(request, matrix cell)`` to
    run, and the requests whose matrix expanded to zero cells — recorded so the
    gate fails closed on them rather than silently skipping.
    """
    plan: list[tuple[Request, MatrixCell, list[AssertionRule]]] = []
    empty: list[Request] = []
    for request in select_requests(project, profile):
        rules = _assert_rules(project, profile, request) if do_assert else []
        cells = expand(project, request, scopes)
        if not cells:
            empty.append(request)
            continue
        for cell in cells:
            plan.append((request, cell, rules))
    return plan, empty


async def run_execution(
    project: LoadedProject,
    profile: ExecutionProfile,
    client: HttpClient,
    candidate_client: HttpClient | None = None,
    *,
    on_progress: Callable[[ExecutionProgress], None] | None = None,
) -> ExecutionResult:
    """Resolve *profile* to a plan and run it, asserting both envs and diffing.

    Args:
        project: The loaded project.
        profile: The execution profile to run.
        client: The transport for the baseline environment.
        candidate_client: A separate transport for the candidate, so the two do
            not share a cookie jar; defaults to *client* when omitted.
        on_progress: An optional callback fired before (``done=False``) and after
            (``done=True``) each cell executes, so a UI can show a live transition.

    Returns:
        The complete execution outcome.

    Raises:
        EnvironmentSelectionError: If the baseline (or a named candidate) is unknown.
    """
    baseline, candidate = _environments(project, profile)
    cand_client = candidate_client or client
    check = profile.spec.check
    do_assert = check.assertions if check is not None else True
    do_diff = (check.diff if check is not None else True) and candidate is not None
    scopes = profile.spec.matrix or {}
    diff_override = _execution_profiles(profile, "diff")
    # Expand the whole plan first so the total is known before the first request.
    plan, empty = _build_plan(project, profile, do_assert=do_assert, scopes=scopes)
    total = len(plan)
    outcomes: list[CellOutcome] = [
        CellOutcome(
            request_id=request.metadata.id or request.metadata.name,
            cell_key="",
            baseline_assertions=[],
            candidate_assertions=[],
            diff=None,
            error="request selected but its matrix expanded to zero cells",
        )
        for request in empty
    ]
    concurrency, retry = run_settings(project)
    limit = asyncio.Semaphore(concurrency)
    # Seed a queued tick for every plan cell before any request goes out, so a UI
    # can render the whole plan as a table up front (queued → running → done).
    if on_progress is not None:
        for index, (request, cell, _rules) in enumerate(plan):
            on_progress(
                ExecutionProgress(
                    request.metadata.id or request.metadata.name,
                    cell.key,
                    index,
                    total,
                    done=False,
                    started=False,
                    method=request.spec.request.method,
                    path=request.spec.request.endpoint,
                )
            )

    async def _run_cell(
        index: int, request: Request, cell: MatrixCell, rules: list[AssertionRule]
    ) -> tuple[int, CellOutcome]:
        request_id = request.metadata.id or request.metadata.name
        method = request.spec.request.method
        path = request.spec.request.endpoint
        async with limit:
            if on_progress is not None:
                on_progress(
                    ExecutionProgress(
                        request_id, cell.key, index, total, done=False, method=method, path=path
                    )
                )
            base = await execute_request(project, baseline, request, client, cell, retry)
            cand = (
                await execute_request(project, candidate, request, cand_client, cell, retry)
                if candidate is not None
                else None
            )
            diff = compare_cell(project, base, cand, diff_override) if do_diff else None
            outcome = CellOutcome(
                request_id=request_id,
                cell_key=cell.key,
                baseline_assertions=evaluate_rules(project, rules, base) if do_assert else [],
                candidate_assertions=(
                    evaluate_rules(project, rules, cand) if do_assert and cand is not None else []
                ),
                diff=diff,
                error=base.error or (cand.error if cand is not None else None),
                baseline=base,
                candidate=cand,
            )
            if on_progress is not None:
                base_pass, base_fail, _ = _tally(outcome.baseline_assertions)
                cand_pass, cand_fail, _ = _tally(outcome.candidate_assertions)
                on_progress(
                    ExecutionProgress(
                        request_id,
                        cell.key,
                        index,
                        total,
                        done=True,
                        method=method,
                        path=path,
                        status=base.response.status if base.response is not None else None,
                        candidate_status=(
                            cand.response.status
                            if cand is not None and cand.response is not None
                            else None
                        ),
                        baseline_ms=(
                            round(base.response.elapsed_ms) if base.response is not None else None
                        ),
                        candidate_ms=(
                            round(cand.response.elapsed_ms)
                            if cand is not None and cand.response is not None
                            else None
                        ),
                        baseline_pass=base_pass,
                        baseline_fail=base_fail,
                        candidate_pass=cand_pass,
                        candidate_fail=cand_fail,
                        drift=diff.drifts[0].path if diff is not None and diff.drifts else "",
                        ok=outcome.ok,
                    )
                )
            return index, outcome

    cell_results = await asyncio.gather(
        *(
            _run_cell(index, request, cell, rules)
            for index, (request, cell, rules) in enumerate(plan)
        )
    )
    # Preserve plan order regardless of completion order under concurrency.
    outcomes.extend(outcome for _, outcome in sorted(cell_results, key=lambda item: item[0]))
    return ExecutionResult(
        profile_id=profile.metadata.id or profile.metadata.name,
        baseline=baseline.metadata.name,
        candidate=candidate.metadata.name if candidate is not None else None,
        checked_assertions=do_assert,
        checked_diff=do_diff,
        outcomes=outcomes,
    )


def _environments(
    project: LoadedProject, profile: ExecutionProfile
) -> tuple[Environment, Environment | None]:
    envs = profile.spec.environments
    baseline = select_environment(project, envs.baseline if envs is not None else None)
    candidate = (
        select_environment(project, envs.candidate)
        if envs is not None and envs.candidate is not None
        else None
    )
    return baseline, candidate


def select_requests(project: LoadedProject, profile: ExecutionProfile) -> list[Request]:
    """The requests an ExecutionProfile selects — its ``select`` tags / ids, or all."""
    requests = sorted(
        (obj for obj in project.objects.values() if isinstance(obj, Request)),
        key=lambda request: request.metadata.id or "",
    )
    select = profile.spec.select
    ids = set(select.requests or []) if select is not None else set()
    tags = set(select.tags or []) if select is not None else set()
    if not ids and not tags:
        return requests
    return [
        request
        for request in requests
        if request.metadata.id in ids or (tags & set(request.metadata.tags or []))
    ]


def _assert_rules(
    project: LoadedProject, profile: ExecutionProfile, request: Request
) -> list[AssertionRule]:
    rules = list(request_response_rules(project, request))  # status/schema sugar + response.assert
    rules += profiles_to_rules(project, _execution_profiles(profile, "assert"))
    return _dedupe_rules(rules)


def _dedupe_rules(rules: list[AssertionRule]) -> list[AssertionRule]:
    """Drop identical rules a layered profile can produce twice, keeping order."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[AssertionRule] = []
    for rule in rules:
        signature = (rule.target, rule.op, repr(rule.value), rule.severity)
        if signature not in seen:
            seen.add(signature)
            unique.append(rule)
    return unique


def _execution_profiles(profile: ExecutionProfile, key: str) -> object:
    profiles = profile.spec.profiles
    if profiles is None:
        return None
    return profiles.assert_ if key == "assert" else profiles.diff
