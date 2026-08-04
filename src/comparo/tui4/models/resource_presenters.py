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
class ProjectSettingDetail:
    """One nested, value-safe Project configuration field."""

    path: tuple[str, ...]
    value: str
    category: str


@dataclass(frozen=True)
class ProjectSetting:
    """One named Project setting with its complete nested safe outline."""

    path: tuple[str, ...]
    name: str
    value: str
    category: str
    details: tuple[ProjectSettingDetail, ...] = ()


@dataclass(frozen=True)
class ProjectSettingGroup:
    """A semantic group of Project settings rendered as one explicit section."""

    title: str
    settings: tuple[ProjectSetting, ...]


@dataclass(frozen=True)
class ProjectOverview:
    """Complete value-safe projection for the Project-specific overview widget."""

    baseline: ProjectEnvironmentFact | None
    candidates: tuple[ProjectEnvironmentFact, ...]
    sources: tuple[ProjectSetting, ...]
    variables: tuple[ProjectSetting, ...]
    secrets: tuple[ProjectSetting, ...]
    default_groups: tuple[ProjectSettingGroup, ...]
    additional: tuple[ProjectSetting, ...]


@dataclass(frozen=True)
class ProjectDependency:
    """One directed edge in the Project's safe dependency graph."""

    direction: str
    path: tuple[str, ...]
    target_kind: str
    target_id: str
    origin: str


@dataclass(frozen=True)
class ProjectResolvedFields:
    """Masked, frontend-safe Project field rows from one resolved batch."""

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


def _project_settings(
    entry: ResourceEntry,
    prefix: tuple[str, ...],
    names: frozenset[str] | None = None,
) -> tuple[ProjectSetting, ...]:
    """Group every safe outline field below a Project configuration namespace."""
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

    settings: list[ProjectSetting] = []
    for name, untyped_fields in grouped.items():
        fields = tuple(untyped_fields)
        root_path = (*prefix, name)
        root = next((field for field in fields if field.path == root_path), None)
        if root is None:
            continue
        details = tuple(
            ProjectSettingDetail(
                path=field.path[len(root_path) :],
                value=field.value,
                category=field.category,
            )
            for field in fields
            if field.path != root_path
        )
        settings.append(
            ProjectSetting(
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
    sources = _project_settings(entry, (), _PROJECT_SOURCE_FIELDS)
    variables = _project_settings(entry, ("variables",))
    secrets = _project_settings(entry, ("secrets",))
    defaults = _project_settings(entry, ("defaults",))
    grouped_names: set[str] = set()
    default_groups: list[ProjectSettingGroup] = []
    for title, names in _DEFAULT_GROUPS:
        settings = tuple(setting for setting in defaults if setting.name in names)
        grouped_names.update(setting.name for setting in settings)
        if settings:
            default_groups.append(ProjectSettingGroup(title, settings))
    remaining_defaults = tuple(setting for setting in defaults if setting.name not in grouped_names)
    if remaining_defaults:
        default_groups.append(ProjectSettingGroup("Additional defaults", remaining_defaults))

    reserved = _PROJECT_SOURCE_FIELDS | frozenset({"variables", "secrets", "defaults"})
    additional = tuple(
        setting for setting in _project_settings(entry, ()) if setting.name not in reserved
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


def project_dependencies(entry: ResourceEntry) -> tuple[ProjectDependency, ...]:
    """Return every outbound and inverse edge from the Project safe outline."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    return tuple(
        ProjectDependency(
            direction="used-by" if relationship.origin == "used-by" else "uses",
            path=relationship.path,
            target_kind=relationship.target.kind,
            target_id=relationship.target.resource_id,
            origin=relationship.origin,
        )
        for relationship in outline.relationships
    )


def project_compiled_exact_fields(entry: ResourceEntry) -> tuple[ResolvedResourceField, ...]:
    """Return safe exact inert/reference values supplied by the application outline."""
    outline = entry.summary.outline
    if outline is None:
        return ()
    reference_kinds = {
        relationship.path: relationship.target.kind
        for relationship in outline.relationships
        if relationship.origin == "link"
    }
    return tuple(
        ResolvedResourceField(
            path=field.path,
            value=field.exact_value,
            tone=f"value.{field.exact_kind}",
            reference_kind=reference_kinds.get(field.path),
        )
        for field in outline.fields
        if field.exact_value is not None and field.exact_kind is not None
    )


def project_resolution_paths(entry: ResourceEntry) -> tuple[tuple[str, ...], ...]:
    """Select the bounded Project expression roots displayed by Overview."""
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


def project_resolved_fields(
    batch: ResolvedValueInspectionBatchResult,
) -> ProjectResolvedFields:
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
            fields.append(ResolvedResourceField(base, value, "warning"))
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
    return ProjectResolvedFields(
        fields=tuple(fields),
        diagnostics=tuple(diagnostics),
        incomplete=incomplete,
    )


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
