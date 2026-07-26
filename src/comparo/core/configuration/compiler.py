"""Registry, linker, semantic validation, and normalization for v1alpha1."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import cast
from urllib.parse import unquote_plus

from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompilationResult
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.configuration.directives import compile_inert_value
from comparo.core.configuration.directives import compile_value_expression
from comparo.core.configuration.directives import seal_expression_literals
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import MappingEntry
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.v1alpha1.ast import V1Alpha1FrontendResult
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Resource
from comparo.core.configuration.v1alpha1.frontend import API_VERSION
from comparo.core.configuration.v1alpha1.schema import FieldMode
from comparo.core.configuration.v1alpha1.schema import FieldSchema
from comparo.core.configuration.v1alpha1.schema import ResourceSchema
from comparo.core.configuration.v1alpha1.schema import schema_for
from comparo.core.configuration.v1alpha1.semantic_validation import validate_resource_semantics
from comparo.core.credentials import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.credentials import MANDATORY_SENSITIVE_QUERY_NAMES
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import SourceLocation
from comparo.core.domain.values import JsonString

_SPEC_MAPPING = DiagnosticCode(value="configuration.spec.mapping-required")
_SPEC_FIELD_UNKNOWN = DiagnosticCode(value="configuration.spec.field-unknown")
_SPEC_FIELD_REQUIRED = DiagnosticCode(value="configuration.spec.field-required")
_STATIC_DYNAMIC = DiagnosticCode(value="configuration.spec.static-dynamic")
_DUPLICATE_RESOURCE = DiagnosticCode(value="configuration.resource.duplicate")
_PROJECT_REQUIRED = DiagnosticCode(value="configuration.project.exactly-one")
_PROJECT_LOCATION = DiagnosticCode(value="configuration.project.location")
_REFERENCE_SHAPE = DiagnosticCode(value="configuration.reference.shape")
_REFERENCE_MISSING = DiagnosticCode(value="configuration.reference.missing")
_REFERENCE_KIND = DiagnosticCode(value="configuration.reference.kind")
_REFERENCE_QUALIFICATION = DiagnosticCode(value="configuration.reference.qualification")
_REFERENCE_CYCLE = DiagnosticCode(value="configuration.reference.cycle")
_INLINE_FORBIDDEN = DiagnosticCode(value="configuration.reference.inline-forbidden")

type RegistryKey = tuple[ResourceKind, ResourceId]


def _issue(
    code: DiagnosticCode,
    span: SourceSpan,
    *,
    related: tuple[SourceSpan, ...] = (),
) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        primary=span,
        related=related,
    )


def _mapping_entries(node: MappingNode) -> dict[str, MappingEntry]:
    return {entry.name: entry for entry in node.entries}


def _secure_wire_credentials(
    field_name: str,
    expression: ValueExpression,
) -> ValueExpression:
    """Seal authored credential leaves before canonical state can retain them as public."""
    if field_name in {"auth", "cookies"}:
        return seal_expression_literals(expression)
    if field_name == "headers":
        return _seal_named_credentials(expression, MANDATORY_SENSITIVE_HEADER_NAMES)
    if field_name == "proxy" and type(expression) is ObjectExpr:
        return _seal_object_members(expression, frozenset({"auth"}))
    if field_name != "request" or type(expression) is not ObjectExpr:
        return expression
    members: list[ObjectExpressionMember] = []
    for member in expression.members:
        secured = member.expression
        if member.name in {"auth", "cookies"}:
            secured = seal_expression_literals(secured)
        elif member.name in {"endpoint", "url"}:
            secured = _seal_url_credentials(secured)
        elif member.name == "headers":
            secured = _seal_named_credentials(secured, MANDATORY_SENSITIVE_HEADER_NAMES)
        elif member.name == "query":
            secured = _seal_named_credentials(secured, MANDATORY_SENSITIVE_QUERY_NAMES)
        members.append(ObjectExpressionMember(name=member.name, expression=secured))
    return ObjectExpr(members=tuple(members), provenance=expression.provenance)


def _seal_url_credentials(expression: ValueExpression) -> ValueExpression:
    """Deny public retention of credential-shaped query data embedded in URL text."""
    if (
        type(expression) is LiteralExpr
        and type(expression.value) is JsonString
        and _redact_url_credentials(expression.value.value) != expression.value.value
    ):
        return seal_expression_literals(expression)
    return expression


def _seal_object_members(
    expression: ObjectExpr,
    names: frozenset[str],
) -> ObjectExpr:
    """Seal literal descendants of exact credential-bearing object members."""
    return ObjectExpr(
        members=tuple(
            ObjectExpressionMember(
                name=member.name,
                expression=(
                    seal_expression_literals(member.expression)
                    if member.name in names
                    else member.expression
                ),
            )
            for member in expression.members
        ),
        provenance=expression.provenance,
    )


def _seal_named_credentials(
    expression: ValueExpression,
    names: frozenset[str],
) -> ValueExpression:
    """Seal known credential values in map and ordered-entry collection forms."""
    if type(expression) is ArrayExpr:
        return ArrayExpr(
            items=tuple(_seal_named_credentials(item, names) for item in expression.items),
            provenance=expression.provenance,
        )
    if type(expression) is not ObjectExpr:
        return expression
    fields = {member.name: member.expression for member in expression.members}
    declared_name = fields.get("name", fields.get("key"))
    credential_name = (
        declared_name.value.value.casefold()
        if type(declared_name) is LiteralExpr and type(declared_name.value) is JsonString
        else None
    )
    members = tuple(
        ObjectExpressionMember(
            name=member.name,
            expression=(
                seal_expression_literals(member.expression)
                if member.name.casefold() in names
                or (member.name == "value" and credential_name in names)
                else member.expression
            ),
        )
        for member in expression.members
    )
    return ObjectExpr(members=members, provenance=expression.provenance)


def _fingerprint_scalar(
    value: None | bool | int | Decimal | str,
    *,
    context: str | None,
) -> object:
    if type(value) is Decimal:
        return {"decimal": str(value.normalize())}
    if context == "url" and type(value) is str:
        return _redact_url_credentials(value)
    return value


def _fingerprint_node(
    node: ConfigurationNode,
    *,
    redact: bool = False,
    context: str | None = None,
) -> object:
    if redact:
        return {"sensitive": True}
    if type(node) is ScalarNode:
        return _fingerprint_scalar(node.value, context=context)
    if type(node) is SequenceNode:
        return [_fingerprint_node(item, context=context) for item in node.items]
    credential_name = _structured_credential_name(node, context=context)
    return {
        entry.name: (
            _fingerprint_sensitive_shape(entry.value)
            if context == "environment-env" and entry.name == "values"
            else _fingerprint_node(
                entry.value,
                redact=_fingerprint_entry_is_sensitive(
                    entry.name,
                    context=context,
                    credential_name=credential_name,
                ),
                context=_fingerprint_context(entry.name, parent=context),
            )
        )
        for entry in sorted(node.entries, key=lambda item: item.name)
    }


def _fingerprint_sensitive_shape(node: ConfigurationNode) -> object:
    """Retain authored structure and exact value types without hashing sensitive content."""
    if type(node) is ScalarNode:
        value_type = "null" if node.value is None else type(node.value).__name__
        return {"sensitive": True, "type": value_type}
    if type(node) is SequenceNode:
        return {
            "sensitive": True,
            "type": "sequence",
            "items": [_fingerprint_sensitive_shape(item) for item in node.items],
        }
    return {
        "sensitive": True,
        "type": "mapping",
        "entries": {
            entry.name: _fingerprint_sensitive_shape(entry.value)
            for entry in sorted(node.entries, key=lambda item: item.name)
        },
    }


def _fingerprint_context(name: str, *, parent: str | None = None) -> str | None:
    if parent == "environment-spec" and name == "env":
        return "environment-env"
    if name in {"headers", "query", "cookies", "auth"}:
        return name
    if name in {"url", "endpoint", "baseUrl"}:
        return "url"
    return None


def _structured_credential_name(node: MappingNode, *, context: str | None) -> str | None:
    if context not in {"headers", "query"}:
        return None
    entries = {entry.name: entry.value for entry in node.entries}
    name = entries.get("name", entries.get("key"))
    if type(name) is not ScalarNode or type(name.value) is not str:
        return None
    return name.value.casefold()


def _fingerprint_entry_is_sensitive(
    name: str,
    *,
    context: str | None,
    credential_name: str | None,
) -> bool:
    normalized = name.casefold()
    if name in {"secrets", "cookies", "auth"} or context in {"cookies", "auth"}:
        return True
    if context == "headers":
        return normalized in MANDATORY_SENSITIVE_HEADER_NAMES or (
            name == "value" and credential_name in MANDATORY_SENSITIVE_HEADER_NAMES
        )
    if context == "query":
        return normalized in MANDATORY_SENSITIVE_QUERY_NAMES or (
            name == "value" and credential_name in MANDATORY_SENSITIVE_QUERY_NAMES
        )
    return False


def _redact_url_credentials(value: str) -> str:
    head, separator, remainder = value.partition("?")
    if not separator:
        return value
    query, fragment_separator, fragment = remainder.partition("#")
    redacted: list[str] = []
    for parameter in query.split("&"):
        encoded_name, equals, encoded_value = parameter.partition("=")
        name = unquote_plus(encoded_name).casefold()
        if equals and name in MANDATORY_SENSITIVE_QUERY_NAMES:
            encoded_value = "<sensitive>"
        redacted.append(encoded_name + equals + encoded_value)
    suffix = fragment_separator + fragment if fragment_separator else ""
    return head + separator + "&".join(redacted) + suffix


def _definition(
    resource: V1Alpha1Resource,
    namespace: NamespaceId,
) -> DefinitionRef:
    content = {
        "apiVersion": API_VERSION,
        "kind": resource.kind.value,
        "id": resource.metadata.resource_id.value,
        "metadata": {
            "name": resource.metadata.details.name,
            "description": resource.metadata.details.description,
            "tags": resource.metadata.details.tags,
        },
        "spec": _fingerprint_node(
            resource.spec,
            redact=resource.kind is ResourceKind.VALUE,
            context=("environment-spec" if resource.kind is ResourceKind.ENVIRONMENT else None),
        ),
    }
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return DefinitionRef(
        identity=ResourceIdentity(
            namespace=namespace,
            kind=resource.kind,
            resource_id=resource.metadata.resource_id,
        ),
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value=hashlib.sha256(encoded).hexdigest()),
        ),
    )


class _Compiler:
    """Stateful aggregation shell around otherwise pure compilation operations."""

    def __init__(
        self,
        frontend: V1Alpha1FrontendResult,
        namespace: NamespaceId,
        entry_source: SourceId,
    ) -> None:
        self.frontend = frontend
        self.namespace = namespace
        self.entry_source = entry_source
        self.diagnostics = list(frontend.diagnostics)
        self.registry: dict[RegistryKey, V1Alpha1Resource] = {}
        self.definitions: dict[RegistryKey, DefinitionRef] = {}
        self.reference_edges: list[tuple[RegistryKey, RegistryKey, SourceSpan]] = []
        self.value_cache: dict[RegistryKey, ValueExpression] = {}
        self.value_stack: list[RegistryKey] = []

    def compile(self) -> CompilationResult:
        """Run registry, semantic, linking, and normalization stages fail-closed."""
        self._register()
        project_resource = self._project_resource()
        compiled = tuple(self._compile_resource(resource) for resource in self.registry.values())
        self._validate_reference_cycles()
        if self.diagnostics or project_resource is None:
            return CompilationResult(diagnostics=self._ordered_diagnostics())
        project = next(
            resource
            for resource in compiled
            if resource.definition.identity.kind is ResourceKind.PROJECT
        )
        resources = tuple(
            sorted(
                (
                    resource
                    for resource in compiled
                    if resource.definition.identity.kind is not ResourceKind.PROJECT
                ),
                key=lambda item: (
                    item.definition.identity.kind.value,
                    item.definition.identity.resource_id.value,
                ),
            )
        )
        return CompilationResult(
            project=CompiledProject(
                api_version=API_VERSION,
                namespace=self.namespace,
                project=project,
                resources=resources,
            )
        )

    def _ordered_diagnostics(self) -> tuple[ConfigurationDiagnostic, ...]:
        return tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (
                    item.primary.start.source_id.value,
                    item.primary.start.document.value,
                    item.primary.start.line.value,
                    item.primary.start.column.value,
                    item.code.value,
                ),
            )
        )

    def _register(self) -> None:
        first_spans: dict[RegistryKey, SourceSpan] = {}
        for resource in self.frontend.resources:
            key = (resource.kind, resource.metadata.resource_id)
            if key in self.registry:
                self.diagnostics.append(
                    _issue(
                        _DUPLICATE_RESOURCE,
                        resource.metadata.span,
                        related=(first_spans[key],),
                    )
                )
                continue
            self.registry[key] = resource
            first_spans[key] = resource.metadata.span
            self.definitions[key] = _definition(resource, self.namespace)

    def _project_resource(self) -> V1Alpha1Resource | None:
        projects = tuple(
            resource
            for (kind, _), resource in self.registry.items()
            if kind is ResourceKind.PROJECT
        )
        if len(projects) != 1:
            span = (
                self.frontend.resources[0].span
                if self.frontend.resources
                else (
                    self.frontend.diagnostics[0].primary
                    if self.frontend.diagnostics
                    else _initial_span(self.entry_source)
                )
            )
            self.diagnostics.append(_issue(_PROJECT_REQUIRED, span))
            return None
        project = projects[0]
        source = project.span.start.source_id
        entry_resources = tuple(
            resource
            for resource in self.frontend.resources
            if resource.span.start.source_id == self.entry_source
        )
        first_document = min(
            (resource.span.start.document.value for resource in entry_resources),
            default=project.span.start.document.value,
        )
        if source != self.entry_source or project.span.start.document.value != first_document:
            self.diagnostics.append(_issue(_PROJECT_LOCATION, project.span))
        return project

    def _compile_resource(self, resource: V1Alpha1Resource) -> CompiledResource:
        definition = self.definitions[(resource.kind, resource.metadata.resource_id)]
        self._validate_semantics(resource)
        spec = self._compile_spec(
            schema_for(resource.kind),
            resource.spec,
            owner=definition.identity,
        )
        return CompiledResource(
            definition=definition,
            metadata=resource.metadata.details,
            spec=spec,
            location=resource.span.start,
        )

    def _compile_spec(
        self,
        schema: ResourceSchema,
        node: ConfigurationNode,
        *,
        owner: ResourceIdentity,
    ) -> CanonicalValue:
        if schema.opaque_spec:
            return compile_inert_value(node)
        if type(node) is not MappingNode:
            self.diagnostics.append(_issue(_SPEC_MAPPING, node.span))
            return CanonicalObject(members=())
        entries = _mapping_entries(node)
        fields = {field.name: field for field in schema.fields}
        self.diagnostics.extend(
            _issue(_SPEC_FIELD_UNKNOWN, entry.name_span)
            for entry in node.entries
            if entry.name not in fields
        )
        self.diagnostics.extend(
            _issue(_SPEC_FIELD_REQUIRED, node.span)
            for field in schema.fields
            if field.required and field.name not in entries
        )
        members = tuple(
            CanonicalMember(
                name=entry.name,
                value=self._compile_field(fields[entry.name], entry.value, owner=owner),
            )
            for entry in node.entries
            if entry.name in fields
        )
        return CanonicalObject(members=members)

    def _compile_field(
        self,
        field: FieldSchema,
        node: ConfigurationNode,
        *,
        owner: ResourceIdentity,
    ) -> CanonicalValue:
        if field.mode is FieldMode.STATIC:
            if _contains_dynamic_syntax(node):
                self.diagnostics.append(_issue(_STATIC_DYNAMIC, node.span))
            return compile_inert_value(node)
        if field.mode is FieldMode.STRUCTURED:
            return self._compile_structured(field, node, owner=owner)
        if field.mode in {
            FieldMode.RESOLVABLE,
            FieldMode.ASSERTION_RULES,
            FieldMode.DIFF_RULES,
        }:
            expression = compile_value_expression(
                node,
                owner=owner,
                diagnostics=self.diagnostics,
                resolve_value=self._resolve_value,
                sensitive_literals=field.name == "secrets",
            )
            return _secure_wire_credentials(field.name, expression)
        if field.mode is FieldMode.SCHEMA:
            return self._compile_schema_binding(field, node, owner=owner)
        if field.mode is FieldMode.REFERENCE:
            return self._compile_binding(field, node, owner=owner)
        if type(node) is not SequenceNode:
            self.diagnostics.append(_issue(_REFERENCE_SHAPE, node.span))
            return CanonicalArray(items=())
        return CanonicalArray(
            items=tuple(self._compile_binding(field, item, owner=owner) for item in node.items)
        )

    def _compile_structured(
        self,
        field: FieldSchema,
        node: ConfigurationNode,
        *,
        owner: ResourceIdentity,
    ) -> CanonicalValue:
        if type(node) is not MappingNode:
            self.diagnostics.append(_issue(_SPEC_MAPPING, node.span))
            return CanonicalObject(members=())
        schema = ResourceSchema(kind=owner.kind, fields=field.fields)
        return self._compile_spec(schema, node, owner=owner)

    def _compile_schema_binding(
        self,
        field: FieldSchema,
        node: ConfigurationNode,
        *,
        owner: ResourceIdentity,
    ) -> CanonicalValue:
        reference = _use_reference(node)
        if reference is not None and reference.startswith(("http://", "https://")):
            try:
                return RemoteSchema(uri=reference)
            except ValueError:
                self.diagnostics.append(_issue(_REFERENCE_SHAPE, node.span))
                return CanonicalObject(members=())
        if reference is not None:
            return self._link_reference(field, reference, node.span, owner=owner)
        return compile_inert_value(node)

    def _compile_binding(
        self,
        field: FieldSchema,
        node: ConfigurationNode,
        *,
        owner: ResourceIdentity,
    ) -> CanonicalValue:
        reference = _use_reference(node)
        if reference is not None:
            return self._link_reference(field, reference, node.span, owner=owner)
        if not field.inline or type(node) is not MappingNode:
            self.diagnostics.append(
                _issue(_INLINE_FORBIDDEN if not field.inline else _REFERENCE_SHAPE, node.span)
            )
            return CanonicalObject(members=())
        kind = field.reference_kinds[0]
        fragment = re.sub(r"(?<!^)(?=[A-Z])", "-", field.name).lower()
        origin = InlineDefinitionOrigin(
            owner=owner,
            fragment=InlineFragment(value=f"#{fragment}"),
        )
        spec = cast(
            "CanonicalObject",
            self._compile_spec(schema_for(kind), node, owner=owner),
        )
        return InlineResource(
            definition=InlineDefinitionRef(
                kind=kind,
                origin=origin,
                fingerprint=_inline_fingerprint(kind, origin, node),
            ),
            spec=spec,
        )

    def _link_reference(
        self,
        field: FieldSchema,
        reference: str,
        span: SourceSpan,
        *,
        owner: ResourceIdentity,
    ) -> CanonicalValue:
        target_key = self._reference_key(reference, field.reference_kinds, span)
        if target_key is None:
            return CanonicalObject(members=())
        definition = self.definitions.get(target_key)
        if definition is None:
            self.diagnostics.append(_issue(_REFERENCE_MISSING, span))
            return CanonicalObject(members=())
        owner_key = (owner.kind, owner.resource_id)
        self.reference_edges.append((owner_key, target_key, span))
        return LinkedResource(target=definition)

    def _reference_key(
        self,
        reference: str,
        expected: tuple[ResourceKind, ...],
        span: SourceSpan,
    ) -> RegistryKey | None:
        if "/" in reference:
            kind_name, resource_name = reference.split("/", 1)
            try:
                kind = ResourceKind(kind_name)
                resource_id = ResourceId(value=resource_name)
            except (TypeError, ValueError):
                self.diagnostics.append(_issue(_REFERENCE_SHAPE, span))
                return None
            if kind not in expected:
                self.diagnostics.append(_issue(_REFERENCE_KIND, span))
                return None
            return kind, resource_id
        if len(expected) != 1:
            self.diagnostics.append(_issue(_REFERENCE_QUALIFICATION, span))
            return None
        try:
            return expected[0], ResourceId(value=reference)
        except ValueError:
            self.diagnostics.append(_issue(_REFERENCE_SHAPE, span))
            return None

    def _resolve_value(self, reference: str, span: SourceSpan) -> ValueExpression | None:
        key = self._reference_key(reference, (ResourceKind.VALUE,), span)
        if key is None:
            return None
        if key in self.value_cache:
            return self.value_cache[key]
        resource = self.registry.get(key)
        if resource is None:
            self.diagnostics.append(_issue(_REFERENCE_MISSING, span))
            return None
        if key in self.value_stack:
            self.diagnostics.append(_issue(_REFERENCE_CYCLE, span))
            return None
        self.value_stack.append(key)
        if type(resource.spec) is not MappingNode:
            self.value_stack.pop()
            return compile_value_expression(
                ScalarNode(value=None, span=resource.spec.span),
                owner=self.definitions[key].identity,
                diagnostics=self.diagnostics,
                resolve_value=self._resolve_value,
            )
        entries = _mapping_entries(resource.spec)
        value_node = entries.get("value")
        if value_node is None:
            self.value_stack.pop()
            return compile_value_expression(
                ScalarNode(value=None, span=resource.spec.span),
                owner=self.definitions[key].identity,
                diagnostics=self.diagnostics,
                resolve_value=self._resolve_value,
            )
        expression = compile_value_expression(
            value_node.value,
            owner=self.definitions[key].identity,
            diagnostics=self.diagnostics,
            resolve_value=self._resolve_value,
        )
        self.value_stack.pop()
        self.value_cache[key] = expression
        return expression

    def _validate_semantics(self, resource: V1Alpha1Resource) -> None:
        self.diagnostics.extend(validate_resource_semantics(resource))

    def _validate_reference_cycles(self) -> None:
        adjacency: dict[RegistryKey, tuple[tuple[RegistryKey, SourceSpan], ...]] = {}
        for source, target, span in self.reference_edges:
            adjacency[source] = (*adjacency.get(source, ()), (target, span))
        visited: set[RegistryKey] = set()
        active: set[RegistryKey] = set()

        def visit(node: RegistryKey) -> None:
            if node in visited:
                return
            active.add(node)
            for target, span in adjacency.get(node, ()):
                if target in active:
                    self.diagnostics.append(_issue(_REFERENCE_CYCLE, span))
                else:
                    visit(target)
            active.remove(node)
            visited.add(node)

        for key in self.registry:
            visit(key)


def _use_reference(node: ConfigurationNode) -> str | None:
    if type(node) is not MappingNode or len(node.entries) != 1:
        return None
    entry = node.entries[0]
    if entry.name != "$use" or type(entry.value) is not ScalarNode:
        return None
    return entry.value.value if type(entry.value.value) is str else None


def _initial_span(source_id: SourceId) -> SourceSpan:
    location = SourceLocation(
        source_id=source_id,
        document=PositiveCount(value=1),
        line=PositiveCount(value=1),
        column=PositiveCount(value=1),
    )
    return SourceSpan(start=location, end=location)


def _contains_dynamic_syntax(node: ConfigurationNode) -> bool:
    if type(node) is ScalarNode:
        return type(node.value) is str and "${" in node.value
    if type(node) is SequenceNode:
        return any(_contains_dynamic_syntax(item) for item in node.items)
    return any(
        entry.name.startswith("$") or _contains_dynamic_syntax(entry.value)
        for entry in node.entries
    )


def _inline_fingerprint(
    kind: ResourceKind,
    origin: InlineDefinitionOrigin,
    node: ConfigurationNode,
) -> Fingerprint:
    """Fingerprint anonymous content while redacting authored secret containers."""
    content = {
        "kind": kind.value,
        "owner": {
            "namespace": origin.owner.namespace.value,
            "kind": origin.owner.kind.value,
            "id": origin.owner.resource_id.value,
        },
        "fragment": origin.fragment.value,
        "spec": _fingerprint_node(
            node,
            context="environment-spec" if kind is ResourceKind.ENVIRONMENT else None,
        ),
    }
    encoded = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(encoded).hexdigest()),
    )


def compile_v1alpha1(
    frontend: V1Alpha1FrontendResult,
    *,
    namespace: NamespaceId,
    entry_source: SourceId,
) -> CompilationResult:
    """Compile a strict v1alpha1 frontend result into a canonical project graph."""
    if type(frontend) is not V1Alpha1FrontendResult:
        raise TypeError("compiler frontend must be V1Alpha1FrontendResult")
    if type(namespace) is not NamespaceId:
        raise TypeError("compiler namespace must be a NamespaceId")
    if type(entry_source) is not SourceId:
        raise TypeError("compiler entry source must be a SourceId")
    return _Compiler(frontend, namespace, entry_source).compile()
