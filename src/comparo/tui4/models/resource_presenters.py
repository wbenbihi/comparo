"""Pure kind-specific projections over secret-safe resource summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.content import Content

from comparo.application import ResolvedInspectionState
from comparo.application import ResolvedValueInspectionBatchResult
from comparo.tui4.models.explorer import PROFILE_KINDS
from comparo.tui4.models.explorer import ProjectEnvironmentFact
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.fields import Field
from comparo.tui4.models.resource import ResolvedResourceField

if TYPE_CHECKING:
    from comparo.application import ProjectResourceSummary
    from comparo.application import ResourceCoordinate
    from comparo.application.project_outline import ProjectOutlineField


def _outline_value(summary: ProjectResourceSummary, label: str) -> str | None:
    """Return one conservative outline value by label, when present."""
    if summary.outline is None:
        return None
    for field in summary.outline.fields:
        if field.label == label:
            return field.value
    return None


def _targets(
    summary: ProjectResourceSummary,
    kind: str | frozenset[str],
    *,
    inbound: bool = False,
) -> tuple[str, ...]:
    """Return related resource IDs by target kind and direction."""
    if summary.outline is None:
        return ()
    kinds = {kind} if isinstance(kind, str) else kind
    return tuple(
        relationship.target.resource_id
        for relationship in summary.outline.relationships
        if relationship.target.kind in kinds and (relationship.origin == "used-by") == inbound
    )


@dataclass(frozen=True)
class ResourceSettingDetail:
    """One nested, value-safe resource configuration field."""

    path: tuple[str, ...]
    value: str
    category: str


@dataclass(frozen=True)
class ResourceSetting:
    """One named setting with its complete nested safe outline."""

    path: tuple[str, ...]
    name: str
    value: str
    category: str
    details: tuple[ResourceSettingDetail, ...] = ()


@dataclass(frozen=True)
class ResourceSettingGroup:
    """A semantic group of settings rendered as one explicit section."""

    title: str
    settings: tuple[ResourceSetting, ...]


@dataclass(frozen=True)
class ProjectOverview:
    """Complete value-safe projection for the Project-specific overview widget."""

    baseline: ProjectEnvironmentFact | None
    candidates: tuple[ProjectEnvironmentFact, ...]
    sources: tuple[ResourceSetting, ...]
    variables: tuple[ResourceSetting, ...]
    secrets: tuple[ResourceSetting, ...]
    default_groups: tuple[ResourceSettingGroup, ...]
    additional: tuple[ResourceSetting, ...]


@dataclass(frozen=True)
class EnvironmentOverview:
    """Complete value-safe projection for an Environment overview widget."""

    connection: tuple[ResourceSetting, ...]
    runtime_inputs: tuple[ResourceSetting, ...]
    request_defaults: tuple[ResourceSetting, ...]
    policies: tuple[ResourceSetting, ...]
    health_checks: tuple[ResourceCoordinate, ...]
    additional: tuple[ResourceSetting, ...]


@dataclass(frozen=True)
class RequestOverview:
    """Complete value-safe projection for a Request overview widget."""

    routing: tuple[ResourceSetting, ...]
    parameters: tuple[ResourceSetting, ...]
    inputs: tuple[ResourceSetting, ...]
    payload: tuple[ResourceSetting, ...]
    behavior: tuple[ResourceSetting, ...]
    expansion: tuple[ResourceSetting, ...]
    evaluation: tuple[ResourceSetting, ...]
    policies: tuple[ResourceSetting, ...]
    additional: tuple[ResourceSetting, ...]


@dataclass(frozen=True)
class ResourceDependency:
    """One directed edge in a resource's safe dependency graph."""

    direction: str
    path: tuple[str, ...]
    target_kind: str
    target_id: str
    origin: str


@dataclass(frozen=True)
class ResourceResolvedFields:
    """Masked, frontend-safe resource fields from one resolved batch."""

    fields: tuple[ResolvedResourceField, ...]
    diagnostics: tuple[str, ...] = ()
    incomplete: bool = False


_PROJECT_SOURCE_FIELDS = frozenset({"data", "sources"})
_DEFAULT_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "Selection",
        frozenset({"subjects", "baseline", "candidates", "environments", "matrices"}),
    ),
    (
        "Execution",
        frozenset({"trials", "maximumAttempts", "failFast", "budgets"}),
    ),
    (
        "Inputs",
        frozenset({"parameters", "variables", "secrets"}),
    ),
    (
        "Evaluation & output",
        frozenset({"assertions", "diffs", "gate", "safety", "redaction", "artifacts"}),
    ),
)
_RESOLVED_VALUE_TONES = {
    "null": "value.null",
    "boolean": "value.boolean",
    "integer": "value.integer",
    "number": "value.number",
    "string": "value.text",
    "bytes": "value.number",
    "duration": "value.number",
    "size": "value.number",
    "instant": "value.number",
    "count": "value.integer",
    "ratio": "value.number",
    "media-type": "value.text",
    "http-field-value": "value.text",
}


def _resource_settings(
    entry: ResourceEntry,
    prefix: tuple[str, ...],
    names: frozenset[str] | None = None,
) -> tuple[ResourceSetting, ...]:
    """Group every safe outline field below one configuration namespace."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    grouped: dict[str, list[ProjectOutlineField]] = {}
    for field in outline.fields:
        if len(field.path) <= len(prefix) or field.path[: len(prefix)] != prefix:
            continue
        name = field.path[len(prefix)]
        if names is not None and name not in names:
            continue
        grouped.setdefault(name, []).append(field)

    settings: list[ResourceSetting] = []
    for name, untyped_fields in grouped.items():
        fields = tuple(untyped_fields)
        root_path = (*prefix, name)
        root = next((field for field in fields if field.path == root_path), None)
        if root is None:
            continue
        details = tuple(
            ResourceSettingDetail(
                path=field.path[len(root_path) :],
                value=field.value,
                category=field.category,
            )
            for field in fields
            if field.path != root_path
        )
        settings.append(
            ResourceSetting(
                path=root_path,
                name=name,
                value=root.value,
                category=root.category,
                details=details,
            )
        )
    return tuple(settings)


def project_overview(entry: ResourceEntry) -> ProjectOverview:
    """Return every Project spec field available through the safe outline contract."""
    sources = _resource_settings(entry, (), _PROJECT_SOURCE_FIELDS)
    variables = _resource_settings(entry, ("variables",))
    secrets = _resource_settings(entry, ("secrets",))
    defaults = _resource_settings(entry, ("defaults",))
    grouped_names: set[str] = set()
    default_groups: list[ResourceSettingGroup] = []
    for title, names in _DEFAULT_GROUPS:
        settings = tuple(setting for setting in defaults if setting.name in names)
        grouped_names.update(setting.name for setting in settings)
        if settings:
            default_groups.append(ResourceSettingGroup(title, settings))
    remaining_defaults = tuple(setting for setting in defaults if setting.name not in grouped_names)
    if remaining_defaults:
        default_groups.append(ResourceSettingGroup("Additional defaults", remaining_defaults))

    reserved = _PROJECT_SOURCE_FIELDS | frozenset({"variables", "secrets", "defaults"})
    additional = tuple(
        setting for setting in _resource_settings(entry, ()) if setting.name not in reserved
    )
    environment_defaults = entry.project_environments
    return ProjectOverview(
        baseline=None if environment_defaults is None else environment_defaults.baseline,
        candidates=() if environment_defaults is None else environment_defaults.candidates,
        sources=sources,
        variables=variables,
        secrets=secrets,
        default_groups=tuple(default_groups),
        additional=additional,
    )


_ENVIRONMENT_CONNECTION_FIELDS = frozenset({"baseUrl", "timeout", "tls", "proxy"})
_ENVIRONMENT_RUNTIME_FIELDS = frozenset({"env", "variables", "secrets"})
_ENVIRONMENT_REQUEST_FIELDS = frozenset({"headers", "cookies", "auth"})
_ENVIRONMENT_POLICY_FIELDS = frozenset({"safety", "redaction", "artifacts"})
_ENVIRONMENT_RESERVED_FIELDS = (
    _ENVIRONMENT_CONNECTION_FIELDS
    | _ENVIRONMENT_RUNTIME_FIELDS
    | _ENVIRONMENT_REQUEST_FIELDS
    | _ENVIRONMENT_POLICY_FIELDS
    | frozenset({"healthChecks"})
)


def environment_overview(entry: ResourceEntry) -> EnvironmentOverview:
    """Return every Environment spec field in explicit semantic groups."""
    settings = _resource_settings(entry, ())
    outline = entry.summary.outline
    health_checks = (
        ()
        if outline is None
        else tuple(
            field.reference
            for field in outline.fields
            if len(field.path) == 2
            and field.path[0] == "healthChecks"
            and field.reference is not None
            and field.reference.kind == "Request"
        )
    )
    return EnvironmentOverview(
        connection=tuple(
            setting for setting in settings if setting.name in _ENVIRONMENT_CONNECTION_FIELDS
        ),
        runtime_inputs=tuple(
            setting for setting in settings if setting.name in _ENVIRONMENT_RUNTIME_FIELDS
        ),
        request_defaults=tuple(
            setting for setting in settings if setting.name in _ENVIRONMENT_REQUEST_FIELDS
        ),
        policies=tuple(
            setting for setting in settings if setting.name in _ENVIRONMENT_POLICY_FIELDS
        ),
        health_checks=health_checks,
        additional=tuple(
            setting for setting in settings if setting.name not in _ENVIRONMENT_RESERVED_FIELDS
        ),
    )


def environment_health_check_targets(
    entry: ResourceEntry,
) -> tuple[ResourceCoordinate, ...]:
    """Return exact declared health Request identities in authored order."""
    return environment_overview(entry).health_checks


def environment_health_check_ids(entry: ResourceEntry) -> tuple[str, ...]:
    """Compatibility projection of ordered health Request identities to IDs."""
    return tuple(check.resource_id for check in environment_health_check_targets(entry))


_REQUEST_ROUTING_FIELDS = frozenset({"method", "endpoint", "url"})
_REQUEST_INPUT_FIELDS = frozenset({"headers", "query", "cookies", "auth"})
_REQUEST_PAYLOAD_FIELDS = frozenset({"body", "bodyType"})
_REQUEST_BEHAVIOR_FIELDS = frozenset({"stream", "timeout", "redirects", "retries"})
_REQUEST_EXPANSION_FIELDS = frozenset({"matrices"})
_REQUEST_EVALUATION_FIELDS = frozenset({"responseSchema", "assertions", "diffs"})
_REQUEST_POLICY_FIELDS = frozenset({"safety", "redaction", "artifacts"})
_REQUEST_RESERVED_FIELDS = (
    frozenset({"parameters", "request"})
    | _REQUEST_EXPANSION_FIELDS
    | _REQUEST_EVALUATION_FIELDS
    | _REQUEST_POLICY_FIELDS
)


def request_overview(entry: ResourceEntry) -> RequestOverview:
    """Return every Request field in stable HTTP and execution-oriented groups."""
    request_settings = _resource_settings(entry, ("request",))
    top_level = _resource_settings(entry, ())
    known_request_fields = (
        _REQUEST_ROUTING_FIELDS
        | _REQUEST_INPUT_FIELDS
        | _REQUEST_PAYLOAD_FIELDS
        | _REQUEST_BEHAVIOR_FIELDS
    )
    additional_request = tuple(
        setting for setting in request_settings if setting.name not in known_request_fields
    )
    additional_top_level = tuple(
        setting for setting in top_level if setting.name not in _REQUEST_RESERVED_FIELDS
    )
    return RequestOverview(
        routing=tuple(
            setting for setting in request_settings if setting.name in _REQUEST_ROUTING_FIELDS
        ),
        parameters=_resource_settings(entry, ("parameters",)),
        inputs=tuple(
            setting for setting in request_settings if setting.name in _REQUEST_INPUT_FIELDS
        ),
        payload=tuple(
            setting for setting in request_settings if setting.name in _REQUEST_PAYLOAD_FIELDS
        ),
        behavior=tuple(
            setting for setting in request_settings if setting.name in _REQUEST_BEHAVIOR_FIELDS
        ),
        expansion=tuple(
            setting for setting in top_level if setting.name in _REQUEST_EXPANSION_FIELDS
        ),
        evaluation=tuple(
            setting for setting in top_level if setting.name in _REQUEST_EVALUATION_FIELDS
        ),
        policies=tuple(setting for setting in top_level if setting.name in _REQUEST_POLICY_FIELDS),
        additional=(*additional_request, *additional_top_level),
    )


def resource_dependencies(entry: ResourceEntry) -> tuple[ResourceDependency, ...]:
    """Return every outbound and inverse edge from a resource's safe outline."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    return tuple(
        ResourceDependency(
            direction="used-by" if relationship.origin == "used-by" else "uses",
            path=relationship.path,
            target_kind=relationship.target.kind,
            target_id=relationship.target.resource_id,
            origin=relationship.origin,
        )
        for relationship in outline.relationships
    )


def compiled_exact_fields(entry: ResourceEntry) -> tuple[ResolvedResourceField, ...]:
    """Return safe exact inert/reference values supplied by the application outline."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    return tuple(
        ResolvedResourceField(
            path=field.path,
            value=field.exact_value,
            tone=f"value.{field.exact_kind}",
            reference_kind=None if field.reference is None else field.reference.kind,
        )
        for field in outline.fields
        if field.exact_value is not None and field.exact_kind is not None
    )


def resource_resolution_paths(entry: ResourceEntry) -> tuple[tuple[str, ...], ...]:
    """Select bounded expression roots that the kind-specific Overview displays."""
    kind = entry.summary.coordinate.kind
    if kind == "Environment":
        outline = entry.summary.outline
        if outline is None:
            return ()
        available = {field.path for field in outline.fields}
        return tuple(
            (name,)
            for name in (
                "baseUrl",
                "variables",
                "secrets",
                "headers",
                "cookies",
                "auth",
                "timeout",
                "proxy",
            )
            if (name,) in available
        )
    if kind == "Request":
        outline = entry.summary.outline
        if outline is None:
            return ()
        available = {field.path for field in outline.fields}
        parameters = tuple(
            field.path
            for field in outline.fields
            if len(field.path) == 2 and field.path[0] == "parameters"
        )
        return tuple(
            path
            for path in (
                *parameters,
                ("request", "method"),
                ("request", "endpoint"),
                ("request", "url"),
                ("request", "query"),
                ("request", "headers"),
                ("request", "cookies"),
                ("request", "auth"),
                ("request", "body"),
                ("request", "bodyType"),
                ("request", "stream"),
                ("request", "timeout"),
                ("request", "redirects"),
                ("request", "retries"),
            )
            if path in available
        )
    if kind != "Project":
        return ()
    overview = project_overview(entry)
    paths: list[tuple[str, ...]] = []
    if overview.variables:
        paths.append(("variables",))
    if overview.secrets:
        paths.append(("secrets",))
    outline = entry.summary.outline
    if outline is not None:
        available = {field.path for field in outline.fields}
        paths.extend(
            path
            for path in (
                ("defaults", "parameters"),
                ("defaults", "variables"),
                ("defaults", "secrets"),
            )
            if path in available
        )
    return tuple(dict.fromkeys(paths))


def resolved_fields(
    batch: ResolvedValueInspectionBatchResult,
) -> ResourceResolvedFields:
    """Flatten safe resolved-value nodes without reopening resolver state."""
    fields: list[ResolvedResourceField] = []
    diagnostics: list[str] = []
    incomplete = False
    for result in batch.results:
        inspection = result.inspection
        if inspection is None:
            diagnostic = result.diagnostics[0].code
            diagnostics.append(diagnostic)
            continue
        base = inspection.field_path
        if inspection.state is not ResolvedInspectionState.PRESENT:
            value = f"<{inspection.state.value}>"
            if inspection.code is not None:
                value = f"{value} · {inspection.code}"
            fields.append(ResolvedResourceField(base, value, "warn"))
            continue
        for node in inspection.nodes:
            path = (*base, *node.path)
            if node.masked:
                value = "<masked>"
            elif node.preview is not None:
                value = node.preview
                if node.truncated:
                    value = f"{value}…"
            else:
                value = f"{node.kind.value} · {node.child_count} member(s)"
            fields.append(
                ResolvedResourceField(
                    path=path,
                    value=value,
                    tone=(
                        "value.masked"
                        if node.masked
                        else _RESOLVED_VALUE_TONES.get(node.kind.value, "muted")
                    ),
                )
            )
        if inspection.next_node is not None:
            incomplete = True
    return ResourceResolvedFields(
        fields=tuple(fields),
        diagnostics=tuple(diagnostics),
        incomplete=incomplete,
    )


# Transitional aliases for downstream code that imported the original Project-only names.
ProjectDependency = ResourceDependency
ProjectResolvedFields = ResourceResolvedFields
project_dependencies = resource_dependencies
project_compiled_exact_fields = compiled_exact_fields
project_resolution_paths = resource_resolution_paths
project_resolved_fields = resolved_fields


def environment_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return rows derived from one Environment summary."""
    summary = entry.summary
    facts = entry.facts
    rows = [
        Field("role", facts.role or "—"),
        Field(
            "locality",
            facts.locality or "—",
            tone="danger" if facts.locality == "live" else None,
        ),
    ]
    safety = _targets(summary, "SafetyProfile")
    if safety:
        rows.append(Field("safety", Content(", ".join(safety))))
    variables = _outline_value(summary, "variables")
    if variables:
        rows.append(Field("variables", Content(variables)))
    return tuple(rows)


def request_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return rows derived from one Request summary."""
    summary = entry.summary
    facts = entry.facts
    rows = [Field("method", facts.method or "—")]
    endpoint = _outline_value(summary, "endpoint") or _outline_value(summary, "url")
    if endpoint:
        rows.append(Field("endpoint", Content(endpoint)))
    for label, target in (
        ("matrices", "Matrix"),
        ("response schema", "Schema"),
        ("profiles", PROFILE_KINDS),
    ):
        targets = _targets(summary, target)
        if targets:
            rows.append(Field(label, Content(", ".join(targets))))
    rows.append(Field("streaming", "yes (tagged)" if facts.streaming else "—"))
    return tuple(rows)


def matrix_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return rows derived from one Matrix summary."""
    summary = entry.summary
    rows: list[Field] = []
    for label in ("axes", "cases", "include", "exclude", "combine"):
        value = _outline_value(summary, label)
        if value:
            rows.append(Field(label, Content(value)))
    used_by = _targets(summary, "Request", inbound=True)
    if used_by:
        rows.append(Field("used by", Content(", ".join(used_by))))
    return tuple(rows)


@dataclass(frozen=True)
class MatrixDimension:
    """One safe declared Matrix dimension or explicit-case collection."""

    id: str
    role: str
    shape: str


def matrix_dimensions(entry: ResourceEntry) -> tuple[MatrixDimension, ...]:
    """Describe authored Matrix groups without resolving execution cells locally."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    dimensions = [
        MatrixDimension(field.label, "axis", field.value)
        for field in outline.fields
        if len(field.path) == 2
        and field.path[0] == "axes"
        and field.category == "container"
        and field.value.startswith("array ·")
    ]
    dimensions.extend(
        MatrixDimension(field.label, "cases", field.value)
        for field in outline.fields
        if len(field.path) == 1
        and field.path[0] in {"cases", "include", "exclude"}
        and field.category == "container"
        and field.value.startswith("array ·")
    )
    return tuple(dimensions)


def schema_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Summarize a local or remote response Schema safely."""
    summary = entry.summary
    rows: list[Field] = []
    for label in ("type", "required", "properties", "items", "url"):
        value = _outline_value(summary, label)
        if value:
            rows.append(Field(label, Content(value)))
    used_by = _targets(summary, "Request", inbound=True)
    if used_by:
        rows.append(Field("used by", Content(", ".join(used_by))))
    return tuple(rows)


def value_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Summarize a reusable Value without resolving its expressions."""
    summary = entry.summary
    rows: list[Field] = []
    value = _outline_value(summary, "value") or _outline_value(summary, "spec")
    if value:
        rows.append(Field("shape", Content(value)))
    consumers = (
        tuple(
            relationship.target.resource_id
            for relationship in summary.outline.relationships
            if relationship.origin == "used-by"
        )
        if summary.outline is not None
        else ()
    )
    if consumers:
        rows.append(Field("used by", Content(", ".join(consumers))))
    return tuple(rows)


def profile_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Summarize any policy/profile using safe structural rows."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    rows = [Field("structure", f"{len(outline.fields)} fields")]
    outbound = tuple(
        relationship.target.resource_id
        for relationship in outline.relationships
        if relationship.origin != "used-by"
    )
    inbound = tuple(
        relationship.target.resource_id
        for relationship in outline.relationships
        if relationship.origin == "used-by"
    )
    if outbound:
        rows.append(Field("uses", Content(", ".join(outbound))))
    if inbound:
        rows.append(Field("used by", Content(", ".join(inbound))))
    return tuple(rows)


def generic_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return a safe structural fallback for future or plugin-defined kinds."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    return (
        Field("structure", f"{len(outline.fields)} fields"),
        Field("relationships", str(len(outline.relationships))),
    )
