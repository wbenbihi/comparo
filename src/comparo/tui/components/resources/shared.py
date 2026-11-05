"""Reusable resource overview fields and dependency graph components."""

from collections.abc import Mapping
from typing import ClassVar
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.widgets import Static

from comparo.tui.bindings import resource_link_bindings
from comparo.tui.components.badge import BadgeTone
from comparo.tui.components.badge import badge_text
from comparo.tui.components.resourcelinks import OpenResource
from comparo.tui.components.treemenu import TreeMenu
from comparo.tui.components.typography import Caption
from comparo.tui.components.typography import Hint
from comparo.tui.models.explorer import ResourceEntry
from comparo.tui.models.navigation import MenuNode
from comparo.tui.models.resource import InspectionPhase
from comparo.tui.models.resource import ResolvedResourceField
from comparo.tui.models.resource import ResolvedValueView
from comparo.tui.models.resource import ResourceDetail
from comparo.tui.models.resource_presenters import ResourceDependency
from comparo.tui.models.resource_presenters import ResourceSetting
from comparo.tui.models.resource_presenters import resource_dependencies
from comparo.tui.theme import kind_glyph
from comparo.tui.theme import style


class ResourceSettingRow(Vertical):
    """One complete resource setting and all of its nested safe fields."""

    def __init__(
        self,
        setting: ResourceSetting,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize a row from a pure setting projection."""
        super().__init__(classes="resource-setting-row")
        self._setting = setting
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render the setting summary followed by every nested field."""
        value = self._value(self._setting.path, self._setting.value)
        heading = Text(self._setting.name, style("label"))
        heading.append("  ")
        heading.append_text(value)
        heading.append(" ")
        heading.append_text(badge_text(self._setting.category, tone=BadgeTone.NEUTRAL))
        yield Static(heading, classes="resource-setting-heading")
        for detail in self._setting.details:
            absolute_path = (*self._setting.path, *detail.path)
            value = self._value(absolute_path, detail.value)
            path = ".".join(detail.path) or "value"
            line = Text("  ↳ ", style("dim"))
            line.append(path, style("muted"))
            line.append("  ")
            line.append_text(value)
            line.append(" ")
            line.append_text(badge_text(detail.category, tone=BadgeTone.NEUTRAL))
            yield Static(line, classes="resource-setting-detail")

    def _value(self, path: tuple[str, ...], fallback: str) -> Text:
        """Return an exact resolved value or a failed ancestor outcome."""
        selected = self._resolved.get(path)
        if selected is not None:
            return self._styled_value(selected.value, selected.tone, selected.reference_kind)
        for length in range(len(path) - 1, 0, -1):
            ancestor = self._resolved.get(path[:length])
            if ancestor is not None and ancestor.value.startswith("<"):
                return self._styled_value(ancestor.value, ancestor.tone, None)
        return self._styled_value(fallback, None, None)

    @staticmethod
    def _styled_value(value: str, tone: str | None, reference_kind: str | None) -> Text:
        """Render a typed value and a target glyph only in resolved overlays."""
        result = Text()
        if reference_kind is not None:
            character, token = kind_glyph(reference_kind)
            result.append(f"{character} ", style(token))
        result.append(value, style(tone or "fg"))
        return result


class ResourceSettingList(Vertical):
    """Explicit setting list with a semantic empty state."""

    def __init__(
        self,
        settings: tuple[ResourceSetting, ...],
        *,
        empty: str,
        resolved: Mapping[tuple[str, ...], ResolvedResourceField],
    ) -> None:
        """Initialize the list without coupling it to a kind or section."""
        super().__init__(classes="resource-setting-list")
        self._settings = settings
        self._empty = empty
        self._resolved = resolved

    @override
    def compose(self) -> ComposeResult:
        """Render every projected setting or explain the empty section."""
        if not self._settings:
            yield Hint(self._empty)
            return
        for setting in self._settings:
            yield ResourceSettingRow(setting, self._resolved)


class ResourceDependencyTree(TreeMenu[str]):
    """One-hop dependency graph over the generic tree mechanics."""

    BINDINGS: ClassVar[list[BindingType]] = resource_link_bindings()

    def __init__(self, dependencies: tuple[ResourceDependency, ...]) -> None:
        """Build directional graph branches from immutable dependency edges."""
        self._dependencies = {dependency.key: dependency for dependency in dependencies}
        branches: list[MenuNode[str]] = []
        for direction, title in (("uses", "Uses"), ("used-by", "Used by")):
            leaves = tuple(
                MenuNode(edge_id, dependency.target_id)
                for edge_id, dependency in self._dependencies.items()
                if dependency.direction == direction
            )
            if leaves:
                branches.append(MenuNode(f"direction:{direction}", title, children=leaves))
        super().__init__(*branches, classes="resource-dependency-graph")

    def on_tree_menu_selected(self, event: TreeMenu.Selected[str]) -> None:
        """Open only an explicitly activated edge, never a directional branch."""
        if event.menu is self:
            event.stop()
            dependency = self._dependencies.get(event.item)
            if dependency is not None:
                self.post_message(OpenResource(self, dependency.target))

    @override
    def format_node(self, node: MenuNode[str]) -> Text:
        """Render graph branches and edges with kind, path, and origin."""
        dependency = self._dependencies.get(node.id)
        if dependency is None:
            return Text.assemble(
                (node.prompt.plain, style("label")),
                (f" {len(node.children)}", style("dim")),
            )
        character, token = kind_glyph(dependency.target_kind)
        label = Text(f"{character} ", style(token))
        label.append(f"{dependency.target_kind}/{dependency.target_id}", style("fg"))
        if dependency.path:
            label.append(f"  {'.'.join(dependency.path)}", style("muted"))
        label.append(" ")
        label.append_text(badge_text(dependency.origin, tone=BadgeTone.NEUTRAL))
        return label


class ResourceDependencyGraph(Vertical):
    """Generic dependency representation with an explicit empty state."""

    def __init__(self, entry: ResourceEntry) -> None:
        """Initialize the graph from safe relationship projections."""
        super().__init__(classes="resource-dependency-view")
        self._kind = entry.summary.coordinate.kind
        self._dependencies = resource_dependencies(entry)

    @override
    def compose(self) -> ComposeResult:
        """Render the graph or explain that the resource is isolated."""
        if not self._dependencies:
            yield Hint(f"This {self._kind} has no dependency edges.")
            return
        yield ResourceDependencyTree(self._dependencies)


def resolution_status(resource: ResourceDetail, context: str) -> Static:
    """Render the common masked-resolution lifecycle and occurrence context."""
    inspection = resource.resolved
    if inspection.phase is InspectionPhase.LOADING:
        return Hint(f"Resolving masked values for {context}…")
    if inspection.phase is InspectionPhase.FAILED:
        return Hint(f"Resolved inspection failed for {context}: {inspection.message}")
    if inspection.phase is not InspectionPhase.READY or inspection.value is None:
        return Hint(inspection.message or f"Resolved values are unavailable for {context}.")
    resolved = inspection.value
    if isinstance(resolved, ResolvedValueView):
        suffix = " · partial view" if resolved.incomplete else ""
        return Caption(f"Resolved · secrets masked{suffix}")
    suffixes = []
    if resolved.incomplete:
        suffixes.append("partial view")
    if resolved.diagnostics:
        suffixes.append(f"{len(resolved.diagnostics)} unavailable fields")
    suffixes.extend(f"{item.binding}={item.case_id}" for item in resolved.matrices)
    suffix = "" if not suffixes else " · " + " · ".join(suffixes)
    return Caption(f"Resolved · {resolved.environment.resource_id} · secrets masked{suffix}")


__all__ = [
    "ResourceDependencyGraph",
    "ResourceDependencyTree",
    "ResourceSettingList",
    "ResourceSettingRow",
    "resolution_status",
]
