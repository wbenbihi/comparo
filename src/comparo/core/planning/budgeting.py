"""Planning-budget composition, provenance, and pre-effect admission."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budget_origins import DefinitionBudgetOrigin
from comparo.core.domain.budget_origins import InvocationBudgetOrigin
from comparo.core.domain.budget_origins import VersionedDefaultSetRef
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.planning import RunPlanCardinality
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.planning._canonical import object_member
from comparo.core.planning._errors import PlanningError as PlanningProblem
from comparo.core.planning.models import DefinitionHandle

_DEFAULT_LIMITS: Mapping[BudgetResource, int] = {
    BudgetResource.REQUESTS: 100,
    BudgetResource.MATRIX_CELLS: 10_000,
    BudgetResource.TRIALS: 10_000,
    BudgetResource.ENVIRONMENT_EXECUTIONS: 50_000,
    BudgetResource.ATTEMPTS: 150_000,
    BudgetResource.COMPARISONS: 50_000,
    BudgetResource.QUEUED_WORK: 64,
    BudgetResource.CONCURRENCY: 8,
}
_RESOURCE_BY_NAME = {resource.value: resource for resource in _DEFAULT_LIMITS}


def compose_planning_budgets(
    *,
    project_resource: CompiledResource,
    project_defaults: CanonicalObject,
    execution_profile: CompiledResource | None,
    safety_profiles: tuple[CompiledResource | InlineResource, ...],
    invocation: RunInvocation,
    authority: PlanningAuthority,
) -> tuple[ResourceBudget, ...]:
    """Compose ordinary requests and authoritative ceilings in deterministic order."""
    ordinary: list[tuple[DefinitionRef, dict[BudgetResource, int]]] = []
    project_values = parse_budget_map(
        object_member(project_defaults, "budgets"),
        owner=project_resource,
    )
    if project_values:
        ordinary.append((project_resource.definition, project_values))
    if execution_profile is not None:
        profile_spec = execution_profile.spec
        if type(profile_spec) is not CanonicalObject:
            raise PlanningProblem("planning.resource.spec-invalid", resource=execution_profile)
        profile_values = parse_budget_map(
            object_member(profile_spec, "budgets"), owner=execution_profile
        )
        if profile_values:
            ordinary.append((execution_profile.definition, profile_values))

    safety = tuple(_safety_budget_map(profile) for profile in safety_profiles)
    invocation_values = {budget.resource: budget.limit.value for budget in invocation.budgets}
    authority_values = {ceiling.resource: ceiling.limit.value for ceiling in authority.ceilings}
    return tuple(
        _compose_budget(
            resource=resource,
            ordinary=tuple(ordinary),
            invocation_values=invocation_values,
            invocation=invocation,
            safety=safety,
            authority=authority,
            authority_values=authority_values,
        )
        for resource in _DEFAULT_LIMITS
    )


def parse_budget_map(
    value: CanonicalValue | None,
    *,
    owner: CompiledResource,
) -> dict[BudgetResource, int]:
    """Parse the strict static map shared by ordinary and safety planning limits."""
    if value is None:
        return {}
    if type(value) is not JsonObject:
        raise PlanningProblem("planning.budgets.shape-invalid", resource=owner, field="budgets")
    result: dict[BudgetResource, int] = {}
    for member in value.members:
        resource = _RESOURCE_BY_NAME.get(member.name)
        if resource is None:
            raise PlanningProblem(
                "planning.budgets.resource-unsupported", resource=owner, field="budgets"
            )
        if type(member.value) is not JsonInteger or member.value.value < 1:
            raise PlanningProblem("planning.budgets.limit-invalid", resource=owner, field="budgets")
        result[resource] = member.value.value
    return result


def effective_limit(budgets: tuple[ResourceBudget, ...], resource: BudgetResource) -> int:
    """Read one exact effective Count limit from the composed planning budget set."""
    return next(
        budget.effective.value
        for budget in budgets
        if budget.resource is resource and type(budget.effective) is Count
    )


def admit_cardinality(
    budgets: tuple[ResourceBudget, ...],
    cardinality: RunPlanCardinality,
) -> None:
    """Reject exact work counts that exceed any effective planning budget."""
    actual = {
        BudgetResource.REQUESTS: None,
        BudgetResource.MATRIX_CELLS: cardinality.cells.value,
        BudgetResource.TRIALS: cardinality.trials.value,
        BudgetResource.ENVIRONMENT_EXECUTIONS: cardinality.environment_executions.value,
        BudgetResource.ATTEMPTS: cardinality.maximum_attempts.value,
        BudgetResource.COMPARISONS: cardinality.comparisons.value,
    }
    for budget in budgets:
        value = actual.get(budget.resource)
        if value is not None and value > effective_limit(budgets, budget.resource):
            raise PlanningProblem(
                "planning.budget.exceeded",
                occurrences=value,
            )


def admit_request_count(budgets: tuple[ResourceBudget, ...], request_count: int) -> None:
    """Admit the remaining planning dimension whose count is not in RunPlanCardinality."""
    if request_count > effective_limit(budgets, BudgetResource.REQUESTS):
        raise PlanningProblem("planning.budget.exceeded", occurrences=request_count)


def _safety_budget_map(
    profile: CompiledResource | InlineResource,
) -> tuple[DefinitionHandle, dict[BudgetResource, int]]:
    definition: DefinitionHandle
    if type(profile) is InlineResource:
        definition = profile.definition
        spec = profile.spec
    else:
        definition = profile.definition
        if type(profile.spec) is not CanonicalObject:
            raise PlanningProblem("planning.resource.spec-invalid", resource=profile)
        spec = profile.spec
    value = object_member(spec, "budgets")
    if value is None:
        return definition, {}
    if type(profile) is CompiledResource:
        return definition, parse_budget_map(value, owner=profile)
    if type(value) is not JsonObject:
        raise PlanningProblem("planning.budgets.shape-invalid", field="budgets")
    result: dict[BudgetResource, int] = {}
    for member in value.members:
        resource = _RESOURCE_BY_NAME.get(member.name)
        if resource is None or type(member.value) is not JsonInteger or member.value.value < 1:
            raise PlanningProblem("planning.budgets.limit-invalid", field="budgets")
        result[resource] = member.value.value
    return definition, result


def _compose_budget(
    *,
    resource: BudgetResource,
    ordinary: tuple[tuple[DefinitionRef, dict[BudgetResource, int]], ...],
    invocation_values: dict[BudgetResource, int],
    invocation: RunInvocation,
    safety: tuple[tuple[DefinitionHandle, dict[BudgetResource, int]], ...],
    authority: PlanningAuthority,
    authority_values: dict[BudgetResource, int],
) -> ResourceBudget:
    requested = _DEFAULT_LIMITS[resource]
    contributions = [
        BudgetContribution(
            kind=BudgetContributionKind.DEFAULT,
            amount=Count(value=requested),
            origin=DefaultBudgetOrigin(default_set=_default_set()),
        )
    ]
    for definition, values in ordinary:
        if resource in values:
            requested = values[resource]
            contributions.append(
                BudgetContribution(
                    kind=BudgetContributionKind.REQUEST,
                    amount=Count(value=requested),
                    origin=DefinitionBudgetOrigin(definition=definition),
                )
            )
    if resource in invocation_values:
        requested = invocation_values[resource]
        contributions.append(
            BudgetContribution(
                kind=BudgetContributionKind.REQUEST,
                amount=Count(value=requested),
                origin=InvocationBudgetOrigin(invocation_id=invocation.invocation_id),
            )
        )
    ceilings: list[int] = []
    for safety_definition, values in safety:
        if resource in values:
            ceilings.append(values[resource])
            contributions.append(
                BudgetContribution(
                    kind=BudgetContributionKind.CEILING,
                    amount=Count(value=values[resource]),
                    origin=DefinitionBudgetOrigin(definition=safety_definition),
                )
            )
    policy_limit = authority_values[resource]
    ceilings.append(policy_limit)
    contributions.append(
        BudgetContribution(
            kind=BudgetContributionKind.CEILING,
            amount=Count(value=policy_limit),
            origin=DefinitionBudgetOrigin(definition=authority.policy),
        )
    )
    cap = min(ceilings)
    effective = min(requested, cap)
    zero = Count(value=0)
    scheduling = resource in {BudgetResource.QUEUED_WORK, BudgetResource.CONCURRENCY}
    operation = {
        BudgetResource.QUEUED_WORK: "schedule.queue",
        BudgetResource.CONCURRENCY: "schedule.execute",
    }.get(resource, f"admit.{resource.value}")
    return ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.RUN if scheduling else BudgetScopeKind.PLAN,
        stage=BudgetStage.SCHEDULING if scheduling else BudgetStage.PLANNING,
        operation=BudgetOperation(value=operation),
        requested=Count(value=requested),
        effective=Count(value=effective),
        cap=Count(value=cap),
        contributions=tuple(contributions),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=RunScope(run_id=invocation.run_id) if scheduling else None,
    )


def _default_set() -> VersionedDefaultSetRef:
    encoded = json.dumps(
        {resource.value: limit for resource, limit in _DEFAULT_LIMITS.items()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return VersionedDefaultSetRef(
        identifier="comparo.execution",
        version="2",
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value=hashlib.sha256(encoded).hexdigest()),
        ),
    )
