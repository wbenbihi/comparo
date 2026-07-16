"""Run an ``ExecutionProfile``: assert both environments, then diff them.

The planner resolves a profile to a concrete plan — which requests, which matrix
cells, which environments — then executes each cell against the baseline (and the
candidate, when one is set), runs the request's assertions on each, and diffs the
pair. It only orchestrates the execute / assertion / diff sinks; it holds no
comparison logic of its own.
"""

import dataclasses

from comparo.core.assertions import AssertionResult
from comparo.core.assertions import compose_rules
from comparo.core.assertions import evaluate_rules
from comparo.core.assertions import passed as assertions_pass
from comparo.core.assertions import request_rules
from comparo.core.compare import CellDiff
from comparo.core.compare import compare_cell
from comparo.core.execute import execute_request
from comparo.core.http import HttpClient
from comparo.core.loader import LoadedProject
from comparo.core.matrix import expand
from comparo.core.models import AssertionProfile
from comparo.core.models import AssertionRule
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
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
        """Whether every cell passed — the execution gate."""
        return all(outcome.ok for outcome in self.outcomes)

    @property
    def drift(self) -> int:
        """How many cells drifted."""
        return sum(1 for o in self.outcomes if o.diff is not None and o.diff.drifted)

    @property
    def errors(self) -> int:
        """How many cells failed to execute."""
        return sum(1 for o in self.outcomes if o.error is not None)


async def run_execution(
    project: LoadedProject, profile: ExecutionProfile, client: HttpClient
) -> ExecutionResult:
    """Resolve *profile* to a plan and run it, asserting both envs and diffing.

    Args:
        project: The loaded project.
        profile: The execution profile to run.
        client: The transport the requests are sent through.

    Returns:
        The complete execution outcome.

    Raises:
        EnvironmentSelectionError: If the baseline (or a named candidate) is unknown.
    """
    baseline, candidate = _environments(project, profile)
    check = profile.spec.check
    do_assert = check.assertions if check is not None else True
    do_diff = (check.diff if check is not None else True) and candidate is not None
    scopes = profile.spec.matrix or {}
    outcomes: list[CellOutcome] = []
    for request in _select(project, profile):
        rules = _assert_rules(project, profile, request) if do_assert else []
        for cell in expand(project, request, scopes):
            base = await execute_request(project, baseline, request, client, cell)
            cand = (
                await execute_request(project, candidate, request, client, cell)
                if candidate is not None
                else None
            )
            outcomes.append(
                CellOutcome(
                    request_id=request.metadata.id or request.metadata.name,
                    cell_key=cell.key,
                    baseline_assertions=evaluate_rules(project, rules, base) if do_assert else [],
                    candidate_assertions=(
                        evaluate_rules(project, rules, cand)
                        if do_assert and cand is not None
                        else []
                    ),
                    diff=compare_cell(project, base, cand) if do_diff else None,
                    error=base.error or (cand.error if cand is not None else None),
                )
            )
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


def _select(project: LoadedProject, profile: ExecutionProfile) -> list[Request]:
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
    rules = list(request_rules(request))  # response.status / response.schema sugar
    response = request.spec.response
    rules += _profiles_to_rules(project, response.assertions if response is not None else None)
    rules += _profiles_to_rules(project, _execution_profiles(profile, "assert"))
    return rules


def _profiles_to_rules(project: LoadedProject, refs: object) -> list[AssertionRule]:
    rules: list[AssertionRule] = []
    for reference in _as_list(refs):
        identifier = _ref_id(reference)
        profile = project.objects.get(identifier) if identifier is not None else None
        if isinstance(profile, AssertionProfile):
            rules.extend(compose_rules(project, profile))
    return rules


def _execution_profiles(profile: ExecutionProfile, key: str) -> object:
    profiles = profile.spec.profiles
    return profiles.get(key) if isinstance(profiles, dict) else None


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ref_id(reference: object) -> str | None:
    if isinstance(reference, dict):
        target = reference.get("$ref")
        if isinstance(target, str):
            return target
    return None
