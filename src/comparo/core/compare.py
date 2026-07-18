"""Run a diff pair: execute every request-cell against both environments and diff.

The baseline and candidate runs happen concurrently; results are paired by
(request id, matrix cell) and diffed under each request's profile.
"""

import asyncio
import dataclasses
import json

from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.diff import diff
from comparo.core.execute import Execution
from comparo.core.execute import execute_all
from comparo.core.http import HttpClient
from comparo.core.loader import LoadedProject
from comparo.core.models import DiffProfile
from comparo.core.models import DiffProfileSpec
from comparo.core.models import DiffRule
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.refs import ref_id as _ref_id
from comparo.core.refs import resolve_specs


@dataclasses.dataclass(frozen=True, slots=True)
class CellDiff:
    """The diff outcome for one request cell across the environment pair.

    ``baseline_body`` and ``candidate_body`` carry the parsed response trees so a
    front-end can render a git-style body diff; they are ``None`` for error or
    non-JSON cells. They are not part of the serialized report.
    """

    request: Request
    cell_key: str
    fields: list[FieldDiff]
    error: str | None = None
    baseline_body: object = None
    candidate_body: object = None
    #: Baseline response metadata, for a saved-run replay's detail tree. Not diffed.
    status: int | None = None
    latency_ms: int | None = None
    size_bytes: int | None = None
    response_headers: tuple[tuple[str, str], ...] = ()

    @property
    def drifted(self) -> bool:
        """Whether any compared field differs."""
        return any(field.state is State.DRIFT for field in self.fields)

    @property
    def skipped(self) -> int:
        """How many fields the profile deliberately did not compare."""
        return sum(1 for field in self.fields if field.state is State.SKIP)

    @property
    def drifts(self) -> list[FieldDiff]:
        """The fields that drifted."""
        return [field for field in self.fields if field.state is State.DRIFT]


async def diff_run(
    project: LoadedProject,
    baseline: Environment,
    candidate: Environment,
    requests: list[Request],
    client: HttpClient,
    candidate_client: HttpClient | None = None,
) -> list[CellDiff]:
    """Execute *requests* against both environments and diff the paired results.

    Args:
        project: The loaded project.
        baseline: The baseline environment.
        candidate: The candidate environment.
        requests: The requests to run and diff.
        client: The transport for the baseline run.
        candidate_client: A separate transport for the candidate run, so the two
            do not share a cookie jar; defaults to *client* when omitted.

    Returns:
        One :class:`CellDiff` per baseline request cell.
    """
    baseline_runs, candidate_runs = await asyncio.gather(
        execute_all(project, baseline, requests, client),
        execute_all(project, candidate, requests, candidate_client or client),
    )
    index = {(run.request.metadata.id, run.cell_key): run for run in candidate_runs}
    return [
        _compare(project, run, index.get((run.request.metadata.id, run.cell_key)))
        for run in baseline_runs
    ]


def compare_cell(
    project: LoadedProject,
    baseline: Execution,
    candidate: Execution | None,
    diff_override: object = None,
) -> CellDiff:
    """Diff one already-executed cell (baseline vs candidate) under its profile.

    Args:
        project: The loaded project (for the request's diff profile).
        baseline: The baseline execution.
        candidate: The candidate execution, or ``None`` if it is missing.
        diff_override: An execution-level diff profile ($ref/inline/list) that
            composes on top of the request/project profile, or ``None``.

    Returns:
        The cell's diff outcome.
    """
    return _compare(project, baseline, candidate, diff_override=diff_override)


def _compare(
    project: LoadedProject,
    baseline: Execution,
    candidate: Execution | None,
    diff_override: object = None,
) -> CellDiff:
    request, key = baseline.request, baseline.cell_key
    if candidate is None:
        return CellDiff(request, key, [], "no candidate result")
    if baseline.error is not None:
        return CellDiff(request, key, [], f"baseline: {baseline.error}")
    if candidate.error is not None:
        return CellDiff(request, key, [], f"candidate: {candidate.error}")
    baseline_response, candidate_response = baseline.response, candidate.response
    if baseline_response is None or candidate_response is None:
        return CellDiff(request, key, [], "missing response")
    default_mode, rules = _compose_diff(project, request, diff_override)
    # A ``$status`` rule (matched by its literal path, never through the JSON-path
    # compiler, so it can't collide with a body field ``$.status``) governs the
    # synthetic status comparison; the rest apply to the body.
    status_rules = [rule for rule in rules if rule.path == "$status"]
    rules = [rule for rule in rules if rule.path != "$status"]
    status = baseline_response.status
    latency = round(baseline_response.elapsed_ms)
    size = len(baseline_response.body)
    headers = tuple(baseline_response.headers)
    status_field = _status_field(baseline_response.status, candidate_response.status, status_rules)
    if baseline_response.events is not None and candidate_response.events is not None:
        # Streamed responses diff as their ordered event sequence, not raw bytes.
        events_a, events_b = baseline_response.events, candidate_response.events
        return CellDiff(
            request,
            key,
            [status_field, *diff(events_a, events_b, default_mode, rules)],
            baseline_body=events_a,
            candidate_body=events_b,
            status=status,
            latency_ms=latency,
            size_bytes=size,
            response_headers=headers,
        )
    try:
        baseline_body = json.loads(baseline_response.body)
        candidate_body = json.loads(candidate_response.body)
    except ValueError:
        # Empty or non-JSON responses (e.g. a status-only check) diff as raw bytes.
        return _raw_compare(
            request,
            key,
            baseline_response.body,
            candidate_response.body,
            status_field,
            status=status,
            latency=latency,
            size=size,
            headers=headers,
        )
    return CellDiff(
        request,
        key,
        [status_field, *diff(baseline_body, candidate_body, default_mode, rules)],
        baseline_body=baseline_body,
        candidate_body=candidate_body,
        status=status,
        latency_ms=latency,
        size_bytes=size,
        response_headers=headers,
    )


def _status_field(baseline: int, candidate: int, rules: list[DiffRule]) -> FieldDiff:
    """Compare HTTP status as a synthetic ``$status`` field, honouring an override.

    A 200→500 with identical bodies is a real regression the body diff can't see,
    so status is always compared; a ``{path: $status, mode: ignore}`` rule (e.g.
    for an endpoint whose status legitimately varies) skips it.
    """
    override = next((rule for rule in rules), None)
    if override is not None and override.mode == "ignore":
        return FieldDiff("$status", State.SKIP, "ignore")
    if baseline == candidate:
        return FieldDiff("$status", State.SAME, "exact")
    return FieldDiff("$status", State.DRIFT, "exact", f"{baseline} → {candidate}")


def _compose_diff(
    project: LoadedProject, request: Request, override: object
) -> tuple[str, list[DiffRule]]:
    specs: list[DiffProfileSpec] = []
    response = request.spec.response
    if response is not None and response.diff is not None:
        specs.extend(resolve_specs(project, response.diff, DiffProfileSpec))
    else:
        specs.extend(_project_default_diff(project))
    if override is not None:
        specs.extend(resolve_specs(project, override, DiffProfileSpec))
    if not specs:
        return "exact", []
    rules: list[DiffRule] = []
    for spec in specs:
        rules.extend(spec.rules or [])
    return specs[-1].default, rules


def _project_default_diff(project: LoadedProject) -> list[DiffProfileSpec]:
    if project.project is None:
        return []
    config = project.project.spec.diff
    if isinstance(config, dict):
        return resolve_specs(project, config.get("default"), DiffProfileSpec)
    return []


def _raw_compare(
    request: Request,
    key: str,
    baseline: bytes,
    candidate: bytes,
    status_field: FieldDiff,
    *,
    status: int,
    latency: int,
    size: int,
    headers: tuple[tuple[str, str], ...],
) -> CellDiff:
    if baseline == candidate:
        body_field = FieldDiff("$", State.SAME, "exact")
    else:
        body_field = FieldDiff("$", State.DRIFT, "exact", "response bodies differ")
    return CellDiff(
        request,
        key,
        [status_field, body_field],
        status=status,
        latency_ms=latency,
        size_bytes=size,
        response_headers=headers,
    )


def profile_for(project: LoadedProject, request: Request) -> DiffProfile | None:
    """Return the diff profile that applies to *request*, if any.

    Args:
        project: The loaded project.
        request: The request whose effective profile is resolved.

    Returns:
        The request's own profile, else the project default, else ``None``.
    """
    return _profile_for(project, request)


def _profile_for(project: LoadedProject, request: Request) -> DiffProfile | None:
    response = request.spec.response
    if response is not None:
        identifier = _ref_id(response.diff)
        profile = project.objects.get(identifier) if identifier is not None else None
        if isinstance(profile, DiffProfile):
            return profile
    if project.project is not None:
        config = project.project.spec.diff
        if isinstance(config, dict):
            identifier = _ref_id(config.get("default"))
            profile = project.objects.get(identifier) if identifier is not None else None
            if isinstance(profile, DiffProfile):
                return profile
    return None
