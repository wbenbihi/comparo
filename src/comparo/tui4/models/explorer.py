"""Explorer view-models — snapshot facts and presentation builders, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Literal

from rich.text import Text
from textual.content import Content

from comparo.tui4.components.list import Field
from comparo.tui4.components.treemenu import MenuNode
from comparo.tui4.fragments import chip
from comparo.tui4.fragments import entity_label
from comparo.tui4.fragments import glyph
from comparo.tui4.fragments import pill
from comparo.tui4.theme import kind_glyph
from comparo.tui4.theme import style

if TYPE_CHECKING:
    from comparo.application import ProjectResourceSummary
    from comparo.application import ProjectWorkspaceResult
    from comparo.application.plan_inspection_identity import ResourceCoordinate

type EnvironmentRole = Literal["baseline", "candidate"]
type Locality = Literal["local", "live"]

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
class ResourceDetail:
    """Snapshot information displayed for one selected resource."""

    key: str
    entry: ResourceEntry


def initial_detail(entry: ResourceEntry) -> ResourceDetail:
    """Build the selected resource's immutable detail projection."""
    return ResourceDetail(key=entry.key, entry=entry)


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


def _project_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Rows derived from project defaults."""
    summary = entry.summary
    rows: list[Field] = []
    subjects = _targets(summary, "Request")
    gates = _targets(summary, "GateProfile")
    roles = _environment_roles(summary)
    baseline = [resource_id for resource_id, role in roles.items() if role == "baseline"]
    candidates = [resource_id for resource_id, role in roles.items() if role == "candidate"]
    if baseline:
        rows.append(Field("baseline", Content(", ".join(baseline))))
    if candidates:
        rows.append(Field("candidates", Content(", ".join(candidates))))
    if subjects:
        rows.append(Field("default subjects", Content(", ".join(subjects))))
    if gates:
        rows.append(Field("gate", Content(", ".join(gates))))
    return tuple(rows)


def _environment_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Rows derived from one environment summary."""
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


def _request_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Rows derived from one request summary."""
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


def _matrix_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Rows derived from one matrix summary."""
    summary = entry.summary
    rows: list[Field] = []
    spec = _outline_value(summary, "spec")
    if spec:
        rows.append(Field("shape", Content(spec)))
    used_by = _targets(summary, "Request", inbound=True)
    if used_by:
        rows.append(Field("used by", Content(", ".join(used_by))))
    return tuple(rows)


def kind_fields(entry: ResourceEntry) -> tuple[Field, ...]:
    """Return kind-specific rows, or no rows for generic resources."""
    kind = entry.summary.coordinate.kind
    if kind == "Project":
        return _project_fields(entry)
    if kind == "Environment":
        return _environment_fields(entry)
    if kind == "Request":
        return _request_fields(entry)
    if kind == "Matrix":
        return _matrix_fields(entry)
    return ()
