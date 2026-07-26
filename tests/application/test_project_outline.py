"""Secret-free project outline projection and defensive boundary coverage."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import msgspec
import pytest

from comparo.application.project_outline import ProjectOutlineField
from comparo.application.project_outline import ProjectRelationship
from comparo.application.project_outline import ProjectResourceOutline
from comparo.application.project_outline import ResourceIdentityCoordinate
from comparo.application.project_outline import outline_resource
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConversionType
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionDependency
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import FileTrim
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import InterpolationMode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LiteralTextSegment
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import SymbolPath
from comparo.core.domain.expressions import ValueNamespace
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString


def _identity(kind: ResourceKind, resource_id: str) -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=kind,
        resource_id=ResourceId(value=resource_id),
    )


def _fingerprint() -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value="a" * 64),
    )


def _provenance(owner: ResourceIdentity, dependency: ResourceIdentity) -> ValueProvenance:
    return ValueProvenance(
        entries=(
            ProvenanceEntry(kind=ProvenanceKind.VERSIONED_DEFAULT),
            ProvenanceEntry(kind=ProvenanceKind.PROJECT, resource=owner),
            ProvenanceEntry(kind=ProvenanceKind.PROFILE, resource=dependency),
        )
    )


def _literal(value: ExactValue, provenance: ValueProvenance) -> LiteralExpr:
    return LiteralExpr(value=value, provenance=provenance)


def test_outline_projects_every_supported_shape_without_values_or_locations() -> None:
    owner = _identity(ResourceKind.REQUEST, "catalog")
    environment = _identity(ResourceKind.ENVIRONMENT, "baseline")
    provenance = _provenance(owner, environment)
    lookup = LookupExpr(
        dependency=ExpressionDependency(
            namespace=ValueNamespace.ENV,
            symbol=SymbolPath(segments=("TOKEN",)),
            available_at=ValueScopeKind.ENVIRONMENT,
        ),
        provenance=provenance,
    )
    path = _literal(JsonString(value="private-path"), provenance)
    raw_file = FileExpr(
        path=path,
        conversion=None,
        trim=FileTrim.NONE,
        maximum_bytes=ByteCount(value=128),
        provenance=provenance,
    )
    converted_file = FileExpr(
        path=path,
        conversion=ConversionType.STRING,
        trim=FileTrim.NONE,
        provenance=provenance,
    )
    candidate = _identity(ResourceKind.ENVIRONMENT, "candidate")
    link = DefinitionRef(identity=candidate, fingerprint=_fingerprint())
    inline = InlineResource(
        definition=InlineDefinitionRef(
            kind=ResourceKind.SCHEMA,
            origin=InlineDefinitionOrigin(
                owner=owner,
                fragment=InlineFragment(value="#response-schema"),
            ),
            fingerprint=_fingerprint(),
        ),
        spec=CanonicalObject(
            members=(CanonicalMember(name="type", value=JsonString(value="object")),)
        ),
    )
    secret = SensitiveLiteralExpr(
        value=SensitiveValue(
            JsonString(value="never-render-this"),
            origins=(TaintOriginId(value="secret/project"),),
        ),
        provenance=provenance,
    )
    expression_object = ObjectExpr(
        members=(ObjectExpressionMember(name="token", expression=lookup),),
        provenance=provenance,
    )
    expression_array = ArrayExpr(items=(lookup,), provenance=provenance)
    interpolation = InterpolationExpr(
        mode=InterpolationMode.EMBEDDED_TEXT,
        segments=(
            LiteralTextSegment(value="Bearer "),
            ExpressionSegment(expression=lookup),
        ),
        provenance=provenance,
    )
    spec = CanonicalObject(
        members=(
            CanonicalMember(
                name="canonical-array",
                value=CanonicalArray(items=(JsonInteger(value=1), JsonString(value="opaque"))),
            ),
            CanonicalMember(name="expression-object", value=expression_object),
            CanonicalMember(name="expression-array", value=expression_array),
            CanonicalMember(name="linked", value=LinkedResource(target=link)),
            CanonicalMember(name="inline", value=inline),
            CanonicalMember(
                name="remote", value=RemoteSchema(uri="https://schemas.example/schema.json")
            ),
            CanonicalMember(name="authorization", value=secret),
            CanonicalMember(name="lookup", value=lookup),
            CanonicalMember(name="raw-file", value=raw_file),
            CanonicalMember(name="converted-file", value=converted_file),
            CanonicalMember(
                name="convert",
                value=ConvertExpr(
                    source=lookup,
                    target=ConversionType.INTEGER,
                    provenance=provenance,
                ),
            ),
            CanonicalMember(
                name="optional",
                value=OptionalExpr(source=lookup, provenance=provenance),
            ),
            CanonicalMember(
                name="fallback",
                value=FallbackExpr(sources=(lookup, raw_file), provenance=provenance),
            ),
            CanonicalMember(name="interpolation", value=interpolation),
            CanonicalMember(name="null", value=_literal(JsonNull(), provenance)),
            CanonicalMember(name="enabled", value=_literal(JsonBoolean(value=True), provenance)),
            CanonicalMember(name="count", value=_literal(JsonInteger(value=7), provenance)),
            CanonicalMember(
                name="amount", value=_literal(JsonDecimal(value=Decimal("1.25")), provenance)
            ),
            CanonicalMember(name="method", value=_literal(JsonString(value="POST"), provenance)),
            CanonicalMember(
                name="endpoint",
                value=_literal(
                    JsonString(value="/private?api_key=endpoint-secret"),
                    provenance,
                ),
            ),
            CanonicalMember(
                name="selector",
                value=_literal(JsonString(value="$.signed.selector-secret"), provenance),
            ),
            CanonicalMember(
                name="payload", value=_literal(JsonString(value="concealed"), provenance)
            ),
            CanonicalMember(name="binary", value=_literal(BytesValue(value=b"abc"), provenance)),
            CanonicalMember(
                name="literal-array",
                value=_literal(JsonArray(items=(JsonInteger(value=1),)), provenance),
            ),
            CanonicalMember(
                name="literal-object",
                value=_literal(
                    JsonObject(
                        members=(JsonObjectMember(name="field", value=JsonString(value="value")),)
                    ),
                    provenance,
                ),
            ),
            CanonicalMember(
                name="json-object",
                value=JsonObject(
                    members=(JsonObjectMember(name="name", value=JsonString(value="safe")),)
                ),
            ),
            CanonicalMember(
                name="json-array", value=JsonArray(items=(JsonDecimal(value=Decimal("2.5")),))
            ),
            CanonicalMember(name="password-hint", value=JsonString(value="conceal")),
        )
    )

    projected = outline_resource(owner, spec)
    values = {field.path: field.value for field in projected.fields}

    assert values[()] == "object · 28 members"
    assert values[("canonical-array",)] == "array · 2 members"
    assert values[("inline",)] == "inline Schema"
    assert values[("remote",)] == "remote schema (URI concealed)"
    assert values[("authorization",)] == "* REDACTED"
    assert values[("raw-file",)] == "$file as bytes (path concealed)"
    assert values[("converted-file",)] == "$file as string (path concealed)"
    assert values[("method",)] == "<string · 4 chars>"
    assert values[("endpoint",)].startswith("<string · ")
    assert values[("selector",)].startswith("<string · ")
    assert values[("payload",)] == "<string · 9 chars>"
    assert values[("binary",)] == "<bytes · 3 bytes>"
    assert values[("literal-array",)] == "array · 1 members"
    assert values[("literal-object",)] == "object · 1 members"
    assert values[("amount",)] == "<number>"
    assert values[("password-hint",)] == "* REDACTED"
    assert "never-render-this" not in repr(projected)
    assert "private-path" not in repr(projected)
    assert "schema.json" not in repr(projected)
    assert "endpoint-secret" not in repr(projected)
    assert "selector-secret" not in repr(projected)
    assert {(item.target.kind, item.origin) for item in projected.relationships} == {
        (ResourceKind.ENVIRONMENT.value, "composition"),
        (ResourceKind.ENVIRONMENT.value, "link"),
    }

    unknown = outline_resource(owner, object())
    assert unknown.fields[0].label == "value"
    assert unknown.fields[0].category == "structure"


def test_outline_never_treats_arbitrary_payload_leaf_names_or_scalars_as_safe() -> None:
    owner = _identity(ResourceKind.REQUEST, "payload-safety")
    spec = CanonicalObject(
        members=(
            CanonicalMember(
                name="request",
                value=CanonicalObject(
                    members=(
                        CanonicalMember(name="method", value=JsonString(value="POST")),
                        CanonicalMember(
                            name="body",
                            value=JsonObject(
                                members=(
                                    JsonObjectMember(
                                        name="name",
                                        value=JsonString(value="John Doe"),
                                    ),
                                    JsonObjectMember(
                                        name="id",
                                        value=JsonString(value="customer-123"),
                                    ),
                                    JsonObjectMember(name="pin", value=JsonInteger(value=1234)),
                                )
                            ),
                        ),
                    )
                ),
            ),
        )
    )

    projected = outline_resource(owner, spec)
    values = {field.path: field.value for field in projected.fields}
    assert values[("request", "method")] == "POST"
    assert values[("request", "body", "name")] == "<string · 8 chars>"
    assert values[("request", "body", "id")] == "<string · 12 chars>"
    assert values[("request", "body", "pin")] == "<integer>"
    assert "John Doe" not in repr(projected)
    assert "customer-123" not in repr(projected)
    assert "1234" not in repr(projected)


def test_outline_never_exposes_request_shaped_value_payloads() -> None:
    canary = "value-method-secret-canary"
    owner = _identity(ResourceKind.VALUE, "request-shaped-secret")
    spec = CanonicalObject(
        members=(
            CanonicalMember(
                name="request",
                value=CanonicalObject(
                    members=(CanonicalMember(name="method", value=JsonString(value=canary)),)
                ),
            ),
        )
    )

    projected = outline_resource(owner, spec)
    values = {field.path: field.value for field in projected.fields}
    assert values[("request", "method")] == f"<string · {len(canary)} chars>"
    assert canary not in repr(projected)


def test_outline_projection_boundaries_reject_untyped_mutable_data() -> None:
    coordinate = ResourceIdentityCoordinate(
        namespace="local",
        kind=ResourceKind.ENVIRONMENT.value,
        resource_id="baseline",
    )
    field = ProjectOutlineField(
        path=("request",),
        label="request",
        value="object",
        depth=1,
        category="container",
    )
    relationship = ProjectRelationship(path=("environment",), target=coordinate, origin="link")
    assert ProjectResourceOutline(fields=(field,), relationships=(relationship,)).fields == (field,)

    for changes, error, message in (
        ({"path": cast("tuple[str, ...]", ["request"])}, TypeError, "path"),
        ({"path": cast("tuple[str, ...]", (1,))}, TypeError, "path"),
        ({"label": cast("str", 1)}, TypeError, "labels"),
        ({"depth": cast("int", True)}, ValueError, "depth"),
        ({"depth": -1}, ValueError, "depth"),
    ):
        with pytest.raises(error, match=message):
            msgspec.structs.replace(field, **changes)

    for relationship_changes, relationship_error, relationship_message in (
        ({"path": cast("tuple[str, ...]", ["environment"])}, TypeError, "path"),
        ({"path": cast("tuple[str, ...]", (1,))}, TypeError, "path"),
        ({"target": cast("ResourceIdentityCoordinate", object())}, TypeError, "target"),
        ({"origin": ""}, ValueError, "origin"),
        ({"origin": cast("str", 1)}, ValueError, "origin"),
    ):
        with pytest.raises(relationship_error, match=relationship_message):
            msgspec.structs.replace(relationship, **relationship_changes)

    outline = ProjectResourceOutline(fields=(field,), relationships=(relationship,))
    with pytest.raises(TypeError, match="fields"):
        msgspec.structs.replace(outline, fields=cast("tuple[ProjectOutlineField, ...]", [field]))
    with pytest.raises(TypeError, match="fields"):
        msgspec.structs.replace(
            outline,
            fields=cast("tuple[ProjectOutlineField, ...]", (object(),)),
        )
    with pytest.raises(TypeError, match="relationships"):
        msgspec.structs.replace(
            outline,
            relationships=cast("tuple[ProjectRelationship, ...]", [relationship]),
        )
    with pytest.raises(TypeError, match="relationships"):
        msgspec.structs.replace(
            outline,
            relationships=cast("tuple[ProjectRelationship, ...]", (object(),)),
        )
    with pytest.raises(TypeError, match="owner"):
        outline_resource(cast("ResourceIdentity", object()), CanonicalObject(members=()))
