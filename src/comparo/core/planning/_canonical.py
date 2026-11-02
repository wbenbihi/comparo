"""Small strict accessors for the directive-free canonical configuration graph."""

from __future__ import annotations

from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.planning._errors import PlanningError


def object_member(value: CanonicalObject, name: str) -> CanonicalValue | None:
    """Return a member by schema-owned name without generic mapping behavior."""
    return next((member.value for member in value.members if member.name == name), None)


def expression_member(value: ObjectExpr, name: str) -> ValueExpression | None:
    """Return an expression member by a schema-owned static name."""
    return next((member.expression for member in value.members if member.name == name), None)


def resource_spec(resource: CompiledResource) -> CanonicalObject:
    """Require the structured canonical object expected by plannable resource kinds."""
    if type(resource.spec) is not CanonicalObject:
        raise PlanningError("planning.resource.spec-invalid", resource=resource)
    return resource.spec


def find_resource(
    project: CompiledProject,
    kind: ResourceKind,
    resource_id: ResourceId,
    *,
    owner: CompiledResource | None = None,
    field: str | None = None,
) -> CompiledResource:
    """Resolve one typed invocation selection against the compiled registry."""
    resource = project.find(kind, resource_id)
    if resource is None:
        raise PlanningError("planning.resource.not-found", resource=owner, field=field)
    return resource


def positive_integer(
    value: CanonicalValue | None,
    *,
    owner: CompiledResource,
    field: str,
) -> int | None:
    """Extract a positive structural integer from canonical inert data."""
    if value is None:
        return None
    if type(value) is not JsonInteger or value.value < 1:
        raise PlanningError("planning.field.positive-integer-required", resource=owner, field=field)
    return value.value


def literal_string(
    expression: object,
    *,
    owner: CompiledResource,
    field: str,
) -> str:
    """Extract a structurally static string from a compiled expression position."""
    if type(expression) is not LiteralExpr or type(expression.value) is not JsonString:
        raise PlanningError("planning.matrix.static-id-required", resource=owner, field=field)
    return expression.value.value
