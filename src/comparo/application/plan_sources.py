"""Actual sensitive dependency projection for an admitted unresolved Plan."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import final

import msgspec

from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import InlineResource
from comparo.core.domain.expression_analysis import expression_dependencies
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionDependency
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.planning.models import PlanningSource
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.models import ValueBindingLayer


@final
class SensitiveSourceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One used sensitive binding and its safe unresolved source identity."""

    name: str
    source: str
    environment_namespace: str
    environment_id: str
    mechanisms: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require non-empty names, coordinates, and source mechanisms."""
        values = (self.name, self.source, self.environment_namespace, self.environment_id)
        if any(type(item) is not str or not item for item in values):
            raise ValueError("sensitive source identities must be non-empty text")
        if (
            type(self.mechanisms) is not tuple
            or not self.mechanisms
            or any(type(item) is not str or not item for item in self.mechanisms)
        ):
            raise ValueError("sensitive source mechanisms must be non-empty typed text")


def inspect_sensitive_sources(prepared: PreparedRun) -> tuple[SensitiveSourceInspection, ...]:
    """Trace only sensitive dependencies reachable from selected executable resources."""
    if type(prepared) is not PreparedRun:
        raise TypeError("sensitive source inspection requires PreparedRun")
    queue = _dependency_queue(prepared)
    bindings = _binding_index(prepared.effective.value_layers)
    found: list[SensitiveSourceInspection] = []
    visited: set[tuple[ExpressionDependency, DefinitionRef]] = set()
    while queue:
        dependency, environment = queue.popleft()
        marker = (dependency, environment)
        if marker in visited:
            continue
        visited.add(marker)
        inspected, dependencies = _trace_dependency(bindings, dependency, environment)
        queue.extend((item, environment) for item in dependencies)
        if inspected is not None:
            found.append(inspected)
    return tuple(dict.fromkeys(found))


def _dependency_queue(
    prepared: PreparedRun,
) -> deque[tuple[ExpressionDependency, DefinitionRef]]:
    resources = {item.definition: item for item in prepared.project.resources}
    definitions = _definition_specs(prepared)
    queue: deque[tuple[ExpressionDependency, DefinitionRef]] = deque()
    for environment in prepared.effective.environments:
        resource = resources.get(environment)
        if resource is not None:
            queue.extend((item, environment) for item in _root_dependencies(resource.spec))
    for subject in prepared.effective.subjects:
        resource = resources.get(subject.definition)
        if resource is None:
            continue
        dependencies = list(_root_dependencies(resource.spec))
        dependencies.extend(
            dependency
            for matrix in subject.matrices
            for case in matrix.cases
            for dependency in expression_dependencies(case.values)
        )
        attachments = (
            *subject.profiles.assertions,
            *subject.profiles.diffs,
            *((subject.profiles.gate,) if subject.profiles.gate is not None else ()),
            *subject.profiles.safety,
            *((subject.profiles.redaction,) if subject.profiles.redaction is not None else ()),
            *((subject.profiles.artifacts,) if subject.profiles.artifacts is not None else ()),
        )
        dependencies.extend(
            dependency
            for attachment in attachments
            for dependency in _root_dependencies(definitions[attachment])
        )
        for environment in prepared.effective.environments:
            queue.extend((item, environment) for item in dependencies)
    return queue


def _definition_specs(
    prepared: PreparedRun,
) -> dict[DefinitionRef | InlineDefinitionRef, CanonicalObject]:
    definitions: dict[DefinitionRef | InlineDefinitionRef, CanonicalObject] = {}

    def visit(value: CanonicalValue) -> None:
        if type(value) is InlineResource:
            definitions[value.definition] = value.spec
            visit(value.spec)
        elif type(value) is CanonicalArray:
            for item in value.items:
                visit(item)
        elif type(value) is CanonicalObject:
            for member in value.members:
                visit(member.value)

    for resource in prepared.project.resources:
        if type(resource.spec) is CanonicalObject:
            definitions[resource.definition] = resource.spec
        visit(resource.spec)
    return definitions


def _root_dependencies(
    value: CanonicalValue,
    *,
    declaration_root: bool = True,
) -> tuple[ExpressionDependency, ...]:
    """Trace executable fields while leaving declaration containers demand-driven."""
    if type(value) is CanonicalObject:
        return tuple(
            dependency
            for member in value.members
            if not (declaration_root and member.name in {"parameters", "variables", "secrets"})
            for dependency in _root_dependencies(member.value, declaration_root=False)
        )
    if type(value) is CanonicalArray:
        return tuple(
            dependency
            for child in value.items
            for dependency in _root_dependencies(child, declaration_root=False)
        )
    if type(value) is InlineResource:
        return _root_dependencies(value.spec, declaration_root=True)
    if is_value_expression(value):
        return expression_dependencies(value)
    return ()


def _trace_dependency(
    bindings: _BindingIndex,
    dependency: ExpressionDependency,
    environment: DefinitionRef,
) -> tuple[SensitiveSourceInspection | None, tuple[ExpressionDependency, ...]]:
    namespace = dependency.namespace
    symbol = ".".join(dependency.symbol.segments)
    identity = environment.identity
    if namespace is ValueNamespace.ENV:
        return (
            SensitiveSourceInspection(
                name=symbol,
                source=f"Environment:{identity.namespace.value}/{identity.resource_id.value}",
                environment_namespace=identity.namespace.value,
                environment_id=identity.resource_id.value,
                mechanisms=(f"$env:{symbol}",),
            ),
            (),
        )
    binding = bindings.find(namespace, dependency.symbol.segments[0], environment)
    if binding is None:
        return None, ()
    layer, expression = binding
    dependencies = expression_dependencies(expression)
    if namespace is not ValueNamespace.SECRETS:
        return None, dependencies
    return (
        SensitiveSourceInspection(
            name=symbol,
            source=_planning_source(layer.source),
            environment_namespace=identity.namespace.value,
            environment_id=identity.resource_id.value,
            mechanisms=_mechanisms(expression),
        ),
        dependencies,
    )


def _dependencies(value: CanonicalValue) -> tuple[ExpressionDependency, ...]:
    if is_value_expression(value):
        return expression_dependencies(value)
    if type(value) is CanonicalArray:
        return tuple(item for child in value.items for item in _dependencies(child))
    if type(value) is CanonicalObject:
        return tuple(item for member in value.members for item in _dependencies(member.value))
    if type(value) is InlineResource:
        return _dependencies(value.spec)
    return ()


@dataclass(frozen=True, slots=True)
class _IndexedBinding:
    order: int
    layer: ValueBindingLayer
    expression: ValueExpression


@dataclass(frozen=True, slots=True)
class _BindingIndex:
    global_bindings: dict[tuple[ValueNamespace, str], _IndexedBinding]
    environment_bindings: dict[
        tuple[DefinitionRef, ValueNamespace, str],
        _IndexedBinding,
    ]

    def find(
        self,
        namespace: ValueNamespace,
        name: str,
        environment: DefinitionRef,
    ) -> tuple[ValueBindingLayer, ValueExpression] | None:
        global_value = self.global_bindings.get((namespace, name))
        environment_value = self.environment_bindings.get((environment, namespace, name))
        selected = max(
            (item for item in (global_value, environment_value) if item is not None),
            key=lambda item: item.order,
            default=None,
        )
        return None if selected is None else (selected.layer, selected.expression)


def _binding_index(layers: tuple[ValueBindingLayer, ...]) -> _BindingIndex:
    global_bindings: dict[tuple[ValueNamespace, str], _IndexedBinding] = {}
    environment_bindings: dict[
        tuple[DefinitionRef, ValueNamespace, str],
        _IndexedBinding,
    ] = {}
    for order, layer in enumerate(layers):
        for namespace, expression in (
            (ValueNamespace.PARAMETERS, layer.parameters),
            (ValueNamespace.VARIABLES, layer.variables),
            (ValueNamespace.SECRETS, layer.secrets),
        ):
            if type(expression) is not ObjectExpr:
                continue
            for member in expression.members:
                value = _IndexedBinding(order=order, layer=layer, expression=member.expression)
                if layer.environment is None:
                    global_bindings[(namespace, member.name)] = value
                else:
                    environment_bindings[(layer.environment, namespace, member.name)] = value
    return _BindingIndex(
        global_bindings=global_bindings,
        environment_bindings=environment_bindings,
    )


def _mechanisms(expression: ValueExpression) -> tuple[str, ...]:
    mechanisms = _expression_mechanisms(expression)
    if mechanisms:
        return tuple(dict.fromkeys(mechanisms))
    return ("sealed inline literal",)


def _expression_mechanisms(expression: ValueExpression) -> tuple[str, ...]:
    if type(expression) is LookupExpr:
        dependency = expression.dependency
        return (f"${dependency.namespace.value}:{'.'.join(dependency.symbol.segments)}",)
    if type(expression) is FileExpr:
        return ("$file", *_expression_mechanisms(expression.path))
    if type(expression) is FallbackExpr:
        return tuple(
            mechanism
            for source in expression.sources
            for mechanism in _expression_mechanisms(source)
        )
    if type(expression) is OptionalExpr:
        return _expression_mechanisms(expression.source)
    if type(expression) is ConvertExpr:
        return _expression_mechanisms(expression.source)
    if type(expression) is ArrayExpr:
        return tuple(
            mechanism for item in expression.items for mechanism in _expression_mechanisms(item)
        )
    if type(expression) is ObjectExpr:
        return tuple(
            mechanism
            for member in expression.members
            for mechanism in _expression_mechanisms(member.expression)
        )
    if type(expression) is InterpolationExpr:
        return tuple(
            mechanism
            for segment in expression.segments
            if type(segment) is ExpressionSegment
            for mechanism in _expression_mechanisms(segment.expression)
        )
    if type(expression) in {LiteralExpr, SensitiveLiteralExpr}:
        return ()
    raise TypeError("unsupported value expression")


def _planning_source(source: PlanningSource) -> str:
    definition = source.definition
    if definition is not None:
        if type(definition) is DefinitionRef:
            identity = definition.identity
            return (
                f"{source.kind.value}:{identity.namespace.value}/{identity.kind.value}/"
                f"{identity.resource_id.value}@{definition.fingerprint.digest.hex_value}"
            )
        owner = definition.origin.owner
        return (
            f"{source.kind.value}:{owner.namespace.value}/{owner.kind.value}/"
            f"{owner.resource_id.value}#{definition.origin.fragment.value}"
            f"@{definition.fingerprint.digest.hex_value}"
        )
    if source.invocation_id is not None:
        return f"{source.kind.value}:{source.invocation_id.value}"
    return source.kind.value


__all__ = ("SensitiveSourceInspection", "inspect_sensitive_sources")
