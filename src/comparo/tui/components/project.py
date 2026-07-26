"""Native Textual Explorer catalog and kind-aware inspector components."""

from __future__ import annotations

from collections import defaultdict
from typing import final
from typing import override

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Button
from textual.widgets import Label
from textual.widgets import Static
from textual.widgets import Tab
from textual.widgets import Tabs
from textual.widgets import Tree
from textual.widgets._tree import TreeNode

from comparo.application.project_outline import ProjectOutlineField
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.tui.components.project_overview import ResourceOverview
from comparo.tui.components.project_tables import ResourceOutlineTable
from comparo.tui.components.project_tables import ResourceRelationshipTable
from comparo.tui.presentation import safe_terminal_text

_KIND_ORDER = (
    "Project",
    "Request",
    "Scenario",
    "Environment",
    "Matrix",
    "Schema",
    "ExecutionProfile",
    "AssertionProfile",
    "DiffProfile",
    "GateProfile",
    "SafetyProfile",
    "RedactionProfile",
    "ArtifactProfile",
    "ExecutionPolicy",
    "Value",
)
_KIND_LABELS = {
    "Project": "PROJECT",
    "Request": "REQ",
    "Scenario": "SCENARIO",
    "Environment": "ENV",
    "Matrix": "MATRIX",
    "Schema": "SCHEMA",
    "ExecutionProfile": "EXEC",
    "AssertionProfile": "ASSERT",
    "DiffProfile": "DIFF",
    "GateProfile": "GATE",
    "SafetyProfile": "SAFETY",
    "RedactionProfile": "REDACT",
    "ArtifactProfile": "ARTIFACT",
    "ExecutionPolicy": "POLICY",
    "Value": "VALUE",
}
_FACETS = {
    "Request": ("Overview", "Resolved", "Authored", "Relationships"),
    "Environment": ("Overview", "Available values", "Sources", "Relationships"),
    "Schema": ("Overview", "Document", "References", "Provenance"),
    "Matrix": ("Overview", "Axes & cases", "Consumers", "Authored"),
    "Project": ("Overview", "Resources", "Sources", "Provenance"),
}
_ACTIONABLE_KINDS = frozenset({"Request", "Scenario", "ExecutionProfile"})


def _facets(kind: str) -> tuple[str, ...]:
    return _FACETS.get(kind, ("Overview", "Effective", "Relationships", "Provenance"))


def _facet_id(label: str) -> str:
    return "explorer-facet-" + label.lower().replace(" ", "-").replace("&", "and")


def _kind_matches(kind: str, selected: str) -> bool:
    if selected == "all":
        return True
    if selected == "profile":
        return kind.endswith("Profile")
    aliases = {"env": "environment", "request": "request"}
    return kind.casefold() == aliases.get(selected, selected)


ProjectResourceItem = TreeNode[ProjectResourceSummary | None]


@final
class ProjectResourceList(Tree[ProjectResourceSummary | None]):
    """Filterable native Tree grouped by resource kind with immediate preview events."""

    @final
    class ResourceHighlighted(Message):
        """The Tree cursor moved to one resource."""

        def __init__(self, resource: ProjectResourceSummary) -> None:
            """Retain the highlighted safe resource."""
            super().__init__()
            self.resource = resource

    @final
    class ResourceSelected(Message):
        """A resource was explicitly activated."""

        def __init__(self, resource: ProjectResourceSummary) -> None:
            """Retain the activated safe resource."""
            super().__init__()
            self.resource = resource

    def __init__(
        self,
        resources: tuple[ProjectResourceSummary, ...],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Create one native catalog from safe immutable summaries."""
        if type(resources) is not tuple or any(
            type(resource) is not ProjectResourceSummary for resource in resources
        ):
            raise TypeError("project resource catalog requires immutable safe summaries")
        self._resources = resources
        self._query = ""
        self._kind_filter = "all"
        self._resource_nodes: dict[
            tuple[str, str, str], TreeNode[ProjectResourceSummary | None]
        ] = {}
        super().__init__("PROJECT RESOURCES", data=None, id=id, classes=classes)
        self.show_root = False

    @override
    def on_mount(self) -> None:
        """Populate and select the first resource after native Tree mount."""
        self._rebuild()

    @property
    def selected_resource(self) -> ProjectResourceSummary | None:
        """Return the resource under the native Tree cursor."""
        node = self.cursor_node
        data = None if node is None else node.data
        return data if type(data) is ProjectResourceSummary else None

    def filter(self, query: str) -> None:
        """Apply a case-insensitive identity, metadata, kind, and tag filter."""
        if type(query) is not str:
            raise TypeError("project resource filter must be text")
        normalized = query.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        if self.is_mounted:
            self._rebuild()

    def filter_kind(self, kind_filter: str) -> None:
        """Limit the catalog to one product-facing kind family."""
        if type(kind_filter) is not str:
            raise TypeError("project resource kind filter must be text")
        normalized = kind_filter.casefold()
        if normalized == self._kind_filter:
            return
        self._kind_filter = normalized
        if self.is_mounted:
            self._rebuild()

    def show_resource(self, resource: ProjectResourceSummary) -> None:
        """Move the native cursor to an exact visible resource."""
        if type(resource) is not ProjectResourceSummary:
            raise TypeError("project resource selection must be typed")
        coordinate = resource.coordinate
        node = self._resource_nodes.get(
            (coordinate.namespace, coordinate.kind, coordinate.resource_id)
        )
        if node is not None:
            self.move_cursor(node)
            self.scroll_to_node(node, animate=False)

    @on(Tree.NodeHighlighted)
    def highlight_resource(
        self, event: Tree.NodeHighlighted[ProjectResourceSummary | None]
    ) -> None:
        """Publish cursor movement immediately; Enter is not required for preview."""
        if type(event.node.data) is ProjectResourceSummary:
            self.post_message(self.ResourceHighlighted(event.node.data))

    @on(Tree.NodeSelected)
    def select_resource(self, event: Tree.NodeSelected[ProjectResourceSummary | None]) -> None:
        """Translate native activation to the stable typed message."""
        if type(event.node.data) is ProjectResourceSummary:
            self.post_message(self.ResourceSelected(event.node.data))

    def _rebuild(self) -> None:
        previous = self.selected_resource
        self.reset("PROJECT RESOURCES", data=None)
        self._resource_nodes.clear()
        grouped: dict[str, list[ProjectResourceSummary]] = defaultdict(list)
        for resource in self._resources:
            if self._matches(resource):
                grouped[resource.coordinate.kind].append(resource)
        for kind in (*_KIND_ORDER, *sorted(set(grouped).difference(_KIND_ORDER))):
            resources = grouped.get(kind)
            if not resources:
                continue
            branch = self.root.add(
                Text.assemble((kind.upper(), "bold"), (f"  {len(resources)}", "dim")),
                data=None,
                expand=True,
            )
            for resource in resources:
                node = branch.add_leaf(_resource_label(resource), data=resource)
                coordinate = resource.coordinate
                self._resource_nodes[
                    (coordinate.namespace, coordinate.kind, coordinate.resource_id)
                ] = node
        self.root.expand_all()
        target = previous
        if target is None or not self._matches(target):
            target = next(iter(grouped.values()), [None])[0]
        if type(target) is ProjectResourceSummary:
            self.call_after_refresh(self.show_resource, target)

    def _matches(self, resource: ProjectResourceSummary) -> bool:
        if not _kind_matches(resource.coordinate.kind, self._kind_filter):
            return False
        if not self._query:
            return True
        haystack = " ".join(
            (
                resource.coordinate.kind,
                resource.coordinate.resource_id,
                resource.name or "",
                resource.description or "",
                *resource.tags,
            )
        ).casefold()
        return self._query in haystack


@final
class ProjectResourceDetail(VerticalScroll):
    """Kind-aware, facet-based resource inspector using safe application projections."""

    @final
    class UseInCompare(Message):
        """The resource's semantic Compare action was clicked."""

        def __init__(self, resource: ProjectResourceSummary) -> None:
            """Retain the safe resource chosen for Compare."""
            super().__init__()
            self.resource = resource

    def __init__(
        self,
        resource: ProjectResourceSummary | None = None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize one inspector with an optional resource selection."""
        super().__init__(id=id, classes=classes)
        self._resource = resource
        self._facet = "Overview"

    @property
    def resource(self) -> ProjectResourceSummary | None:
        """Return the currently previewed resource."""
        return self._resource

    @override
    def compose(self) -> ComposeResult:
        """Compose the selected resource's applicable facets and active reading surface."""
        resource = self._resource
        if resource is None:
            yield Static("Select a project resource to inspect it.", markup=False)
            return
        coordinate = resource.coordinate
        with Horizontal(classes="explorer-inspector-heading"):
            with Vertical(classes="explorer-inspector-title-block"):
                yield Label(
                    safe_terminal_text(resource.name or coordinate.resource_id),
                    classes="explorer-inspector-title",
                    markup=False,
                )
                yield Static(_resource_subtitle(resource), classes="explorer-inspector-subtitle")
            if coordinate.kind in _ACTIONABLE_KINDS:
                yield Button("Use in Compare", id="explorer-use-in-compare", variant="primary")
        yield Static(_metadata_line(resource), classes="explorer-inspector-metadata", markup=False)
        facets = _facets(coordinate.kind)
        if self._facet not in facets:
            self._facet = facets[0]
        yield Tabs(
            *(Tab(label, id=_facet_id(label)) for label in facets),
            active=_facet_id(self._facet),
            id="explorer-facets",
        )
        yield from self._facet_content(resource, self._facet)

    @override
    def render(self) -> str:
        """Provide a deterministic text fallback for snapshots and assistive renderers."""
        resource = self._resource
        return "Select a project resource" if resource is None else _overview_text(resource)

    def show_resource(self, resource: ProjectResourceSummary | None) -> None:
        """Immediately switch inspector context while preserving the component identity."""
        if resource is not None and type(resource) is not ProjectResourceSummary:
            raise TypeError("project resource detail requires a typed summary")
        if resource == self._resource:
            return
        self._resource = resource
        self._facet = "Overview"
        if self.is_mounted:
            self.refresh(recompose=True)

    @on(Tabs.TabActivated, "#explorer-facets")
    def activate_facet(self, event: Tabs.TabActivated) -> None:
        """Switch the inspector reading surface without changing catalog selection."""
        label = event.tab.label.plain
        if label == self._facet:
            return
        self._facet = label
        self.refresh(recompose=True)

    @on(Button.Pressed, "#explorer-use-in-compare")
    def request_compare(self) -> None:
        """Route pointer activation through the same semantic action as the key binding."""
        if self._resource is not None:
            self.post_message(self.UseInCompare(self._resource))

    def _facet_content(
        self,
        resource: ProjectResourceSummary,
        facet: str,
    ) -> ComposeResult:
        normalized = facet.casefold()
        if facet == "Overview":
            yield ResourceOverview(resource)
            return
        if normalized in {"relationships", "references", "consumers"}:
            yield ResourceRelationshipTable(resource, classes="explorer-detail-table")
            return
        if normalized in {"sources", "provenance"}:
            yield Label("PROVENANCE", classes="explorer-section-title")
            yield Static(_provenance_text(resource), classes="explorer-provenance", markup=False)
            return
        yield ResourceOutlineTable(resource, classes="explorer-detail-table")


def _resource_label(resource: ProjectResourceSummary) -> Text:
    coordinate = resource.coordinate
    label = _KIND_LABELS.get(coordinate.kind, coordinate.kind.upper())
    name = safe_terminal_text(resource.name or coordinate.resource_id, maximum_characters=72)
    suffix = _request_summary(resource)
    return Text.assemble(
        (f"{label:<8}", "dim"),
        (name, "bold"),
        ((f"  {suffix}" if suffix else ""), "dim"),
        (f"\n{'':8}{coordinate.resource_id}", "dim"),
    )


def _resource_subtitle(resource: ProjectResourceSummary) -> str:
    request = _request_summary(resource)
    kind = _KIND_LABELS.get(resource.coordinate.kind, resource.coordinate.kind.upper())
    return safe_terminal_text(f"{kind}  {request}" if request else kind)


def _request_summary(resource: ProjectResourceSummary) -> str:
    if resource.coordinate.kind != "Request" or resource.outline is None:
        return ""
    method = _outline_value(resource.outline.fields, ("request", "method"))
    endpoint = _outline_value(resource.outline.fields, ("request", "endpoint"))
    return " ".join(value for value in (method, endpoint) if value)


def _outline_value(
    fields: tuple[ProjectOutlineField, ...],
    path: tuple[str, ...],
) -> str:
    field = next((item for item in fields if item.path == path), None)
    return "" if field is None else field.value


def _metadata_line(resource: ProjectResourceSummary) -> str:
    coordinate = resource.coordinate
    tags = " ".join(f"#{tag}" for tag in resource.tags) or "untagged"
    return safe_terminal_text(
        f"{coordinate.kind}/{coordinate.resource_id}  ·  {coordinate.namespace}  ·  {tags}",
        maximum_characters=512,
    )


def _overview_text(resource: ProjectResourceSummary) -> str:
    coordinate = resource.coordinate
    description = resource.description or "No description provided."
    source = resource.source
    fields = () if resource.outline is None else resource.outline.fields
    top_level = tuple(field for field in fields if len(field.path) == 1)
    highlights = "\n".join(f"  {field.label:<22} {field.value}" for field in top_level[:12])
    lines = [
        safe_terminal_text(description, maximum_characters=1024),
        "",
        f"Identity          {coordinate.namespace}/{coordinate.kind}/{coordinate.resource_id}",
        f"Fingerprint       {coordinate.fingerprint[:16]}…",
        f"Source            {source.source} · document {source.document} · line {source.line}",
    ]
    if highlights:
        lines.extend(("", "Effective structure", highlights))
    return "\n".join(lines)


def _provenance_text(resource: ProjectResourceSummary) -> str:
    source = resource.source
    return (
        f"Logical source     {source.source}\n"
        f"Document           {source.document}\n"
        f"Start              line {source.line}, column {source.column}\n"
        f"Definition         {resource.coordinate.fingerprint}\n\n"
        "Sensitive literal payloads and unsafe exact values are never projected to the TUI."
    )


__all__ = (
    "ProjectResourceDetail",
    "ProjectResourceItem",
    "ProjectResourceList",
    "ResourceOutlineTable",
    "ResourceRelationshipTable",
)
