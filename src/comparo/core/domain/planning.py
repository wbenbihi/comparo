"""Compact immutable run recipes with exact admission-control cardinalities."""

from __future__ import annotations

from enum import StrEnum
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import run_scope_of


def _require_resource_kind(
    resource: DefinitionRef,
    *,
    expected: ResourceKind,
    label: str,
) -> None:
    if type(resource) is not DefinitionRef:
        raise TypeError(f"{label} must be a DefinitionRef")
    if resource.identity.kind is not expected:
        raise ValueError(f"{label} must identify {expected.value}")


@final
class EnvironmentPair(msgspec.Struct, frozen=True, kw_only=True):
    """A directed baseline-to-candidate comparison pair."""

    baseline: DefinitionRef
    candidate: DefinitionRef

    def __post_init__(self) -> None:
        """Require two distinct Environment resources."""
        _require_resource_kind(
            self.baseline,
            expected=ResourceKind.ENVIRONMENT,
            label="comparison baseline",
        )
        _require_resource_kind(
            self.candidate,
            expected=ResourceKind.ENVIRONMENT,
            label="comparison candidate",
        )
        if self.baseline.identity == self.candidate.identity:
            raise ValueError("comparison pair requires distinct Environments")


@final
class MatrixAxisPlan(msgspec.Struct, frozen=True, kw_only=True):
    """One declared Matrix and its stable ordered case identities."""

    binding: MatrixBindingId
    matrix: DefinitionRef
    cases: tuple[MatrixCaseId, ...]

    def __post_init__(self) -> None:
        """Require a Matrix definition and non-empty unique cases."""
        if type(self.binding) is not MatrixBindingId:
            raise TypeError("matrix axis binding must be a MatrixBindingId")
        if type(self.matrix) is not DefinitionRef:
            raise TypeError("matrix axis requires a DefinitionRef")
        if self.matrix.identity.kind is not ResourceKind.MATRIX:
            raise ValueError("matrix axis definition must identify Matrix")
        if type(self.cases) is not tuple:
            raise TypeError("matrix cases must be an immutable tuple")
        if not self.cases:
            raise ValueError("matrix axis must contain at least one case")
        if any(type(case) is not MatrixCaseId for case in self.cases):
            raise TypeError("matrix cases must contain MatrixCaseId values")
        if len(set(self.cases)) != len(self.cases):
            raise ValueError("matrix case identities must be unique within an axis")


@final
class MatrixSpace(msgspec.Struct, frozen=True, kw_only=True):
    """A compact Cartesian-product recipe, not an eager tuple of cells."""

    axes: tuple[MatrixAxisPlan, ...]
    cell_count: PositiveCount

    def __post_init__(self) -> None:
        """Verify immutable unique axes and exact product cardinality."""
        if type(self.axes) is not tuple:
            raise TypeError("matrix axes must be an immutable tuple")
        if any(type(axis) is not MatrixAxisPlan for axis in self.axes):
            raise TypeError("matrix axes must contain MatrixAxisPlan values")
        bindings = tuple(axis.binding for axis in self.axes)
        if len(set(bindings)) != len(bindings):
            raise ValueError("matrix axes must have unique attachment bindings")
        if type(self.cell_count) is not PositiveCount:
            raise TypeError("matrix cell count must be a PositiveCount")
        expected = 1
        for axis in self.axes:
            expected *= len(axis.cases)
        if self.cell_count.value != expected:
            raise ValueError("matrix cell count must equal the exact axis product")


@final
class MatrixCaseCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """One selected case qualified by the Matrix definition that owns its identity."""

    binding: MatrixBindingId
    matrix: DefinitionRef
    case_id: MatrixCaseId

    def __post_init__(self) -> None:
        """Prevent equal case slugs from different Matrices becoming ambiguous."""
        if type(self.binding) is not MatrixBindingId:
            raise TypeError("matrix case coordinate binding must be a MatrixBindingId")
        if type(self.matrix) is not DefinitionRef:
            raise TypeError("matrix case coordinate requires a DefinitionRef")
        if self.matrix.identity.kind is not ResourceKind.MATRIX:
            raise ValueError("matrix case coordinate definition must identify Matrix")
        if type(self.case_id) is not MatrixCaseId:
            raise TypeError("matrix case coordinate requires a MatrixCaseId")


@final
class CellRecipe(msgspec.Struct, frozen=True, kw_only=True):
    """The definition and Matrix space from which plan cells are lazily generated."""

    subject: DefinitionRef
    matrix: MatrixSpace

    def __post_init__(self) -> None:
        """Require an executable Request or future Scenario definition."""
        if type(self.subject) is not DefinitionRef:
            raise TypeError("cell recipe subject must be a DefinitionRef")
        if self.subject.identity.kind not in {ResourceKind.REQUEST, ResourceKind.SCENARIO}:
            raise ValueError("cell recipe subject must identify Request or Scenario")
        if type(self.matrix) is not MatrixSpace:
            raise TypeError("cell recipe matrix must be a MatrixSpace")


@final
class PlannedCell(msgspec.Struct, frozen=True, kw_only=True):
    """One lazily yielded logical cell, independent from trials and attempts."""

    cell_id: CellId
    recipe: CellRecipe
    matrix_cases: tuple[MatrixCaseCoordinate, ...]

    def __post_init__(self) -> None:
        """Require a typed cell occurrence and immutable case coordinates."""
        if type(self.cell_id) is not CellId:
            raise TypeError("planned cell requires a CellId")
        if type(self.recipe) is not CellRecipe:
            raise TypeError("planned cell recipe must be a CellRecipe")
        if type(self.matrix_cases) is not tuple:
            raise TypeError("planned cell cases must be an immutable tuple")
        if any(type(case) is not MatrixCaseCoordinate for case in self.matrix_cases):
            raise TypeError("planned cell cases must contain MatrixCaseCoordinate values")
        if len(self.matrix_cases) != len(self.recipe.matrix.axes):
            raise ValueError("planned cell must select exactly one case for every Matrix binding")
        for axis, coordinate in zip(self.recipe.matrix.axes, self.matrix_cases, strict=True):
            if coordinate.binding != axis.binding or coordinate.matrix != axis.matrix:
                raise ValueError("planned cell coordinates must match the recipe's ordered axes")
            if coordinate.case_id not in axis.cases:
                raise ValueError("planned cell case must be declared by its Matrix axis")

    @property
    def subject(self) -> DefinitionRef:
        """Return the exact executable definition carried by the cell recipe."""
        return self.recipe.subject


@final
class PlanSliceCardinality(msgspec.Struct, frozen=True, kw_only=True):
    """Exact logical work counts for one compact plan slice."""

    cells: Count
    trials: Count
    environment_executions: Count
    comparisons: Count
    maximum_attempts: Count

    def __post_init__(self) -> None:
        """Require exact typed counts."""
        for amount in (
            self.cells,
            self.trials,
            self.environment_executions,
            self.comparisons,
            self.maximum_attempts,
        ):
            if type(amount) is not Count:
                raise TypeError("plan slice cardinalities must be Count values")


@final
class PlanSlice(msgspec.Struct, frozen=True, kw_only=True):
    """A compact execution recipe for one subject and Matrix space."""

    cells: CellRecipe
    trial_count: PositiveCount
    environments: tuple[DefinitionRef, ...]
    comparisons: tuple[EnvironmentPair, ...]
    maximum_attempts_per_execution: PositiveCount
    cardinality: PlanSliceCardinality

    def __post_init__(self) -> None:
        """Validate topology and recompute every admission-control count."""
        if type(self.cells) is not CellRecipe:
            raise TypeError("plan slice cells must be a CellRecipe")
        if type(self.trial_count) is not PositiveCount:
            raise TypeError("plan slice trial count must be a PositiveCount")
        _validate_environments(self.environments)
        _validate_pairs(self.comparisons, self.environments)
        if type(self.maximum_attempts_per_execution) is not PositiveCount:
            raise TypeError("maximum attempts per execution must be a PositiveCount")
        if type(self.cardinality) is not PlanSliceCardinality:
            raise TypeError("plan slice cardinality must be a PlanSliceCardinality")
        expected = _slice_cardinality(
            cells=self.cells.matrix.cell_count.value,
            trials=self.trial_count.value,
            environments=len(self.environments),
            comparisons=len(self.comparisons),
            attempts=self.maximum_attempts_per_execution.value,
        )
        if self.cardinality != expected:
            raise ValueError("plan slice cardinality does not match its compact recipe")


def _validate_environments(environments: tuple[DefinitionRef, ...]) -> None:
    if type(environments) is not tuple:
        raise TypeError("selected Environments must be an immutable tuple")
    if not environments:
        raise ValueError("a plan slice requires at least one Environment")
    for environment in environments:
        _require_resource_kind(
            environment,
            expected=ResourceKind.ENVIRONMENT,
            label="selected Environment",
        )
    identities = tuple(environment.identity for environment in environments)
    if len(set(identities)) != len(identities):
        raise ValueError("selected Environment identities must be unique")


def _validate_pairs(
    pairs: tuple[EnvironmentPair, ...],
    environments: tuple[DefinitionRef, ...],
) -> None:
    if type(pairs) is not tuple:
        raise TypeError("comparison pairs must be an immutable tuple")
    if any(type(pair) is not EnvironmentPair for pair in pairs):
        raise TypeError("comparisons must contain EnvironmentPair values")
    if len(set(pairs)) != len(pairs):
        raise ValueError("comparison pairs must be unique")
    if any(
        pair.baseline not in environments or pair.candidate not in environments for pair in pairs
    ):
        raise ValueError("comparison pair Environments must be selected by the plan slice")


def _slice_cardinality(
    *,
    cells: int,
    trials: int,
    environments: int,
    comparisons: int,
    attempts: int,
) -> PlanSliceCardinality:
    sampled_cells = cells * trials
    environment_executions = sampled_cells * environments
    return PlanSliceCardinality(
        cells=Count(value=cells),
        trials=Count(value=sampled_cells),
        environment_executions=Count(value=environment_executions),
        comparisons=Count(value=sampled_cells * comparisons),
        maximum_attempts=Count(value=environment_executions * attempts),
    )


@final
class RunPlanCardinality(msgspec.Struct, frozen=True, kw_only=True):
    """Exact total logical work admitted for a complete run plan."""

    cells: Count
    trials: Count
    environment_executions: Count
    comparisons: Count
    maximum_attempts: Count

    def __post_init__(self) -> None:
        """Require exact typed total counts."""
        for amount in (
            self.cells,
            self.trials,
            self.environment_executions,
            self.comparisons,
            self.maximum_attempts,
        ):
            if type(amount) is not Count:
                raise TypeError("run plan cardinalities must be Count values")


class PlanOrdering(StrEnum):
    """Versioned deterministic lazy-enumeration ordering."""

    SUBJECT_MATRIX_TRIAL_ENVIRONMENT = "subject-matrix-trial-environment/v1"


@final
class RunPlan(msgspec.Struct, frozen=True, kw_only=True):
    """A fingerprinted compact run recipe accepted before any external effect."""

    run_id: RunId
    slices: tuple[PlanSlice, ...]
    budgets: tuple[ResourceBudget, ...]
    ordering: PlanOrdering
    cardinality: RunPlanCardinality
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        """Require immutable slices and exact aggregate cardinality."""
        if type(self.run_id) is not RunId:
            raise TypeError("run plan requires a RunId")
        _validate_plan_collections(self.slices, self.budgets)
        if type(self.ordering) is not PlanOrdering:
            raise TypeError("run plan ordering must be a PlanOrdering")
        if type(self.cardinality) is not RunPlanCardinality:
            raise TypeError("run plan cardinality must be a RunPlanCardinality")
        if self.cardinality != _total_cardinality(self.slices):
            raise ValueError("run plan cardinality does not equal its slice totals")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("run plan fingerprint must be a Fingerprint")
        _validate_plan_budgets(self)


def _validate_plan_collections(
    slices: tuple[PlanSlice, ...],
    budgets: tuple[ResourceBudget, ...],
) -> None:
    """Require immutable typed plan slices and effective budget records."""
    if type(slices) is not tuple:
        raise TypeError("run plan slices must be an immutable tuple")
    if not slices:
        raise ValueError("run plan requires at least one slice")
    if any(type(plan_slice) is not PlanSlice for plan_slice in slices):
        raise TypeError("run plan slices must contain PlanSlice values")
    if type(budgets) is not tuple:
        raise TypeError("run plan budgets must be an immutable tuple")
    if not budgets:
        raise ValueError("run plan requires finite effective budgets")
    if any(type(budget) is not ResourceBudget for budget in budgets):
        raise TypeError("run plan budgets must contain ResourceBudget values")
    keys = tuple(
        (budget.resource, budget.scope_kind, budget.stage, budget.operation, budget.scope)
        for budget in budgets
    )
    if len(set(keys)) != len(keys):
        raise ValueError("run plan budgets must have unique enforcement coordinates")


def _validate_plan_budgets(plan: RunPlan) -> None:
    """Require unused admission snapshots and sufficient planning dimensions."""
    if any(item.cells.subject.identity.kind is ResourceKind.SCENARIO for item in plan.slices):
        raise ValueError(
            "Scenario slices cannot be admitted before scenario-step cardinality is defined"
        )
    for budget in plan.budgets:
        if not _usage_is_zero(budget):
            raise ValueError("run plan budgets must be unused admission snapshots")
        if budget.scope is not None and run_scope_of(budget.scope).run_id != plan.run_id:
            raise ValueError("run-scoped plan budget must belong to the planned run")

    required = {
        BudgetResource.REQUESTS: Count(value=len(plan.slices)),
        BudgetResource.MATRIX_CELLS: plan.cardinality.cells,
        BudgetResource.TRIALS: plan.cardinality.trials,
        BudgetResource.ENVIRONMENT_EXECUTIONS: plan.cardinality.environment_executions,
        BudgetResource.ATTEMPTS: plan.cardinality.maximum_attempts,
        BudgetResource.COMPARISONS: plan.cardinality.comparisons,
    }
    for resource, cardinality in required.items():
        matches = tuple(
            budget
            for budget in plan.budgets
            if budget.resource is resource
            and budget.scope_kind is BudgetScopeKind.PLAN
            and budget.stage is BudgetStage.PLANNING
        )
        if len(matches) != 1:
            raise ValueError("run plan requires one planning admission budget per dimension")
        effective = cast("Count", matches[0].effective)
        if cardinality.value > effective.value:
            raise ValueError("run plan cardinality exceeds an effective planning budget")


def _usage_is_zero(budget: ResourceBudget) -> bool:
    amounts = (budget.usage.consumed, budget.usage.in_use, budget.usage.high_water)
    for amount in amounts:
        if type(amount) in {Count, ByteCount} and cast("Count | ByteCount", amount).value != 0:
            return False
        if type(amount) is Duration and amount.nanoseconds != 0:
            return False
        if type(amount) is Ratio and amount.numerator != 0:
            return False
    return True


def _total_cardinality(slices: tuple[PlanSlice, ...]) -> RunPlanCardinality:
    return RunPlanCardinality(
        cells=Count(value=sum(item.cardinality.cells.value for item in slices)),
        trials=Count(value=sum(item.cardinality.trials.value for item in slices)),
        environment_executions=Count(
            value=sum(item.cardinality.environment_executions.value for item in slices)
        ),
        comparisons=Count(value=sum(item.cardinality.comparisons.value for item in slices)),
        maximum_attempts=Count(
            value=sum(item.cardinality.maximum_attempts.value for item in slices)
        ),
    )
