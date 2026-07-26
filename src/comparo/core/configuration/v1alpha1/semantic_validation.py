"""Cross-field and operation validation for v1alpha1 resources."""

from __future__ import annotations

from collections.abc import Callable

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import MappingEntry
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.v1alpha1.ast import V1Alpha1Resource
from comparo.core.configuration.v1alpha1.operations import ASSERTION_OPERATION_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_COMPARATOR_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_PAIRING_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_REDUCER_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_TRANSFORM_ALIASES
from comparo.core.configuration.v1alpha1.operations import supports_operation
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.identity import ResourceKind

_SEMANTIC_MATRIX = DiagnosticCode(value="configuration.semantic.matrix-shape")
_SEMANTIC_TOPOLOGY = DiagnosticCode(value="configuration.semantic.execution-topology")
_SEMANTIC_REQUEST = DiagnosticCode(value="configuration.semantic.request-shape")
_RULE_SHAPE = DiagnosticCode(value="configuration.semantic.rule-shape")
_OPERATION_STATIC = DiagnosticCode(value="configuration.semantic.operation-static")
_OPERATION_UNSUPPORTED = DiagnosticCode(value="configuration.semantic.operation-unsupported")


def _issue(code: DiagnosticCode, node: ConfigurationNode) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        primary=node.span,
    )


def _entries(node: MappingNode) -> dict[str, MappingEntry]:
    return {entry.name: entry for entry in node.entries}


def _valid_request_node(node: ConfigurationNode) -> bool:
    if type(node) is not MappingNode:
        return False
    entries = _entries(node)
    allowed = {
        "method",
        "endpoint",
        "url",
        "headers",
        "query",
        "cookies",
        "auth",
        "body",
        "bodyType",
        "stream",
        "timeout",
        "redirects",
        "retries",
    }
    if not set(entries) <= allowed or "method" not in entries:
        return False
    method = entries["method"].value
    destination_count = int("endpoint" in entries) + int("url" in entries)
    valid_method = False
    if type(method) is ScalarNode and type(method.value) is str and "${" not in method.value:
        try:
            HttpMethod(value=method.value.upper())
        except ValueError:
            pass
        else:
            valid_method = True
    return valid_method and destination_count == 1


def _operation_name(node: ConfigurationNode) -> tuple[object, ConfigurationNode] | None:
    if type(node) is ScalarNode:
        return node.value, node
    if type(node) is not MappingNode:
        return None
    name = _entries(node).get("name")
    if name is None or type(name.value) is not ScalarNode:
        return None
    return name.value.value, name.value


def _validate_operation(
    node: ConfigurationNode,
    aliases: frozenset[str],
) -> tuple[ConfigurationDiagnostic, ...]:
    operation = _operation_name(node)
    if operation is None:
        return (_issue(_OPERATION_STATIC, node),)
    name, name_node = operation
    if type(name) is not str or "${" in name:
        return (_issue(_OPERATION_STATIC, name_node),)
    if not supports_operation(name, aliases):
        return (_issue(_OPERATION_UNSUPPORTED, name_node),)
    return ()


def _walk_diff_operations(node: ConfigurationNode) -> tuple[ConfigurationDiagnostic, ...]:
    if type(node) is ScalarNode:
        return ()
    if type(node) is SequenceNode:
        return tuple(issue for item in node.items for issue in _walk_diff_operations(item))
    if len(node.entries) == 1 and node.entries[0].name == "$literal":
        return ()
    diagnostics: list[ConfigurationDiagnostic] = []
    operation_fields = {
        "comparator": DIFF_COMPARATOR_ALIASES,
        "pairing": DIFF_PAIRING_ALIASES,
        "reducer": DIFF_REDUCER_ALIASES,
    }
    for entry in node.entries:
        if entry.name == "transforms":
            if type(entry.value) is not SequenceNode:
                diagnostics.append(_issue(_RULE_SHAPE, entry.value))
            else:
                diagnostics.extend(
                    issue
                    for transform in entry.value.items
                    for issue in _validate_operation(transform, DIFF_TRANSFORM_ALIASES)
                )
        elif entry.name in operation_fields:
            diagnostics.extend(_validate_operation(entry.value, operation_fields[entry.name]))
        else:
            diagnostics.extend(_walk_diff_operations(entry.value))
    return tuple(diagnostics)


def _validate_rules(
    node: ConfigurationNode,
    *,
    assertion: bool,
) -> tuple[ConfigurationDiagnostic, ...]:
    if type(node) is not SequenceNode:
        return (_issue(_RULE_SHAPE, node),)
    diagnostics: list[ConfigurationDiagnostic] = []
    required_field = "operator" if assertion else "comparator"
    for rule in node.items:
        if type(rule) is not MappingNode:
            diagnostics.append(_issue(_RULE_SHAPE, rule))
            continue
        entries = _entries(rule)
        operation = entries.get(required_field)
        if operation is None:
            diagnostics.append(_issue(_RULE_SHAPE, rule))
        elif assertion:
            diagnostics.extend(_validate_operation(operation.value, ASSERTION_OPERATION_ALIASES))
        if not assertion:
            diagnostics.extend(_walk_diff_operations(rule))
    return tuple(diagnostics)


def _invalid_execution_topology(node: MappingNode) -> bool:
    entries = _entries(node)
    comparison = "baseline" in entries or "candidates" in entries
    assertions_only = "environments" in entries
    return comparison == assertions_only or ("candidates" in entries and "baseline" not in entries)


type _SemanticValidator = Callable[
    [V1Alpha1Resource, dict[str, MappingEntry]],
    tuple[ConfigurationDiagnostic, ...],
]


def _validate_matrix(
    resource: V1Alpha1Resource,
    entries: dict[str, MappingEntry],
) -> tuple[ConfigurationDiagnostic, ...]:
    present = int("axes" in entries) + int("cases" in entries)
    return () if present == 1 else (_issue(_SEMANTIC_MATRIX, resource.spec),)


def _validate_execution(
    resource: V1Alpha1Resource,
    entries: dict[str, MappingEntry],
) -> tuple[ConfigurationDiagnostic, ...]:
    del entries
    if type(resource.spec) is MappingNode and _invalid_execution_topology(resource.spec):
        return (_issue(_SEMANTIC_TOPOLOGY, resource.spec),)
    return ()


def _validate_project(
    resource: V1Alpha1Resource,
    entries: dict[str, MappingEntry],
) -> tuple[ConfigurationDiagnostic, ...]:
    defaults_entry = entries.get("defaults")
    if defaults_entry is None or type(defaults_entry.value) is not MappingNode:
        return ()
    defaults = defaults_entry.value
    default_entries = _entries(defaults)
    comparison = "baseline" in default_entries or "candidates" in default_entries
    assertions_only = "environments" in default_entries
    if (comparison and assertions_only) or (
        "candidates" in default_entries and "baseline" not in default_entries
    ):
        return (_issue(_SEMANTIC_TOPOLOGY, defaults),)
    return ()


def _validate_request(
    resource: V1Alpha1Resource,
    entries: dict[str, MappingEntry],
) -> tuple[ConfigurationDiagnostic, ...]:
    request = entries.get("request")
    if request is None or not _valid_request_node(request.value):
        return (_issue(_SEMANTIC_REQUEST, resource.spec),)
    return ()


def _validate_assertion_profile(
    resource: V1Alpha1Resource,
    entries: dict[str, MappingEntry],
) -> tuple[ConfigurationDiagnostic, ...]:
    del resource
    rules = entries.get("rules")
    return () if rules is None else _validate_rules(rules.value, assertion=True)


def _validate_diff_profile(
    resource: V1Alpha1Resource,
    entries: dict[str, MappingEntry],
) -> tuple[ConfigurationDiagnostic, ...]:
    del resource
    rules = entries.get("rules")
    return () if rules is None else _validate_rules(rules.value, assertion=False)


_VALIDATORS: dict[ResourceKind, _SemanticValidator] = {
    ResourceKind.PROJECT: _validate_project,
    ResourceKind.REQUEST: _validate_request,
    ResourceKind.MATRIX: _validate_matrix,
    ResourceKind.EXECUTION_PROFILE: _validate_execution,
    ResourceKind.ASSERTION_PROFILE: _validate_assertion_profile,
    ResourceKind.DIFF_PROFILE: _validate_diff_profile,
}


def validate_resource_semantics(
    resource: V1Alpha1Resource,
) -> tuple[ConfigurationDiagnostic, ...]:
    """Return every version-owned semantic error for one resource."""
    if type(resource.spec) is not MappingNode:
        return ()
    validator = _VALIDATORS.get(resource.kind)
    if validator is not None:
        return validator(resource, _entries(resource.spec))
    return ()
