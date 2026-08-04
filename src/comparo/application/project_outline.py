"""Secret-free structural outlines for interactive project exploration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from typing import final

import msgspec

from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString

_SAFE_EXACT_SCALAR_PATHS = frozenset({("request", "method")})
_SECRET_FIELD_PARTS = frozenset(
    {"authorization", "cookie", "credential", "key", "password", "secret", "token"}
)
_EXACT_KINDS = frozenset({"null", "boolean", "integer", "number", "text", "reference"})


@final
class ResourceIdentityCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """One resource identity without pretending a provenance entry carries a revision."""

    namespace: str
    kind: str
    resource_id: str

    def __post_init__(self) -> None:
        """Require a complete bounded resource identity."""
        if any(
            type(item) is not str or not item or len(item) > 255
            for item in (self.namespace, self.kind, self.resource_id)
        ):
            raise ValueError("resource identity coordinate must be bounded non-empty text")


@final
class ProjectOutlineField(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded, value-safe structural row in a compiled resource."""

    path: tuple[str, ...]
    label: str
    value: str
    depth: int
    category: str
    exact_value: str | None = None
    exact_kind: str | None = None
    reference: ResourceIdentityCoordinate | None = None

    def __post_init__(self) -> None:
        """Require a fully typed, renderer-independent row."""
        if type(self.path) is not tuple or any(type(part) is not str for part in self.path):
            raise TypeError("project outline path must be an immutable text tuple")
        if any(type(value) is not str for value in (self.label, self.value, self.category)):
            raise TypeError("project outline labels must be text")
        if self.exact_value is not None and type(self.exact_value) is not str:
            raise TypeError("project outline exact value must be text or None")
        if (self.exact_value is None) != (self.exact_kind is None):
            raise ValueError("project outline exact value and kind must be present together")
        if self.exact_kind is not None:
            if type(self.exact_kind) is not str:
                raise TypeError("project outline exact kind must be text or None")
            if self.exact_kind not in _EXACT_KINDS:
                raise ValueError("project outline exact kind must be supported")
        _require_reference_shape(self.exact_kind, self.reference)
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("project outline depth must be a non-negative integer")


def _require_reference_shape(
    exact_kind: str | None,
    reference: ResourceIdentityCoordinate | None,
) -> None:
    """Keep exact reference display and typed identity structurally inseparable."""
    if reference is not None and type(reference) is not ResourceIdentityCoordinate:
        raise TypeError("project outline reference must be typed or absent")
    if (reference is None) != (exact_kind != "reference"):
        raise ValueError("project outline reference must match an exact reference value")


@final
class ProjectRelationship(msgspec.Struct, frozen=True, kw_only=True):
    """One resource dependency without values or filesystem coordinates."""

    path: tuple[str, ...]
    target: ResourceIdentityCoordinate
    origin: str

    def __post_init__(self) -> None:
        """Require a typed target and safe structural origin."""
        if type(self.path) is not tuple or any(type(part) is not str for part in self.path):
            raise TypeError("project relationship path must be an immutable text tuple")
        if type(self.target) is not ResourceIdentityCoordinate:
            raise TypeError("project relationship target must be typed")
        if type(self.origin) is not str or not self.origin:
            raise ValueError("project relationship origin must be non-empty text")


@final
class ProjectResourceOutline(msgspec.Struct, frozen=True, kw_only=True):
    """Safe effective structure and dependency graph for one resource."""

    fields: tuple[ProjectOutlineField, ...] = ()
    relationships: tuple[ProjectRelationship, ...] = ()

    def __post_init__(self) -> None:
        """Keep every projected field and relationship immutable and typed."""
        if type(self.fields) is not tuple or any(
            type(field) is not ProjectOutlineField for field in self.fields
        ):
            raise TypeError("project outline fields must be an immutable typed tuple")
        if type(self.relationships) is not tuple or any(
            type(item) is not ProjectRelationship for item in self.relationships
        ):
            raise TypeError("project outline relationships must be an immutable typed tuple")


type _Node = object


def outline_resource(
    owner: ResourceIdentity,
    spec: _Node,
) -> ProjectResourceOutline:
    """Project a canonical resource into a secret-free structural outline."""
    if type(owner) is not ResourceIdentity:
        raise TypeError("resource outline owner must be a ResourceIdentity")
    fields: list[ProjectOutlineField] = []
    relationships: dict[tuple[str, str, str], ProjectRelationship] = {}
    _outline_node(spec, (), fields, relationships, owner)
    return ProjectResourceOutline(
        fields=tuple(fields),
        relationships=tuple(relationships.values()),
    )


def _outline_node(  # noqa: C901 - exhaustive closed canonical/value-expression dispatch
    node: _Node,
    path: tuple[str, ...],
    fields: list[ProjectOutlineField],
    relationships: dict[tuple[str, str, str], ProjectRelationship],
    owner: ResourceIdentity,
) -> None:
    if type(node) is CanonicalObject:
        _container(fields, path, "object", len(node.members))
        for member in node.members:
            _outline_node(member.value, (*path, member.name), fields, relationships, owner)
        return
    if type(node) is CanonicalArray:
        _container(fields, path, "array", len(node.items))
        for index, item in enumerate(node.items):
            _outline_node(item, (*path, str(index)), fields, relationships, owner)
        return
    if type(node) is ObjectExpr:
        _container(fields, path, "object", len(node.members))
        _provenance_relationships(node.provenance.entries, path, relationships, owner)
        for expression_member in node.members:
            _outline_node(
                expression_member.expression,
                (*path, expression_member.name),
                fields,
                relationships,
                owner,
            )
        return
    if type(node) is ArrayExpr:
        _container(fields, path, "array", len(node.items))
        _provenance_relationships(node.provenance.entries, path, relationships, owner)
        for index, item in enumerate(node.items):
            _outline_node(item, (*path, str(index)), fields, relationships, owner)
        return
    if type(node) is JsonObject:
        _container(fields, path, "object", len(node.members))
        for json_member in node.members:
            _outline_node(
                json_member.value,
                (*path, json_member.name),
                fields,
                relationships,
                owner,
            )
        return
    if type(node) is JsonArray:
        _container(fields, path, "array", len(node.items))
        for index, item in enumerate(node.items):
            _outline_node(item, (*path, str(index)), fields, relationships, owner)
        return
    if type(node) is LinkedResource:
        target = node.target.identity
        coordinate = ResourceIdentityCoordinate(
            namespace=target.namespace.value,
            kind=target.kind.value,
            resource_id=target.resource_id.value,
        )
        _field(
            fields,
            path,
            f"$use {target.kind.value}/{target.resource_id.value}",
            "reference",
            exact_value=f"{target.kind.value}/{target.resource_id.value}",
            exact_kind="reference",
            reference=coordinate,
        )
        _relationship(path, node.target, relationships, "link")
        return
    if type(node) is InlineResource:
        _field(fields, path, f"inline {node.kind.value}", "definition")
        _outline_node(node.spec, (*path, "spec"), fields, relationships, owner)
        return
    if type(node) is RemoteSchema:
        _field(fields, path, "remote schema (URI concealed)", "reference")
        return
    if type(node) is SensitiveLiteralExpr:
        _field(fields, path, "* REDACTED", "sensitive")
        _provenance_relationships(node.provenance.entries, path, relationships, owner)
        return
    if type(node) is LookupExpr:
        dependency = node.dependency
        symbol = ".".join(dependency.symbol.segments)
        _field(fields, path, f"${dependency.namespace.value}.{symbol}", "directive")
        _provenance_relationships(node.provenance.entries, path, relationships, owner)
        return
    if type(node) is FileExpr:
        conversion = "bytes" if node.conversion is None else node.conversion.value
        _field(fields, path, f"$file as {conversion} (path concealed)", "directive")
        _provenance_relationships(node.provenance.entries, path, relationships, owner)
        return
    if type(node) is ConvertExpr:
        _field(fields, path, f"$convert as {node.target.value}", "directive")
        _outline_node(node.source, (*path, "source"), fields, relationships, owner)
        return
    if type(node) is OptionalExpr:
        _field(fields, path, "$optional", "directive")
        _outline_node(node.source, (*path, "source"), fields, relationships, owner)
        return
    if type(node) is FallbackExpr:
        _field(fields, path, f"$fallback ({len(node.sources)} sources)", "directive")
        for index, source in enumerate(node.sources):
            _outline_node(source, (*path, str(index)), fields, relationships, owner)
        return
    if type(node) is InterpolationExpr:
        _field(fields, path, f"interpolation ({node.mode.value})", "directive")
        for index, segment in enumerate(node.segments):
            if type(segment) is ExpressionSegment:
                _outline_node(
                    segment.expression,
                    (*path, f"expression-{index + 1}"),
                    fields,
                    relationships,
                    owner,
                )
        return
    if type(node) is LiteralExpr:
        exact_display = _safe_exact_display(node.value, path, owner)
        _field(
            fields,
            path,
            _exact_preview(node.value, path, owner),
            "literal",
            exact_value=None if exact_display is None else exact_display[0],
            exact_kind=None if exact_display is None else exact_display[1],
        )
        _provenance_relationships(node.provenance.entries, path, relationships, owner)
        return
    if type(node) in {JsonNull, JsonBoolean, JsonInteger, JsonDecimal, JsonString, BytesValue}:
        exact = cast("ExactValue", node)
        exact_display = _safe_exact_display(exact, path, owner)
        _field(
            fields,
            path,
            _exact_preview(exact, path, owner),
            "literal",
            exact_value=None if exact_display is None else exact_display[0],
            exact_kind=None if exact_display is None else exact_display[1],
        )
        return
    _field(fields, path, type(node).__name__, "structure")


def _container(
    fields: list[ProjectOutlineField],
    path: tuple[str, ...],
    kind: str,
    count: int,
) -> None:
    label = "spec" if not path else path[-1]
    fields.append(
        ProjectOutlineField(
            path=path,
            label=label,
            value=f"{kind} · {count} {'member' if count == 1 else 'members'}",
            depth=len(path),
            category="container",
        )
    )


def _field(
    fields: list[ProjectOutlineField],
    path: tuple[str, ...],
    value: str,
    category: str,
    *,
    exact_value: str | None = None,
    exact_kind: str | None = None,
    reference: ResourceIdentityCoordinate | None = None,
) -> None:
    fields.append(
        ProjectOutlineField(
            path=path,
            label="value" if not path else path[-1],
            value=value,
            depth=len(path),
            category=category,
            exact_value=exact_value,
            exact_kind=exact_kind,
            reference=reference,
        )
    )


def _exact_preview(
    value: ExactValue,
    path: tuple[str, ...],
    owner: ResourceIdentity,
) -> str:
    if _sensitive_path(path):
        return "* REDACTED"
    if type(value) is JsonNull:
        return "null"
    if type(value) is JsonBoolean:
        return "<boolean>"
    if type(value) is JsonInteger:
        return "<integer>"
    if type(value) is JsonDecimal:
        return "<number>"
    if type(value) is JsonString:
        if _safe_exact_scalar_path(owner, path):
            return value.value
        return f"<string · {len(value.value)} chars>"
    if type(value) is BytesValue:
        return f"<bytes · {len(value.value)} bytes>"
    if type(value) is JsonArray:
        return f"array · {len(value.items)} members"
    return f"object · {len(value.members)} members"


def _safe_exact_scalar_path(owner: ResourceIdentity, path: tuple[str, ...]) -> bool:
    return owner.kind is ResourceKind.REQUEST and path in _SAFE_EXACT_SCALAR_PATHS


def _safe_exact_display(
    value: ExactValue,
    path: tuple[str, ...],
    owner: ResourceIdentity,
) -> tuple[str, str] | None:
    """Return a bounded terminal-safe exact Project value when disclosure is inert."""
    if owner.kind is not ResourceKind.PROJECT or _sensitive_path(path):
        return None
    if path[:1] in {("variables",), ("secrets",)} or path[:2] in {
        ("defaults", "parameters"),
        ("defaults", "variables"),
        ("defaults", "secrets"),
    }:
        return None
    if type(value) is JsonNull:
        return "null", "null"
    if type(value) is JsonBoolean:
        return ("true" if value.value else "false"), "boolean"
    if type(value) is JsonInteger:
        return str(value.value), "integer"
    if type(value) is JsonDecimal:
        return str(value.value), "number"
    if type(value) is not JsonString:
        return None
    text = value.value
    if path[:1] in {("data",), ("sources",)}:
        text = Path(text).name or "<project root>"
    escaped = json.dumps(text, ensure_ascii=True)[1:-1]
    return (escaped, "text") if len(escaped) <= 256 else None


def _sensitive_path(path: tuple[str, ...]) -> bool:
    return any(any(part in segment.lower() for part in _SECRET_FIELD_PARTS) for segment in path)


def _provenance_relationships(
    entries: tuple[ProvenanceEntry, ...],
    path: tuple[str, ...],
    relationships: dict[tuple[str, str, str], ProjectRelationship],
    owner: ResourceIdentity,
) -> None:
    for entry in entries:
        resource = entry.resource
        if resource is None or resource == owner:
            continue
        coordinate = ResourceIdentityCoordinate(
            namespace=resource.namespace.value,
            kind=resource.kind.value,
            resource_id=resource.resource_id.value,
        )
        key = (coordinate.namespace, coordinate.kind, coordinate.resource_id)
        relationships.setdefault(
            key,
            ProjectRelationship(path=path, target=coordinate, origin="composition"),
        )


def _relationship(
    path: tuple[str, ...],
    target: DefinitionRef,
    relationships: dict[tuple[str, str, str], ProjectRelationship],
    origin: str,
) -> None:
    identity = target.identity
    coordinate = ResourceIdentityCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
    )
    key = (coordinate.namespace, coordinate.kind, coordinate.resource_id)
    relationships.setdefault(
        key,
        ProjectRelationship(path=path, target=coordinate, origin=origin),
    )


__all__ = (
    "ProjectOutlineField",
    "ProjectRelationship",
    "ProjectResourceOutline",
    "ResourceIdentityCoordinate",
    "outline_resource",
)
