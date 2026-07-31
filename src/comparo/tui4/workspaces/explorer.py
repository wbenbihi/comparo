"""Explorer workspace — layout, state, and backend workers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import ClassVar
from typing import override

from textual import work
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.reactive import reactive

from comparo.tui4.bindings import FILTER_KEY
from comparo.tui4.bindings import explorer_bindings
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.legend import Legend
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resourcedetail import ResourceDetailOverview
from comparo.tui4.components.treemenu import TreeMenu
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import ResourceDetail
from comparo.tui4.models.explorer import build_catalog
from comparo.tui4.models.explorer import catalog_nodes
from comparo.tui4.models.explorer import initial_detail
from comparo.tui4.models.explorer import legend_items
from comparo.tui4.types import ComparoScreen

from .base import Workspace

if TYPE_CHECKING:
    from comparo.application import ProjectWorkspaceResult


class ExplorerWorkspace(Workspace):
    """Explorer Workspace."""

    SCREEN = ComparoScreen.EXPLORE

    BINDINGS: ClassVar[list[BindingType]] = explorer_bindings()

    catalog: reactive[ExplorerCatalog | None] = reactive(None, init=False)
    filter_text: reactive[str] = reactive("", init=False)
    selected: reactive[str | None] = reactive(None, init=False)
    detail: reactive[ResourceDetail | None] = reactive(None, init=False)

    # ------------------------------------------------------------------ layout

    @override
    def compose(self) -> ComposeResult:
        """Left: filterable tree over the glyph legend. Right: kind-aware overview."""
        with Horizontal(id="explorer-layout"):
            yield Panel(
                FilterInput(title="Filter", key_hint=FILTER_KEY),
                TreeMenu[str](id="explorer-tree"),
                Legend(id="explorer-legend"),
                title="Project",
                id="explorer-tree-panel",
            )
            yield ResourceDetailOverview(id="explorer-detail")

    @override
    def focus_default(self) -> None:
        """Focus the resource tree."""
        self.query_one(TreeMenu).focus()

    # ----------------------------------------------------------------- loading

    def on_mount(self) -> None:
        """Load the project catalog."""
        self._load_generation = 0
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
        """Feed tree and legend; drop vanished selections."""
        catalog = self.catalog
        if (
            self.selected is not None
            and catalog is not None
            and self.selected not in catalog.entries
        ):
            self.selected = None
            self.detail = None
        self._refresh_tree()
        self.query_one(Legend).items = legend_items(catalog) if catalog else ()

    def watch_filter_text(self) -> None:
        """Filter change: re-derive the tree."""
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        """Push derived nodes into the generic tree."""
        catalog = self.catalog
        self.query_one(TreeMenu).nodes = catalog_nodes(catalog, self.filter_text) if catalog else ()

    def watch_detail(self) -> None:
        """Feed the overview."""
        self.query_one(ResourceDetailOverview).detail = self.detail

    # --------------------------------------------------------------- selection

    def on_tree_menu_highlighted(self, event: TreeMenu.Highlighted[str]) -> None:
        """Track the highlighted resource."""
        self.selected = event.item

    def watch_selected(self) -> None:
        """Project the selected catalog entry into the detail column."""
        if self.catalog is None or self.selected is None:
            return
        entry = self.catalog.entries.get(self.selected)
        if entry is None:
            return
        self.detail = initial_detail(entry)

    # ------------------------------------------------------------------ filter

    def action_focus_filter(self) -> None:
        """Focus the filter box."""
        self.query_one(FilterInput).focus()

    def on_filter_input_changed(self, event: FilterInput.Changed) -> None:
        """Live filter."""
        self.filter_text = event.value

    def on_filter_input_submitted(self, event: FilterInput.Submitted) -> None:
        """Commit: move to the results."""
        self.query_one(TreeMenu).focus()

    def on_filter_input_dismissed(self, event: FilterInput.Dismissed) -> None:
        """Close: back to the results."""
        self.query_one(TreeMenu).focus()
