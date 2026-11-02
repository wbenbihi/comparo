"""Shared builders for planning-domain tests."""

from decimal import Decimal
from uuid import UUID

from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.planning import CellRecipe
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.planning import MatrixAxisPlan
from comparo.core.domain.planning import MatrixSpace
from comparo.core.domain.planning import PlanOrdering
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning import PlanSliceCardinality
from comparo.core.domain.planning import RunPlan
from comparo.core.domain.planning import RunPlanCardinality
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import BudgetScope
from tests.core.domain.budget_origin_factories import default_budget_origin


def _identity(kind: ResourceKind, value: str) -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=kind,
        resource_id=ResourceId(value=value),
    )


def _definition(kind: ResourceKind, value: str, digest: str = "a") -> DefinitionRef:
    return DefinitionRef(
        identity=_identity(kind, value),
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value=digest * 64),
        ),
    )


def _matrix_space() -> MatrixSpace:
    axis = MatrixAxisPlan(
        binding=MatrixBindingId(value="locale"),
        matrix=_definition(ResourceKind.MATRIX, "locales"),
        cases=(MatrixCaseId(value="en"), MatrixCaseId(value="fr")),
    )
    return MatrixSpace(axes=(axis,), cell_count=PositiveCount(value=2))


def _slice() -> PlanSlice:
    baseline = _definition(ResourceKind.ENVIRONMENT, "staging", digest="c")
    candidate = _definition(ResourceKind.ENVIRONMENT, "prod", digest="d")
    return PlanSlice(
        cells=CellRecipe(
            subject=_definition(ResourceKind.REQUEST, "health", digest="b"),
            matrix=_matrix_space(),
        ),
        trial_count=PositiveCount(value=3),
        environments=(baseline, candidate),
        comparisons=(EnvironmentPair(baseline=baseline, candidate=candidate),),
        maximum_attempts_per_execution=PositiveCount(value=2),
        cardinality=PlanSliceCardinality(
            cells=Count(value=2),
            trials=Count(value=6),
            environment_executions=Count(value=12),
            comparisons=Count(value=6),
            maximum_attempts=Count(value=24),
        ),
    )


def _planning_budget(resource: BudgetResource, limit: int) -> ResourceBudget:
    amount = Count(value=limit)
    return ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.PLAN,
        stage=BudgetStage.PLANNING,
        operation=BudgetOperation(value=f"admit.{resource.value}"),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=amount,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(
            consumed=Count(value=0),
            in_use=Count(value=0),
            high_water=Count(value=0),
        ),
    )


def _plan_budgets(
    cardinality: RunPlanCardinality,
    *,
    requests: int = 1,
) -> tuple[ResourceBudget, ...]:
    return (
        _planning_budget(BudgetResource.REQUESTS, requests),
        _planning_budget(BudgetResource.MATRIX_CELLS, cardinality.cells.value),
        _planning_budget(BudgetResource.TRIALS, cardinality.trials.value),
        _planning_budget(
            BudgetResource.ENVIRONMENT_EXECUTIONS,
            cardinality.environment_executions.value,
        ),
        _planning_budget(BudgetResource.ATTEMPTS, cardinality.maximum_attempts.value),
        _planning_budget(BudgetResource.COMPARISONS, cardinality.comparisons.value),
    )


def _run_plan() -> RunPlan:
    return _run_plan_for_slices(_slice())


def _run_plan_for_slices(*plan_slices: PlanSlice) -> RunPlan:
    cardinality = RunPlanCardinality(
        cells=Count(value=sum(item.cardinality.cells.value for item in plan_slices)),
        trials=Count(value=sum(item.cardinality.trials.value for item in plan_slices)),
        environment_executions=Count(
            value=sum(item.cardinality.environment_executions.value for item in plan_slices)
        ),
        comparisons=Count(value=sum(item.cardinality.comparisons.value for item in plan_slices)),
        maximum_attempts=Count(
            value=sum(item.cardinality.maximum_attempts.value for item in plan_slices)
        ),
    )
    return RunPlan(
        run_id=RunId(value=UUID("12345678-1234-5678-1234-567812345678")),
        slices=plan_slices,
        budgets=_plan_budgets(cardinality, requests=len(plan_slices)),
        ordering=PlanOrdering.SUBJECT_MATRIX_TRIAL_ENVIRONMENT,
        cardinality=cardinality,
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="f" * 64),
        ),
    )


def _extra_budget(
    *,
    resource: BudgetResource,
    scope_kind: BudgetScopeKind,
    stage: BudgetStage,
    amount: Duration | Ratio,
    consumed: Duration | Ratio,
    scope: BudgetScope | None = None,
) -> ResourceBudget:
    zero = Duration(nanoseconds=0) if type(amount) is Duration else Ratio.from_decimal(Decimal(0))
    return ResourceBudget(
        resource=resource,
        scope_kind=scope_kind,
        stage=stage,
        operation=BudgetOperation(value=f"test.{resource.value}"),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=amount,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(consumed=consumed, in_use=zero, high_water=zero),
        scope=scope,
    )
