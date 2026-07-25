"""Typed frontend-neutral construction of deliberate planner invocations."""

from __future__ import annotations

from enum import StrEnum
from typing import final
from uuid import UUID

import msgspec

from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.directives import seal_expression_literals
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import InheritTopology
from comparo.core.domain.planning_inputs import InvocationValueOverlay
from comparo.core.domain.planning_inputs import MatrixSelection
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.planning.models import PlanningResult
from comparo.core.planning.planner import plan_run


class InvocationBuildErrorCode(StrEnum):
    """Closed failures while translating a frontend invocation."""

    INVALID_IDENTIFIER = "invalid-identifier"
    DUPLICATE_SELECTION = "duplicate-selection"
    CONTRADICTORY_TOPOLOGY = "contradictory-topology"
    DUPLICATE_BUDGET = "duplicate-budget"
    INVALID_LIMIT = "invalid-limit"
    INVALID_VALUE_NAMESPACE = "invalid-value-namespace"
    INVALID_VALUE_RESOURCE = "invalid-value-resource"
    VALUE_RESOURCE_KIND_INVALID = "value-resource-kind-invalid"
    VALUE_RESOURCE_MISSING = "value-resource-missing"
    VALUE_RESOURCE_OBJECT_REQUIRED = "value-resource-object-required"
    DUPLICATE_VALUE_RESOURCE = "duplicate-value-resource"


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


_INVOCATION_VALUE_NAMESPACES = frozenset(
    {
        ValueNamespace.PARAMETERS,
        ValueNamespace.VARIABLES,
        ValueNamespace.SECRETS,
    }
)


@final
class ValueResourceOverride(msgspec.Struct, frozen=True, kw_only=True):
    """One invocation namespace supplied by a compiled Value resource."""

    namespace: ValueNamespace
    environment: str | None
    resource: str

    def __post_init__(self) -> None:
        """Admit only bounded Value references and optional Environment scopes."""
        if type(self.namespace) is not ValueNamespace:
            raise TypeError("value resource override namespace must be typed")
        if self.namespace not in _INVOCATION_VALUE_NAMESPACES:
            raise InvocationBuildError(InvocationBuildErrorCode.INVALID_VALUE_NAMESPACE)
        if self.environment is not None:
            if type(self.environment) is not str:
                raise TypeError("value resource override Environment must be text or None")
            try:
                ResourceId(value=self.environment)
            except ValueError as error:
                raise InvocationBuildError(
                    InvocationBuildErrorCode.INVALID_VALUE_RESOURCE
                ) from error
        if type(self.resource) is not str:
            raise TypeError("value resource override resource must be text")
        _value_resource_id(self.resource)


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
    value_resources: tuple[ValueResourceOverride, ...] = ()

    def __post_init__(self) -> None:
        """Reject ambiguous primitive collections before constructing domain values."""
        _validate_selections(self.subjects, self.environments, self.candidates)
        _validate_matrices(self.matrices)
        _validate_budgets(self.budgets)
        _validate_value_resources(self.value_resources)
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
        build_invocation(
            options,
            invocation_id=invocation_id,
            run_id=run_id,
            project=project,
        ),
        authority,
    )


def build_invocation(
    options: PlanningOptions,
    *,
    invocation_id: UUID,
    run_id: UUID,
    project: CompiledProject | None = None,
) -> RunInvocation:
    """Translate primitive frontend choices to the closed planner override model."""
    if type(options) is not PlanningOptions:
        raise TypeError("invocation construction requires PlanningOptions")
    if type(invocation_id) is not UUID or type(run_id) is not UUID:
        raise TypeError("invocation construction requires exact UUID values")
    if project is not None and type(project) is not CompiledProject:
        raise TypeError("invocation construction project must be CompiledProject or None")
    if options.value_resources and project is None:
        raise InvocationBuildError(InvocationBuildErrorCode.VALUE_RESOURCE_MISSING)
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
            values=(
                ()
                if project is None
                else _value_resource_overlays(project, options.value_resources)
            ),
        )
    except InvocationBuildError:
        raise
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


def _validate_value_resources(values: tuple[ValueResourceOverride, ...]) -> None:
    if type(values) is not tuple or any(type(item) is not ValueResourceOverride for item in values):
        raise TypeError("planning Value resource overrides must be immutable and typed")
    scopes = tuple((item.namespace, item.environment) for item in values)
    if len(scopes) != len(set(scopes)):
        raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_VALUE_RESOURCE)


def _value_resource_id(reference: str) -> ResourceId:
    if not reference or reference.count("/") > 1:
        raise InvocationBuildError(InvocationBuildErrorCode.INVALID_VALUE_RESOURCE)
    kind, separator, raw_id = reference.partition("/")
    if separator:
        if kind != ResourceKind.VALUE.value:
            raise InvocationBuildError(InvocationBuildErrorCode.VALUE_RESOURCE_KIND_INVALID)
    else:
        raw_id = kind
    try:
        return ResourceId(value=raw_id)
    except ValueError as error:
        raise InvocationBuildError(InvocationBuildErrorCode.INVALID_VALUE_RESOURCE) from error


def _value_resource_expression(
    project: CompiledProject,
    override: ValueResourceOverride,
) -> ObjectExpr:
    resource = project.find(ResourceKind.VALUE, _value_resource_id(override.resource))
    if resource is None:
        raise InvocationBuildError(InvocationBuildErrorCode.VALUE_RESOURCE_MISSING)
    if type(resource.spec) is not CanonicalObject:
        raise InvocationBuildError(InvocationBuildErrorCode.VALUE_RESOURCE_OBJECT_REQUIRED)
    expression = next(
        (member.value for member in resource.spec.members if member.name == "value"),
        None,
    )
    if type(expression) is not ObjectExpr:
        raise InvocationBuildError(InvocationBuildErrorCode.VALUE_RESOURCE_OBJECT_REQUIRED)
    if override.namespace is not ValueNamespace.SECRETS:
        return expression
    sealed = seal_expression_literals(expression)
    if type(sealed) is not ObjectExpr:  # pragma: no cover - recursive shape invariant
        raise TypeError("secret Value object sealing changed the expression root")
    return sealed


def _value_resource_overlays(
    project: CompiledProject,
    values: tuple[ValueResourceOverride, ...],
) -> tuple[InvocationValueOverlay, ...]:
    grouped: dict[ResourceId | None, dict[ValueNamespace, ObjectExpr]] = {}
    for override in values:
        environment = (
            None if override.environment is None else ResourceId(value=override.environment)
        )
        grouped.setdefault(environment, {})[override.namespace] = _value_resource_expression(
            project, override
        )
    scopes = sorted(
        grouped,
        key=lambda environment: (
            environment is not None,
            "" if environment is None else environment.value,
        ),
    )
    return tuple(
        InvocationValueOverlay(
            environment=environment,
            parameters=grouped[environment].get(ValueNamespace.PARAMETERS),
            variables=grouped[environment].get(ValueNamespace.VARIABLES),
            secrets=grouped[environment].get(ValueNamespace.SECRETS),
        )
        for environment in scopes
    )


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
    "BudgetResource",
    "InvocationBuildError",
    "InvocationBuildErrorCode",
    "MatrixOverride",
    "PlanningOptions",
    "ValueNamespace",
    "ValueResourceOverride",
    "build_invocation",
    "plan_project",
)
