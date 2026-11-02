"""Construction and lookup of immutable run, Environment, and Matrix snapshots."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.planning import PlannedCell
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.value_references import runtime_value_scope_kind
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.models import ValueBindingLayer
from comparo.core.resolution.models import CellInputs
from comparo.core.resolution.models import EnvironmentSnapshot
from comparo.core.resolution.models import MatrixInputBinding
from comparo.core.resolution.scopes import cell_scope
from comparo.core.resolution.scopes import environment_scope
from comparo.core.resolution.scopes import execution_scope


@final
class NamespaceBinding(msgspec.Struct, frozen=True, kw_only=True):
    """One selected namespace expression and its concrete production scope."""

    expression: ValueExpression
    production_scope: RuntimeScope
    configuration: ValueProvenance

    def __post_init__(self) -> None:
        """Require a typed expression, scope, and provenance."""
        from comparo.core.domain.expressions import is_value_expression

        if not is_value_expression(self.expression):
            raise TypeError("namespace binding requires a ValueExpression")
        runtime_value_scope_kind(self.production_scope)
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("namespace binding requires ValueProvenance")


def cell_inputs(prepared: PreparedRun, cell: PlannedCell) -> CellInputs:
    """Bind one lazy cell coordinate to its exact unresolved Matrix case objects."""
    if type(prepared) is not PreparedRun:
        raise TypeError("cell input construction requires a PreparedRun")
    if type(cell) is not PlannedCell:
        raise TypeError("cell input construction requires a PlannedCell")
    subject = next(
        (
            candidate
            for candidate in prepared.effective.subjects
            if candidate.definition == cell.subject
        ),
        None,
    )
    if subject is None:
        raise ValueError("planned cell subject is absent from the prepared run")
    bindings: list[MatrixInputBinding] = []
    for coordinate in cell.matrix_cases:
        matrix = next(
            (
                candidate
                for candidate in subject.matrices
                if candidate.binding == coordinate.binding
                and candidate.definition == coordinate.matrix
            ),
            None,
        )
        if matrix is None:
            raise ValueError("planned Matrix coordinate is absent from its effective subject")
        case = next(
            (candidate for candidate in matrix.cases if candidate.case_id == coordinate.case_id),
            None,
        )
        if case is None or type(case.values) is not ObjectExpr:
            raise ValueError("planned Matrix case has no object input definition")
        bindings.append(
            MatrixInputBinding(
                binding=coordinate.binding,
                case_id=coordinate.case_id,
                values=case.values,
            )
        )
    return CellInputs(cell_id=cell.cell_id, bindings=tuple(bindings))


def validate_environment_snapshots(
    prepared: PreparedRun,
    snapshots: tuple[EnvironmentSnapshot, ...],
) -> None:
    """Require one exact run-start snapshot for every and only selected Environment."""
    if type(snapshots) is not tuple or any(
        type(snapshot) is not EnvironmentSnapshot for snapshot in snapshots
    ):
        raise TypeError("Environment snapshots must be a typed immutable tuple")
    expected = tuple(prepared.effective.environments)
    actual = tuple(snapshot.environment for snapshot in snapshots)
    if len(actual) != len(set(actual)):
        raise ValueError("Environment snapshots must be unique")
    if set(actual) != set(expected):
        raise ValueError("Environment snapshots must exactly cover selected Environments")


def environment_snapshot(
    snapshots: tuple[EnvironmentSnapshot, ...],
    scope: EnvironmentScope,
) -> EnvironmentSnapshot:
    """Return the exact snapshot for one Environment scope."""
    result = next(
        (snapshot for snapshot in snapshots if snapshot.environment.identity == scope.environment),
        None,
    )
    if result is None:
        raise ValueError("Environment scope has no run-start snapshot")
    return result


def namespace_binding(
    prepared: PreparedRun,
    namespace: ValueNamespace,
    symbol: SymbolPath,
    consumer: RuntimeScope,
) -> NamespaceBinding | None:
    """Select the most-specific applicable unresolved namespace binding."""
    if namespace not in {
        ValueNamespace.VARIABLES,
        ValueNamespace.SECRETS,
        ValueNamespace.PARAMETERS,
    }:
        raise ValueError("namespace bindings support variables, secrets, and parameters")
    selected: NamespaceBinding | None = None
    for layer in prepared.effective.value_layers:
        expression = _layer_expression(layer, namespace)
        if expression is None:
            continue
        scoped = _layer_scope(layer, namespace, consumer)
        if scoped is None:
            continue
        member = _first_member(expression, symbol)
        if member is not None:
            selected = NamespaceBinding(
                expression=member,
                production_scope=scoped,
                configuration=member.provenance,
            )
    return selected


def matrix_binding(
    inputs: CellInputs | None,
    symbol: SymbolPath,
) -> tuple[ValueExpression, ValueProvenance] | None:
    """Resolve qualified or unique unqualified Matrix input expression identity."""
    if inputs is None:
        return None
    segments = symbol.segments
    if len(segments) > 1:
        binding = next(
            (item for item in inputs.bindings if item.binding.value == segments[0]),
            None,
        )
        if binding is None:
            return None
        expression = _object_path(binding.values, segments[1:])
        return None if expression is None else (expression, expression.provenance)
    matches = tuple(
        expression
        for binding in inputs.bindings
        if (expression := _object_path(binding.values, segments)) is not None
    )
    if len(matches) != 1:
        return None
    return matches[0], matches[0].provenance


def _layer_expression(
    layer: ValueBindingLayer,
    namespace: ValueNamespace,
) -> ValueExpression | None:
    if namespace is ValueNamespace.VARIABLES:
        return layer.variables
    if namespace is ValueNamespace.SECRETS:
        return layer.secrets
    return layer.parameters


def _layer_scope(
    layer: ValueBindingLayer,
    namespace: ValueNamespace,
    consumer: RuntimeScope,
) -> RuntimeScope | None:
    if layer.environment is None:
        if namespace is ValueNamespace.PARAMETERS:
            return cell_scope(consumer)
        return RunScope(run_id=prepared_run_scope(consumer).run_id)
    environment = environment_scope(consumer)
    if environment is None or environment.environment != layer.environment.identity:
        return None
    if namespace is ValueNamespace.PARAMETERS:
        return execution_scope(consumer)
    return environment


def prepared_run_scope(scope: RuntimeScope) -> RunScope:
    """Return the exact run scope without importing ambient context state."""
    from comparo.core.domain.scopes import run_scope_of

    return run_scope_of(scope)


def _first_member(expression: ValueExpression, symbol: SymbolPath) -> ValueExpression | None:
    if type(expression) is not ObjectExpr:
        return None
    return _object_path(expression, symbol.segments)


def _object_path(expression: ObjectExpr, segments: tuple[str, ...]) -> ValueExpression | None:
    current: ValueExpression = expression
    for segment in segments:
        if type(current) is not ObjectExpr:
            return None
        candidate = next(
            (member.expression for member in current.members if member.name == segment),
            None,
        )
        if candidate is None:
            return None
        current = candidate
    return current
