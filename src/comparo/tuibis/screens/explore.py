"""Explore workspace — PROJECT tree beside kind-aware, fully live inspectors."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import VerticalScroll

from comparo.tuibis import fixtures
from comparo.tuibis.viewmodels import EntityKind
from comparo.tuibis.viewmodels import KeyHint
from comparo.tuibis.viewmodels import ProjectVM
from comparo.tuibis.widgets import EnvironmentInspector
from comparo.tuibis.widgets import Panel
from comparo.tuibis.widgets import ProjectTree
from comparo.tuibis.widgets import RequestInspector
from comparo.tuibis.widgets.filter_bar import FilterBar
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.inspectors import GraphInspector
from comparo.tuibis.widgets.inspectors import MatrixInspector

KEY_HINTS: tuple[KeyHint, ...] = (
    KeyHint("tab", "focus panel"),
    KeyHint("↑↓", "move"),
    KeyHint("enter", "open"),
    KeyHint("v", "authored⇄resolved"),
    KeyHint("g", "graph"),
    KeyHint("/", "filter"),
)

_TITLES: dict[str, str] = {
    "req:quote": "» Price quote · GET /v1/quote",
    "req:checkout": "» Checkout · POST /v1/checkout · ~ write-capable",
    "env:staging": "⌂ staging · LIVE · DEFAULT",
    "mat:plans": "⊞ Plans · plan x currency · 6 cells",
}


class ExploreWorkspace(Horizontal):
    """PROJECT tree, live filter, and kind-aware inspectors with variants."""

    BINDINGS: ClassVar = [
        Binding("slash", "focus_filter", "Filter", show=False),
        Binding("v", "toggle_variant", "Authored/resolved", show=False),
        Binding("g", "open_graph", "Graph", show=False),
    ]

    def __init__(self, project: ProjectVM) -> None:
        """Build the tree, the filter bar, and every inspector up front."""
        self._project = project
        self._current = "req:quote"
        self._graph_open = False
        self._before_graph = "req:quote"
        self._query = ""
        inspectors: dict[str, VerticalScroll] = {}
        for key, vm in project.requests.items():
            inspectors[key] = RequestInspector(
                vm, body_explainers=fixtures.BODY_EXPLAINERS.get(key)
            )
        for key, env_vm in project.envs.items():
            inspectors[key] = EnvironmentInspector(env_vm)
        inspectors["mat:plans"] = MatrixInspector(
            name="Plans",
            summary="cartesian product · plan x currency → 6 cells",
            axes=fixtures.matrix_axes(),
            cases=fixtures.matrix_cases(),
            details=fixtures.matrix_case_details(),
        )
        for key, items in fixtures.GRAPHS.items():
            inspectors[f"graph:{key}"] = GraphInspector(project.requests[key].name, items)
        self._inspectors = inspectors
        self._host = InspectorHost(inspectors, initial="req:quote")
        self._host.id = "explore-inspectors"
        self._inspector_panel = Panel(
            self._host,
            title=f"{_TITLES['req:quote']} · resolved",
            hint="v authored⇄resolved · g graph · / filter",
            id="inspector-panel",
        )
        self._filter_bar = FilterBar(
            "search resources…",
            ("all", "req", "env", "sch", "mat", "prof", "val"),
            "all",
        )
        self._tree = ProjectTree(project)
        super().__init__(
            Panel(
                self._filter_bar,
                self._tree,
                title="PROJECT",
                glyph=EntityKind.PROJECT,
                hint="/ filter · esc clear",
                id="project-panel",
            ),
            self._inspector_panel,
            id="explore-workspace",
        )

    # ------------------------------------------------------------- titles

    def _set_title(self, host_key: str) -> None:
        """Update the inspector panel border title for a host key."""
        if host_key.startswith("graph:"):
            base = _TITLES.get(host_key.removeprefix("graph:"), "")
            self._inspector_panel.border_title = f"{base} · graph"
            return
        if host_key.endswith(":authored"):
            base = _TITLES.get(host_key.removesuffix(":authored"), "")
            self._inspector_panel.border_title = f"{base} · authored"
            return
        if host_key in _TITLES:
            suffix = " · resolved" if host_key in self._project.requests else ""
            self._inspector_panel.border_title = f"{_TITLES[host_key]}{suffix}"

    # ------------------------------------------------------------ messages

    def on_project_tree_leaf_highlighted(self, event: ProjectTree.LeafHighlighted) -> None:
        """Swap the inspector for the highlighted resource."""
        if event.key not in self._inspectors:
            return
        self._current = event.key
        self._graph_open = False
        self._host.show(event.key)
        self._set_title(event.key)

    def on_filter_bar_changed(self, event: FilterBar.Changed) -> None:
        """Filter the project tree live as the query changes."""
        self._query = event.query
        self._tree.set_filter(event.query)

    def on_graph_inspector_node_activated(self, event: GraphInspector.NodeActivated) -> None:
        """Jump from a graph node to that resource's inspector."""
        if event.key not in self._inspectors:
            return
        self._graph_open = False
        self._current = event.key
        self._host.show(event.key)
        self._set_title(event.key)

    # ------------------------------------------------------------- actions

    def action_focus_filter(self) -> None:
        """Focus the filter query input (bound to /)."""
        self._filter_bar.focus_query()

    def action_toggle_variant(self) -> None:
        """Toggle the visible request between authored and resolved."""
        if self._graph_open:
            return
        current = self._host.current
        target = (
            current.removesuffix(":authored")
            if current.endswith(":authored")
            else f"{current}:authored"
        )
        if target not in self._inspectors:
            return
        self._host.show(target)
        self._set_title(target)

    def action_open_graph(self) -> None:
        """Open the dependency graph for the current request."""
        if self._graph_open:
            return
        graph_key = f"graph:{self._host.current.removesuffix(':authored')}"
        graph = self._inspectors.get(graph_key)
        if not isinstance(graph, GraphInspector):
            return
        self._before_graph = self._host.current
        self._graph_open = True
        self._host.show(graph_key)
        self._set_title(graph_key)
        graph.focus_list()

    # ------------------------------------------------------------ commands

    def handle_command(self, command: str) -> bool:
        """Handle esc: close the graph first, then clear the filter."""
        if command != "back":
            return False
        if self._graph_open:
            self._graph_open = False
            self._host.show(self._before_graph)
            self._set_title(self._before_graph)
            self._tree.focus()
            return True
        filter_input = self._filter_bar._input  # FilterBar exposes no clear API yet
        if filter_input.has_focus or self._query:
            filter_input.value = ""
            self._query = ""
            self._tree.focus()
            return True
        return False
