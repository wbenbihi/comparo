"""Strict common-envelope parsing for the ``comparo/v1alpha1`` frontend."""

from __future__ import annotations

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import MappingEntry
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ParsedDocument
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.configuration.v1alpha1.ast import V1Alpha1FrontendResult
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Metadata
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Resource
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.metadata import Metadata

API_VERSION = "comparo/v1alpha1"
_ENVELOPE_FIELDS = frozenset({"apiVersion", "kind", "metadata", "spec"})
_METADATA_FIELDS = frozenset({"id", "name", "description", "tags"})

_ROOT_MAPPING = DiagnosticCode(value="configuration.envelope.mapping-required")
_MISSING_FIELD = DiagnosticCode(value="configuration.envelope.field-required")
_UNKNOWN_FIELD = DiagnosticCode(value="configuration.envelope.field-unknown")
_INVALID_FIELD = DiagnosticCode(value="configuration.envelope.field-invalid")
_PROTOTYPE_VERSION = DiagnosticCode(value="configuration.version.prototype-unsupported")
_UNSUPPORTED_VERSION = DiagnosticCode(value="configuration.version.unsupported")
_UNKNOWN_KIND = DiagnosticCode(value="configuration.kind.unknown")
_OPERATOR_KIND = DiagnosticCode(value="configuration.kind.operator-owned")
_METADATA_INVALID = DiagnosticCode(value="configuration.metadata.invalid")


def _issue(code: DiagnosticCode, span: SourceSpan) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        primary=span,
    )


def _entries(mapping: MappingNode) -> dict[str, MappingEntry]:
    return {entry.name: entry for entry in mapping.entries}


def _plain_string(node: object) -> str | None:
    if type(node) is ScalarNode and type(node.value) is str:
        return node.value
    return None


def _parse_tags(node: object) -> tuple[str, ...] | None:
    if type(node) is not SequenceNode:
        return None
    tags: list[str] = []
    for item in node.items:
        tag = _plain_string(item)
        if tag is None:
            return None
        tags.append(tag)
    return tuple(tags)


def _parse_metadata(
    node: ConfigurationNode,
    diagnostics: list[ConfigurationDiagnostic],
) -> V1Alpha1Metadata | None:
    if type(node) is not MappingNode:
        diagnostics.append(_issue(_METADATA_INVALID, node.span))
        return None
    fields = _entries(node)
    diagnostics.extend(
        _issue(_UNKNOWN_FIELD, entry.name_span)
        for entry in node.entries
        if entry.name not in _METADATA_FIELDS
    )
    missing = tuple(name for name in ("id", "name") if name not in fields)
    diagnostics.extend(_issue(_MISSING_FIELD, node.span) for _ in missing)
    resource_id = _plain_string(fields["id"].value) if "id" in fields else None
    name = _plain_string(fields["name"].value) if "name" in fields else None
    description = _plain_string(fields["description"].value) if "description" in fields else None
    tags = _parse_tags(fields["tags"].value) if "tags" in fields else ()
    if resource_id is None or name is None or ("description" in fields and description is None):
        diagnostics.append(_issue(_METADATA_INVALID, node.span))
        return None
    if tags is None:
        diagnostics.append(_issue(_METADATA_INVALID, node.span))
        return None
    try:
        return V1Alpha1Metadata(
            resource_id=ResourceId(value=resource_id),
            details=Metadata(name=name, description=description, tags=tags),
            span=node.span,
        )
    except (TypeError, ValueError):
        diagnostics.append(_issue(_METADATA_INVALID, node.span))
        return None


def _parse_kind(
    fields: dict[str, MappingEntry],
    diagnostics: list[ConfigurationDiagnostic],
) -> ResourceKind | None:
    version = _plain_string(fields["apiVersion"].value)
    if version == "comparo/v1":
        diagnostics.append(_issue(_PROTOTYPE_VERSION, fields["apiVersion"].value.span))
        return None
    if version != API_VERSION:
        diagnostics.append(_issue(_UNSUPPORTED_VERSION, fields["apiVersion"].value.span))
        return None
    kind_name = _plain_string(fields["kind"].value)
    try:
        kind = ResourceKind(kind_name) if kind_name is not None else None
    except ValueError:
        kind = None
    if kind is None:
        diagnostics.append(_issue(_UNKNOWN_KIND, fields["kind"].value.span))
        return None
    if kind is ResourceKind.EXECUTION_POLICY:
        diagnostics.append(_issue(_OPERATOR_KIND, fields["kind"].value.span))
        return None
    return kind


def _parse_document(
    document: ParsedDocument,
    diagnostics: list[ConfigurationDiagnostic],
) -> V1Alpha1Resource | None:
    if type(document.root) is not MappingNode:
        diagnostics.append(_issue(_ROOT_MAPPING, document.root.span))
        return None
    root = document.root
    fields = _entries(root)
    diagnostics.extend(
        _issue(_UNKNOWN_FIELD, entry.name_span)
        for entry in root.entries
        if entry.name not in _ENVELOPE_FIELDS
    )
    missing = tuple(name for name in _ENVELOPE_FIELDS if name not in fields)
    diagnostics.extend(_issue(_MISSING_FIELD, root.span) for _ in missing)
    if missing:
        return None
    kind = _parse_kind(fields, diagnostics)
    if kind is None:
        return None
    metadata = _parse_metadata(fields["metadata"].value, diagnostics)
    if metadata is None:
        return None
    return V1Alpha1Resource(
        kind=kind,
        metadata=metadata,
        spec=fields["spec"].value,
        span=root.span,
    )


def _order(diagnostic: ConfigurationDiagnostic) -> tuple[str, int, int, str]:
    start = diagnostic.primary.start
    return (
        start.source_id.value,
        start.document.value,
        start.line.value,
        diagnostic.code.value,
    )


def parse_v1alpha1_documents(
    documents: tuple[ParsedDocument, ...],
) -> V1Alpha1FrontendResult:
    """Parse source documents into the strict version-specific envelope AST."""
    if type(documents) is not tuple or any(
        type(document) is not ParsedDocument for document in documents
    ):
        raise TypeError("v1alpha1 frontend requires a tuple of ParsedDocument values")
    diagnostics: list[ConfigurationDiagnostic] = []
    resources = tuple(
        resource
        for document in documents
        if (resource := _parse_document(document, diagnostics)) is not None
    )
    return V1Alpha1FrontendResult(
        resources=resources,
        diagnostics=tuple(sorted(diagnostics, key=_order)),
    )
