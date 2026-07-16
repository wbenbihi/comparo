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
from comparo.core.diff import profile_rules
from comparo.core.execute import Execution
from comparo.core.execute import execute_all
from comparo.core.http import HttpClient
from comparo.core.loader import LoadedProject
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import Request


@dataclasses.dataclass(frozen=True, slots=True)
class CellDiff:
    """The diff outcome for one request cell across the environment pair."""

    request: Request
    cell_key: str
    fields: list[FieldDiff]
    error: str | None = None

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
) -> list[CellDiff]:
    """Execute *requests* against both environments and diff the paired results.

    Args:
        project: The loaded project.
        baseline: The baseline environment.
        candidate: The candidate environment.
        requests: The requests to run and diff.
        client: The transport to send through.

    Returns:
        One :class:`CellDiff` per baseline request cell.
    """
    baseline_runs, candidate_runs = await asyncio.gather(
        execute_all(project, baseline, requests, client),
        execute_all(project, candidate, requests, client),
    )
    index = {(run.request.metadata.id, run.cell_key): run for run in candidate_runs}
    return [
        _compare(project, run, index.get((run.request.metadata.id, run.cell_key)))
        for run in baseline_runs
    ]


def _compare(project: LoadedProject, baseline: Execution, candidate: Execution | None) -> CellDiff:
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
    try:
        baseline_body = json.loads(baseline_response.body)
        candidate_body = json.loads(candidate_response.body)
    except ValueError:
        # Empty or non-JSON responses (e.g. a status-only check) diff as raw bytes.
        return _raw_compare(request, key, baseline_response.body, candidate_response.body)
    default_mode, rules = profile_rules(_profile_for(project, request))
    return CellDiff(request, key, diff(baseline_body, candidate_body, default_mode, rules))


def _raw_compare(request: Request, key: str, baseline: bytes, candidate: bytes) -> CellDiff:
    if baseline == candidate:
        return CellDiff(request, key, [FieldDiff("$", State.SAME, "exact")])
    return CellDiff(request, key, [FieldDiff("$", State.DRIFT, "exact", "response bodies differ")])


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


def _ref_id(reference: object) -> str | None:
    if isinstance(reference, dict):
        target = reference.get("$ref")
        if isinstance(target, str):
            return target
    return None
