"""Secret-free structural outlines for interactive project exploration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast
from typing import final
from urllib.parse import urlsplit

import msgspec

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import RedactionAction
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.diffs.rules import DiffSeverity
from comparo.core.domain.enums import DomainStrEnum
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
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RuleId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.selectors.http_fields import HttpFieldProjection
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import Sensitivity
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
_REQUEST_ROUTE_PATHS = frozenset({("request", "endpoint"), ("request", "url")})
_SECRET_FIELD_PARTS = frozenset(
    {"authorization", "cookie", "credential", "key", "password", "secret", "token"}
)
_EXACT_KINDS = frozenset({"null", "boolean", "integer", "number", "text", "reference"})
_PROFILE_CONTROL_KINDS = frozenset(
    {
        ResourceKind.ASSERTION_PROFILE,
        ResourceKind.DIFF_PROFILE,
        ResourceKind.GATE_PROFILE,
        ResourceKind.SAFETY_PROFILE,
        ResourceKind.REDACTION_PROFILE,
        ResourceKind.ARTIFACT_PROFILE,
    }
)
_STRUCTURAL_SELECTOR = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(?:[.:/][A-Za-z0-9_$*\[\]-]+)*")
_OPERATION_IDENTITY = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9.-]{0,62}|"
    r"[a-z][a-z0-9.-]*/[A-Za-z][A-Za-z0-9.-]{0,62}@[1-9][0-9]{0,5})"
)
_GATE_COUNT_FIELDS = frozenset(
    {
        "violatedRules",
        "driftedRules",
        "unavailableRules",
        "incomparableRules",
        "incompleteRules",
        "errorRules",
    }
)


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
    safe_preview: str | None = None
    exact_value: str | None = None
    exact_kind: str | None = None
    reference: ResourceCoordinate | None = None

    def __post_init__(self) -> None:
        """Require a fully typed, renderer-independent row."""
        if type(self.path) is not tuple or any(type(part) is not str for part in self.path):
            raise TypeError("project outline path must be an immutable text tuple")
        if any(type(value) is not str for value in (self.label, self.value, self.category)):
            raise TypeError("project outline labels must be text")
        _require_safe_preview(self.safe_preview)
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


def _require_safe_preview(value: str | None) -> None:
    """Require a bounded terminal-safe optional display projection."""
    if value is not None and type(value) is not str:
        raise TypeError("project outline safe preview must be text or None")
    if value is not None and (not value or len(value) > 2048 or not value.isprintable()):
        raise ValueError("project outline safe preview must be bounded printable text")


def _require_reference_shape(
    exact_kind: str | None,
    reference: ResourceCoordinate | None,
) -> None:
    """Keep exact reference display and typed identity structurally inseparable."""
    if reference is not None and type(reference) is not ResourceCoordinate:
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
        coordinate = ResourceCoordinate(
            namespace=target.namespace.value,
            kind=target.kind.value,
            resource_id=target.resource_id.value,
            fingerprint=node.target.fingerprint.digest.hex_value,
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
            safe_preview=_safe_route_preview(node.value, path, owner),
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
            safe_preview=_safe_route_preview(exact, path, owner),
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
    safe_preview: str | None = None,
    exact_value: str | None = None,
    exact_kind: str | None = None,
    reference: ResourceCoordinate | None = None,
) -> None:
    fields.append(
        ProjectOutlineField(
            path=path,
            label="value" if not path else path[-1],
            value=value,
            depth=len(path),
            category=category,
            safe_preview=safe_preview,
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


def _safe_route_preview(
    value: ExactValue,
    path: tuple[str, ...],
    owner: ResourceIdentity,
) -> str | None:
    """Return a bounded authored route while concealing query values and user information."""
    if (
        owner.kind is not ResourceKind.REQUEST
        or path not in _REQUEST_ROUTE_PATHS
        or type(value) is not JsonString
    ):
        return None
    raw = value.value
    try:
        parsed = urlsplit(raw)
        if path == ("request", "url"):
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
            ):
                return "<absolute URL>"
            host = parsed.hostname
            authority = f"[{host}]" if ":" in host else host
            if parsed.port is not None:
                authority = f"{authority}:{parsed.port}"
            route = f"{parsed.scheme}://{authority}{parsed.path or '/'}"
        else:
            route = parsed.path or "/"
        if parsed.query:
            route = f"{route}?…"
        if parsed.fragment:
            route = f"{route}#…"
    except ValueError:
        return "<route unavailable>"
    escaped = json.dumps(route, ensure_ascii=False)[1:-1]
    if not escaped.isprintable():
        escaped = json.dumps(route, ensure_ascii=True)[1:-1]
    return escaped if len(escaped) <= 2048 else f"<route · {len(raw)} chars>"


def _safe_exact_display(
    value: ExactValue,
    path: tuple[str, ...],
    owner: ResourceIdentity,
) -> tuple[str, str] | None:
    """Return a bounded terminal-safe exact value when disclosure is structurally inert."""
    if _sensitive_path(path):
        return None
    if owner.kind is ResourceKind.MATRIX:
        return _safe_matrix_exact_display(value, path)
    if owner.kind is ResourceKind.SCHEMA:
        return _safe_schema_exact_display(value, path)
    if owner.kind in _PROFILE_CONTROL_KINDS or owner.kind in {
        ResourceKind.EXECUTION_PROFILE,
        ResourceKind.EXECUTION_POLICY,
        ResourceKind.SCENARIO,
    }:
        return _safe_resource_control_exact_display(value, path, owner.kind)
    if owner.kind is not ResourceKind.PROJECT:
        return None
    return _safe_project_exact_display(value, path)


def _safe_resource_control_exact_display(
    value: ExactValue,
    path: tuple[str, ...],
    kind: ResourceKind,
) -> tuple[str, str] | None:
    """Expose only validated fields from closed, kind-specific structural grammars."""
    if not path:
        return None
    if kind is ResourceKind.EXECUTION_PROFILE:
        return _safe_execution_profile_control(value, path)
    if kind is ResourceKind.SCENARIO:
        return _safe_scenario_control(value, path)
    if kind in {ResourceKind.ASSERTION_PROFILE, ResourceKind.DIFF_PROFILE}:
        return _safe_rule_profile_control(value, path, kind)
    if kind is ResourceKind.GATE_PROFILE:
        return _safe_gate_profile_control(value, path)
    if kind is ResourceKind.SAFETY_PROFILE:
        return _safe_safety_profile_control(value, path)
    if kind is ResourceKind.REDACTION_PROFILE:
        return _safe_redaction_profile_control(value, path)
    if kind is ResourceKind.ARTIFACT_PROFILE:
        return _safe_artifact_profile_control(value, path)
    # ExecutionPolicy is an external authority document. Until its catalog DTO is typed,
    # the project outline may describe shape but must not disclose arbitrary policy scalars.
    return None


def _safe_execution_profile_control(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose validated authored controls, never effective or value-bearing inputs."""
    if path in {("trials",), ("maximumAttempts",)}:
        return _positive_integer(value)
    if path == ("failFast",) and type(value) is JsonBoolean:
        return _bounded_exact_scalar(value)
    return None


def _safe_scenario_control(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose only validated step topology; step payload fields stay concealed."""
    if len(path) != 3 or path[0] != "steps" or not path[1].isdigit():
        return None
    field = path[2]
    if type(value) is not JsonString:
        return None
    try:
        if field == "id" or field == "after":
            RuleId(value=value.value)
        elif field == "request":
            ResourceId(value=value.value)
        else:
            return None
    except (TypeError, ValueError):
        return None
    return _bounded_exact_text(value.value)


def _safe_rule_profile_control(
    value: ExactValue,
    path: tuple[str, ...],
    kind: ResourceKind,
) -> tuple[str, str] | None:
    """Expose validated rule identity and operation names, never operands or predicates."""
    if len(path) < 3 or path[0] != "rules" or not path[1].isdigit():
        return None
    tail = path[2:]
    common = _safe_rule_common_control(value, tail, kind)
    if common is not None:
        return common
    operation = _safe_rule_operation(value, tail, kind)
    if operation is not None:
        return operation
    selector_tail = _rule_selector_tail(tail)
    return (
        None if selector_tail is None else _safe_structured_selector_control(value, selector_tail)
    )


def _safe_rule_common_control(
    value: ExactValue,
    tail: tuple[str, ...],
    kind: ResourceKind,
) -> tuple[str, str] | None:
    """Expose common validated rule identifiers and closed semantics."""
    if tail == ("id",):
        return _validated_slug(value, RuleId)
    if tail == ("selector",):
        return _validated_selector(value)
    if kind is ResourceKind.DIFF_PROFILE and tail in {
        ("baseline", "selector"),
        ("candidate", "selector"),
    }:
        return _validated_selector(value)
    if tail == ("severity",):
        enum_type = Severity if kind is ResourceKind.ASSERTION_PROFILE else DiffSeverity
        return _validated_enum(value, enum_type)
    if tail == ("quantifier",) and kind is ResourceKind.ASSERTION_PROFILE:
        return _validated_enum(value, Quantifier)
    if tail == ("completeness",):
        completeness_type = (
            CompletenessRequirement
            if kind is ResourceKind.ASSERTION_PROFILE
            else DiffCompletenessRequirement
        )
        return _validated_enum(value, completeness_type)
    if len(tail) == 2 and tail[0] == "tags" and tail[1].isdigit():
        if type(value) is not JsonString:
            return None
        try:
            Metadata(tags=(value.value,))
        except (TypeError, ValueError):
            return None
        return _bounded_exact_text(value.value)
    return None


def _safe_rule_operation(
    value: ExactValue,
    tail: tuple[str, ...],
    kind: ResourceKind,
) -> tuple[str, str] | None:
    """Expose version-neutral operation identities at exact schema-owned paths."""
    if tail in {("operator",), ("operator", "name")}:
        return _validated_operation(value) if kind is ResourceKind.ASSERTION_PROFILE else None
    if kind is ResourceKind.DIFF_PROFILE:
        for name in ("reducer", "pairing", "comparator"):
            if tail in {(name,), (name, "name")}:
                return _validated_operation(value)
    if kind is not ResourceKind.DIFF_PROFILE:
        return None
    for prefix in ((), ("baseline",), ("candidate",)):
        relative = tail[len(prefix) :] if tail[: len(prefix)] == prefix else ()
        if (
            len(relative) in {2, 3}
            and relative[0] == "transforms"
            and relative[1].isdigit()
            and (len(relative) == 2 or relative[2] == "name")
        ):
            return _validated_operation(value)
        if relative in {
            ("reducer",),
            ("reducer", "name"),
        }:
            return _validated_operation(value)
    return None


def _rule_selector_tail(tail: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return a structured selector-relative path for shared or asymmetric rule sides."""
    if tail[:1] == ("selector",):
        return tail[1:]
    if tail[:2] in {("baseline", "selector"), ("candidate", "selector")}:
        return tail[2:]
    return None


def _safe_structured_selector_control(
    value: ExactValue,
    tail: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose selector vocabulary and HTTP field identity while concealing path expressions."""
    if tail == ("source",):
        return _validated_enum(value, SelectorSource)
    if tail == ("view",):
        return _validated_enum(value, BodyViewKind)
    if tail == ("occurrence",) and type(value) is JsonString:
        if value.value in {"terminal", "allAttempts", "allExchanges"}:
            return _bounded_exact_text(value.value)
        return None
    if tail in {("field",), ("field", "name")} and type(value) is JsonString:
        try:
            HeaderName(value=value.value)
        except (TypeError, ValueError):
            return None
        return _bounded_exact_text(value.value)
    if tail == ("field", "projection"):
        return _validated_enum(value, HttpFieldProjection)
    if tail == ("field", "occurrence") and type(value) is JsonInteger and value.value >= 0:
        return _bounded_exact_scalar(value)
    # `path` is intentionally shape-only: JSON Pointer/JSONPath segments may contain payload-like
    # literals even though source/view metadata remains safe to summarize.
    return None


def _safe_gate_profile_control(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose only the closed GateProfile schema and validated typed values."""
    if len(path) == 2 and path[1].isdigit():
        if path[0] == "severities":
            return _validated_enum(value, GateSeverity)
        if path[0] == "environmentRoles":
            return _validated_enum(value, EnvironmentRole)
    if (
        path
        in {
            ("requireCompleteRun",),
            ("requireAssertionCoverage",),
            ("requireDiffCoverage",),
        }
        and type(value) is JsonBoolean
    ):
        return _bounded_exact_scalar(value)
    if len(path) == 3 and path[0] == "thresholds" and path[1].isdigit():
        if path[2] == "severity":
            return _validated_enum(value, GateSeverity)
        if path[2] in _GATE_COUNT_FIELDS and type(value) is JsonInteger and value.value >= 0:
            return _bounded_exact_scalar(value)
    return None


def _safe_safety_profile_control(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose only validated classification and remote-schema restriction fields."""
    if path == ("classification", "responseBody"):
        return _validated_enum(value, Sensitivity)
    if (
        len(path) == 3
        and path[:2] == ("classification", "responseHeaders")
        and path[2].isdigit()
        and type(value) is JsonString
    ):
        try:
            HeaderName(value=value.value)
        except (TypeError, ValueError):
            return None
        return _bounded_exact_text(value.value)
    if path == ("schemas", "allowAny") and type(value) is JsonBoolean:
        return _bounded_exact_scalar(value)
    if (
        len(path) == 3
        and path[:2] == ("schemas", "sources")
        and path[2].isdigit()
        and type(value) is JsonString
        and _safe_schema_source(value.value)
    ):
        return _bounded_exact_text(value.value)
    return None


def _safe_redaction_profile_control(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose validated redaction topology, never patterns or replacement payloads."""
    if path == ("backstop",) and type(value) is JsonBoolean:
        return _bounded_exact_scalar(value)
    if len(path) != 3 or path[0] != "rules" or not path[1].isdigit():
        return None
    if path[2] == "id":
        return _validated_slug(value, RuleId)
    if path[2] == "selector":
        return _validated_selector(value)
    if path[2] == "action":
        return _validated_enum(value, RedactionAction)
    return None


def _safe_artifact_profile_control(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose only implemented capture and persistence controls."""
    if (
        len(path) == 2
        and path[0] == "capture"
        and path[1] in {"requests", "responses", "bodies", "evidence"}
        and type(value) is JsonBoolean
    ):
        return _bounded_exact_scalar(value)
    if path == ("storage", "persist"):
        return _validated_enum(value, ArtifactPersistMode)
    if path == ("storage", "unsafeSource"):
        return _validated_enum(value, UnsafeSourceBehavior)
    return None


def _positive_integer(value: ExactValue) -> tuple[str, str] | None:
    """Return one valid positive authored count without integer truthiness."""
    if type(value) is not JsonInteger or value.value < 1:
        return None
    return _bounded_exact_scalar(value)


def _validated_slug(
    value: ExactValue,
    identifier_type: type[RuleId] | type[ResourceId],
) -> tuple[str, str] | None:
    """Return text only after a domain identifier validates it as structural."""
    if type(value) is not JsonString:
        return None
    try:
        identifier_type(value=value.value)
    except (TypeError, ValueError):
        return None
    return _bounded_exact_text(value.value)


def _validated_selector(value: ExactValue) -> tuple[str, str] | None:
    """Admit only literal-free structural selector notation for Overview display."""
    if (
        type(value) is not JsonString
        or len(value.value) > 256
        or _STRUCTURAL_SELECTOR.fullmatch(value.value) is None
    ):
        return None
    return _bounded_exact_text(value.value)


def _validated_enum[EnumT: DomainStrEnum](
    value: ExactValue,
    enum_type: type[EnumT],
) -> tuple[str, str] | None:
    """Return a scalar only when a closed domain vocabulary accepts it."""
    if type(value) is not JsonString:
        return None
    try:
        enum_type(value.value)
    except ValueError:
        return None
    return _bounded_exact_text(value.value)


def _validated_operation(
    value: ExactValue,
) -> tuple[str, str] | None:
    """Return a version-neutral, payload-free operation identity grammar."""
    if type(value) is not JsonString or _OPERATION_IDENTITY.fullmatch(value.value) is None:
        return None
    return _bounded_exact_text(value.value)


def _safe_schema_source(value: str) -> bool:
    """Accept a non-credentialed HTTP(S) schema source without query or fragment data."""
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _safe_project_exact_display(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose inert Project controls while keeping every value namespace concealed."""
    if path[:1] in {("variables",), ("secrets",)} or path[:2] in {
        ("defaults", "parameters"),
        ("defaults", "variables"),
        ("defaults", "secrets"),
    }:
        return None
    if type(value) in {JsonNull, JsonBoolean, JsonInteger, JsonDecimal}:
        return _bounded_exact_scalar(value)
    if type(value) is not JsonString:
        return None
    text = value.value
    if path[:1] in {("data",), ("sources",)}:
        text = Path(text).name or "<project root>"
    escaped = json.dumps(text, ensure_ascii=True)[1:-1]
    return (escaped, "text") if len(escaped) <= 256 else None


def _safe_matrix_exact_display(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose Matrix control identities, never the case values they select."""
    structural_string = (
        path == ("combine",)
        or (len(path) == 2 and path[0] == "exclude")
        or (path[-1:] == ("id",) and path[:1] in {("axes",), ("cases",), ("include",)})
    )
    if not structural_string or type(value) is not JsonString:
        return None
    return _bounded_exact_text(value.value)


def _safe_schema_exact_display(
    value: ExactValue,
    path: tuple[str, ...],
) -> tuple[str, str] | None:
    """Expose JSON Schema structure and constraints while concealing example payloads."""
    if not path:
        return None
    keyword = path[-1]
    if type(value) is JsonString and (
        keyword in {"type", "format"} or (len(path) == 2 and path[0] == "required")
    ):
        return _bounded_exact_text(value.value)
    if keyword in {
        "additionalProperties",
        "deprecated",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "readOnly",
        "uniqueItems",
        "writeOnly",
    }:
        return _bounded_exact_scalar(value)
    return None


def _bounded_exact_scalar(value: ExactValue) -> tuple[str, str] | None:
    """Render one non-payload scalar under the outline's exact-value ceiling."""
    if type(value) is JsonNull:
        return "null", "null"
    if type(value) is JsonBoolean:
        return ("true" if value.value else "false"), "boolean"
    if type(value) is JsonInteger:
        text = str(value.value)
        return (text, "integer") if len(text) <= 256 else None
    if type(value) is JsonDecimal:
        text = str(value.value)
        return (text, "number") if len(text) <= 256 else None
    if type(value) is JsonString:
        return _bounded_exact_text(value.value)
    return None


def _bounded_exact_text(value: str) -> tuple[str, str] | None:
    """Escape a short exact string for terminal-safe structural presentation."""
    escaped = json.dumps(value, ensure_ascii=True)[1:-1]
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
