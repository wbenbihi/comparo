"""Typed frontend-neutral construction of deliberate planner invocations."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import final
from uuid import UUID

import msgspec

from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.directives import seal_expression_literals
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LiteralTextSegment
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import ClearBase
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import Inherit
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import InheritTopology
from comparo.core.domain.planning_inputs import InvocationEnvironmentValue
from comparo.core.domain.planning_inputs import InvocationProfileOverrides
from comparo.core.domain.planning_inputs import InvocationRequestEdit
from comparo.core.domain.planning_inputs import InvocationValueOverlay
from comparo.core.domain.planning_inputs import MatrixSelection
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import JsonString
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
    DUPLICATE_INLINE_VALUE = "duplicate-inline-value"
    DUPLICATE_ENVIRONMENT_VALUE = "duplicate-environment-value"
    DUPLICATE_REQUEST_EDIT = "duplicate-request-edit"
    SECRET_VALUE_UNSEALED = "secret-value-unsealed"  # noqa: S105 - closed diagnostic code


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
    cases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep case filters immutable and reject ambiguous duplicate identities."""
        if type(self.binding) is not str or type(self.matrix) is not str:
            raise TypeError("Matrix override identities must be text")
        if type(self.cases) is not tuple or any(type(item) is not str for item in self.cases):
            raise TypeError("Matrix override cases must be an immutable text tuple")
        if len(self.cases) != len(set(self.cases)):
            raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_SELECTION)


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
class InlineValueOverride(msgspec.Struct, frozen=True, kw_only=True):
    """One already-safe unresolved inline namespace object supplied by a frontend."""

    namespace: ValueNamespace
    expression: ObjectExpr
    environment: str | None = None

    def __post_init__(self) -> None:
        """Require a supported namespace and prevent raw secret literals from retention."""
        if (
            type(self.namespace) is not ValueNamespace
            or self.namespace not in _INVOCATION_VALUE_NAMESPACES
        ):
            raise InvocationBuildError(InvocationBuildErrorCode.INVALID_VALUE_NAMESPACE)
        if type(self.expression) is not ObjectExpr:
            raise TypeError("inline Value override requires an ObjectExpr")
        if self.environment is not None:
            if type(self.environment) is not str:
                raise TypeError("inline Value Environment must be text or absent")
            try:
                ResourceId(value=self.environment)
            except ValueError as error:
                raise InvocationBuildError(
                    InvocationBuildErrorCode.INVALID_VALUE_RESOURCE
                ) from error
        if self.namespace is ValueNamespace.SECRETS and not _sealed_secret_expression(
            self.expression
        ):
            raise InvocationBuildError(InvocationBuildErrorCode.SECRET_VALUE_UNSEALED)


@final
class EnvironmentValueOverride(msgspec.Struct, frozen=True, kw_only=True):
    """One sealed per-Environment `$env` value supplied by a frontend."""

    environment: str
    name: str
    value: SensitiveValue[JsonString]

    def __post_init__(self) -> None:
        """Retain only validated identities and an already sealed text payload."""
        if type(self.environment) is not str or type(self.name) is not str:
            raise TypeError("Environment value override identities must be text")
        try:
            ResourceId(value=self.environment)
            SymbolPath(segments=(self.name,))
        except ValueError as error:
            raise InvocationBuildError(InvocationBuildErrorCode.INVALID_IDENTIFIER) from error
        if type(self.value) is not SensitiveValue or self.value.payload_type is not JsonString:
            raise TypeError("Environment value overrides must retain a sealed JsonString")


@final
class RequestOverride(msgspec.Struct, frozen=True, kw_only=True):
    """Typed Request field edits whose expressions may reference invocation values."""

    subject: str
    method: ValueExpression | None = None
    url: ValueExpression | None = None
    endpoint: ValueExpression | None = None
    query: ValueExpression | None = None
    headers: ValueExpression | None = None
    cookies: ValueExpression | None = None
    body: ValueExpression | None = None
    auth: ValueExpression | None = None
    timeouts: ValueExpression | None = None
    retries: ValueExpression | None = None
    tls: ValueExpression | None = None
    proxy: ValueExpression | None = None
    redirects: ValueExpression | None = None

    def __post_init__(self) -> None:
        """Require a stable subject and at least one typed field expression."""
        if type(self.subject) is not str:
            raise TypeError("Request override subject must be text")
        try:
            ResourceId(value=self.subject)
        except ValueError as error:
            raise InvocationBuildError(InvocationBuildErrorCode.INVALID_IDENTIFIER) from error
        values = self._expressions()
        if not values:
            raise InvocationBuildError(InvocationBuildErrorCode.INVALID_VALUE_RESOURCE)
        if any(not is_value_expression(value) for value in values):
            raise TypeError("Request override fields must be ValueExpression values")

    def _expressions(self) -> tuple[ValueExpression, ...]:
        values = (
            self.method,
            self.url,
            self.endpoint,
            self.query,
            self.headers,
            self.cookies,
            self.body,
            self.auth,
            self.timeouts,
            self.retries,
            self.tls,
            self.proxy,
            self.redirects,
        )
        return tuple(value for value in values if value is not None)


@final
class ClearProfileSelection(msgspec.Struct, frozen=True, kw_only=True):
    """Explicitly detach one inherited scalar profile in an invocation."""


@final
class ProfileSelectionOverrides(msgspec.Struct, frozen=True, kw_only=True):
    """Primitive named profile replacements shared by CLI, TUI, and CI."""

    assertions: tuple[str, ...] | None = None
    diffs: tuple[str, ...] | None = None
    gate: str | ClearProfileSelection | None = None
    safety: tuple[str, ...] | None = None
    redaction: str | ClearProfileSelection | None = None
    artifacts: str | ClearProfileSelection | None = None

    def __post_init__(self) -> None:
        """Reject mutable, duplicate, and non-text profile selections."""
        for values in (self.assertions, self.diffs, self.safety):
            if values is not None and (
                type(values) is not tuple
                or any(type(item) is not str for item in values)
                or len(values) != len(set(values))
            ):
                raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_SELECTION)
        if any(
            value is not None and type(value) not in {str, ClearProfileSelection}
            for value in (self.gate, self.redaction, self.artifacts)
        ):
            raise TypeError("single profile selections must inherit, clear, or name a profile")


def inline_value_override(
    namespace: ValueNamespace,
    expression: ObjectExpr,
    *,
    environment: str | None = None,
) -> InlineValueOverride:
    """Construct an inline override and seal every secret literal before retention."""
    if type(namespace) is not ValueNamespace or type(expression) is not ObjectExpr:
        raise TypeError("inline Value override factory requires typed inputs")
    secured = (
        seal_expression_literals(expression) if namespace is ValueNamespace.SECRETS else expression
    )
    if type(secured) is not ObjectExpr:  # pragma: no cover - recursive shape invariant
        raise TypeError("inline Value sealing changed the object root")
    return InlineValueOverride(
        namespace=namespace,
        expression=secured,
        environment=environment,
    )


def environment_value_override(
    environment: str,
    name: str,
    value: str,
) -> EnvironmentValueOverride:
    """Seal one frontend string immediately, before it enters PlanningOptions."""
    if any(type(item) is not str for item in (environment, name, value)):
        raise TypeError("Environment value override factory requires exact strings")
    digest = hashlib.sha256(f"{environment}\0{name}".encode()).hexdigest()[:24]
    return EnvironmentValueOverride(
        environment=environment,
        name=name,
        value=SensitiveValue(
            JsonString(value=value),
            origins=(TaintOriginId(value=f"invocation-env/{digest}"),),
        ),
    )


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
    inline_values: tuple[InlineValueOverride, ...] = ()
    environment_values: tuple[EnvironmentValueOverride, ...] = ()
    requests: tuple[RequestOverride, ...] = ()
    profiles: ProfileSelectionOverrides = msgspec.field(default_factory=ProfileSelectionOverrides)

    def __post_init__(self) -> None:
        """Reject ambiguous primitive collections before constructing domain values."""
        _validate_selections(self.subjects, self.environments, self.candidates)
        _validate_matrices(self.matrices)
        _validate_budgets(self.budgets)
        _validate_value_resources(self.value_resources)
        _validate_inline_values(self.inline_values)
        _validate_environment_values(self.environment_values)
        _validate_request_overrides(self.requests)
        if type(self.profiles) is not ProfileSelectionOverrides:
            raise TypeError("planning profile overrides must be typed")
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
            values=_value_overlays(project, options.value_resources, options.inline_values),
            environment_values=tuple(
                InvocationEnvironmentValue(
                    environment=ResourceId(value=item.environment),
                    symbol=SymbolPath(segments=(item.name,)),
                    value=item.value,
                )
                for item in options.environment_values
            ),
            requests=tuple(_request_edit(item) for item in options.requests),
            profiles=_profile_overrides(options.profiles),
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


def _validate_inline_values(values: tuple[InlineValueOverride, ...]) -> None:
    if type(values) is not tuple or any(type(item) is not InlineValueOverride for item in values):
        raise TypeError("planning inline Value overrides must be immutable and typed")
    scopes = tuple((item.namespace, item.environment) for item in values)
    if len(scopes) != len(set(scopes)):
        raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_INLINE_VALUE)


def _validate_environment_values(values: tuple[EnvironmentValueOverride, ...]) -> None:
    if type(values) is not tuple or any(
        type(item) is not EnvironmentValueOverride for item in values
    ):
        raise TypeError("planning Environment value overrides must be immutable and typed")
    coordinates = tuple((item.environment, item.name) for item in values)
    if len(coordinates) != len(set(coordinates)):
        raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_ENVIRONMENT_VALUE)


def _validate_request_overrides(values: tuple[RequestOverride, ...]) -> None:
    if type(values) is not tuple or any(type(item) is not RequestOverride for item in values):
        raise TypeError("planning Request overrides must be immutable and typed")
    subjects = tuple(item.subject for item in values)
    if len(subjects) != len(set(subjects)):
        raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_REQUEST_EDIT)


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


def _value_overlays(
    project: CompiledProject | None,
    resources: tuple[ValueResourceOverride, ...],
    inline: tuple[InlineValueOverride, ...],
) -> tuple[InvocationValueOverlay, ...]:
    """Merge disjoint resource and inline namespaces at each invocation scope."""
    grouped: dict[ResourceId | None, dict[ValueNamespace, ValueExpression]] = {}
    if project is not None:
        for overlay in _value_resource_overlays(project, resources):
            for namespace, expression in _overlay_expressions(overlay):
                grouped.setdefault(overlay.environment, {})[namespace] = expression
    for item in inline:
        environment = None if item.environment is None else ResourceId(value=item.environment)
        values = grouped.setdefault(environment, {})
        if item.namespace in values:
            raise InvocationBuildError(InvocationBuildErrorCode.DUPLICATE_INLINE_VALUE)
        values[item.namespace] = item.expression
    return tuple(
        InvocationValueOverlay(
            environment=environment,
            parameters=values.get(ValueNamespace.PARAMETERS),
            variables=values.get(ValueNamespace.VARIABLES),
            secrets=values.get(ValueNamespace.SECRETS),
        )
        for environment, values in sorted(
            grouped.items(),
            key=lambda item: (
                item[0] is not None,
                "" if item[0] is None else item[0].value,
            ),
        )
    )


def _overlay_expressions(
    overlay: InvocationValueOverlay,
) -> tuple[tuple[ValueNamespace, ValueExpression], ...]:
    candidates = (
        (ValueNamespace.PARAMETERS, overlay.parameters),
        (ValueNamespace.VARIABLES, overlay.variables),
        (ValueNamespace.SECRETS, overlay.secrets),
    )
    return tuple((namespace, value) for namespace, value in candidates if value is not None)


def _request_edit(value: RequestOverride) -> InvocationRequestEdit:
    return InvocationRequestEdit(
        subject=ResourceId(value=value.subject),
        method=value.method,
        url=value.url,
        endpoint=value.endpoint,
        query=value.query,
        headers=value.headers,
        cookies=value.cookies,
        body=value.body,
        auth=value.auth,
        timeouts=value.timeouts,
        retries=value.retries,
        tls=value.tls,
        proxy=value.proxy,
        redirects=value.redirects,
    )


def _profile_overrides(value: ProfileSelectionOverrides) -> InvocationProfileOverrides:
    return InvocationProfileOverrides(
        assertions=_profile_collection(value.assertions),
        diffs=_profile_collection(value.diffs),
        gate=_profile_scalar(value.gate),
        safety=_profile_collection(value.safety),
        redaction=_profile_scalar(value.redaction),
        artifacts=_profile_scalar(value.artifacts),
    )


def _profile_collection(
    values: tuple[str, ...] | None,
) -> CollectionOverridePlan[ResourceId, ResourceId]:
    if values is None:
        return CollectionOverridePlan(base=KeepBase())
    if not values:
        return CollectionOverridePlan(base=ClearBase())
    return CollectionOverridePlan(
        base=ReplaceBase(items=tuple(ResourceId(value=value) for value in values))
    )


def _profile_scalar(
    value: str | ClearProfileSelection | None,
) -> Inherit | Clear | SetValue[ResourceId]:
    if value is None:
        return Inherit()
    if type(value) is ClearProfileSelection:
        return Clear()
    return SetValue(value=ResourceId(value=value))


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
            cases=tuple(MatrixCaseId(value=value) for value in item.cases),
        )
        for item in values
    )
    return CollectionOverridePlan(base=ReplaceBase(items=matrices))


def _positive_override(value: int | None) -> Inherit | SetValue[PositiveCount]:
    return Inherit() if value is None else SetValue(value=PositiveCount(value=value))


def _sealed_secret_expression(expression: ValueExpression) -> bool:
    """Return whether an expression retains no raw authored secret output literals."""
    if type(expression) is SensitiveLiteralExpr:
        return True
    if type(expression) in {LookupExpr, FileExpr}:
        return True
    if type(expression) is LiteralExpr:
        return False
    if type(expression) is FallbackExpr:
        return all(_sealed_secret_expression(item) for item in expression.sources)
    if type(expression) is OptionalExpr or type(expression) is ConvertExpr:
        return _sealed_secret_expression(expression.source)
    if type(expression) is ArrayExpr:
        return all(_sealed_secret_expression(item) for item in expression.items)
    if type(expression) is ObjectExpr:
        return all(_sealed_secret_expression(member.expression) for member in expression.members)
    if type(expression) is InterpolationExpr:
        return all(
            type(segment) is ExpressionSegment and _sealed_secret_expression(segment.expression)
            for segment in expression.segments
        ) and not any(type(segment) is LiteralTextSegment for segment in expression.segments)
    return False


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
    "ClearProfileSelection",
    "EnvironmentValueOverride",
    "InlineValueOverride",
    "InvocationBuildError",
    "InvocationBuildErrorCode",
    "MatrixOverride",
    "PlanningOptions",
    "ProfileSelectionOverrides",
    "RequestOverride",
    "ValueNamespace",
    "ValueResourceOverride",
    "build_invocation",
    "environment_value_override",
    "inline_value_override",
    "plan_project",
)
