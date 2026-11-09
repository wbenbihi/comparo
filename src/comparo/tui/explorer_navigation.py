"""Relationship navigation and value-free history for the Explorer workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from comparo.application.project_outline import ResourceIdentityCoordinate
from comparo.tui.components.explorernavigator import ExplorerNavigator
from comparo.tui.components.explorernavigator import ExplorerNavigatorPosition
from comparo.tui.components.resourcedetail import ResourceDetailOverview
from comparo.tui.components.resourcedetail import ResourceViewPosition
from comparo.tui.components.resourcelinks import OpenResource
from comparo.tui.models.explorer import ResourceEntry
from comparo.tui.models.resource import InspectionPhase
from comparo.tui.models.resource import ResourceActionId
from comparo.tui.models.resource import ResourceOverviewMode
from comparo.tui.models.resource import ResourceRepresentation
from comparo.tui.models.resource import resolution_environment
from comparo.tui.models.resource import select_representation
from comparo.tui.models.resource_links import linked_entry
from comparo.tui.models.resource_links import logical_identity
from comparo.tui.models.resource_presenters import resource_overview_supports_resolution

if TYPE_CHECKING:
    from comparo.tui.workspaces.explorer import ExplorerWorkspace


@dataclass(frozen=True)
class ResourceVisit:
    """A location to rebind against the current catalog when navigating Back."""

    target: ResourceIdentityCoordinate
    representation: ResourceRepresentation
    overview_mode: ResourceOverviewMode
    navigator: ExplorerNavigatorPosition
    inspector: ResourceViewPosition


class ExplorerNavigation:
    """Coordinate explicit links without adding ordinary highlights to history."""

    def __init__(self, workspace: ExplorerWorkspace) -> None:
        """Keep history scoped to this workspace instance."""
        self.workspace = workspace
        self._history: list[ResourceVisit] = []

    @property
    def can_go_back(self) -> bool:
        """Expose Back only while a previous explicit location exists."""
        return bool(self._history)

    @property
    def previous_target(self) -> ResourceIdentityCoordinate | None:
        """Return the origin of the latest explicit relationship navigation."""
        return self._history[-1].target if self._history else None

    def can_resolve(self) -> bool:
        """Values and Matrix data do not require an execution Environment."""
        detail = self.workspace.detail
        return (
            detail is not None
            and resource_overview_supports_resolution(detail.entry)
            and (
                detail.entry.summary.coordinate.kind in {"Value", "Matrix"}
                or resolution_environment(detail) is not None
            )
        )

    def schema_catalog(self, entry: ResourceEntry) -> tuple[ResourceEntry, ...]:
        """Supply already admitted local schema outlines for reference navigation."""
        catalog = self.workspace.catalog
        if catalog is None or entry.summary.coordinate.kind != "Schema":
            return ()
        return tuple(
            item for item in catalog.entries.values() if item.summary.coordinate.kind == "Schema"
        )

    def selected_changed(self) -> None:
        """Load a fresh revision while preserving the workspace's selected view."""
        workspace = self.workspace
        previous = workspace.detail
        representation = (
            ResourceRepresentation.OVERVIEW if previous is None else previous.representation
        )
        mode = ResourceOverviewMode.RAW if previous is None else previous.overview_mode
        workspace._resource_generation += 1
        workspace._resolution_generation += 1
        entry = (
            workspace.catalog.entries.get(workspace.selected)
            if workspace.catalog is not None and workspace.selected is not None
            else None
        )
        if entry is None:
            workspace.detail = None
        else:
            workspace._select_entry(entry, representation, mode)
        workspace.app.refresh_bindings()

    def follow(self, event: OpenResource) -> None:
        """Follow a mounted link using its complete logical resource identity."""
        workspace = self.workspace
        host = workspace.query_one(ResourceDetailOverview)
        detail = workspace.detail
        if (
            detail is None
            or host.rendered_key != detail.key
            or not event.control.is_mounted
            or host not in event.control.ancestors
        ):
            return
        entry = linked_entry(workspace.catalog, event.target)
        if entry is None:
            workspace.notify(
                "This related resource is unavailable or ambiguous.", severity="warning"
            )
            return
        if entry.key == detail.key:
            return
        navigator = workspace.query_one(ExplorerNavigator)
        self._history.append(
            ResourceVisit(
                logical_identity(detail.entry.summary.coordinate),
                detail.representation,
                detail.overview_mode,
                navigator.capture_position(),
                host.capture_position(),
            )
        )
        self._history = self._history[-50:]
        host.prepare_navigation(entry.key)
        navigator.reveal_resource(entry.key)
        workspace.selected = entry.key
        workspace.app.refresh_bindings()

    def back(self) -> None:
        """Restore a surviving location with new data from its current revision."""
        workspace = self.workspace
        skipped = False
        while self._history:
            visit = self._history.pop()
            entry = linked_entry(workspace.catalog, visit.target)
            if entry is None:
                skipped = True
                continue
            host = workspace.query_one(ResourceDetailOverview)
            navigator = workspace.query_one(ExplorerNavigator)
            host.prepare_navigation(entry.key, visit.inspector)
            navigator.restore_position(visit.navigator, entry.key)
            # Apply the saved view once, without loading the destination's prior view first.
            workspace.set_reactive(type(workspace).selected, entry.key)
            workspace._resource_generation += 1
            workspace._resolution_generation += 1
            workspace._select_entry(entry, visit.representation, visit.overview_mode)
            if not any(position.focused for position in visit.inspector.widgets):
                navigator.restore_focus(visit.navigator)
            break
        if skipped:
            workspace.notify(
                "Unavailable resources were skipped in navigation history.", severity="warning"
            )
        workspace.app.refresh_bindings()

    def restore_resource_view(
        self,
        representation: ResourceRepresentation,
        overview_mode: ResourceOverviewMode,
    ) -> None:
        """Apply the prior workspace view to a newly selected resource."""
        detail = self.workspace.detail
        if detail is None:
            return
        if representation is ResourceRepresentation.AUTHORED:
            self.workspace._apply_resource_action(ResourceActionId.AUTHORED)
            return
        if representation is ResourceRepresentation.EFFECTIVE:
            self.workspace._apply_resource_action(ResourceActionId.EFFECTIVE)
            return
        if representation is ResourceRepresentation.DEPENDENCIES:
            self.workspace.detail = select_representation(
                detail, ResourceRepresentation.DEPENDENCIES
            )
            return
        if representation is ResourceRepresentation.RESOLVED:
            self.workspace.detail = select_representation(detail, ResourceRepresentation.RESOLVED)
            return
        if overview_mode is ResourceOverviewMode.RESOLVED:
            self.workspace._load_resolved_overview(detail)

    def show_overview(self) -> None:
        """Load a preserved resolved mode lazily when returning from Graph or Source."""
        detail = self.workspace.detail
        if detail is None:
            return
        if detail.overview_mode is ResourceOverviewMode.RESOLVED and detail.resolved.phase in {
            InspectionPhase.IDLE,
            InspectionPhase.UNAVAILABLE,
        }:
            self.workspace._load_resolved_overview(detail)
        else:
            self.workspace.detail = select_representation(detail, ResourceRepresentation.OVERVIEW)
