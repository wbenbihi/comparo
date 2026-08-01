"""Explorer catalog and tree projections over safe workspace snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Literal

from comparo.tui4.models.navigation import MenuNode
from comparo.tui4.theme import kind_glyph

if TYPE_CHECKING:
    from comparo.application import ProjectResourceSummary
    from comparo.application import ProjectSourceSummary
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


def coordinate_key(coordinate: ResourceCoordinate) -> str:
    """Return an opaque stable selection key for one exact resource revision."""
    return (
        f"{coordinate.namespace}/{coordinate.kind}/"
        f"{coordinate.resource_id}#{coordinate.fingerprint}"
    )


@dataclass(frozen=True)
class ResourceFacts:
    """Safe display facts derived from one resource summary.

    Method comes from the outline's explicitly safe scalar, Matrix use from
    relationship targets, and locality/streaming from authored tag conventions.
    """

    method: str | None = None
    uses_matrix: bool = False
    streaming: bool = False
    role: EnvironmentRole | None = None
    locality: Locality | None = None


@dataclass(frozen=True)
class ResourceEntry:
    """One selectable resource with its source revision and display facts."""

    key: str
    summary: ProjectResourceSummary
    source: ProjectSourceSummary
    facts: ResourceFacts


@dataclass(frozen=True)
class CatalogSection:
    """One fixed-order tree section."""

    title: str
    entries: tuple[ResourceEntry, ...]


@dataclass(frozen=True)
class ExplorerCatalog:
    """Everything resource navigation needs from one workspace snapshot."""

    project: ResourceEntry
    sections: tuple[CatalogSection, ...]
    entries: dict[str, ResourceEntry]
    kinds: tuple[str, ...]


def environment_roles(project: ProjectResourceSummary) -> dict[str, EnvironmentRole]:
    """Return Environment resource ID to role from Project defaults."""
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
    """Return the explicitly safe exact method scalar from the outline."""
    if summary.outline is None:
        return None
    for field in summary.outline.fields:
        if field.label == "method" and field.category == "literal":
            return field.value
    return None


def _facts(summary: ProjectResourceSummary, roles: dict[str, EnvironmentRole]) -> ResourceFacts:
    """Derive safe display facts for one resource."""
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
    """Build fixed sections and exact-coordinate entries from a safe snapshot."""
    snapshot = result.snapshot
    if snapshot is None:
        return None
    roles = environment_roles(snapshot.project)
    source_by_resource = {
        coordinate: source for source in result.sources for coordinate in source.resources
    }
    entries: dict[str, ResourceEntry] = {}
    by_kind: dict[str, list[ResourceEntry]] = {}
    for resource in snapshot.resources:
        entry = ResourceEntry(
            coordinate_key(resource.coordinate),
            resource,
            source_by_resource[resource.coordinate],
            _facts(resource, roles),
        )
        entries[entry.key] = entry
        by_kind.setdefault(resource.coordinate.kind, []).append(entry)
    project_entry = ResourceEntry(
        coordinate_key(snapshot.project.coordinate),
        snapshot.project,
        source_by_resource[snapshot.project.coordinate],
        ResourceFacts(),
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


def _entry_matches(entry: ResourceEntry, needle: str) -> bool:
    """Filter on kind, name, ID, description, and tags."""
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
    """Return unformatted Project and resource nodes in fixed section order."""
    needle = needle.casefold()
    project = catalog.project.summary
    nodes: list[MenuNode[str]] = [
        MenuNode(catalog.project.key, project.name or project.coordinate.resource_id)
    ]
    for section in catalog.sections:
        entries = [entry for entry in section.entries if _entry_matches(entry, needle)]
        if not entries:
            continue
        nodes.append(
            MenuNode(
                f"section:{section.title}",
                section.title,
                children=tuple(
                    MenuNode(
                        entry.key,
                        entry.summary.name or entry.summary.coordinate.resource_id,
                    )
                    for entry in entries
                ),
            )
        )
    return tuple(nodes)


def legend_items(catalog: ExplorerCatalog) -> tuple[tuple[str, str, str], ...]:
    """Return one glyph/token/label legend item per kind in the project."""
    return tuple((*kind_glyph(kind), kind) for kind in catalog.kinds)
