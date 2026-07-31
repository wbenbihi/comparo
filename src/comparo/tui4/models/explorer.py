"""Explorer view-models — snapshot facts and presentation builders, no I/O."""

from dataclasses import dataclass
from typing import Literal

from rich.text import Text
from textual.content import Content

from comparo.application import ProjectSourceSummary
from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.project_outline import ProjectOutlineField
from comparo.application.project_outline import ProjectRelationship
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.source_inspection import EffectiveResourceWindow
from comparo.application.source_inspection import SourceWindow
from comparo.tui4.components.list import Field
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.treemenu import MenuNode
from comparo.tui4.fragments import chip
from comparo.tui4.fragments import entity_label
from comparo.tui4.fragments import glyph
from comparo.tui4.fragments import pill
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style

type SectionState = Literal["pending", "loading", "ready", "failed"]
type EnvironmentRole = Literal["baseline", "candidate"]
type Locality = Literal["local", "live"]

CATEGORY_TONES: dict[str, str] = {"sensitive": "danger"}

RELATIONSHIP_COLUMNS = (
    Column("path", "Path"),
    Column("target", "Target"),
    Column("origin", "Origin", width=12),
)

CONSUMER_COLUMNS = (
    Column("target", "Consumer"),
    Column("path", "Via"),
)

PROFILE_KINDS = frozenset(
    {
        "AssertionProfile",
        "DiffProfile",
        "GateProfile",
        "SafetyProfile",
        "RedactionProfile",
        "ArtifactProfile",
        "ExecutionProfile",
    }
)

SECTIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("Environments", frozenset({"Environment"})),
    ("Requests", frozenset({"Request"})),
    ("Matrices", frozenset({"Matrix"})),
    ("Schemas", frozenset({"Schema"})),
    ("Values", frozenset({"Value"})),
    ("Profiles", PROFILE_KINDS),
)

KIND_SECTION_TITLES: dict[str, str] = {
    "Project": "Defaults",
    "Environment": "Environment",
    "Request": "Request",
    "Matrix": "Matrix",
}

LOCAL_TAGS = frozenset({"local"})
LIVE_TAGS = frozenset({"live", "production", "prod"})
STREAM_TAGS = frozenset({"stream", "streaming", "sse"})

_ROLE_PILLS = {
    "baseline": ("base", "pill.baseline"),
    "candidate": ("cand", "pill.candidate"),
}
_LOCALITY_PILLS = {
    "local": ("local", "pill.local"),
    "live": ("live", "pill.live"),
}


def coordinate_key(coordinate: ResourceCoordinate) -> str:
    """Opaque stable selection key for one exact resource revision."""
    return (
        f"{coordinate.namespace}/{coordinate.kind}/"
        f"{coordinate.resource_id}#{coordinate.fingerprint}"
    )


@dataclass(frozen=True)
class ResourceFacts:
    """Safe display facts derived from one resource summary.

    method comes from the outline's explicitly safe scalar; matrix use from
    relationship targets; locality and streaming are authored tag conventions
    (URLs and booleans are concealed by contract).
    """

    method: str | None = None
    uses_matrix: bool = False
    streaming: bool = False
    role: EnvironmentRole | None = None
    locality: Locality | None = None


@dataclass(frozen=True)
class ResourceEntry:
    """One selectable resource with its display facts."""

    key: str
    summary: ProjectResourceSummary
    facts: ResourceFacts


@dataclass(frozen=True)
class CatalogSection:
    """One fixed-order tree section."""

    title: str
    entries: tuple[ResourceEntry, ...]


@dataclass(frozen=True)
class ExplorerCatalog:
    """Everything the Explorer needs from one workspace snapshot."""

    project: ResourceEntry
    sections: tuple[CatalogSection, ...]
    entries: dict[str, ResourceEntry]
    sources: dict[str, ProjectSourceSummary]
    kinds: tuple[str, ...]


def _environment_roles(project: ProjectResourceSummary) -> dict[str, EnvironmentRole]:
    """Environment resource id → role from Project defaults."""
    roles: dict[str, EnvironmentRole] = {}
    if project.outline is None:
        return roles
    for relationship in project.outline.relationships:
        if relationship.target.kind != "Environment":
            continue
        if "baseline" in relationship.path:
            roles[relationship.target.resource_id] = "baseline"
        elif "candidates" in relationship.path:
            roles.setdefault(relationship.target.resource_id, "candidate")
    return roles


def _request_method(summary: ProjectResourceSummary) -> str | None:
    """The explicitly safe exact method scalar from the outline."""
    if summary.outline is None:
        return None
    for field in summary.outline.fields:
        if field.label == "method" and field.category == "literal":
            return field.value
    return None


def _facts(summary: ProjectResourceSummary, roles: dict[str, EnvironmentRole]) -> ResourceFacts:
    """Derive display facts for one resource."""
    tags = set(summary.tags)
    locality: Locality | None = None
    if tags & LOCAL_TAGS:
        locality = "local"
    elif tags & LIVE_TAGS:
        locality = "live"
    uses_matrix = summary.outline is not None and any(
        relationship.target.kind == "Matrix" for relationship in summary.outline.relationships
    )
    return ResourceFacts(
        method=_request_method(summary),
        uses_matrix=uses_matrix,
        streaming=bool(tags & STREAM_TAGS),
        role=roles.get(summary.coordinate.resource_id),
        locality=locality,
    )


def build_catalog(result: ProjectWorkspaceResult) -> ExplorerCatalog | None:
    """Fixed section order, per-resource facts, keys by exact coordinate."""
    snapshot = result.snapshot
    if snapshot is None:
        return None
    roles = _environment_roles(snapshot.project)
    entries: dict[str, ResourceEntry] = {}
    by_kind: dict[str, list[ResourceEntry]] = {}
    for resource in snapshot.resources:
        entry = ResourceEntry(
            coordinate_key(resource.coordinate), resource, _facts(resource, roles)
        )
        entries[entry.key] = entry
        by_kind.setdefault(resource.coordinate.kind, []).append(entry)
    project_entry = ResourceEntry(
        coordinate_key(snapshot.project.coordinate), snapshot.project, ResourceFacts()
    )
    entries[project_entry.key] = project_entry

    sections: list[CatalogSection] = []
    kinds: list[str] = ["Project"]
    consumed: set[str] = set()
    for title, section_kinds in SECTIONS:
        grouped = [entry for kind in sorted(section_kinds) for entry in by_kind.get(kind, [])]
        consumed |= section_kinds
        if grouped:
            sections.append(CatalogSection(title, tuple(grouped)))
            kinds.extend(kind for kind in sorted(section_kinds) if kind in by_kind)
    for kind in sorted(set(by_kind) - consumed):
        sections.append(CatalogSection(f"{kind}s", tuple(by_kind[kind])))
        kinds.append(kind)
    return ExplorerCatalog(
        project=project_entry,
        sections=tuple(sections),
        entries=entries,
        sources={source.source_id: source for source in result.sources},
        kinds=tuple(kinds),
    )


# ---------------------------------------------------------- presentation


def entry_label(entry: ResourceEntry) -> Text:
    """Kind glyph, method chip, name + dim id, pills."""
    summary = entry.summary
    facts = entry.facts
    character, token = kind_glyph(summary.coordinate.kind)
    prefix: list[Text] = [glyph(character, token)]
    if facts.method is not None:
        prefix.append(chip(facts.method, f"method.{facts.method}", width=7))
    pills: list[Text] = []
    if facts.role is not None:
        text, pill_token = _ROLE_PILLS[facts.role]
        pills.append(pill(text, pill_token))
    if facts.locality is not None:
        text, pill_token = _LOCALITY_PILLS[facts.locality]
        pills.append(pill(text, pill_token))
    if facts.uses_matrix:
        pills.append(pill("mtx", "pill.matrix"))
    if facts.streaming:
        pills.append(pill("sse", "pill.stream"))
    return entity_label(
        summary.name,
        summary.coordinate.resource_id,
        prefix=tuple(prefix),
        pills=tuple(pills),
    )


def _entry_matches(entry: ResourceEntry, needle: str) -> bool:
    """Filter on kind, name, id, description, and tags."""
    if not needle:
        return True
    summary = entry.summary
    haystacks = (
        summary.coordinate.kind,
        summary.coordinate.resource_id,
        summary.name or "",
        summary.description or "",
        *summary.tags,
    )
    return any(needle in haystack.casefold() for haystack in haystacks)


def catalog_nodes(catalog: ExplorerCatalog, needle: str = "") -> tuple[MenuNode[str], ...]:
    """Project root plus filtered fixed-order sections."""
    needle = needle.casefold()
    nodes: list[MenuNode[str]] = [MenuNode(catalog.project.key, entry_label(catalog.project))]
    for section in catalog.sections:
        entries = [entry for entry in section.entries if _entry_matches(entry, needle)]
        if not entries:
            continue
        nodes.append(
            MenuNode(
                f"section:{section.title}",
                Text.assemble((section.title, style("label")), (f" {len(entries)}", style("dim"))),
                children=tuple(MenuNode(entry.key, entry_label(entry)) for entry in entries),
            )
        )
    return tuple(nodes)


def legend_items(catalog: ExplorerCatalog) -> tuple[tuple[str, str, str], ...]:
    """One (glyph, token, label) legend item per kind present in this project."""
    return tuple((*kind_glyph(kind), kind) for kind in catalog.kinds)


# ----------------------------------------------------------- detail model


@dataclass(frozen=True)
class Section[T]:
    """One independently loaded slice of resource data."""

    label: str
    state: SectionState = "pending"
    value: T | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ResourceDetail:
    """Everything displayable about one selected resource."""

    key: str
    entry: ResourceEntry
    source: Section[SourceWindow]
    effective: Section[EffectiveResourceWindow]


def initial_detail(entry: ResourceEntry) -> ResourceDetail:
    """Snapshot data ready, fetched sections pending."""
    return ResourceDetail(
        key=entry.key,
        entry=entry,
        source=Section("Authored · masked"),
        effective=Section("Effective"),
    )


def section_subtitle(section: Section) -> str:
    """Disclosure label plus load state or error code."""
    if section.state == "failed":
        return f"{section.label} · {section.error_code}"
    if section.state != "ready":
        return f"{section.label} · {section.state}"
    return section.label


# ------------------------------------------------------- overview fields


def _outline_value(summary: ProjectResourceSummary, label: str) -> str | None:
    """The conservative outline value for one field label, when present."""
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
    """Related resource ids by target kind and direction."""
    if summary.outline is None:
        return ()
    kinds = {kind} if isinstance(kind, str) else kind
    return tuple(
        relationship.target.resource_id
        for relationship in summary.outline.relationships
        if relationship.target.kind in kinds and (relationship.origin == "used-by") == inbound
    )


def identity_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Identity rows shared by every kind."""
    summary = entry.summary
    source = summary.source
    return (
        Field("id", Content(summary.coordinate.resource_id)),
        Field("kind", summary.coordinate.kind),
        Field("name", Content(summary.name) if summary.name else "—"),
        Field("tags", Content(", ".join(summary.tags)) if summary.tags else "—"),
        Field("revision", summary.coordinate.fingerprint[:12]),
        Field("source", Content(f"{source.source} · L{source.line}")),
        Field("description", Content(summary.description) if summary.description else "—"),
    )


def _join(ids: tuple[str, ...]) -> Field | None:
    """Helper for optional list rows."""
    return None


def kind_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Kind-specific rows; empty for kinds with nothing beyond identity."""
    summary = entry.summary
    facts = entry.facts
    kind = summary.coordinate.kind
    rows: list[Field] = []
    if kind == "Project":
        baseline = _targets(summary, "Environment")
        subjects = _targets(summary, "Request")
        gates = _targets(summary, "GateProfile")
        roles = _environment_roles(summary)
        base = [rid for rid, role in roles.items() if role == "baseline"]
        cands = [rid for rid, role in roles.items() if role == "candidate"]
        if base:
            rows.append(Field("baseline", Content(", ".join(base))))
        if cands:
            rows.append(Field("candidates", Content(", ".join(cands))))
        if subjects:
            rows.append(Field("default subjects", Content(", ".join(subjects))))
        if gates:
            rows.append(Field("gate", Content(", ".join(gates))))
        del baseline
    elif kind == "Environment":
        rows.append(Field("role", facts.role or "—"))
        rows.append(
            Field(
                "locality",
                facts.locality or "—",
                tone="danger" if facts.locality == "live" else None,
            )
        )
        safety = _targets(summary, "SafetyProfile")
        if safety:
            rows.append(Field("safety", Content(", ".join(safety))))
        variables = _outline_value(summary, "variables")
        if variables:
            rows.append(Field("variables", Content(variables)))
    elif kind == "Request":
        rows.append(Field("method", facts.method or "—"))
        endpoint = _outline_value(summary, "endpoint") or _outline_value(summary, "url")
        if endpoint:
            rows.append(Field("endpoint", Content(endpoint)))
        matrices = _targets(summary, "Matrix")
        if matrices:
            rows.append(Field("matrices", Content(", ".join(matrices))))
        schemas = _targets(summary, "Schema")
        if schemas:
            rows.append(Field("response schema", Content(", ".join(schemas))))
        profiles = _targets(summary, PROFILE_KINDS)
        if profiles:
            rows.append(Field("profiles", Content(", ".join(profiles))))
        rows.append(Field("streaming", "yes (tagged)" if facts.streaming else "—"))
    elif kind == "Matrix":
        spec = _outline_value(summary, "spec")
        if spec:
            rows.append(Field("shape", Content(spec)))
        used_by = _targets(summary, "Request", inbound=True)
        if used_by:
            rows.append(Field("used by", Content(", ".join(used_by))))
    return tuple(rows)


def kind_section_title(kind: str) -> str:
    """Panel title for the kind-specific section."""
    return KIND_SECTION_TITLES.get(kind, kind)


def dependency_records(entry: ResourceEntry) -> tuple[Record[str], ...]:
    """Outbound dependencies (link and composition)."""
    if entry.summary.outline is None:
        return ()
    return tuple(
        Record(
            id=str(index),
            cells=(
                Content(".".join(relationship.path) or "(root)"),
                Content(f"{relationship.target.kind}/{relationship.target.resource_id}"),
                relationship.origin,
            ),
        )
        for index, relationship in enumerate(entry.summary.outline.relationships)
        if relationship.origin != "used-by"
    )


def consumer_records(entry: ResourceEntry) -> tuple[Record[str], ...]:
    """Inbound used-by relationships."""
    if entry.summary.outline is None:
        return ()
    return tuple(
        Record(
            id=str(index),
            cells=(
                Content(f"{relationship.target.kind}/{relationship.target.resource_id}"),
                Content(".".join(relationship.path) or "(root)"),
            ),
        )
        for index, relationship in enumerate(entry.summary.outline.relationships)
        if relationship.origin == "used-by"
    )


def _outline_rows(fields: tuple[ProjectOutlineField, ...]) -> tuple[Field, ...]:
    """Structural rows, indented by depth, toned by category."""
    return tuple(
        Field(
            key="  " * field.depth + field.label,
            value=Content(field.value),
            tone=CATEGORY_TONES.get(field.category),
        )
        for field in fields
    )


def outline_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Snapshot outline rows."""
    outline = entry.summary.outline
    return _outline_rows(outline.fields) if outline else ()


def effective_fields(window: EffectiveResourceWindow) -> tuple[Field, ...]:
    """Effective structural rows."""
    return _outline_rows(window.fields)


def effective_subtitle(section: Section[EffectiveResourceWindow]) -> str:
    """Effective panel subtitle including paging honesty."""
    if section.state != "ready" or section.value is None:
        return section_subtitle(section)
    window = section.value
    if window.next_field is not None:
        return f"{section.label} · {len(window.fields)} of {window.total_fields} fields"
    return f"{section.label} · {window.total_fields} fields"


def source_subtitle(section: Section[SourceWindow]) -> str:
    """Authored panel subtitle including redaction honesty."""
    if section.state != "ready" or section.value is None:
        return section_subtitle(section)
    window = section.value
    if window.redaction_count:
        return f"{section.label} · {window.redaction_count} redactions"
    return section.label
