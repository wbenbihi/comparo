"""Explorer workspace — layout, state, and backend workers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import ClassVar
from typing import Protocol
from typing import cast
from typing import override

from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.reactive import reactive

from comparo.application import SourceInspectionError
from comparo.tui4.backend import ResolvedFieldBatch
from comparo.tui4.bindings import explorer_bindings
from comparo.tui4.components.explorernavigator import ExplorerNavigator
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resourcedetail import ResourceDetailOverview
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.explorer import select_execution_environment
from comparo.tui4.models.resource import ResolvedResourceView
from comparo.tui4.models.resource import ResourceActionId
from comparo.tui4.models.resource import ResourceDetail
from comparo.tui4.models.resource import ResourceOverviewMode
from comparo.tui4.models.resource import ResourceRepresentation
from comparo.tui4.models.resource import bind_execution_environment
from comparo.tui4.models.resource import failed_authored
from comparo.tui4.models.resource import failed_effective
from comparo.tui4.models.resource import failed_resolved
from comparo.tui4.models.resource import initial_detail
from comparo.tui4.models.resource import loaded_authored
from comparo.tui4.models.resource import loaded_effective
from comparo.tui4.models.resource import loaded_resolved
from comparo.tui4.models.resource import loading_authored
from comparo.tui4.models.resource import loading_effective
from comparo.tui4.models.resource import loading_resolved
from comparo.tui4.models.resource import select_representation
from comparo.tui4.models.resource import toggle_overview_mode
from comparo.tui4.models.resource import unavailable_resolved
from comparo.tui4.models.resource_presenters import project_resolution_paths
from comparo.tui4.models.resource_presenters import project_resolved_fields
from comparo.tui4.types import ComparoScreen

from .base import Workspace

if TYPE_CHECKING:
    from comparo.application import AuthoredResourceWindow
    from comparo.application import EffectiveResourceWindow
    from comparo.application import ProjectWorkspaceResult
    from comparo.application.plan_inspection_identity import ResourceCoordinate


class _CompareOwner(Protocol):
    """Minimal app contract required by Explorer's cross-workspace action."""

    def use_in_compare(self, subject: ResourceCoordinate) -> None:
        """Retain a subject and activate Compare."""

    def select_execution_environment(self, environment: ResourceCoordinate) -> None:
        """Set the shared Environment context for contextual inspections."""


class ExplorerWorkspace(Workspace):
    """Explorer Workspace."""

    SCREEN = ComparoScreen.EXPLORE

    BINDINGS: ClassVar[list[BindingType]] = explorer_bindings()

    catalog: reactive[ExplorerCatalog | None] = reactive(None, init=False)
    selected: reactive[str | None] = reactive(None, init=False)
    detail: reactive[ResourceDetail | None] = reactive(None, init=False)
    execution_environment: reactive[ResourceCoordinate | None] = reactive(None, init=False)

    # ------------------------------------------------------------------ layout

    @override
    def compose(self) -> ComposeResult:
        """Left: filterable tree over the glyph legend. Right: kind-aware overview."""
        with Horizontal(id="explorer-layout"):
            yield Panel(
                ExplorerNavigator(id="explorer-navigator"),
                title="Project",
                id="explorer-tree-panel",
            )
            yield ResourceDetailOverview(id="explorer-detail")

    @override
    def focus_default(self) -> None:
        """Focus the resource tree."""
        self.query_one(ExplorerNavigator).focus_tree()

    # ----------------------------------------------------------------- loading

    def on_mount(self) -> None:
        """Load the project catalog."""
        self._load_generation = 0
        self._resource_generation = 0
        self.action_reload()

    def action_reload(self) -> None:
        """Kick a latest-wins project reload."""
        self._load_generation += 1
        self.query_one("#explorer-tree-panel", Panel).border_subtitle = "loading…"
        self._load_workspace(self._load_generation)

    @work(thread=True, exclusive=True, group="workspace-load")
    def _load_workspace(self, generation: int) -> None:
        """Blocking application call — thread worker only."""
        result = self.backend.load_workspace()
        self.app.call_from_thread(self._apply_workspace, generation, result)

    def _apply_workspace(self, generation: int, result: ProjectWorkspaceResult) -> None:
        """Apply a load result unless it is stale."""
        if generation != self._load_generation:
            return
        panel = self.query_one("#explorer-tree-panel", Panel)
        catalog = build_catalog(result)
        if catalog is None:
            panel.border_subtitle = f"{len(result.diagnostics)} problem(s)"
            return
        panel.border_subtitle = f"{len(catalog.entries)} resources"
        self.catalog = catalog
        environment = select_execution_environment(catalog, self.execution_environment)
        if environment is not None:
            cast(_CompareOwner, self.app).select_execution_environment(
                environment.summary.coordinate
            )

    # ------------------------------------------------------------------- state

    def watch_catalog(self) -> None:
        """Feed the navigator and drop vanished selections."""
        catalog = self.catalog
        if (
            self.selected is not None
            and catalog is not None
            and self.selected not in catalog.entries
        ):
            self.selected = None
            self.detail = None
        self.query_one(ExplorerNavigator).catalog = catalog

    def watch_detail(self) -> None:
        """Feed the overview."""
        self.query_one(ResourceDetailOverview).detail = self.detail

    def watch_execution_environment(self) -> None:
        """Reflect the shared context in the tree and invalidate contextual values."""
        self._resource_generation += 1
        self.query_one(ExplorerNavigator).execution_environment = self.execution_environment
        if self.detail is not None:
            self.detail = bind_execution_environment(self.detail, self.execution_environment)

    # --------------------------------------------------------------- selection

    def on_explorer_navigator_resource_highlighted(
        self, event: ExplorerNavigator.ResourceHighlighted
    ) -> None:
        """Track the resource selected by the Explorer-specific navigator."""
        self.selected = event.entry.key

    def watch_selected(self) -> None:
        """Project the selected catalog entry into the detail column."""
        self._resource_generation += 1
        if self.catalog is None or self.selected is None:
            self.detail = None
            self.app.refresh_bindings()
            return
        entry = self.catalog.entries.get(self.selected)
        if entry is None:
            self.detail = None
            self.app.refresh_bindings()
            return
        self.detail = initial_detail(entry, self.execution_environment)
        self.app.refresh_bindings()

    # --------------------------------------------------------- resource views

    def on_resource_detail_overview_action_requested(
        self, event: ResourceDetailOverview.ActionRequested
    ) -> None:
        """Coordinate representation loads and cross-workspace resource actions."""
        self._apply_resource_action(event.action)

    def _apply_resource_action(self, action: ResourceActionId) -> None:
        """Apply one representation or workflow action to the selected resource."""
        detail = self.detail
        if detail is None:
            return
        if action is ResourceActionId.OVERVIEW:
            self.detail = select_representation(detail, ResourceRepresentation.OVERVIEW)
            return
        if action is ResourceActionId.AUTHORED:
            if detail.authored.value is not None:
                self.detail = select_representation(detail, ResourceRepresentation.AUTHORED)
                return
            self.detail = loading_authored(detail)
            self._load_authored(
                self._resource_generation,
                detail.key,
                detail.entry.summary.coordinate,
                detail.entry.source.sha256,
            )
            return
        if action is ResourceActionId.EFFECTIVE:
            if detail.effective.value is not None:
                self.detail = select_representation(detail, ResourceRepresentation.EFFECTIVE)
                return
            self.detail = loading_effective(detail)
            self._load_effective(
                self._resource_generation,
                detail.key,
                detail.entry.summary.coordinate,
            )
            return
        if action is ResourceActionId.RESOLVED:
            self.detail = select_representation(detail, ResourceRepresentation.RESOLVED)
            return
        if action is ResourceActionId.USE_IN_COMPARE:
            cast(_CompareOwner, self.app).use_in_compare(detail.entry.summary.coordinate)

    @work(thread=True)
    def _load_authored(
        self,
        generation: int,
        key: str,
        coordinate: ResourceCoordinate,
        expected_sha256: str | None,
    ) -> None:
        """Load one bounded masked authored document off the event loop."""
        try:
            result = self.backend.inspect_authored(coordinate, expected_sha256)
        except SourceInspectionError as error:
            self.app.call_from_thread(
                self._apply_authored_failure, generation, key, error.code.value
            )
        except Exception:  # noqa: BLE001 - never surface source-bearing adapter details
            self.app.call_from_thread(self._apply_authored_failure, generation, key, "internal")
        else:
            self.app.call_from_thread(self._apply_authored, generation, key, result)

    def _apply_authored(self, generation: int, key: str, result: AuthoredResourceWindow) -> None:
        """Apply authored data only to the revision that requested it."""
        if not self._is_current_resource(generation, key):
            return
        detail = self.detail
        if detail is not None:
            self.detail = loaded_authored(detail, result)

    def _apply_authored_failure(self, generation: int, key: str, code: str) -> None:
        """Apply only a value-free authored inspection failure code."""
        if not self._is_current_resource(generation, key):
            return
        detail = self.detail
        if detail is not None:
            self.detail = failed_authored(detail, code)

    @work(thread=True)
    def _load_effective(self, generation: int, key: str, coordinate: ResourceCoordinate) -> None:
        """Load one bounded effective structural window off the event loop."""
        try:
            result = self.backend.inspect_effective(coordinate)
        except SourceInspectionError as error:
            self.app.call_from_thread(
                self._apply_effective_failure, generation, key, error.code.value
            )
        except Exception:  # noqa: BLE001 - never surface source-bearing adapter details
            self.app.call_from_thread(self._apply_effective_failure, generation, key, "internal")
        else:
            self.app.call_from_thread(self._apply_effective, generation, key, result)

    def _apply_effective(self, generation: int, key: str, result: EffectiveResourceWindow) -> None:
        """Apply effective data only to the revision that requested it."""
        if not self._is_current_resource(generation, key):
            return
        detail = self.detail
        if detail is not None:
            self.detail = loaded_effective(detail, result)

    def _apply_effective_failure(self, generation: int, key: str, code: str) -> None:
        """Apply only a value-free effective inspection failure code."""
        if not self._is_current_resource(generation, key):
            return
        detail = self.detail
        if detail is not None:
            self.detail = failed_effective(detail, code)

    def _is_current_resource(self, generation: int, key: str) -> bool:
        """Check the latest-wins identity before applying worker output."""
        return (
            generation == self._resource_generation
            and self.detail is not None
            and self.detail.key == key
        )

    # ------------------------------------------------------------------ filter

    def action_focus_filter(self) -> None:
        """Focus the filter box."""
        self.query_one(ExplorerNavigator).focus_filter()

    def action_toggle_legend(self) -> None:
        """Delegate optional legend visibility to the navigator component."""
        self.query_one(ExplorerNavigator).toggle_legend()

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Expose Project-only view bindings only for a selected Project."""
        if action in {"project_view", "toggle_project_resolution"}:
            detail = self.detail
            return detail is not None and detail.entry.summary.coordinate.kind == "Project"
        if action == "set_execution_environment":
            detail = self.detail
            return detail is not None and detail.entry.summary.coordinate.kind == "Environment"
        return super().check_action(action, parameters)

    def action_set_execution_environment(self) -> None:
        """Make the highlighted Environment the shared context on explicit Space."""
        detail = self.detail
        if detail is not None and detail.entry.summary.coordinate.kind == "Environment":
            self._set_execution_environment(detail.entry)

    def _set_execution_environment(self, entry: ResourceEntry) -> None:
        """Commit one exact Environment revision through the app-owned state."""
        cast(_CompareOwner, self.app).select_execution_environment(entry.summary.coordinate)

    def action_project_view(self, view: str) -> None:
        """Select a Project representation without moving focus out of the tree."""
        detail = self.detail
        if detail is None or detail.entry.summary.coordinate.kind != "Project":
            return
        if view == "overview":
            self.detail = select_representation(detail, ResourceRepresentation.OVERVIEW)
        elif view == "source":
            self._apply_resource_action(ResourceActionId.AUTHORED)
        elif view == "dependencies":
            self.detail = select_representation(detail, ResourceRepresentation.DEPENDENCIES)

    def action_toggle_project_resolution(self) -> None:
        """Toggle raw/resolved content inside the Project Overview."""
        detail = self.detail
        if detail is None or detail.entry.summary.coordinate.kind != "Project":
            return
        if detail.overview_mode is ResourceOverviewMode.RESOLVED:
            self.detail = toggle_overview_mode(detail)
            return
        if detail.resolved.value is not None:
            self.detail = toggle_overview_mode(detail)
            return
        environment = detail.execution_environment
        if environment is None:
            self.detail = toggle_overview_mode(detail)
            return
        paths = project_resolution_paths(detail.entry)
        if not paths:
            self.detail = unavailable_resolved(
                detail,
                "No project-level variables or secrets require resolution.",
            )
            return
        self.detail = loading_resolved(detail)
        self._load_project_resolved(
            self._resource_generation,
            detail.key,
            detail.entry.summary.coordinate,
            environment,
            paths,
        )

    @work(exclusive=True, group="project-resolution")
    async def _load_project_resolved(
        self,
        generation: int,
        key: str,
        resource: ResourceCoordinate,
        environment: ResourceCoordinate,
        paths: tuple[tuple[str, ...], ...],
    ) -> None:
        """Load one masked Project field batch without blocking the event loop."""
        try:
            result = await self.backend.inspect_resolved_fields(resource, environment, paths)
        except Exception:  # noqa: BLE001 - never expose source-bearing adapter details
            self._apply_resolved_failure(generation, key, environment, "internal")
            return
        self._apply_resolved(generation, key, environment, result)

    def _apply_resolved(
        self,
        generation: int,
        key: str,
        environment: ResourceCoordinate,
        result: ResolvedFieldBatch,
    ) -> None:
        """Apply a resolved batch only to its exact selection and context."""
        if not self._is_current_resource(generation, key):
            return
        detail = self.detail
        if detail is None or detail.execution_environment != environment:
            return
        if result.values is None or result.occurrence is None:
            code = result.diagnostics[0] if result.diagnostics else "internal"
            self.detail = failed_resolved(detail, code)
            return
        projected = project_resolved_fields(result.values)
        occurrence = result.occurrence
        self.detail = loaded_resolved(
            detail,
            ResolvedResourceView(
                environment=environment,
                execution_ordinal=occurrence.ordinal,
                cell_id=occurrence.cell_id,
                trial_id=occurrence.trial_id,
                fields=projected.fields,
                diagnostics=projected.diagnostics,
                incomplete=projected.incomplete,
            ),
        )

    def _apply_resolved_failure(
        self,
        generation: int,
        key: str,
        environment: ResourceCoordinate,
        code: str,
    ) -> None:
        """Apply only a closed resolved-inspection failure code."""
        if not self._is_current_resource(generation, key):
            return
        detail = self.detail
        if detail is not None and detail.execution_environment == environment:
            self.detail = failed_resolved(detail, code)
