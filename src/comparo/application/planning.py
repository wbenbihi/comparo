"""Typed frontend-neutral construction of deliberate planner invocations."""

from __future__ import annotations

from enum import StrEnum
from typing import final
from uuid import UUID

import msgspec

from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import RunId
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import InheritTopology
from comparo.core.domain.planning_inputs import MatrixSelection
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import PositiveCount
from comparo.core.planning.models import PlanningResult
from comparo.core.planning.planner import plan_run


class InvocationBuildErrorCode(StrEnum):
    """Closed failures while translating a frontend invocation."""

    INVALID_IDENTIFIER = "invalid-identifier"
    DUPLICATE_SELECTION = "duplicate-selection"
    CONTRADICTORY_TOPOLOGY = "contradictory-topology"
    DUPLICATE_BUDGET = "duplicate-budget"
    INVALID_LIMIT = "invalid-limit"


class InvocationBuildError(ValueError):
    """Value-free invocation-construction failure safe across frontend boundaries."""

    __slots__ = ("code",)

    def __init__(self, code: InvocationBuildErrorCode) -> None:
        """Retain only a closed category, never the invalid user token."""
        if type(code) is not InvocationBuildErrorCode:
            raise TypeError("invocation build error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class MatrixOverride(msgspec.Struct, frozen=True, kw_only=True):
    """One explicit Matrix binding supplied by a frontend."""

    binding: str
    matrix: str


@final
class BudgetOverride(msgspec.Struct, frozen=True, kw_only=True):
    """One explicit planning-budget request supplied by a frontend."""

    resource: BudgetResource
    limit: int

    def __post_init__(self) -> None:
        """Keep parsing separate while requiring an exact positive integer."""
        if type(self.resource) is not BudgetResource:
            raise TypeError("budget override resource must be typed")
        if type(self.limit) is not int or self.limit < 1:
            raise InvocationBuildError(InvocationBuildErrorCode.INVALID_LIMIT)


@final
class PlanningOptions(msgspec.Struct, frozen=True, kw_only=True):
    """Primitive planning overrides shared by CLI, TUI, and CI adapters."""

    execution_profile: str | None = None
    subjects: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    baseline: str | None = None
    candidates: tuple[str, ...] = ()
    matrices: tuple[MatrixOverride, ...] = ()
    trials: int | None = None
    maximum_attempts: int | None = None
    fail_fast: bool | None = None
    budgets: tuple[BudgetOverride, ...] = ()

    def __post_init__(self) -> None:
        """Reject ambiguous primitive collections before constructing domain values."""
        _validate_selections(self.subjects, self.environments, self.candidates)
        _validate_matrices(self.matrices)
        _validate_budgets(self.budgets)
        for value in (self.trials, self.maximum_attempts):
            if value is not None and (type(value) is not int or value < 1):
                raise InvocationBuildError(InvocationBuildErrorCode.INVALID_LIMIT)
        if self.fail_fast is not None and type(self.fail_fast) is not bool:
            raise TypeError("planning fail-fast override must be bool or None")
        _validate_topology(self)


def plan_project(
    project: CompiledProject,
    authority: PlanningAuthority,
    options: PlanningOptions,
    *,
    invocation_id: UUID,
    run_id: UUID,
) -> PlanningResult:
    """Construct and run the one shared deterministic planner."""
    return plan_run(
        project,
        build_invocation(options, invocation_id=invocation_id, run_id=run_id),
        authority,
    )


def build_invocation(
    options: PlanningOptions,
    *,
    invocation_id: UUID,
    run_id: UUID,
) -> RunInvocation:
    """Translate primitive frontend choices to the closed planner override model."""
    if type(options) is not PlanningOptions:
        raise TypeError("invocation construction requires PlanningOptions")
    if type(invocation_id) is not UUID or type(run_id) is not UUID:
        raise TypeError("invocation construction requires exact UUID values")
    try:
        return RunInvocation(
            invocation_id=InvocationId(value=invocation_id),
            run_id=RunId(value=run_id),
            execution_profile=_optional_id(options.execution_profile),
            subjects=_resource_selection(options.subjects),
            topology=_topology(options),
            matrices=_matrix_selection(options.matrices),
            trials=_positive_override(options.trials),
            maximum_attempts=_positive_override(options.maximum_attempts),
            fail_fast=Inherit() if options.fail_fast is None else SetValue(value=options.fail_fast),
            budgets=tuple(
                PlanningBudgetValue(
                    resource=item.resource,
                    limit=PositiveCount(value=item.limit),
                )
                for item in options.budgets
            ),
        )
    except (TypeError, ValueError) as error:
        raise InvocationBuildError(InvocationBuildErrorCode.INVALID_IDENTIFIER) from error


def _validate_topology(options: PlanningOptions) -> None:
    comparison_fields = options.baseline is not None or bool(options.candidates)
    if options.environments and comparison_fields:
        raise InvocationBuildError(InvocationBuildErrorCode.CONTRADICTORY_TOPOLOGY)
    if (options.baseline is None) != (not options.candidates):
        raise InvocationBuildError(InvocationBuildErrorCode.CONTRADICTORY_TOPOLOGY)


def _validate_selections(*collections: tuple[str, ...]) -> None:
    for collection in collections:
        if type(collection) is not tuple or any(type(item) is not str for item in collection):
            raise TypeError("planning selections must be immutable text tuples")
        if len(collection) != len(set(collection)):
            raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_SELECTION)


def _validate_matrices(values: tuple[MatrixOverride, ...]) -> None:
    if type(values) is not tuple or any(type(item) is not MatrixOverride for item in values):
        raise TypeError("planning Matrix overrides must be immutable and typed")
    bindings = tuple(item.binding for item in values)
    if len(bindings) != len(set(bindings)):
        raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_SELECTION)


def _validate_budgets(values: tuple[BudgetOverride, ...]) -> None:
    if type(values) is not tuple or any(type(item) is not BudgetOverride for item in values):
        raise TypeError("planning budget overrides must be immutable and typed")
    resources = tuple(item.resource for item in values)
    if len(resources) != len(set(resources)):
        raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_BUDGET)


def _optional_id(value: str | None) -> ResourceId | None:
    return None if value is None else ResourceId(value=value)


def _resource_selection(
    values: tuple[str, ...],
) -> CollectionOverridePlan[ResourceId, ResourceId]:
    if not values:
        return CollectionOverridePlan(base=KeepBase())
    resources = tuple(ResourceId(value=value) for value in values)
    return CollectionOverridePlan(base=ReplaceBase(items=resources))


def _matrix_selection(
    values: tuple[MatrixOverride, ...],
) -> CollectionOverridePlan[MatrixBindingId, MatrixSelection]:
    if not values:
        return CollectionOverridePlan(base=KeepBase())
    matrices = tuple(
        MatrixSelection(
            binding=MatrixBindingId(value=item.binding),
            matrix=ResourceId(value=item.matrix),
        )
        for item in values
    )
    return CollectionOverridePlan(base=ReplaceBase(items=matrices))


def _positive_override(value: int | None) -> Inherit | SetValue[PositiveCount]:
    return Inherit() if value is None else SetValue(value=PositiveCount(value=value))


def _topology(
    options: PlanningOptions,
) -> InheritTopology | AssertionTopology | ComparisonTopology:
    if options.environments:
        return AssertionTopology(
            environments=tuple(ResourceId(value=value) for value in options.environments)
        )
    if options.baseline is not None:
        return ComparisonTopology(
            baseline=ResourceId(value=options.baseline),
            candidates=tuple(ResourceId(value=value) for value in options.candidates),
        )
    return InheritTopology()


__all__ = (
    "BudgetOverride",
    "InvocationBuildError",
    "InvocationBuildErrorCode",
    "MatrixOverride",
    "PlanningOptions",
    "build_invocation",
    "plan_project",
)
