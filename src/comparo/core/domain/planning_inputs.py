"""Typed, value-unresolved inputs supplied to deterministic run planning."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import ScalarOverride
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.primitives import PositiveCount

_PLANNING_RESOURCES = frozenset(
    {
        BudgetResource.REQUESTS,
        BudgetResource.MATRIX_CELLS,
        BudgetResource.TRIALS,
        BudgetResource.ENVIRONMENT_EXECUTIONS,
        BudgetResource.ATTEMPTS,
        BudgetResource.COMPARISONS,
    }
)


def _default_resource_edit() -> CollectionOverridePlan[ResourceId, ResourceId]:
    return CollectionOverridePlan(base=KeepBase())


@final
class MatrixSelection(msgspec.Struct, frozen=True, kw_only=True):
    """Select one Matrix under an explicit local binding alias."""

    binding: MatrixBindingId
    matrix: ResourceId

    def __post_init__(self) -> None:
        """Require stable typed selection coordinates."""
        if type(self.binding) is not MatrixBindingId:
            raise TypeError("matrix selection binding must be a MatrixBindingId")
        if type(self.matrix) is not ResourceId:
            raise TypeError("matrix selection resource must be a ResourceId")


@final
class InheritTopology(msgspec.Struct, frozen=True, kw_only=True):
    """Retain the least-specific complete execution topology."""


@final
class ComparisonTopology(msgspec.Struct, frozen=True, kw_only=True):
    """Replace topology with one baseline and one or more candidates."""

    baseline: ResourceId
    candidates: tuple[ResourceId, ...]

    def __post_init__(self) -> None:
        """Require a non-empty, unique, baseline-distinct candidate list."""
        if type(self.baseline) is not ResourceId:
            raise TypeError("comparison baseline must be a ResourceId")
        _validate_resource_ids(self.candidates, label="comparison candidates")
        if not self.candidates:
            raise ValueError("comparison topology requires at least one candidate")
        if self.baseline in self.candidates:
            raise ValueError("comparison baseline cannot also be a candidate")


@final
class ComparisonTopologyEdit(msgspec.Struct, frozen=True, kw_only=True):
    """Edit an inherited comparison topology without an untyped collection merge."""

    baseline: ScalarOverride[ResourceId] = Inherit()
    candidates: CollectionOverridePlan[ResourceId, ResourceId] = _default_resource_edit()

    def __post_init__(self) -> None:
        """Require typed scalar and keyed collection operations."""
        if type(self.baseline) not in {Inherit, Clear} and not (
            type(self.baseline) is SetValue and type(self.baseline.value) is ResourceId
        ):
            raise TypeError("comparison baseline edit must inherit, clear, or set a ResourceId")
        _validate_resource_edit(self.candidates, label="comparison candidate")


@final
class AssertionTopology(msgspec.Struct, frozen=True, kw_only=True):
    """Replace topology with one or more assertion-only Environments."""

    environments: tuple[ResourceId, ...]

    def __post_init__(self) -> None:
        """Require a non-empty unique Environment selection."""
        _validate_resource_ids(self.environments, label="assertion Environments")
        if not self.environments:
            raise ValueError("assertion topology requires at least one Environment")


type TopologyOverride = (
    InheritTopology | ComparisonTopology | ComparisonTopologyEdit | AssertionTopology
)


@final
class PlanningBudgetValue(msgspec.Struct, frozen=True, kw_only=True):
    """One finite planning-dimension request or authoritative ceiling."""

    resource: BudgetResource
    limit: PositiveCount

    def __post_init__(self) -> None:
        """Restrict this planning surface to RunPlan admission dimensions."""
        if type(self.resource) is not BudgetResource:
            raise TypeError("planning budget resource must be a BudgetResource")
        if self.resource not in _PLANNING_RESOURCES:
            raise ValueError("planning budget resource is not admitted by RunPlan")
        if type(self.limit) is not PositiveCount:
            raise TypeError("planning budget limit must be a PositiveCount")


@final
class InvocationValueOverlay(msgspec.Struct, frozen=True, kw_only=True):
    """Unresolved invocation bindings, optionally scoped to one Environment."""

    environment: ResourceId | None = None
    parameters: ValueExpression | None = None
    variables: ValueExpression | None = None
    secrets: ValueExpression | None = None

    def __post_init__(self) -> None:
        """Retain only typed expressions without resolving or rendering them."""
        if self.environment is not None and type(self.environment) is not ResourceId:
            raise TypeError("value overlay Environment must be a ResourceId")
        for expression in (self.parameters, self.variables, self.secrets):
            if expression is not None and not is_value_expression(expression):
                raise TypeError("value overlay entries must be ValueExpression values")
        if self.parameters is self.variables is self.secrets is None:
            raise ValueError("value overlay must define at least one namespace")


def _default_matrix_edit() -> CollectionOverridePlan[MatrixBindingId, MatrixSelection]:
    return CollectionOverridePlan(base=KeepBase())


@final
class RunInvocation(msgspec.Struct, frozen=True, kw_only=True):
    """One deliberate CLI, TUI, or CI planning invocation."""

    invocation_id: InvocationId
    run_id: RunId
    execution_profile: ResourceId | None = None
    subjects: CollectionOverridePlan[ResourceId, ResourceId] = _default_resource_edit()
    topology: TopologyOverride = InheritTopology()
    matrices: CollectionOverridePlan[MatrixBindingId, MatrixSelection] = _default_matrix_edit()
    trials: ScalarOverride[PositiveCount] = Inherit()
    maximum_attempts: ScalarOverride[PositiveCount] = Inherit()
    budgets: tuple[PlanningBudgetValue, ...] = ()
    values: tuple[InvocationValueOverlay, ...] = ()

    def __post_init__(self) -> None:
        """Validate the closed override surface without consulting external state."""
        if type(self.invocation_id) is not InvocationId:
            raise TypeError("run invocation requires an InvocationId")
        if type(self.run_id) is not RunId:
            raise TypeError("run invocation requires a RunId")
        if self.execution_profile is not None and type(self.execution_profile) is not ResourceId:
            raise TypeError("execution profile selection must be a ResourceId")
        _validate_resource_edit(self.subjects, label="subject")
        if type(self.topology) not in {
            InheritTopology,
            ComparisonTopology,
            ComparisonTopologyEdit,
            AssertionTopology,
        }:
            raise TypeError("run invocation topology must be a typed topology override")
        _validate_matrix_edit(self.matrices)
        _validate_count_overrides(self.trials, self.maximum_attempts)
        _validate_invocation_budgets(self.budgets)
        _validate_invocation_values(self.values)


def _validate_count_overrides(*overrides: ScalarOverride[PositiveCount]) -> None:
    for override in overrides:
        if type(override) not in {Inherit, Clear} and not (
            type(override) is SetValue and type(override.value) is PositiveCount
        ):
            raise TypeError("count overrides must inherit, clear, or set a PositiveCount")


def _validate_invocation_budgets(budgets: tuple[PlanningBudgetValue, ...]) -> None:
    if type(budgets) is not tuple or any(
        type(budget) is not PlanningBudgetValue for budget in budgets
    ):
        raise TypeError("invocation budgets must be a typed immutable tuple")
    resources = tuple(budget.resource for budget in budgets)
    if len(resources) != len(set(resources)):
        raise ValueError("invocation planning budget resources must be unique")


def _validate_invocation_values(values: tuple[InvocationValueOverlay, ...]) -> None:
    if type(values) is not tuple or any(
        type(overlay) is not InvocationValueOverlay for overlay in values
    ):
        raise TypeError("invocation values must be a typed immutable tuple")
    environments = tuple(overlay.environment for overlay in values)
    if len(environments) != len(set(environments)):
        raise ValueError("invocation value overlays must have unique Environment scopes")


@final
class PlanningAuthority(msgspec.Struct, frozen=True, kw_only=True):
    """Operator-owned authority applicable before any effect or value resolution."""

    policy: DefinitionRef
    allowed_subjects: tuple[ResourceIdentity, ...] | None = None
    allowed_environments: tuple[ResourceIdentity, ...] | None = None
    ceilings: tuple[PlanningBudgetValue, ...] = ()

    def __post_init__(self) -> None:
        """Bind allowlists and ceilings to one exact ExecutionPolicy revision."""
        if type(self.policy) is not DefinitionRef:
            raise TypeError("planning authority policy must be a DefinitionRef")
        if self.policy.identity.kind is not ResourceKind.EXECUTION_POLICY:
            raise ValueError("planning authority must identify an ExecutionPolicy")
        _validate_allowlist(self.allowed_subjects, subject=True)
        _validate_allowlist(self.allowed_environments, subject=False)
        if type(self.ceilings) is not tuple or any(
            type(ceiling) is not PlanningBudgetValue for ceiling in self.ceilings
        ):
            raise TypeError("planning authority ceilings must be a typed immutable tuple")
        resources = tuple(ceiling.resource for ceiling in self.ceilings)
        if len(resources) != len(set(resources)):
            raise ValueError("planning authority ceiling resources must be unique")
        if frozenset(resources) != _PLANNING_RESOURCES:
            raise ValueError("planning authority requires every RunPlan admission ceiling")


def _validate_resource_ids(values: tuple[ResourceId, ...], *, label: str) -> None:
    if type(values) is not tuple or any(type(value) is not ResourceId for value in values):
        raise TypeError(f"{label} must be an immutable ResourceId tuple")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _validate_resource_edit(edit: object, *, label: str) -> None:
    if type(edit) is not CollectionOverridePlan:
        raise TypeError(f"{label} selection must be a CollectionOverridePlan")
    base_items = edit.base.items if type(edit.base) is ReplaceBase else ()
    for item in (
        *base_items,
        *edit.remove,
        *(operation.identity for operation in edit.set_items),
        *edit.add,
    ):
        if type(item) is not ResourceId:
            raise TypeError(f"{label} selection identities must be ResourceId values")
    for operation in edit.set_items:
        if type(operation.item) is not ResourceId:
            raise TypeError(f"{label} set values must be ResourceId values")


def _validate_matrix_edit(edit: object) -> None:
    if type(edit) is not CollectionOverridePlan:
        raise TypeError("Matrix selection must be a CollectionOverridePlan")
    for binding in (*edit.remove, *(operation.identity for operation in edit.set_items)):
        if type(binding) is not MatrixBindingId:
            raise TypeError("Matrix selection identities must be MatrixBindingId values")
    base_items = edit.base.items if type(edit.base) is ReplaceBase else ()
    for item in (*base_items, *edit.add, *(operation.item for operation in edit.set_items)):
        if type(item) is not MatrixSelection:
            raise TypeError("Matrix selection values must be MatrixSelection values")


def _validate_allowlist(
    identities: tuple[ResourceIdentity, ...] | None,
    *,
    subject: bool,
) -> None:
    if identities is None:
        return
    if type(identities) is not tuple or any(
        type(identity) is not ResourceIdentity for identity in identities
    ):
        raise TypeError("planning authority allowlists must be typed immutable tuples")
    if len(identities) != len(set(identities)):
        raise ValueError("planning authority allowlist identities must be unique")
    allowed = (
        {ResourceKind.REQUEST, ResourceKind.SCENARIO} if subject else {ResourceKind.ENVIRONMENT}
    )
    if any(identity.kind not in allowed for identity in identities):
        raise ValueError("planning authority allowlist contains an invalid resource kind")
