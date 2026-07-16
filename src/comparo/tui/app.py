"""The comparo terminal UI.

Built to the comparo-ink design: a top nav bar of screen tabs, a full foldable
project tree on the Explorer, and rich per-object detail (the resolved outbound
request with a syntax-highlighted body, or the config of any other object). The
Diff screen carries the signature tri-state gutter. The core never depends on
this module.
"""

import asyncio
import json
from typing import ClassVar
from typing import Literal
from typing import cast

import msgspec
from rich.console import Group
from rich.console import RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import App
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher
from textual.widgets import DataTable
from textual.widgets import Input
from textual.widgets import Label
from textual.widgets import OptionList
from textual.widgets import SelectionList
from textual.widgets import Static
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from comparo.core.checks import Check
from comparo.core.checks import passed as checks_passed
from comparo.core.checks import run_checks
from comparo.core.compare import CellDiff
from comparo.core.compare import diff_run
from comparo.core.curl import to_curl
from comparo.core.diagnostics import Diagnostic
from comparo.core.diagnostics import LoadError
from comparo.core.execute import Execution
from comparo.core.execute import execute_request
from comparo.core.health import Health
from comparo.core.health import HealthReport
from comparo.core.health import check_health
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import expand
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import Instance
from comparo.core.models import Matrix
from comparo.core.models import Project
from comparo.core.models import Request
from comparo.core.models import Schema
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail
from comparo.core.report import RunReport
from comparo.core.report import build_report
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink
from comparo.core.resolve import resolve_pair
from comparo.core.resolve import select_environment
from comparo.core.secrets import SecretError
from comparo.tui.theme import COMPARO_INK

_INK = "#0d1017"
_TEXT = "#c5d0de"
_TEXT_HI = "#eaf0f8"
_DIM = "#5c6878"
_ACCENT = "#6d9eff"
_AXIS = "#a98bf0"
_SAME = "#48a97f"
_DRIFT = "#e0566b"
_SKIP = "#5c6878"
_WARN = "#d99b3f"
_DANGER = "#ff5f52"
_LABEL = "#7f8ba0"
_SYNTAX_BG = "#182030"

_METHOD: dict[str, str] = {
    "GET": _SAME,
    "POST": _ACCENT,
    "PUT": _WARN,
    "PATCH": _AXIS,
    "DELETE": _DRIFT,
    "HEAD": _DIM,
    "OPTIONS": _DIM,
}
_MODE: dict[str, str] = {
    "ignore": _SKIP,
    "exact": _SAME,
    "shape": _ACCENT,
    "type": _AXIS,
    "tolerance": _WARN,
}
_KINDS: tuple[tuple[str, type], ...] = (
    ("Environments", Environment),
    ("Requests", Request),
    ("Matrices", Matrix),
    ("Schemas", Schema),
    ("Instances", Instance),
    ("Diff Profiles", DiffProfile),
)

_EXPLORER_KEYS = (
    ("↑↓", "move"),
    ("space", "fold"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("3", "diff"),
    ("q", "quit"),
)
_DIFF_KEYS = (
    ("x", "run diff"),
    ("↑↓", "cells"),
    ("1-5", "tabs"),
    ("?", "help"),
    ("q", "quit"),
)
_RUN_KEYS = (
    ("↑↓", "move"),
    ("enter", "open"),
    ("space", "select"),
    ("m", "cases"),
    ("x", "run"),
    ("esc", "back"),
    ("q", "quit"),
)
_REPORT_KEYS = (
    ("1-5", "tabs"),
    ("?", "help"),
    ("q", "quit"),
)
_SETTINGS_KEYS = (
    ("1-5", "tabs"),
    ("?", "help"),
    ("q", "quit"),
)
_ERROR_KEYS = (
    ("r", "re-check"),
    ("q", "quit"),
)
_RUN_GLYPH: dict[str, tuple[str, str]] = {
    "pending": ("○", _DIM),
    "running": ("◐", _WARN),
    "ok": ("✓", _SAME),
    "failed": ("✗", _DRIFT),
}
_STATUS: dict[str, tuple[str, str]] = {
    "pending": ("○", _DIM),
    "running": ("◐", _WARN),
    "success": ("✓", _SAME),
    "partial": ("◑", _WARN),
    "failed": ("✗", _DRIFT),
    "skipped": ("–", _DIM),
}

_KIND_COLOR: dict[type, str] = {
    Environment: _SAME,
    Request: _TEXT_HI,
    Matrix: _AXIS,
    Schema: _ACCENT,
    Instance: _SAME,
    DiffProfile: _WARN,
}
_HEALTH_COLOR: dict[Health, str] = {
    Health.UNKNOWN: _DIM,
    Health.PASS: _SAME,
    Health.PARTIAL: _WARN,
    Health.FAIL: _DRIFT,
}
_HEALTH_LABEL: dict[Health, str] = {
    Health.UNKNOWN: "health unknown · press h",
    Health.PASS: "healthy",
    Health.PARTIAL: "partially healthy",
    Health.FAIL: "unreachable",
}

_ENV_KEYS = (
    ("↑↓", "move"),
    ("h", "health"),
    ("enter", "default"),
    ("/", "filter"),
    ("g", "graph"),
    ("q", "quit"),
)
_RESOLVE_KEYS = (
    ("↑↓", "move"),
    ("r", "raw/resolved"),
    ("p", "curl"),
    ("/", "filter"),
    ("g", "graph"),
    ("q", "quit"),
)
_HEALTH_SEVERITY: dict[Health, Literal["information", "warning", "error"]] = {
    Health.UNKNOWN: "information",
    Health.PASS: "information",
    Health.PARTIAL: "warning",
    Health.FAIL: "error",
}

_HELP_TITLE: dict[str, str] = {
    "explorer": "EXPLORER — understand how the project is configured",
    "diff": "DIFF — compare responses across environments",
    "error": "PROJECT WILL NOT LOAD",
    "run": "RUN",
    "report": "REPORT",
    "settings": "SETTINGS",
}
_HELP_SCREEN: dict[str, tuple[tuple[str, str], ...]] = {
    "explorer": (
        ("↑ ↓", "move through the project tree"),
        ("space", "fold / unfold a section"),
        ("tab", "switch the active panel — tree, detail, provenance"),
        ("enter", "on an environment: make it the default for resolution"),
        ("h", "on an environment: run its health checks live"),
        ("r", "on a request or instance: toggle raw ⇄ resolved"),
        ("p", "on a request: show its curl (c inside copies real secrets)"),
        ("/", "filter the tree by name, kind, or tag"),
        ("g", "open the reference graph — what links to what"),
    ),
    "run": (
        ("↑ ↓", "move through the rows"),
        ("enter", "drill in — request → its matrix cells → a full report"),
        ("space", "toggle a request or cell in / out of the run"),
        ("m", "on a matrix request: pick which cases run"),
        ("x", "execute the selected cells"),
        ("esc", "go back up a level"),
    ),
    "diff": (
        ("x", "replay every request against both environments and diff"),
        ("↑ ↓", "move through the cells"),
    ),
    "report": (("(read-only)", "the report of the most recent diff run"),),
    "settings": (("(read-only)", "the effective project configuration"),),
    "error": (("r", "re-check the project after editing the files"),),
}
_HELP_GLOBAL = (
    ("1 … 5", "switch screens — Explorer, Run, Diff, Report, Settings"),
    ("&é\"'(", "same tabs on an AZERTY top row (no Shift needed)"),
    ("?", "show this help"),
    ("q", "quit comparo"),
)


class NavBar(Horizontal):
    """The top screen-tab bar: logo, tabs, and a right-aligned status."""

    TABS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("explorer", "Explorer"),
        ("run", "Run"),
        ("diff", "Diff"),
        ("report", "Report"),
        ("settings", "Settings"),
    )
    active: reactive[str] = reactive("explorer")

    def __init__(self, status: str) -> None:
        """Build the nav bar.

        Args:
            status: Right-aligned status markup.
        """
        super().__init__(id="navbar")
        self._status = status

    def compose(self) -> ComposeResult:
        """Yield the logo, the tabs, and the status."""
        yield Label("● comparo", id="nav-logo")
        for index, (tab_id, label) in enumerate(self.TABS):
            if index:
                yield Label("", classes="nav-sep")
            yield Label(label, id=f"nav-{tab_id}", classes="nav-item")
        yield Label(Text.from_markup(self._status), id="nav-status")

    def on_mount(self) -> None:
        """Highlight the active tab."""
        self._sync()

    def set_status(self, markup: str) -> None:
        """Replace the right-aligned status markup.

        Args:
            markup: The new status markup.
        """
        self._status = markup
        self.query_one("#nav-status", Label).update(Text.from_markup(markup))

    def watch_active(self) -> None:
        """Re-highlight when the active tab changes."""
        self._sync()

    def _sync(self) -> None:
        for tab_id, _ in self.TABS:
            self.query_one(f"#nav-{tab_id}", Label).set_class(tab_id == self.active, "-active")


class StatusBar(Horizontal):
    """The bottom bar: every active key (key coloured, action dim) + context."""

    def __init__(self) -> None:
        """Build an empty status bar."""
        super().__init__(id="statusbar")

    def compose(self) -> ComposeResult:
        """Yield the keys area and the context area."""
        yield Static(id="status-keys")
        yield Static(id="status-context")

    def show(self, keys: tuple[tuple[str, str], ...], context: str) -> None:
        """Render the key hints and the context.

        Args:
            keys: ``(key, action)`` pairs to show, left-aligned.
            context: Context markup, right-aligned.
        """
        bar = Text()
        for index, (key, action) in enumerate(keys):
            if index:
                bar.append("    ")
            bar.append(f" {key} ", style=f"bold {_INK} on {_ACCENT}")
            bar.append(f" {action}", style=_DIM)
        self.query_one("#status-keys", Static).update(bar)
        self.query_one("#status-context", Static).update(Text.from_markup(context))


class ExplorerView(Horizontal):
    """Browse the whole project and inspect the selected object."""

    def __init__(self, project: LoadedProject, environment: Environment | None) -> None:
        """Build the Explorer.

        Args:
            project: The project to explore.
            environment: The environment to resolve requests against.
        """
        super().__init__(id="explorer-view", classes="view")
        self.project = project
        self.environment = environment
        self.filter_query = ""
        self.raw = False
        self.health: dict[str, Health] = {}
        self.health_reports: dict[str, HealthReport] = {}
        self._current: object = None
        self._env_nodes: dict[str, TreeNode[object]] = {}
        self._default_env_id = environment.metadata.id if environment is not None else None

    def compose(self) -> ComposeResult:
        """Yield the tree and the detail/context panels."""
        with Vertical(id="tree-panel", classes="panel"):
            yield Tree("project", id="tree")
        with Vertical(id="detail"):
            with VerticalScroll(id="detail-panel", classes="panel hero"):
                yield Static(id="detail-content")
            with VerticalScroll(id="context-panel", classes="panel"):
                yield Static(id="context-content")

    def on_mount(self) -> None:
        """Title the panels, build the tree, and preselect the first request."""
        self.query_one("#tree-panel").border_title = "PROJECT"
        self.query_one("#context-panel").border_title = "PROVENANCE"
        tree: Tree[object] = self.query_one("#tree", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        self._populate("", prefer_request=True)
        tree.focus()

    def apply_filter(self, query: str) -> int:
        """Rebuild the tree keeping only objects that match *query*.

        Args:
            query: A case-insensitive substring matched against each object's
                name, id, kind, and tags. Empty shows everything.

        Returns:
            The number of objects left visible.
        """
        self.filter_query = query
        return self._populate(query)

    def toggle_raw(self) -> None:
        """Flip the selected request/instance between resolved and raw source."""
        self.raw = not self.raw
        self._reshow()

    def refresh_footer(self) -> None:
        """Re-render the footer for the current selection."""
        self._update_footer(self._selected())

    def run_health_on_selected(self) -> None:
        """Probe the selected environment's health checks in the background."""
        environment = self._selected()
        if not isinstance(environment, Environment):
            return
        self.query_one("#detail-panel").border_subtitle = "running health checks…"
        self.run_worker(self._run_health(environment), exclusive=True, group="health")

    def print_curl(self) -> None:
        """Show the curl for the selected request, picking a matrix case first."""
        request = self._selected()
        environment = self.environment
        if not isinstance(request, Request) or environment is None:
            return
        cells = expand(self.project, request)
        if len(cells) > 1:
            self.app.push_screen(
                MatrixPickerModal(cells),
                lambda cell: self._open_curl(request, environment, cell),
            )
        else:
            self._open_curl(request, environment, cells[0])

    def _open_curl(
        self, request: Request, environment: Environment, cell: MatrixCell | None
    ) -> None:
        if cell is not None:
            self.app.push_screen(CurlModal(self.project, environment, request, cell))

    def set_default(self, environment: Environment) -> None:
        """Make *environment* the default all requests resolve against."""
        self.environment = environment
        self._default_env_id = environment.metadata.id
        for env_id, node in self._env_nodes.items():
            obj = node.data
            if isinstance(obj, Environment):
                node.set_label(self._env_label(obj, env_id))
        self._reshow()

    async def _run_health(self, environment: Environment) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        client = HttpxClient()
        try:
            report = await check_health(self.project, environment, client)
        finally:
            await client.aclose()
        env_id = environment.metadata.id or ""
        self.health[env_id] = report.status
        self.health_reports[env_id] = report
        node = self._env_nodes.get(env_id)
        if node is not None:
            node.set_label(self._env_label(environment, env_id))
        if self._selected() is environment:
            self._show(environment)
        passed = sum(1 for result in report.results if result.ok)
        summary = f"{passed}/{len(report.results)} checks passed" if report.results else "no checks"
        self.app.notify(
            f"{environment.metadata.name}: {_HEALTH_LABEL[report.status]} · {summary}",
            title="Health check",
            severity=_HEALTH_SEVERITY[report.status],
        )

    def _populate(self, query: str, *, prefer_request: bool = False) -> int:
        tree: Tree[object] = self.query_one("#tree", Tree)
        tree.clear()
        self._env_nodes = {}
        needle = query.strip().lower()
        first_leaf: TreeNode[object] | None = None
        first_request: TreeNode[object] | None = None
        total = 0
        manifest = self.project.project
        if manifest is not None and _matches(manifest, Project, needle):
            first_leaf = tree.root.add_leaf(_project_leaf(manifest), data=manifest)
            total += 1
        for label, kind in _KINDS:
            objects: list[object] = [
                obj
                for obj in self.project.objects.values()
                if isinstance(obj, kind) and _matches(obj, kind, needle)
            ]
            if not objects and needle:
                continue
            branch = tree.root.add(_branch(label, len(objects)), expand=True)
            for obj in objects:
                if isinstance(obj, Environment):
                    env_id = obj.metadata.id or ""
                    node = branch.add_leaf(self._env_label(obj, env_id), data=obj)
                    self._env_nodes[env_id] = node
                else:
                    node = branch.add_leaf(_leaf(obj), data=obj)
                total += 1
                if first_leaf is None:
                    first_leaf = node
                if first_request is None and kind is Request:
                    first_request = node
        target = (first_request or first_leaf) if prefer_request else first_leaf
        if target is not None:
            tree.move_cursor(target)
            self._show(target.data)
        return total

    def _env_label(self, environment: Environment, env_id: str) -> Text:
        return _leaf(
            environment,
            health=self.health.get(env_id, Health.UNKNOWN),
            default=env_id == self._default_env_id,
        )

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[object]) -> None:
        """Show the highlighted object."""
        self._show(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected[object]) -> None:
        """Make an environment the default when it is selected with Enter."""
        if isinstance(event.node.data, Environment):
            cast("ComparoApp", self.app).set_default_environment(event.node.data)

    def _selected(self) -> object:
        return self._current

    def _reshow(self) -> None:
        self._show(self._current)

    def _show(self, obj: object) -> None:
        if obj is None:
            return
        self._current = obj
        detail = self.query_one("#detail-panel")
        context = self.query_one("#context-panel")
        if isinstance(obj, Request) and self.environment is not None:
            resolved = Resolver(self.project, self.environment).resolve_request(obj)
            detail.border_title = _title(obj, resolved.method)
            detail.border_subtitle = self._resolve_subtitle()
            self._set_detail(_request_detail(self.project, obj, resolved, raw=self.raw))
            context.border_title = "PROVENANCE"
            self._set_context(_render_provenance(resolved.trail))
        elif isinstance(obj, Environment):
            env_id = obj.metadata.id or ""
            detail.border_title = _title(obj, "ENVIRONMENT")
            detail.border_subtitle = _HEALTH_LABEL[self.health.get(env_id, Health.UNKNOWN)]
            self._set_detail(_environment_detail(obj, self.health_reports.get(env_id)))
            context.border_title = "DESCRIPTION"
            self._set_context(_description(obj))
        elif isinstance(obj, Project):
            detail.border_title = _title(obj, "PROJECT")
            detail.border_subtitle = "the manifest"
            self._set_detail(_project_detail(obj))
            context.border_title = "DESCRIPTION"
            self._set_context(_description(obj))
        elif isinstance(obj, Instance):
            value, trail = self._resolve_instance(obj)
            detail.border_title = _title(obj, "INSTANCE")
            detail.border_subtitle = self._resolve_subtitle()
            self._set_detail(_json(obj.spec.value if self.raw else value))
            titled, content = (
                ("PROVENANCE", _render_provenance(trail))
                if trail and not self.raw
                else ("DESCRIPTION", _description(obj))
            )
            context.border_title = titled
            self._set_context(content)
        else:
            detail.border_title = _title(obj, type(obj).__name__.upper())
            detail.border_subtitle = ""
            self._set_detail(_object_detail(obj))
            context.border_title = "DESCRIPTION"
            self._set_context(_description(obj))
        self._update_footer(obj)

    def _resolve_subtitle(self) -> str:
        if self.raw:
            return "raw · as written"
        env = self.environment.metadata.name if self.environment else "—"
        return f"resolved for {env}"

    def _resolve_instance(self, instance: Instance) -> tuple[object, list[Trail]]:
        if self.environment is None:
            return instance.spec.value, []
        return Resolver(self.project, self.environment).resolve_tree(instance.spec.value)

    def _update_footer(self, obj: object) -> None:
        keys: tuple[tuple[str, str], ...]
        if isinstance(obj, Environment):
            keys = _ENV_KEYS
        elif isinstance(obj, (Request, Instance)):
            keys = _RESOLVE_KEYS
        else:
            keys = _EXPLORER_KEYS
        default = self.environment.metadata.name if self.environment else "—"
        self.app.query_one(StatusBar).show(keys, f"default env · {default}")

    def _set_detail(self, content: RenderableType) -> None:
        self.query_one("#detail-content", Static).update(content)

    def _set_context(self, content: RenderableType) -> None:
        self.query_one("#context-content", Static).update(content)


class RunView(Vertical):
    """A drill-down of tables: requests → matrix cells → a full per-cell report.

    The requests table shows each request's cell count, aggregate status, and
    how many cells are handled. ``enter`` drills in — to a request's matrix
    cells (with status code and checks), and from there to a full report of one
    cell. ``space`` toggles selection, ``m`` picks cases, ``x`` runs, ``esc``
    goes back up a level.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "up", "back"),
        Binding("space", "toggle_selection", "toggle"),
    ]

    def __init__(self, project: LoadedProject) -> None:
        """Build the run view.

        Args:
            project: The project whose requests are executed.
        """
        super().__init__(id="run-view", classes="view")
        self.project = project
        self._selected: set[tuple[str, str]] = set()
        self._state: dict[tuple[str, str], str] = {}
        self._exec: dict[tuple[str, str], Execution] = {}
        self._checks: dict[tuple[str, str], list[Check]] = {}
        self._level = "requests"
        self._focus: Request | None = None
        self._focus_cell: MatrixCell | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        """Yield the switchable table and report panels."""
        with ContentSwitcher(initial="run-table-wrap", id="run-switch"):
            with Vertical(id="run-table-wrap", classes="panel hero"):
                yield DataTable(id="run-table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="run-report-wrap", classes="panel hero"):
                yield Static(id="run-report")

    def on_mount(self) -> None:
        """Select everything by default and show the requests table."""
        for request in _requests(self.project):
            for cell in expand(self.project, request):
                self._selected.add(_run_key(request, cell))
        self._show_requests()

    def refresh_screen(self) -> None:
        """Re-render the current level for the current environment."""
        if self._level == "cells" and self._focus is not None:
            self._show_cells(self._focus)
        elif self._level == "report" and self._focus is not None and self._focus_cell is not None:
            self._show_report(self._focus, self._focus_cell)
        else:
            self._show_requests()

    def execute(self) -> None:
        """Run the selected request cells against the current environment."""
        environment = _app_env(self)
        if environment is None:
            self.app.notify("Pick an environment in the Explorer first", severity="warning")
            return
        plan = self._plan()
        if not plan:
            self.app.notify("Nothing selected to run", severity="warning")
            return
        self.run_worker(self._run(environment, plan), exclusive=True, group="run")

    def action_up(self) -> None:
        """Go back up one level."""
        if self._level == "report" and self._focus is not None:
            if len(expand(self.project, self._focus)) > 1:
                self._show_cells(self._focus)
            else:
                self._show_requests()
        elif self._level == "cells":
            self._show_requests()

    def action_toggle_selection(self) -> None:
        """Toggle the cursor row in or out of the run."""
        if self._busy or self._level == "report":
            return
        key = self._cursor_key()
        if key is None:
            return
        if self._level == "requests":
            request = self._by_id(key)
            if request is None:
                return
            keys = {_run_key(request, cell) for cell in expand(self.project, request)}
            if keys <= self._selected:
                self._selected -= keys
            else:
                self._selected |= keys
        elif self._focus is not None:
            self._selected ^= {(self._focus.metadata.id or self._focus.metadata.name, key)}
        self._refresh_row(key)
        self._title()

    def open_case_picker(self) -> None:
        """Open the matrix-case multi-select for the current request."""
        request = self._current_request()
        if request is None:
            return
        cells = expand(self.project, request)
        if len(cells) <= 1:
            self.app.notify("This request has no matrix cases", severity="information")
            return
        request_id = request.metadata.id or request.metadata.name
        chosen = {key for (rid, key) in self._selected if rid == request_id}
        self.app.push_screen(
            MatrixSelectModal(request, cells, chosen),
            lambda keys: self._apply_cases(request, cells, keys),
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Drill into the selected row."""
        key = event.row_key.value
        if key is None:
            return
        if self._level == "requests":
            request = self._by_id(key)
            if request is None:
                return
            cells = expand(self.project, request)
            if len(cells) > 1:
                self._show_cells(request)
            else:
                self._show_report(request, cells[0])
        elif self._level == "cells" and self._focus is not None:
            cell = self._cell_by_key(self._focus, key)
            if cell is not None:
                self._show_report(self._focus, cell)

    async def _run(self, environment: Environment, plan: list[tuple[Request, MatrixCell]]) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        self._busy = True
        for request, cell in plan:
            self._state[_run_key(request, cell)] = "running"
        self._refresh_current()
        client = HttpxClient()
        limit = asyncio.Semaphore(4)

        async def one(request: Request, cell: MatrixCell) -> None:
            async with limit:
                execution = await execute_request(self.project, environment, request, client, cell)
            key = _run_key(request, cell)
            self._exec[key] = execution
            self._checks[key] = run_checks(self.project, request, execution)
            self._state[key] = "ok" if execution.ok else "failed"
            self._on_cell_done(request, cell)

        try:
            await asyncio.gather(*(one(request, cell) for request, cell in plan))
        finally:
            await client.aclose()
            self._busy = False
        ok = sum(1 for request, cell in plan if self._cell_ok(request, cell))
        self.app.notify(
            f"{ok}/{len(plan)} cells passed every check",
            title="Run complete",
            severity="information" if ok == len(plan) else "warning",
        )

    # ── level renders ──────────────────────────────────────────────────────
    def _show_requests(self) -> None:
        self._level = "requests"
        self.query_one("#run-switch", ContentSwitcher).current = "run-table-wrap"
        table = self.query_one("#run-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="sel", width=3)
        table.add_column("REQUEST", key="name")
        table.add_column("METHOD", key="method", width=8)
        table.add_column("CELLS", key="cells", width=6)
        table.add_column("STATUS", key="status", width=12)
        table.add_column("HANDLED", key="handled", width=9)
        for request in _requests(self.project):
            row_key = request.metadata.id or request.metadata.name
            table.add_row(*self._request_row(request), key=row_key)
        self._title()
        table.focus()

    def _show_cells(self, request: Request) -> None:
        self._level = "cells"
        self._focus = request
        self.query_one("#run-switch", ContentSwitcher).current = "run-table-wrap"
        table = self.query_one("#run-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="sel", width=3)
        table.add_column("CASE", key="case")
        table.add_column("STATUS", key="status", width=8)
        table.add_column("CODE", key="code", width=6)
        table.add_column("CHECKS", key="checks", width=18)
        table.add_column("LATENCY", key="latency", width=9)
        for cell in expand(self.project, request):
            table.add_row(*self._cell_row(request, cell), key=cell.key)
        self._title()
        table.focus()

    def _show_report(self, request: Request, cell: MatrixCell) -> None:
        self._level = "report"
        self._focus = request
        self._focus_cell = cell
        self.query_one("#run-switch", ContentSwitcher).current = "run-report-wrap"
        environment = _app_env(self)
        key = _run_key(request, cell)
        self.query_one("#run-report", Static).update(
            _report_full(
                self.project,
                environment,
                request,
                cell,
                self._exec.get(key),
                self._state.get(key, "pending"),
                self._checks.get(key, []),
            )
        )
        self._title()

    def _title(self) -> None:
        environment = _app_env(self)
        env = environment.metadata.name if environment else "no environment"
        sep = f" [{_DIM}]›[/] "
        if self._level == "requests":
            wrap = self.query_one("#run-table-wrap")
            wrap.border_title = "REQUESTS"
            wrap.border_subtitle = f"{env} · {len(self._selected)} selected"
        elif self._level == "cells" and self._focus is not None:
            wrap = self.query_one("#run-table-wrap")
            wrap.border_title = Text.from_markup(f"REQUESTS{sep}{self._focus.metadata.name}")
            wrap.border_subtitle = f"{len(expand(self.project, self._focus))} cases · esc back"
        elif self._level == "report" and self._focus is not None and self._focus_cell is not None:
            wrap = self.query_one("#run-report-wrap")
            crumb = f"REQUESTS{sep}{self._focus.metadata.name}"
            if self._focus_cell.key:
                crumb += f"{sep}{self._focus_cell.key}"
            wrap.border_title = Text.from_markup(crumb)
            wrap.border_subtitle = "esc back"

    # ── rows ───────────────────────────────────────────────────────────────
    def _request_row(self, request: Request) -> list[Text]:
        cells = expand(self.project, request)
        keys = {_run_key(request, cell) for cell in cells}
        chosen = len(keys & self._selected)
        box = "[✓]" if chosen == len(keys) else "[~]" if chosen else "[ ]"
        status = self._request_status(request)
        glyph, colour = _STATUS[status]
        method = request.spec.request.method
        return [
            Text(box, style=_ACCENT if chosen else _DIM),
            Text(request.metadata.name, style=_TEXT_HI),
            Text(method, style=_METHOD.get(method, _TEXT)),
            Text(f"×{len(cells)}", style=_AXIS),
            Text(f"{glyph} {status}", style=colour),
            Text(self._handled(request), style=_DIM),
        ]

    def _cell_row(self, request: Request, cell: MatrixCell) -> list[Text]:
        key = _run_key(request, cell)
        selected = key in self._selected
        state = self._state.get(key, "pending")
        glyph, colour = _RUN_GLYPH[state]
        execution = self._exec.get(key)
        response = execution.response if execution else None
        code = str(response.status) if response else "—"
        latency = f"{response.elapsed_ms:.0f}ms" if response else "—"
        code_colour = _SAME if code.startswith("2") else _DIM if code == "—" else _WARN
        return [
            Text("[✓]" if selected else "[ ]", style=_ACCENT if selected else _DIM),
            Text(cell.key or "base", style=_TEXT),
            Text(glyph, style=colour),
            Text(code, style=code_colour),
            _checks_cell(self._checks.get(key, [])),
            Text(latency, style=_DIM),
        ]

    def _refresh_row(self, key: str) -> None:
        table = self.query_one("#run-table", DataTable)
        if self._level == "requests":
            request = self._by_id(key)
            if request is None:
                return
            columns = ("sel", "name", "method", "cells", "status", "handled")
            for column, value in zip(columns, self._request_row(request), strict=True):
                table.update_cell(key, column, value)
        elif self._level == "cells" and self._focus is not None:
            cell = self._cell_by_key(self._focus, key)
            if cell is None:
                return
            columns = ("sel", "case", "status", "code", "checks", "latency")
            for column, value in zip(columns, self._cell_row(self._focus, cell), strict=True):
                table.update_cell(key, column, value)

    def _refresh_current(self) -> None:
        if self._level == "requests":
            for request in _requests(self.project):
                self._refresh_row(request.metadata.id or request.metadata.name)
        elif self._level == "cells" and self._focus is not None:
            for cell in expand(self.project, self._focus):
                self._refresh_row(cell.key)

    def _on_cell_done(self, request: Request, cell: MatrixCell) -> None:
        if self._level == "requests":
            self._refresh_row(request.metadata.id or request.metadata.name)
        elif self._level == "cells" and self._focus is request:
            self._refresh_row(cell.key)
        elif (
            self._level == "report"
            and self._focus is request
            and self._focus_cell is not None
            and self._focus_cell.key == cell.key
        ):
            self._show_report(request, cell)

    def _apply_cases(
        self, request: Request, cells: list[MatrixCell], keys: set[str] | None
    ) -> None:
        if keys is None:
            return
        request_id = request.metadata.id or request.metadata.name
        for cell in cells:
            key = (request_id, cell.key)
            self._selected.add(key) if cell.key in keys else self._selected.discard(key)
        if self._level == "requests":
            self._refresh_row(request_id)
        elif self._level == "cells" and self._focus is request:
            for cell in cells:
                self._refresh_row(cell.key)
        self._title()

    # ── status model ───────────────────────────────────────────────────────
    def _plan(self) -> list[tuple[Request, MatrixCell]]:
        return [
            (request, cell)
            for request in _requests(self.project)
            for cell in expand(self.project, request)
            if _run_key(request, cell) in self._selected
        ]

    def _cell_ok(self, request: Request, cell: MatrixCell) -> bool:
        key = _run_key(request, cell)
        return self._state.get(key) == "ok" and checks_passed(self._checks.get(key, []))

    def _request_status(self, request: Request) -> str:
        cells = [c for c in expand(self.project, request) if _run_key(request, c) in self._selected]
        if not cells:
            return "skipped"
        states = [self._state.get(_run_key(request, c), "pending") for c in cells]
        if any(state == "running" for state in states):
            return "running"
        if all(state == "pending" for state in states):
            return "pending"
        if any(state == "pending" for state in states):
            return "running"
        passing = sum(1 for c in cells if self._cell_ok(request, c))
        if passing == len(cells):
            return "success"
        return "failed" if passing == 0 else "partial"

    def _handled(self, request: Request) -> str:
        cells = [c for c in expand(self.project, request) if _run_key(request, c) in self._selected]
        if not cells:
            return "–"
        done = sum(1 for c in cells if self._state.get(_run_key(request, c)) in ("ok", "failed"))
        return f"{done}/{len(cells)}"

    # ── lookups ────────────────────────────────────────────────────────────
    def _cursor_key(self) -> str | None:
        table = self.query_one("#run-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def _current_request(self) -> Request | None:
        if self._level == "requests":
            key = self._cursor_key()
            return self._by_id(key) if key is not None else None
        return self._focus

    def _by_id(self, request_id: str) -> Request | None:
        obj = self.project.objects.get(request_id)
        if isinstance(obj, Request):
            return obj
        return next((r for r in _requests(self.project) if r.metadata.name == request_id), None)

    def _cell_by_key(self, request: Request, cell_key: str) -> MatrixCell | None:
        return next((c for c in expand(self.project, request) if c.key == cell_key), None)


class DiffView(Horizontal):
    """Replay every request against a baseline/candidate pair and diff the results."""

    def __init__(self, project: LoadedProject) -> None:
        """Build the diff view.

        Args:
            project: The project whose requests are diffed.
        """
        super().__init__(id="diff-view", classes="view")
        self.project = project
        self._cells: list[CellDiff] = []
        self._pair: tuple[Environment, Environment] | None = None

    def compose(self) -> ComposeResult:
        """Yield the cell list and the field-diff panel."""
        with Vertical(id="drift-panel", classes="panel"):
            yield Static(id="drift-content")
        with VerticalScroll(id="diffpane-panel", classes="panel hero"):
            yield Static(id="diff-detail")

    def on_mount(self) -> None:
        """Resolve the diff pair and render."""
        self.refresh_screen()

    def refresh_screen(self) -> None:
        """Re-resolve the pair and re-render."""
        try:
            self._pair = resolve_pair(self.project, None, None, None)
        except EnvironmentSelectionError:
            self._pair = None
        self.query_one("#drift-panel").border_title = "CELLS"
        pane = self.query_one("#diffpane-panel")
        if self._pair is not None:
            baseline, candidate = self._pair
            pane.border_title = Text.from_markup(
                f"{baseline.metadata.name}  [{_DIM}]⇄[/]  {candidate.metadata.name}"
            )
        else:
            pane.border_title = "DIFF"
        drift = sum(1 for cell in self._cells if cell.drifted)
        pane.border_subtitle = f"{drift} drift" if self._cells else "press x to run"
        self.query_one("#drift-content", Static).update(_diff_cells(self._cells))
        self.query_one("#diff-detail", Static).update(_diff_pane(self._cells, self._pair))

    def execute(self) -> None:
        """Run the diff across the pair."""
        if self._pair is None:
            self.app.notify("No diffPairs configured in the project manifest", severity="warning")
            return
        self.query_one("#diffpane-panel").border_subtitle = "running…"
        self.run_worker(self._run(self._pair), exclusive=True, group="diff")

    async def _run(self, pair: tuple[Environment, Environment]) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        baseline, candidate = pair
        client = HttpxClient()
        try:
            self._cells = await diff_run(
                self.project, baseline, candidate, _requests(self.project), client
            )
        finally:
            await client.aclose()
        report = build_report(baseline.metadata.name, candidate.metadata.name, self._cells)
        cast("ComparoApp", self.app).last_report = report
        self.refresh_screen()
        drift = sum(1 for cell in self._cells if cell.drifted)
        errors = sum(1 for cell in self._cells if cell.error is not None)
        passed = drift == 0 and errors == 0
        self.app.notify(
            f"{drift} drift · {errors} error — gate {'PASS' if passed else 'FAIL'}",
            title="Diff complete",
            severity="information" if passed else "error",
        )


class ReportView(Vertical):
    """Render the report of the most recent diff run."""

    def __init__(self) -> None:
        """Build the report view."""
        super().__init__(id="report-view", classes="view")

    def compose(self) -> ComposeResult:
        """Yield the report panel."""
        with VerticalScroll(id="report-panel", classes="panel hero"):
            yield Static(id="report-content")

    def on_mount(self) -> None:
        """Render the last report, if any."""
        self.refresh_screen()

    def refresh_screen(self) -> None:
        """Re-render from the app's last diff report."""
        report = cast("ComparoApp", self.app).last_report
        panel = self.query_one("#report-panel")
        content = self.query_one("#report-content", Static)
        if report is None:
            panel.border_title = "REPORT"
            panel.border_subtitle = "no run yet"
            content.update(Text("Run a diff (press x on the Diff screen) to build a report.", _DIM))
            return
        panel.border_title = Text.from_markup(
            f"REPORT  [{_DIM}]{report.baseline} ⇄ {report.candidate}[/]"
        )
        panel.border_subtitle = "gate PASS" if report.passed else "gate FAIL"
        content.update(_report_render(report))


class SettingsView(Vertical):
    """A read-only overview of the effective project configuration."""

    def __init__(self, project: LoadedProject) -> None:
        """Build the settings view.

        Args:
            project: The project whose configuration is shown.
        """
        super().__init__(id="settings-view", classes="view")
        self.project = project

    def compose(self) -> ComposeResult:
        """Yield the settings panel."""
        with VerticalScroll(id="settings-panel", classes="panel hero"):
            yield Static(id="settings-content")

    def on_mount(self) -> None:
        """Render the configuration."""
        self.refresh_screen()

    def refresh_screen(self) -> None:
        """Re-render the configuration for the current environment."""
        panel = self.query_one("#settings-panel")
        panel.border_title = "SETTINGS"
        panel.border_subtitle = "read-only"
        environment = _app_env(self)
        self.query_one("#settings-content", Static).update(
            _settings_render(self.project, environment)
        )


class FilterModal(ModalScreen[None]):
    """A narrow overlay that live-filters the Explorer tree as you type."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, explorer: ExplorerView) -> None:
        """Build the filter modal over an Explorer.

        Args:
            explorer: The Explorer whose tree this modal filters.
        """
        super().__init__()
        self._explorer = explorer

    def compose(self) -> ComposeResult:
        """Yield the dialog: a prompt, an input, and a live match count."""
        with Vertical(id="filter-dialog", classes="modal"):
            yield Input(placeholder="name, kind, or tag…", id="filter-input")
            yield Static(id="filter-count")

    def on_mount(self) -> None:
        """Title the dialog and seed the input with the current filter."""
        self.query_one("#filter-dialog").border_title = "FILTER"
        self.query_one(Input).value = self._explorer.filter_query

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-filter the tree on every keystroke."""
        count = self._explorer.apply_filter(event.value)
        self._show_count(count, event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Keep the filter and close."""
        self.dismiss(None)

    def action_cancel(self) -> None:
        """Clear the filter and close."""
        self._explorer.apply_filter("")
        self.dismiss(None)

    def _show_count(self, count: int, query: str) -> None:
        label = self.query_one("#filter-count", Static)
        if not query.strip():
            label.update(Text("everything", style=_DIM))
            return
        colour = _SAME if count else _DRIFT
        label.update(Text(f"{count} match{'' if count == 1 else 'es'}", style=colour))


class GraphModal(ModalScreen[None]):
    """A full overlay drawing the reference graph between project objects."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "close"),
        Binding("g", "close", "close"),
        Binding("q", "close", "close"),
    ]

    def __init__(self, project: LoadedProject) -> None:
        """Build the graph modal for a project.

        Args:
            project: The project whose references are drawn.
        """
        super().__init__()
        self._project = project

    def compose(self) -> ComposeResult:
        """Yield a scrollable panel of the reference graph."""
        with VerticalScroll(id="graph-dialog", classes="modal"):
            yield Static(_graph(self._project), id="graph-content")

    def on_mount(self) -> None:
        """Title the dialog."""
        dialog = self.query_one("#graph-dialog")
        dialog.border_title = "PROJECT GRAPH"
        dialog.border_subtitle = "references · g or esc to close"

    def action_close(self) -> None:
        """Close the overlay."""
        self.dismiss(None)


class HelpModal(ModalScreen[None]):
    """An overlay listing every command available on the current screen."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "close"),
        Binding("question_mark", "close", "close"),
        Binding("q", "close", "close"),
    ]

    def __init__(self, screen: str) -> None:
        """Build the help overlay for a screen.

        Args:
            screen: The active screen id, whose keys are described first.
        """
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        """Yield the scrollable help body."""
        with VerticalScroll(id="help-dialog", classes="modal"):
            yield Static(_help_body(self._screen), id="help-content")

    def on_mount(self) -> None:
        """Title the dialog."""
        dialog = self.query_one("#help-dialog")
        dialog.border_title = "HELP"
        dialog.border_subtitle = "? or esc to close"

    def action_close(self) -> None:
        """Close the overlay."""
        self.dismiss(None)


class MatrixPickerModal(ModalScreen["MatrixCell | None"]):
    """A small overlay to pick which matrix case a curl is generated for."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, cells: list[MatrixCell]) -> None:
        """Build the picker over a request's expanded matrix cells.

        Args:
            cells: Every matrix combination for the request.
        """
        super().__init__()
        self._cells = cells

    def compose(self) -> ComposeResult:
        """Yield the option list of matrix cases."""
        with Vertical(id="picker-dialog", classes="modal"):
            yield OptionList(*(cell.key or "base (no matrix)" for cell in self._cells))

    def on_mount(self) -> None:
        """Title the dialog and focus the list."""
        self.query_one("#picker-dialog").border_title = "SELECT MATRIX CASE"
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the chosen cell."""
        self.dismiss(self._cells[event.option_index])

    def action_cancel(self) -> None:
        """Close without choosing."""
        self.dismiss(None)


class CurlModal(ModalScreen[None]):
    """Shows the masked curl for a request; ``c`` copies the real one."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "close"),
        Binding("q", "close", "close"),
        Binding("c", "copy", "copy"),
    ]

    def __init__(
        self, project: LoadedProject, environment: Environment, request: Request, cell: MatrixCell
    ) -> None:
        """Build the curl overlay.

        Args:
            project: The loaded project.
            environment: The environment to resolve against.
            request: The request to render.
            cell: The matrix case to inject.
        """
        super().__init__()
        self._project = project
        self._environment = environment
        self._request = request
        self._cell = cell

    def compose(self) -> ComposeResult:
        """Yield the scrollable curl body."""
        with VerticalScroll(id="curl-dialog", classes="modal"):
            yield Static(id="curl-content")

    def on_mount(self) -> None:
        """Render the masked curl and title the dialog."""
        dialog = self.query_one("#curl-dialog")
        dialog.border_title = "CURL" + (f" · {self._cell.key}" if self._cell.key else "")
        dialog.border_subtitle = "c copy with real secrets · esc close"
        self.query_one("#curl-content", Static).update(_bash(self._curl(Sink.DISPLAY)))

    def action_copy(self) -> None:
        """Copy the real (unmasked) curl to the clipboard."""
        try:
            command = self._curl(Sink.EXECUTE)
        except SecretError as error:
            self.app.notify(str(error), title="Cannot copy", severity="error")
            return
        self.app.copy_to_clipboard(command)
        self.app.notify("Real curl copied to clipboard", title="Copied", severity="information")

    def action_close(self) -> None:
        """Close the overlay."""
        self.dismiss(None)

    def _curl(self, sink: Sink) -> str:
        resolver = Resolver(self._project, self._environment, sink)
        return to_curl(resolver.resolve_request(self._request, self._cell))


class MatrixSelectModal(ModalScreen["set[str] | None"]):
    """A multi-select overlay for choosing which matrix cases run."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "apply", "apply"),
        Binding("a", "all", "all"),
        Binding("n", "none", "none"),
    ]

    def __init__(self, request: Request, cells: list[MatrixCell], selected: set[str]) -> None:
        """Build the case picker.

        Args:
            request: The request whose cases are picked.
            cells: Every matrix cell of the request.
            selected: The currently-selected cell keys.
        """
        super().__init__()
        self._request = request
        self._cells = cells
        self._selected = selected

    def compose(self) -> ComposeResult:
        """Yield the selection list of cases."""
        with Vertical(id="mselect-dialog", classes="modal"):
            yield SelectionList[str](
                *(
                    (cell.key or "base", cell.key, cell.key in self._selected)
                    for cell in self._cells
                )
            )

    def on_mount(self) -> None:
        """Title the dialog and focus the list."""
        dialog = self.query_one("#mselect-dialog")
        dialog.border_title = f"CASES · {self._request.metadata.name}"
        dialog.border_subtitle = "space toggle · a all · n none · esc apply"
        self.query_one(SelectionList).focus()

    def action_apply(self) -> None:
        """Apply the current selection and close."""
        self.dismiss(set(self.query_one(SelectionList).selected))

    def action_all(self) -> None:
        """Select every case."""
        self.query_one(SelectionList).select_all()

    def action_none(self) -> None:
        """Deselect every case."""
        self.query_one(SelectionList).deselect_all()


class ErrorView(VerticalScroll):
    """The replacement screen shown when a project will not load.

    Lists every diagnostic grouped by file, with each loader hint rendered as
    the fix to apply, and re-checks in place when the user presses ``r``.
    """

    def __init__(self, error: LoadError) -> None:
        """Build the error view.

        Args:
            error: The load failure whose diagnostics are shown.
        """
        super().__init__(id="error-view", classes="panel")
        self._error = error

    def compose(self) -> ComposeResult:
        """Yield the diagnostics report."""
        yield Static(id="error-content")

    def on_mount(self) -> None:
        """Render the initial diagnostics."""
        self.set_error(self._error)

    def set_error(self, error: LoadError) -> None:
        """Show a fresh set of diagnostics.

        Args:
            error: The load failure to render.
        """
        self._error = error
        count = len(error.diagnostics)
        self.border_title = f"{count} PROBLEM(S) — project will not load"
        self.border_subtitle = str(error.root)
        self.query_one("#error-content", Static).update(_error_report(error))

    def set_ok(self) -> None:
        """Show that the project now loads cleanly."""
        self.border_title = "PROJECT LOADS"
        self.border_subtitle = str(self._error.root)
        self.query_one("#error-content", Static).update(_ok_report())


class ComparoApp(App[None]):
    """The comparo application shell."""

    CSS_PATH = "comparo.tcss"
    TITLE = "comparo"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        # Each tab is on a top-row key: the digit (QWERTY) and the AZERTY
        # unshifted character on the same physical key, so no Shift is needed.
        Binding("1,ampersand", "screen('explorer')", "Explorer"),
        Binding("2,é", "screen('run')", "Run"),
        Binding("3,quotation_mark", "screen('diff')", "Diff"),
        Binding("4,apostrophe", "screen('report')", "Report"),
        Binding("5,left_parenthesis", "screen('settings')", "Settings"),
        Binding("slash", "filter", "Filter"),
        Binding("g", "graph", "Graph"),
        Binding("h", "health", "Health"),
        Binding("m", "matrix", "Cases"),
        Binding("p", "curl", "curl"),
        Binding("r", "raw_or_reload", "Raw / reload"),
        Binding("x", "execute", "Run"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(
        self, project: LoadedProject | None = None, *, error: LoadError | None = None
    ) -> None:
        """Build the app for a loaded project, or a load failure.

        Args:
            project: The project to open, or ``None`` when *error* is given.
            error: A load failure to show on the replacement screen instead.
        """
        super().__init__()
        self.project = project
        self.error = error
        self.environment = _default_environment(project) if project is not None else None
        self.last_report: RunReport | None = None

    @classmethod
    def from_error(cls, error: LoadError) -> "ComparoApp":
        """Build an app that opens straight onto the error screen.

        Args:
            error: The load failure to display.

        Returns:
            An app in error mode.
        """
        return cls(error=error)

    def compose(self) -> ComposeResult:
        """Yield the nav bar, the content (or error screen), and the status bar."""
        yield NavBar(self._nav_status())
        if self.error is not None:
            yield ErrorView(self.error)
        else:
            project = self.project
            assert project is not None
            with ContentSwitcher(initial="explorer-view", id="content"):
                yield ExplorerView(project, self.environment)
                yield RunView(project)
                yield DiffView(project)
                yield ReportView()
                yield SettingsView(project)
        yield StatusBar()

    def on_mount(self) -> None:
        """Register the theme and set the initial status."""
        self.register_theme(COMPARO_INK)
        self.theme = "comparo-ink"
        self._status("error" if self.error is not None else "explorer")

    def action_screen(self, name: str) -> None:
        """Switch to a named screen.

        Args:
            name: The screen id (``explorer``, ``diff``, …).
        """
        if self.error is not None:
            return
        self.query_one("#content", ContentSwitcher).current = f"{name}-view"
        self.query_one(NavBar).active = name
        view = self.query_one(f"#{name}-view")
        if isinstance(view, (RunView, DiffView, ReportView, SettingsView)):
            view.refresh_screen()
        self._status(name)

    def action_execute(self) -> None:
        """Run the active screen's action — execute (Run) or diff (Diff)."""
        if self.error is not None:
            return
        active = self.query_one(NavBar).active
        if active == "run":
            self.query_one(RunView).execute()
        elif active == "diff":
            self.query_one(DiffView).execute()

    def action_matrix(self) -> None:
        """Open the matrix-case picker on the Run screen."""
        if self.error is not None or self.query_one(NavBar).active != "run":
            return
        self.query_one(RunView).open_case_picker()

    def action_filter(self) -> None:
        """Open the filter overlay on the Explorer."""
        if self.error is not None or self.query_one(NavBar).active != "explorer":
            return
        self.push_screen(FilterModal(self.query_one(ExplorerView)))

    def action_graph(self) -> None:
        """Open the reference-graph overlay."""
        if self.error is not None or self.project is None:
            return
        self.push_screen(GraphModal(self.project))

    def action_health(self) -> None:
        """Probe the selected environment's health checks."""
        if self.error is not None or self.query_one(NavBar).active != "explorer":
            return
        self.query_one(ExplorerView).run_health_on_selected()

    def action_curl(self) -> None:
        """Show the curl for the selected request."""
        if self.error is not None or self.query_one(NavBar).active != "explorer":
            return
        self.query_one(ExplorerView).print_curl()

    def action_help(self) -> None:
        """Open the help overlay for the current screen."""
        screen = "error" if self.error is not None else self.query_one(NavBar).active
        self.push_screen(HelpModal(screen))

    def action_raw_or_reload(self) -> None:
        """``r`` re-checks a failed project, or toggles raw/resolved on the Explorer."""
        if self.error is not None:
            self._reload()
        elif self.query_one(NavBar).active == "explorer":
            self.query_one(ExplorerView).toggle_raw()

    def set_default_environment(self, environment: Environment) -> None:
        """Adopt *environment* as the default requests resolve against.

        Args:
            environment: The environment to make default.
        """
        self.environment = environment
        self.query_one(ExplorerView).set_default(environment)
        self.query_one(NavBar).set_status(self._nav_status())

    def _reload(self) -> None:
        if self.error is None:
            return
        try:
            load_project(self.error.root)
        except LoadError as fresh:
            self.error = fresh
            self.query_one(ErrorView).set_error(fresh)
            self.query_one(NavBar).set_status(f"[{_DRIFT}]✗ {len(fresh.diagnostics)} problem(s)[/]")
        else:
            self.query_one(ErrorView).set_ok()
            self.query_one(NavBar).set_status(f"[{_SAME}]✓ loads[/]")

    def on_click(self, event: object) -> None:
        """Switch screens when a nav tab is clicked."""
        identifier = getattr(getattr(event, "widget", None), "id", None)
        if isinstance(identifier, str) and identifier.startswith("nav-"):
            name = identifier.removeprefix("nav-")
            if name in {tab for tab, _ in NavBar.TABS}:
                self.action_screen(name)

    def _nav_status(self) -> str:
        if self.error is not None:
            return f"[{_DRIFT}]✗ {len(self.error.diagnostics)} problem(s)[/]"
        name = (
            self.project.project.metadata.name
            if self.project and self.project.project
            else "project"
        )
        env = self.environment.metadata.name if self.environment else "—"
        return f"[{_DIM}]{name}   env [/][{_TEXT_HI}]{env}[/]"

    def _status(self, screen: str) -> None:
        if screen == "error":
            self.query_one(StatusBar).show(_ERROR_KEYS, "load failed · fix files, press r")
            return
        if screen == "explorer":
            self.query_one(ExplorerView).refresh_footer()
            return
        env = self.environment.metadata.name if self.environment else "—"
        keys, context = {
            "run": (_RUN_KEYS, f"env · {env}"),
            "diff": (_DIFF_KEYS, "baseline ⇄ candidate"),
            "report": (_REPORT_KEYS, "last diff run"),
            "settings": (_SETTINGS_KEYS, "read-only"),
        }.get(screen, (_EXPLORER_KEYS, ""))
        self.query_one(StatusBar).show(keys, context)


def _default_environment(project: LoadedProject) -> Environment | None:
    try:
        return select_environment(project, None)
    except EnvironmentSelectionError:
        for obj in project.objects.values():
            if isinstance(obj, Environment):
                return obj
        return None


def _branch(label: str, count: int) -> Text:
    return Text.assemble((f"{label}  ", f"bold {_LABEL}"), (f"{count}", _DIM))


def _leaf(obj: object, *, health: Health = Health.UNKNOWN, default: bool = False) -> Text:
    metadata = getattr(obj, "metadata", None)
    name = str(getattr(metadata, "name", "?"))
    row = Text()
    if isinstance(obj, Environment):
        row.append("● ", style=_HEALTH_COLOR[health])
        row.append(name, style=_TEXT_HI if default else _TEXT)
        if _is_remote(obj):
            row.append("  live", style=f"bold {_DANGER}")
        if default:
            row.append("  default", style=f"bold {_ACCENT}")
    elif isinstance(obj, Matrix):
        row.append(name, style=_AXIS)
        row.append(f"  ×{len(obj.spec.values)}", style=_DIM)
    elif isinstance(obj, Request):
        row.append(name, style=_TEXT)
        if obj.spec.matrix:
            row.append("  matrix", style=_AXIS)
    else:
        row.append(name, style=_TEXT)
    return row


def _project_leaf(manifest: Project) -> Text:
    row = Text()
    row.append("◆ ", style=_ACCENT)
    row.append(str(manifest.metadata.name or "project"), style=f"bold {_TEXT_HI}")
    row.append("  project", style=_DIM)
    return row


def _project_detail(manifest: Project) -> Group:
    spec = manifest.spec
    parts: list[RenderableType] = []
    head = Text()
    if spec.data:
        head.append("data       ", style=_LABEL)
        head.append(f"{spec.data}\n", style=_TEXT)
    environments = spec.environments if isinstance(spec.environments, dict) else {}
    default = environments.get("default")
    if isinstance(default, str):
        head.append("default    ", style=_LABEL)
        head.append(f"{default}\n", style=_ACCENT)
    parts.append(head)
    pairs = environments.get("diffPairs")
    if isinstance(pairs, list) and pairs:
        block = Text("\nDIFF PAIRS", style=_LABEL)
        for pair in pairs:
            if isinstance(pair, dict):
                block.append(f"\n  {pair.get('name', '')!s:<16}", style=_TEXT)
                block.append(
                    f"{pair.get('baseline', '')} ⇄ {pair.get('candidate', '')}", style=_AXIS
                )
        parts.append(block)
    sections: tuple[tuple[str, object], ...] = (
        ("run", spec.run),
        ("diff", spec.diff),
        ("selection", spec.selection),
        ("report", spec.report),
        ("redaction", spec.redaction),
        ("plugins", spec.plugins),
    )
    for label, value in sections:
        if value:
            parts.append(Text(f"\n\n{label.upper()}", style=_LABEL))
            parts.append(_json(value))
    return Group(*parts)


def _is_remote(environment: Environment) -> bool:
    url = environment.spec.base_url.lower()
    return not any(host in url for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


def _matches(obj: object, kind: type, needle: str) -> bool:
    if not needle:
        return True
    metadata = getattr(obj, "metadata", None)
    haystack = [
        str(getattr(metadata, "name", "")),
        str(getattr(metadata, "id", "") or ""),
        kind.__name__.lower(),
    ]
    haystack.extend(getattr(metadata, "tags", None) or [])
    return any(needle in part.lower() for part in haystack)


def _title(obj: object, tag: str) -> Text:
    metadata = getattr(obj, "metadata", None)
    identifier = str(getattr(metadata, "id", "") or getattr(metadata, "name", ""))
    return Text.assemble((identifier, f"bold {_ACCENT}"), ("   ", ""), (tag, _AXIS))


def _description(obj: object) -> Text:
    metadata = getattr(obj, "metadata", None)
    description = getattr(metadata, "description", None)
    if description:
        return Text(str(description), style=_TEXT)
    return Text("no description", style=_DIM)


def _request_detail(
    project: LoadedProject, request: Request, resolved: ResolvedRequest, *, raw: bool = False
) -> Group:
    outbound = request.spec.request
    parts: list[RenderableType] = []
    head = Text()
    head.append(
        f" {resolved.method} ", style=f"bold {_INK} on {_METHOD.get(resolved.method, _ACCENT)}"
    )
    head.append("  ")
    head.append(outbound.endpoint if raw else resolved.url, style=_TEXT_HI)
    parts.append(head)
    if request.metadata.description:
        parts.append(Text(f"\n{request.metadata.description}", style=_DIM))
    tags = request.metadata.tags or []
    matrices = _matrix_summary(project, request.spec.matrix)
    meta = Text()
    if tags:
        meta.append("\ntags       ", style=_LABEL)
        meta.append(" · ".join(tags), style=_AXIS)
    if matrices:
        meta.append("\nmatrix     ", style=_LABEL)
        meta.append(matrices, style=_AXIS)
    parts.append(meta)
    headers = Text("\n\nHEADERS", style=_LABEL)
    for key, rendered in _header_rows(outbound.headers, resolved.headers, raw=raw):
        headers.append(f"\n  {key:<18}", style=_DIM)
        headers.append(rendered)
    parts.append(headers)
    query_source = (outbound.query or {}) if raw else resolved.query
    if query_source:
        query = Text("\n\nQUERY", style=_LABEL)
        for key, value in query_source.items():
            query.append(f"\n  {key:<18}", style=_DIM)
            query.append(_hole_str(value) if raw else str(value), style=_AXIS)
        parts.append(query)
    body_source = outbound.body if raw else resolved.body
    if body_source is not None:
        parts.append(Text("\n\nBODY", style=_LABEL))
        parts.append(_json(body_source))
    response = request.spec.response
    if response is not None:
        section = Text("\n\nRESPONSE", style=_LABEL)
        if response.status:
            section.append("\n  status   ", style=_DIM)
            section.append(str(response.status), style=_TEXT)
        for name, reference in (("schema", response.schema), ("diff", response.diff)):
            identifier = _ref_id(reference)
            if identifier:
                section.append(f"\n  {name:<9}", style=_DIM)
                section.append(identifier, style=_TEXT)
        parts.append(section)
    return Group(*parts)


def _header_rows(
    raw_headers: object, resolved_headers: list[tuple[str, object]], *, raw: bool
) -> list[tuple[str, Text]]:
    if raw:
        pairs = _raw_header_pairs(raw_headers)
        return [(key, Text(_hole_str(value), style=_AXIS)) for key, value in pairs]
    rows: list[tuple[str, Text]] = []
    for key, value in resolved_headers:
        masked = "••••" in str(value)
        rows.append((key, Text(str(value), style=_DRIFT if masked else _TEXT)))
    return rows


def _raw_header_pairs(headers: object) -> list[tuple[str, object]]:
    if isinstance(headers, dict):
        target = headers.get("$val")
        if isinstance(target, str):
            return [("(reference)", {"$val": target})]
    pairs: list[tuple[str, object]] = []
    if isinstance(headers, list):
        for item in headers:
            if isinstance(item, dict) and "key" in item:
                pairs.append((str(item["key"]), item.get("value")))
    return pairs


def _hole_str(value: object) -> str:
    if isinstance(value, dict) and len(value) == 1:
        key, target = next(iter(value.items()))
        return f"{key} {target}"
    return str(value)


def _object_detail(obj: object) -> RenderableType:
    if isinstance(obj, Environment):
        return _environment_detail(obj, None)
    if isinstance(obj, Matrix):
        return Group(_matrix_head(obj), _json(obj.spec.values))
    if isinstance(obj, DiffProfile):
        return _diffprofile_detail(obj)
    if isinstance(obj, Schema):
        return _json(obj.spec)
    if isinstance(obj, Instance):
        return _json(obj.spec.value)
    return Text(str(obj), style=_TEXT)


def _environment_detail(env: Environment, report: HealthReport | None) -> Text:
    spec = env.spec
    text = Text()
    remote = _is_remote(env)
    text.append("baseUrl    ", style=_LABEL)
    text.append(f"{spec.base_url}", style=_ACCENT)
    text.append("   live\n" if remote else "   local\n", style=_DANGER if remote else _DIM)
    if spec.timeout is not None:
        text.append("timeout    ", style=_LABEL)
        text.append(f"connect {spec.timeout.connect} · read {spec.timeout.read}\n", style=_TEXT)
    for section, mapping in (("VARIABLES", spec.variables), ("SECRETS", spec.secrets)):
        if mapping:
            text.append(f"\n{section}\n", style=_LABEL)
            for key in mapping:
                text.append(f"  {key:<22}", style=_DIM)
                text.append(
                    "••••••\n" if section == "SECRETS" else f"{mapping[key]}\n",
                    style=_DRIFT if section == "SECRETS" else _TEXT,
                )
    if spec.health:
        text.append("\nHEALTH", style=_LABEL)
        if report is not None:
            text.append(f"   {report.status.value}", style=_HEALTH_COLOR[report.status])
        text.append("\n", style=_LABEL)
        outcomes = {result.endpoint: result for result in (report.results if report else [])}
        for check in spec.health:
            result = outcomes.get(check.endpoint)
            if result is None:
                text.append(f"  ○ {check.method} {check.endpoint}\n", style=_DIM)
            else:
                glyph, colour = ("✓", _SAME) if result.ok else ("✗", _DRIFT)
                text.append(f"  {glyph} {check.method} {check.endpoint}", style=colour)
                text.append(f"   {result.detail}\n", style=_DIM)
    return text


def _matrix_head(matrix: Matrix) -> Text:
    spec = matrix.spec
    text = Text()
    text.append("target   ", style=_LABEL)
    text.append(f"{spec.target}\n", style=_TEXT)
    text.append("mode     ", style=_LABEL)
    text.append(f"{spec.mode}\n", style=_TEXT)
    text.append(f"\nVALUES  ×{len(spec.values)}\n", style=_LABEL)
    return text


def _diffprofile_detail(profile: DiffProfile) -> Text:
    spec = profile.spec
    text = Text()
    text.append("default  ", style=_LABEL)
    text.append(f"{spec.default}\n", style=_MODE.get(spec.default, _TEXT))
    if spec.rules:
        text.append("\nRULES\n", style=_LABEL)
        for rule in spec.rules:
            text.append(f"  {rule.path:<30}", style=_TEXT)
            text.append(f"{rule.mode}\n", style=_MODE.get(rule.mode, _TEXT))
    return text


def _render_provenance(trail: list[Trail]) -> Text:
    if not trail:
        return Text("all literal — nothing resolved", style=_DIM)
    text = Text()
    for entry in trail:
        colour = _DRIFT if entry.tainted else _AXIS
        text.append(f"{entry.path:<22}", style=_TEXT)
        text.append("← ", style=_DIM)
        text.append(entry.detail, style=colour)
        if entry.tainted:
            text.append("  · masked", style=_DIM)
        elif entry.origin is Origin.VARIABLE:
            text.append("  · variable", style=_DIM)
        elif entry.origin is Origin.INSTANCE:
            text.append("  · instance", style=_DIM)
        text.append("\n")
    return text


def _help_body(screen: str) -> Text:
    text = Text()
    text.append(f"{_HELP_TITLE.get(screen, screen.upper())}\n\n", style=f"bold {_TEXT_HI}")
    for key, description in _HELP_SCREEN.get(screen, ()):
        _help_row(text, key, description)
    text.append("\nEVERYWHERE\n", style=f"bold {_LABEL}")
    for key, description in _HELP_GLOBAL:
        _help_row(text, key, description)
    return text


def _help_row(text: Text, key: str, description: str) -> None:
    text.append(f"  {key:<8}", style=f"bold {_ACCENT}")
    text.append(f"  {description}\n", style=_TEXT)


def _json(value: object) -> Syntax:
    rendered = json.dumps(value, indent=2, ensure_ascii=False)
    return Syntax(rendered, "json", theme="one-dark", background_color=_SYNTAX_BG, word_wrap=True)


def _bash(command: str) -> Syntax:
    return Syntax(command, "bash", theme="one-dark", background_color=_SYNTAX_BG, word_wrap=True)


def _matrix_summary(project: LoadedProject, matrix: list[object] | None) -> str:
    parts: list[str] = []
    for reference in matrix or []:
        identifier = _ref_id(reference)
        obj = project.objects.get(identifier) if identifier else None
        if isinstance(obj, Matrix):
            parts.append(f"{(identifier or '').split('.')[-1]} ×{len(obj.spec.values)}")
    return " · ".join(parts)


def _ref_id(reference: object) -> str | None:
    if isinstance(reference, dict):
        target = reference.get("$ref")
        if isinstance(target, str):
            return target
    return None


def _sigil_refs(spec: object, sigil: str) -> set[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            target = node.get(sigil)
            if isinstance(target, str):
                found.add(target)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(msgspec.to_builtins(spec))
    return found


def _edges(project: LoadedProject) -> list[tuple[str, str, str]]:
    """Return ``(request_id, relation, target_id)`` links out of every request."""
    edges: list[tuple[str, str, str]] = []
    for obj in project.objects.values():
        if not isinstance(obj, Request):
            continue
        source = obj.metadata.id or obj.metadata.name
        for reference in obj.spec.matrix or []:
            target = _ref_id(reference)
            if target:
                edges.append((source, "matrix", target))
        response = obj.spec.response
        if response is not None:
            for relation, reference in (("schema", response.schema), ("diff", response.diff)):
                target = _ref_id(reference)
                if target:
                    edges.append((source, relation, target))
        for target in sorted(_sigil_refs(obj.spec, "$val")):
            edges.append((source, "value", target))
    return edges


def _short(project: LoadedProject, identifier: str) -> str:
    obj = project.objects.get(identifier)
    metadata = getattr(obj, "metadata", None)
    name = getattr(metadata, "name", None)
    return str(name) if name else identifier.split(".")[-1]


def _kind_of(project: LoadedProject, identifier: str) -> tuple[str, str]:
    obj = project.objects.get(identifier)
    if obj is None:
        return "?", _DIM
    return type(obj).__name__, _KIND_COLOR.get(type(obj), _TEXT)


def _graph(project: LoadedProject) -> Text:
    edges = _edges(project)
    if not edges:
        return Text("no references between objects yet", style=_DIM)
    outgoing: dict[str, list[tuple[str, str]]] = {}
    incoming: dict[str, set[str]] = {}
    for source, relation, target in edges:
        outgoing.setdefault(source, []).append((relation, target))
        incoming.setdefault(target, set()).add(source)

    text = Text()
    text.append("REQUESTS", style=f"bold {_LABEL}")
    text.append("   what each request links to\n\n", style=_DIM)
    for source in sorted(outgoing):
        text.append("● ", style=_ACCENT)
        text.append(f"{_short(project, source)}\n", style=_TEXT_HI)
        links = outgoing[source]
        for index, (relation, target) in enumerate(links):
            connector = "└─" if index == len(links) - 1 else "├─"
            kind_name, colour = _kind_of(project, target)
            text.append(f"  {connector} {relation:<7}→ ", style=_DIM)
            text.append(_short(project, target), style=colour)
            text.append(f"  {kind_name.lower()}\n", style=_DIM)
        text.append("\n")

    text.append("SHARED OBJECTS", style=f"bold {_LABEL}")
    text.append("   what references them\n\n", style=_DIM)
    for target in sorted(incoming):
        kind_name, colour = _kind_of(project, target)
        sources = sorted(_short(project, source) for source in incoming[target])
        text.append(f"{_short(project, target):<22}", style=colour)
        text.append(f"{kind_name.lower():<12}", style=_DIM)
        text.append("← ", style=_DIM)
        text.append(", ".join(sources) + "\n", style=_TEXT)
    return text


def _error_report(error: LoadError) -> Text:
    grouped: dict[str, list[Diagnostic]] = {}
    for diagnostic in error.diagnostics:
        try:
            location = str(diagnostic.file.relative_to(error.root))
        except ValueError:
            location = str(diagnostic.file)
        grouped.setdefault(location, []).append(diagnostic)

    text = Text()
    for location, diagnostics in grouped.items():
        text.append(f"▌ {location}\n", style=f"bold {_DRIFT}")
        for diagnostic in diagnostics:
            text.append("  • ", style=_DRIFT)
            if diagnostic.line is not None:
                text.append(f"line {diagnostic.line}  ", style=_WARN)
            text.append(f"{diagnostic.message}\n", style=_TEXT_HI)
            if diagnostic.hint is not None:
                text.append("    ✎ fix  ", style=f"bold {_SAME}")
                text.append(f"{diagnostic.hint}\n", style=_SAME)
        text.append("\n")
    text.append("fix the files above and press ", style=_DIM)
    text.append("r", style=f"bold {_ACCENT}")
    text.append(" to re-check.", style=_DIM)
    return text


def _ok_report() -> Text:
    text = Text()
    text.append("✓ ", style=f"bold {_SAME}")
    text.append("Every object now parses, indexes, and resolves.\n\n", style=_TEXT_HI)
    text.append("Relaunch ", style=_DIM)
    text.append("comparo tui", style=_ACCENT)
    text.append(" to explore the project.", style=_DIM)
    return text


def _app_env(widget: object) -> Environment | None:
    app = getattr(widget, "app", None)
    return getattr(app, "environment", None)


def _requests(project: LoadedProject) -> list[Request]:
    return sorted(
        (obj for obj in project.objects.values() if isinstance(obj, Request)),
        key=lambda request: request.metadata.id or "",
    )


def _table() -> Table:
    return Table(box=None, expand=True, pad_edge=False, show_edge=False)


def _run_key(request: Request, cell: MatrixCell) -> tuple[str, str]:
    return (request.metadata.id or request.metadata.name, cell.key)


def _checks_cell(checks: list[Check]) -> Text:
    if not checks:
        return Text("—", style=_DIM)
    ok = sum(1 for check in checks if check.ok)
    text = Text(f"{ok}/{len(checks)} ", style=_SAME if ok == len(checks) else _DRIFT)
    for check in checks:
        text.append("✓" if check.ok else "✗", style=_SAME if check.ok else _DRIFT)
    return text


def _report_full(
    project: LoadedProject,
    environment: Environment | None,
    request: Request,
    cell: MatrixCell,
    execution: Execution | None,
    state: str,
    checks: list[Check],
) -> Group:
    resolved = (
        Resolver(project, environment).resolve_request(request, cell)
        if environment is not None
        else None
    )
    method = resolved.method if resolved else request.spec.request.method
    parts: list[RenderableType] = []
    head = Text()
    head.append(f" {method} ", style=f"bold {_INK} on {_METHOD.get(method, _ACCENT)}")
    head.append("  ")
    head.append(resolved.url if resolved else request.spec.request.endpoint, style=_TEXT_HI)
    parts.append(head)
    if request.metadata.description:
        parts.append(Text(f"\n{request.metadata.description}", style=_DIM))
    meta = Text()
    if cell.key:
        meta.append("\ncase       ", style=_LABEL)
        meta.append(cell.key, style=_AXIS)
    glyph, colour = _RUN_GLYPH[state]
    meta.append("\nstatus     ", style=_LABEL)
    meta.append(f"{glyph} {state}", style=colour)
    if execution is not None and execution.response is not None:
        response = execution.response
        meta.append(f"   {response.status} · {response.elapsed_ms:.0f}ms", style=_TEXT)
    parts.append(meta)

    if checks:
        section = Text("\n\nCHECKS", style=_LABEL)
        for check in checks:
            mark, tint = ("✓", _SAME) if check.ok else ("✗", _DRIFT)
            section.append(f"\n  {mark} {check.name:<10}", style=tint)
            section.append(check.detail, style=_DIM)
        parts.append(section)

    if resolved is not None:
        request_section = Text("\n\nREQUEST", style=_LABEL)
        for key, value in resolved.headers:
            masked = "••••" in str(value)
            request_section.append(f"\n  {key:<20}", style=_DIM)
            request_section.append(str(value), style=_DRIFT if masked else _TEXT)
        parts.append(request_section)
        if resolved.body is not None:
            parts.append(Text("\n\nREQUEST BODY", style=_LABEL))
            parts.append(_json(resolved.body))

    if execution is not None and execution.response is not None:
        parts.append(Text("\n\nRESPONSE HEADERS", style=_LABEL))
        headers = Text()
        for key, value in execution.response.headers[:12]:
            headers.append(f"\n  {key:<22}", style=_DIM)
            headers.append(str(value), style=_TEXT)
        parts.append(headers)
        parts.append(Text("\n\nRESPONSE BODY", style=_LABEL))
        parts.append(_body_render(execution.response.body))
    elif execution is not None and execution.error is not None:
        error = Text("\n\n")
        error.append(execution.error, style=_DRIFT)
        parts.append(error)
    elif state == "pending":
        parts.append(Text("\n\nnot run — press x to execute", style=_DIM))
    return Group(*parts)


def _body_render(body: bytes) -> RenderableType:
    try:
        return _json(json.loads(body))
    except (ValueError, TypeError):
        return Text(body.decode("utf-8", "replace")[:4000], style=_TEXT)


def _diff_cells(cells: list[CellDiff]) -> Text:
    if not cells:
        return Text("press x to run the diff", style=_DIM)
    text = Text()
    for cell in cells:
        if cell.error is not None:
            glyph, colour = "!", _WARN
        elif cell.drifted:
            glyph, colour = "✗", _DRIFT
        else:
            glyph, colour = "✓", _SAME
        text.append(f" {glyph} ", style=f"bold {colour}")
        text.append(cell.request.metadata.name, style=_TEXT_HI if cell.drifted else _TEXT)
        if cell.cell_key:
            text.append(f"\n    {cell.cell_key}", style=_AXIS)
        text.append("\n")
    return text


def _diff_pane(cells: list[CellDiff], pair: tuple[Environment, Environment] | None) -> Text:
    if not cells:
        return _diff_empty(pair)
    notable = [cell for cell in cells if cell.drifted or cell.error is not None]
    if not notable:
        return Text("✓ every cell is identical — gate PASS", style=f"bold {_SAME}")
    text = Text()
    for cell in notable:
        name = cell.request.metadata.id or cell.request.metadata.name
        title = f"{name} · {cell.cell_key}" if cell.cell_key else name
        if cell.error is not None:
            text.append(f"! {title}\n", style=f"bold {_WARN}")
            text.append(f"  {cell.error}\n\n", style=_DIM)
            continue
        text.append(f"✗ {title}\n", style=f"bold {_DRIFT}")
        for field in cell.drifts:
            text.append("  ▌ ", style=_DRIFT)
            text.append(f"{field.path}  ", style=_TEXT_HI)
            text.append(f"{field.detail}\n", style=_DIM)
        text.append("\n")
    return text


def _diff_empty(pair: tuple[Environment, Environment] | None) -> Text:
    text = Text()
    if pair is None:
        text.append("No diff pair configured.\n\n", style=f"bold {_WARN}")
        text.append("Add one to the project manifest:\n\n", style=_DIM)
        text.append(
            "  environments:\n    diffPairs:\n      - name: local-vs-prod\n"
            "        baseline: local\n        candidate: prod",
            style=_TEXT,
        )
        return text
    baseline, candidate = pair
    ready = f"Ready to diff {baseline.metadata.name} ⇄ {candidate.metadata.name}.\n\n"
    text.append(ready, style=_TEXT_HI)
    text.append("Press ", style=_DIM)
    text.append("x", style=f"bold {_ACCENT}")
    text.append(" to replay every request against both and compare.\n\n", style=_DIM)
    text.append("▏", style=_SAME)
    text.append(" identical    ", style=_DIM)
    text.append("▌", style=_DRIFT)
    text.append(" drift    ", style=_DIM)
    text.append("╎", style=_SKIP)
    text.append(" not compared", style=_DIM)
    return text


def _report_render(report: RunReport) -> Group:
    header = Text()
    verdict = "PASS\n" if report.passed else "FAIL\n"
    header.append(f"gate {verdict}", style=f"bold {_SAME if report.passed else _DRIFT}")
    header.append(
        f"{report.same} same · {report.drift} drift · {report.errors} error"
        f" · {report.skipped} fields skipped\n",
        style=_TEXT,
    )
    table = _table()
    table.add_column("", width=2)
    table.add_column("REQUEST", style=_TEXT_HI, no_wrap=True)
    table.add_column("CASE", style=_AXIS)
    table.add_column("STATE", justify="right")
    glyphs = {"same": ("✓", _SAME), "drift": ("✗", _DRIFT), "error": ("!", _WARN)}
    for cell in report.cells:
        glyph, colour = glyphs.get(cell.state, ("○", _DIM))
        table.add_row(
            Text(glyph, style=colour),
            Text(cell.request_id, style=_TEXT_HI),
            Text(cell.cell_key or "—", style=_AXIS),
            Text(cell.state, style=colour),
        )
    return Group(header, table)


def _settings_render(project: LoadedProject, environment: Environment | None) -> Text:
    manifest = project.project
    environments = [obj for obj in project.objects.values() if isinstance(obj, Environment)]
    text = Text()
    text.append("PROJECT\n", style=_LABEL)
    text.append("  name        ", style=_DIM)
    text.append(f"{manifest.metadata.name if manifest else '—'}\n", style=_TEXT_HI)
    text.append("  root        ", style=_DIM)
    text.append(f"{project.root}\n", style=_TEXT)
    text.append("  objects     ", style=_DIM)
    text.append(f"{len(project.objects)}\n", style=_TEXT)

    text.append("\nACTIVE ENVIRONMENT\n", style=_LABEL)
    text.append("  default     ", style=_DIM)
    text.append(f"{environment.metadata.name if environment else '—'}\n", style=_ACCENT)
    if environment is not None:
        text.append("  baseUrl     ", style=_DIM)
        text.append(f"{environment.spec.base_url}\n", style=_TEXT)

    text.append(f"\nENVIRONMENTS  {len(environments)}\n", style=_LABEL)
    for env in environments:
        remote = _is_remote(env)
        text.append(f"  ● {env.metadata.name:<12}", style=_HEALTH_COLOR[Health.UNKNOWN])
        text.append(f"{env.spec.base_url}", style=_DIM)
        text.append("  live\n" if remote else "  local\n", style=_DANGER if remote else _DIM)

    run_config = manifest.spec.run if manifest else None
    if run_config:
        text.append("\nRUN\n", style=_LABEL)
        text.append(f"  {json.dumps(run_config, ensure_ascii=False)}\n", style=_TEXT)

    text.append("\nENGINE\n", style=_LABEL)
    text.append("  core stays free of httpx and the interfaces — enforced in CI.\n", style=_DIM)
    return text
