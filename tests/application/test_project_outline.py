"""Secret-free project outline projection and defensive boundary coverage."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import msgspec
import pytest

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.project_outline import ProjectOutlineField
from comparo.application.project_outline import ProjectRelationship
from comparo.application.project_outline import ProjectResourceOutline
from comparo.application.project_outline import ResourceIdentityCoordinate
from comparo.application.project_outline import outline_resource
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
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
    linked = next(field for field in projected.fields if field.path == ("linked",))
    assert linked.reference == ResourceCoordinate(
        namespace=candidate.namespace.value,
        kind=candidate.kind.value,
        resource_id=candidate.resource_id.value,
        fingerprint=link.fingerprint.digest.hex_value,
    )

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


def test_outline_exposes_only_sanitized_authored_request_routes() -> None:
    endpoint_canary = "endpoint-query-secret"
    userinfo_canary = "userinfo-secret"
    owner = _identity(ResourceKind.REQUEST, "route-preview")
    endpoint = CanonicalObject(
        members=(
            CanonicalMember(
                name="request",
                value=CanonicalObject(
                    members=(
                        CanonicalMember(
                            name="endpoint",
                            value=JsonString(value=f"/customers?token={endpoint_canary}"),
                        ),
                    )
                ),
            ),
        )
    )
    absolute = CanonicalObject(
        members=(
            CanonicalMember(
                name="request",
                value=CanonicalObject(
                    members=(
                        CanonicalMember(
                            name="url",
                            value=JsonString(
                                value=f"https://user:{userinfo_canary}@example.test/status"
                            ),
                        ),
                    )
                ),
            ),
        )
    )

    endpoint_projection = outline_resource(owner, endpoint)
    endpoint_field = next(
        field for field in endpoint_projection.fields if field.path == ("request", "endpoint")
    )
    absolute_projection = outline_resource(owner, absolute)
    absolute_field = next(
        field for field in absolute_projection.fields if field.path == ("request", "url")
    )

    assert endpoint_field.safe_preview == "/customers?…"
    assert endpoint_field.exact_value is None
    assert endpoint_canary not in repr(endpoint_projection)
    assert absolute_field.safe_preview == "<absolute URL>"
    assert userinfo_canary not in repr(absolute_projection)

    terminal_projection = outline_resource(
        owner,
        CanonicalObject(
            members=(
                CanonicalMember(
                    name="request",
                    value=CanonicalObject(
                        members=(
                            CanonicalMember(
                                name="endpoint",
                                value=JsonString(value="/customers/\u202ehidden"),
                            ),
                        )
                    ),
                ),
            )
        ),
    )
    terminal_field = next(
        field for field in terminal_projection.fields if field.path == ("request", "endpoint")
    )
    assert terminal_field.safe_preview == "/customers/\\u202ehidden"
    assert terminal_field.safe_preview.isprintable()


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


def test_outline_exposes_matrix_control_ids_but_not_case_values() -> None:
    owner = _identity(ResourceKind.MATRIX, "audiences")
    secret_value = "private-segment-canary"
    spec = CanonicalObject(
        members=(
            CanonicalMember(
                name="axes",
                value=CanonicalObject(
                    members=(
                        CanonicalMember(
                            name="region",
                            value=CanonicalArray(
                                items=(
                                    CanonicalObject(
                                        members=(
                                            CanonicalMember(
                                                name="id", value=JsonString(value="eu")
                                            ),
                                            CanonicalMember(
                                                name="value",
                                                value=JsonString(value=secret_value),
                                            ),
                                        )
                                    ),
                                )
                            ),
                        ),
                    )
                ),
            ),
            CanonicalMember(name="combine", value=JsonString(value="product")),
            CanonicalMember(
                name="exclude", value=CanonicalArray(items=(JsonString(value="eu-free"),))
            ),
        )
    )

    projected = outline_resource(owner, spec)
    exact = {field.path: field.exact_value for field in projected.fields}

    assert exact[("axes", "region", "0", "id")] == "eu"
    assert exact[("combine",)] == "product"
    assert exact[("exclude", "0")] == "eu-free"
    assert exact[("axes", "region", "0", "value")] is None
    assert secret_value not in repr(projected)


def test_outline_exposes_schema_structure_but_not_payload_examples() -> None:
    owner = _identity(ResourceKind.SCHEMA, "catalog-response")
    example_canary = "customer-payload-canary"
    spec = CanonicalObject(
        members=(
            CanonicalMember(name="type", value=JsonString(value="object")),
            CanonicalMember(
                name="required",
                value=CanonicalArray(items=(JsonString(value="items"),)),
            ),
            CanonicalMember(name="minProperties", value=JsonInteger(value=1)),
            CanonicalMember(name="example", value=JsonString(value=example_canary)),
        )
    )

    projected = outline_resource(owner, spec)
    exact = {field.path: field.exact_value for field in projected.fields}

    assert exact[("type",)] == "object"
    assert exact[("required", "0")] == "items"
    assert exact[("minProperties",)] == "1"
    assert exact[("example",)] is None
    assert example_canary not in repr(projected)


def test_project_outline_exposes_only_bounded_safe_exact_configuration_values() -> None:
    canary = "project-variable-secret-canary"
    owner = _identity(ResourceKind.PROJECT, "demo")
    spec = CanonicalObject(
        members=(
            CanonicalMember(name="data", value=JsonString(value="/private/demo/.comparo")),
            CanonicalMember(
                name="defaults",
                value=CanonicalObject(
                    members=(
                        CanonicalMember(name="maximumAttempts", value=JsonInteger(value=2)),
                        CanonicalMember(name="failFast", value=JsonBoolean(value=False)),
                    )
                ),
            ),
            CanonicalMember(
                name="variables",
                value=ObjectExpr(
                    members=(
                        ObjectExpressionMember(
                            name="privateValue",
                            expression=_literal(
                                JsonString(value=canary),
                                ValueProvenance(
                                    entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),)
                                ),
                            ),
                        ),
                    ),
                    provenance=ValueProvenance(
                        entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),)
                    ),
                ),
            ),
        )
    )

    projected = outline_resource(owner, spec)
    exact = {field.path: field.exact_value for field in projected.fields}
    exact_kinds = {field.path: field.exact_kind for field in projected.fields}
    assert exact[("data",)] == ".comparo"
    assert exact[("defaults", "maximumAttempts")] == "2"
    assert exact[("defaults", "failFast")] == "false"
    assert exact[("variables", "privateValue")] is None
    assert exact_kinds[("data",)] == "text"
    assert exact_kinds[("defaults", "maximumAttempts")] == "integer"
    assert exact_kinds[("defaults", "failFast")] == "boolean"
    assert canary not in repr(projected)


def test_outline_exposes_profile_controls_but_conceals_rule_operands_and_patterns() -> None:
    assertion = outline_resource(
        _identity(ResourceKind.ASSERTION_PROFILE, "checks"),
        CanonicalObject(
            members=(
                CanonicalMember(
                    name="rules",
                    value=CanonicalArray(
                        items=(
                            CanonicalObject(
                                members=(
                                    CanonicalMember(name="id", value=JsonString(value="status-ok")),
                                    CanonicalMember(
                                        name="selector",
                                        value=JsonString(value="response.status"),
                                    ),
                                    CanonicalMember(
                                        name="operator",
                                        value=CanonicalObject(
                                            members=(
                                                CanonicalMember(
                                                    name="name",
                                                    value=JsonString(value="equals"),
                                                ),
                                                CanonicalMember(
                                                    name="expected",
                                                    value=JsonString(value="operand-canary"),
                                                ),
                                            )
                                        ),
                                    ),
                                )
                            ),
                        )
                    ),
                ),
            )
        ),
    )
    redaction = outline_resource(
        _identity(ResourceKind.REDACTION_PROFILE, "safe"),
        CanonicalObject(
            members=(
                CanonicalMember(
                    name="patterns",
                    value=CanonicalArray(items=(JsonString(value="pattern-canary"),)),
                ),
                CanonicalMember(
                    name="backstop",
                    value=JsonBoolean(value=True),
                ),
            )
        ),
    )

    exact = {field.path: field.exact_value for field in assertion.fields}
    assert exact[("rules", "0", "id")] == "status-ok"
    assert exact[("rules", "0", "selector")] == "response.status"
    assert exact[("rules", "0", "operator", "name")] == "equals"
    assert exact[("rules", "0", "operator", "expected")] is None
    assert (
        next(field for field in redaction.fields if field.path == ("patterns", "0")).exact_value
        is None
    )
    assert (
        next(field for field in redaction.fields if field.path == ("backstop",)).exact_value
        == "true"
    )
    encoded = msgspec.json.encode((assertion, redaction))
    assert b"operand-canary" not in encoded
    assert b"pattern-canary" not in encoded


def test_outline_profile_whitelists_never_serialize_payload_canaries() -> None:
    """Every profile family admits exact typed controls without reopening static payloads."""

    def object_value(*members: tuple[str, CanonicalValue]) -> CanonicalObject:
        return CanonicalObject(
            members=tuple(CanonicalMember(name=name, value=value) for name, value in members)
        )

    huge = int("9" * 400)
    canaries = (
        "selector-literal-canary",
        "min-operand-canary",
        "max-operand-canary",
        "exact-operand-canary",
        "baseline-path-canary",
        "candidate-path-canary",
        "transform-operand-canary",
        "reducer-operand-canary",
        "pairing-operand-canary",
        "gate-payload-canary",
        "classification-payload-canary",
        "backstop-replacement-canary",
        "storage-payload-canary",
        "scenario-request-payload-canary",
        "policy-payload-canary",
    )
    resources = (
        outline_resource(
            _identity(ResourceKind.ASSERTION_PROFILE, "assertions"),
            object_value(
                (
                    "rules",
                    CanonicalArray(
                        items=(
                            object_value(
                                ("id", JsonString(value="safe-rule")),
                                (
                                    "selector",
                                    JsonString(
                                        value=(
                                            "response.body[?(@.token == 'selector-literal-canary')]"
                                        )
                                    ),
                                ),
                                (
                                    "operator",
                                    object_value(
                                        ("name", JsonString(value="between")),
                                        ("min", JsonString(value="min-operand-canary")),
                                        ("max", JsonString(value="max-operand-canary")),
                                        ("exact", JsonString(value="exact-operand-canary")),
                                    ),
                                ),
                            ),
                        )
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.DIFF_PROFILE, "diffs"),
            object_value(
                (
                    "rules",
                    CanonicalArray(
                        items=(
                            object_value(
                                ("id", JsonString(value="migration")),
                                (
                                    "baseline",
                                    object_value(
                                        (
                                            "selector",
                                            object_value(
                                                (
                                                    "source",
                                                    JsonString(value="response.body"),
                                                ),
                                                ("view", JsonString(value="json")),
                                                (
                                                    "path",
                                                    JsonString(value="/baseline-path-canary"),
                                                ),
                                            ),
                                        ),
                                        (
                                            "transforms",
                                            CanonicalArray(
                                                items=(
                                                    object_value(
                                                        (
                                                            "name",
                                                            JsonString(value="normalizeText"),
                                                        ),
                                                        (
                                                            "replacement",
                                                            JsonString(
                                                                value="transform-operand-canary"
                                                            ),
                                                        ),
                                                    ),
                                                )
                                            ),
                                        ),
                                        (
                                            "reducer",
                                            object_value(
                                                ("name", JsonString(value="groupBy")),
                                                (
                                                    "path",
                                                    JsonString(value="/reducer-operand-canary"),
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                                (
                                    "candidate",
                                    object_value(
                                        (
                                            "selector",
                                            object_value(
                                                (
                                                    "source",
                                                    JsonString(value="response.body"),
                                                ),
                                                ("view", JsonString(value="json")),
                                                (
                                                    "path",
                                                    JsonString(value="/candidate-path-canary"),
                                                ),
                                            ),
                                        ),
                                    ),
                                ),
                                (
                                    "pairing",
                                    object_value(
                                        ("name", JsonString(value="keyed")),
                                        (
                                            "baselinePath",
                                            JsonString(value="/pairing-operand-canary"),
                                        ),
                                    ),
                                ),
                                ("comparator", JsonString(value="exact")),
                            ),
                        )
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.GATE_PROFILE, "gate"),
            object_value(
                (
                    "thresholds",
                    CanonicalArray(
                        items=(
                            object_value(
                                ("severity", JsonString(value="blocking")),
                                ("violatedRules", JsonInteger(value=huge)),
                                ("payload", JsonString(value="gate-payload-canary")),
                            ),
                        )
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.SAFETY_PROFILE, "safety"),
            object_value(
                (
                    "classification",
                    object_value(
                        ("responseBody", JsonString(value="public")),
                        (
                            "payload",
                            JsonString(value="classification-payload-canary"),
                        ),
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.REDACTION_PROFILE, "redaction"),
            object_value(
                (
                    "backstop",
                    object_value(
                        (
                            "replacement",
                            JsonString(value="backstop-replacement-canary"),
                        ),
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.ARTIFACT_PROFILE, "artifacts"),
            object_value(
                (
                    "storage",
                    object_value(
                        ("persist", JsonString(value="always")),
                        ("payload", JsonString(value="storage-payload-canary")),
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.SCENARIO, "workflow"),
            object_value(
                (
                    "steps",
                    CanonicalArray(
                        items=(
                            object_value(
                                ("id", JsonString(value="safe-step")),
                                (
                                    "request",
                                    JsonString(value="scenario-request-payload-canary /secret"),
                                ),
                            ),
                        )
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.EXECUTION_POLICY, "authority"),
            object_value(
                (
                    "http",
                    object_value(
                        ("payload", JsonString(value="policy-payload-canary")),
                    ),
                ),
            ),
        ),
        outline_resource(
            _identity(ResourceKind.EXECUTION_PROFILE, "huge-count"),
            object_value(("trials", JsonInteger(value=huge))),
        ),
        outline_resource(
            _identity(ResourceKind.PROJECT, "huge-project-count"),
            object_value(
                (
                    "defaults",
                    object_value(("maximumAttempts", JsonInteger(value=huge))),
                ),
            ),
        ),
    )

    encoded = msgspec.json.encode(resources)
    for canary in canaries:
        assert canary.encode() not in encoded
    assert str(huge).encode() not in encoded
    diff_exact = {field.path: field.exact_value for field in resources[1].fields}
    gate_exact = {field.path: field.exact_value for field in resources[2].fields}
    safety_exact = {field.path: field.exact_value for field in resources[3].fields}
    artifact_exact = {field.path: field.exact_value for field in resources[5].fields}
    assert diff_exact[("rules", "0", "baseline", "selector", "source")] == "response.body"
    assert diff_exact[("rules", "0", "baseline", "selector", "path")] is None
    assert diff_exact[("rules", "0", "baseline", "transforms", "0", "name")] == ("normalizeText")
    assert diff_exact[("rules", "0", "baseline", "reducer", "name")] == "groupBy"
    assert diff_exact[("rules", "0", "pairing", "name")] == "keyed"
    assert diff_exact[("rules", "0", "comparator")] == "exact"
    assert gate_exact[("thresholds", "0", "severity")] == "blocking"
    assert gate_exact[("thresholds", "0", "violatedRules")] is None
    assert safety_exact[("classification", "responseBody")] == "public"
    assert artifact_exact[("storage", "persist")] == "always"


def test_outline_projection_boundaries_reject_untyped_mutable_data() -> None:
    coordinate = ResourceIdentityCoordinate(
        namespace="local",
        kind=ResourceKind.ENVIRONMENT.value,
        resource_id="baseline",
    )
    revision = ResourceCoordinate(
        namespace="local",
        kind=ResourceKind.ENVIRONMENT.value,
        resource_id="baseline",
        fingerprint="0" * 64,
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
    referenced = msgspec.structs.replace(
        field,
        exact_value="Environment/baseline",
        exact_kind="reference",
        reference=revision,
    )
    assert referenced.reference == revision

    for changes, error, message in (
        ({"path": cast("tuple[str, ...]", ["request"])}, TypeError, "path"),
        ({"path": cast("tuple[str, ...]", (1,))}, TypeError, "path"),
        ({"label": cast("str", 1)}, TypeError, "labels"),
        ({"safe_preview": cast("str", 1)}, TypeError, "safe preview"),
        ({"safe_preview": ""}, ValueError, "safe preview"),
        ({"safe_preview": "line\nbreak"}, ValueError, "safe preview"),
        ({"safe_preview": "x" * 2049}, ValueError, "safe preview"),
        ({"exact_value": cast("str", 1)}, TypeError, "exact value"),
        ({"exact_value": "one"}, ValueError, "present together"),
        ({"exact_kind": "integer"}, ValueError, "present together"),
        (
            {"exact_value": "one", "exact_kind": cast("str", 1)},
            TypeError,
            "exact kind",
        ),
        ({"exact_value": "one", "exact_kind": "unsupported"}, ValueError, "supported"),
        ({"reference": cast("ResourceCoordinate", object())}, TypeError, "reference"),
        ({"reference": coordinate}, TypeError, "reference"),
        (
            {
                "exact_value": "Environment/baseline",
                "exact_kind": "reference",
            },
            ValueError,
            "reference",
        ),
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
