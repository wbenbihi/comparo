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
from comparo.tui4.bindings import explorer_bindings
from comparo.tui4.components.explorernavigator import ExplorerNavigator
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resourcedetail import ResourceDetailOverview
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.resource import ResourceActionId
from comparo.tui4.models.resource import ResourceDetail
from comparo.tui4.models.resource import ResourceRepresentation
from comparo.tui4.models.resource import failed_authored
from comparo.tui4.models.resource import failed_effective
from comparo.tui4.models.resource import initial_detail
from comparo.tui4.models.resource import loaded_authored
from comparo.tui4.models.resource import loaded_effective
from comparo.tui4.models.resource import loading_authored
from comparo.tui4.models.resource import loading_effective
from comparo.tui4.models.resource import select_representation
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


class ExplorerWorkspace(Workspace):
    """Explorer Workspace."""

    SCREEN = ComparoScreen.EXPLORE

    BINDINGS: ClassVar[list[BindingType]] = explorer_bindings()

    catalog: reactive[ExplorerCatalog | None] = reactive(None, init=False)
    selected: reactive[str | None] = reactive(None, init=False)
    detail: reactive[ResourceDetail | None] = reactive(None, init=False)

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
            return
        entry = self.catalog.entries.get(self.selected)
        if entry is None:
            self.detail = None
            return
        self.detail = initial_detail(entry)

    # --------------------------------------------------------- resource views

    def on_resource_detail_overview_action_requested(
        self, event: ResourceDetailOverview.ActionRequested
    ) -> None:
        """Coordinate representation loads and cross-workspace resource actions."""
        detail = self.detail
        if detail is None:
            return
        action = event.action
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
