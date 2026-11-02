"""Shared authored-source sensitivity classification for fingerprints and safe inspection."""

from __future__ import annotations

from urllib.parse import unquote_plus

from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.credentials import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.credentials import MANDATORY_SENSITIVE_QUERY_NAMES

type SourceSensitivityContext = str | None


def child_sensitivity_context(
    name: str,
    *,
    parent: SourceSensitivityContext = None,
) -> SourceSensitivityContext:
    """Return the sensitivity context inherited by one structured child field."""
    if parent == "environment-spec" and name == "env":
        return "environment-env"
    if name in {"headers", "query", "cookies", "auth"}:
        return name
    if name in {"url", "endpoint", "baseUrl"}:
        return "url"
    return None


def structured_credential_name(
    node: MappingNode,
    *,
    context: SourceSensitivityContext,
) -> str | None:
    """Extract an exact structured header/query name without retaining its peer value."""
    if context not in {"headers", "query"}:
        return None
    entries = {entry.name: entry.value for entry in node.entries}
    name = entries.get("name", entries.get("key"))
    if type(name) is not ScalarNode or type(name.value) is not str:
        return None
    return name.value.casefold()


def source_entry_is_sensitive(
    name: str,
    *,
    context: SourceSensitivityContext,
    credential_name: str | None,
) -> bool:
    """Classify fields whose entire authored subtree contains credential material."""
    normalized = name.casefold()
    if name in {"secrets", "cookies", "auth"} or context in {"cookies", "auth"}:
        return True
    if context == "environment-env" and name == "values":
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


def redact_url_credentials(value: str) -> str:
    """Replace credential-shaped URL query values while retaining non-secret URL structure."""
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


def sensitive_source_spans(root: ConfigurationNode) -> tuple[SourceSpan, ...]:
    """Return exact scalar spans the compiler classifies as authored sensitive payloads."""
    spans: list[SourceSpan] = []
    _visit_sensitive_source(root, spans, inherited=False, context=None)
    if type(root) is not MappingNode:
        return tuple(spans)
    fields = {entry.name: entry.value for entry in root.entries}
    kind = fields.get("kind")
    spec = fields.get("spec")
    if type(kind) is not ScalarNode or type(kind.value) is not str or spec is None:
        return tuple(spans)
    _visit_sensitive_source(
        spec,
        spans,
        inherited=kind.value == "Value",
        context="environment-spec" if kind.value == "Environment" else None,
    )
    return tuple(
        sorted(
            set(spans),
            key=lambda span: (
                span.start.source_id.value,
                span.start.document.value,
                span.start.line.value,
                span.start.column.value,
            ),
        )
    )


def _visit_sensitive_source(
    node: ConfigurationNode,
    spans: list[SourceSpan],
    *,
    inherited: bool,
    context: SourceSensitivityContext,
) -> None:
    if type(node) is ScalarNode:
        if inherited or (
            type(node.value) is str
            and (
                "${secrets." in node.value
                or (context == "url" and redact_url_credentials(node.value) != node.value)
            )
        ):
            spans.append(node.span)
        return
    if type(node) is SequenceNode:
        for item in node.items:
            _visit_sensitive_source(
                item,
                spans,
                inherited=inherited,
                context=context,
            )
        return
    credential_name = structured_credential_name(node, context=context)
    for entry in node.entries:
        child_context = child_sensitivity_context(entry.name, parent=context)
        _visit_sensitive_source(
            entry.value,
            spans,
            inherited=inherited
            or source_entry_is_sensitive(
                entry.name,
                context=context,
                credential_name=credential_name,
            ),
            context=child_context,
        )


__all__ = (
    "SourceSensitivityContext",
    "child_sensitivity_context",
    "redact_url_credentials",
    "sensitive_source_spans",
    "source_entry_is_sensitive",
    "structured_credential_name",
)
