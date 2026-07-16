"""The comparo terminal UI.

Built to the comparo-ink design: a top nav bar of screen tabs, a full foldable
project tree on the Explorer, and rich per-object detail (the resolved outbound
request with a syntax-highlighted body, or the config of any other object). The
Diff screen carries the signature tri-state gutter. The core never depends on
this module.
"""

import asyncio
import json
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar
from typing import Literal
from typing import cast
from uuid import uuid4

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
from textual.worker import Worker

from comparo.adapters.reporters import REPORTERS
from comparo.core.assertions import AssertionResult
from comparo.core.assertions import passed as assertions_passed
from comparo.core.checks import Check
from comparo.core.checks import passed as checks_passed
from comparo.core.checks import run_checks
from comparo.core.compare import CellDiff
from comparo.core.compare import diff_run
from comparo.core.compare import profile_for
from comparo.core.curl import to_curl
from comparo.core.diagnostics import Diagnostic
from comparo.core.diagnostics import LoadError
from comparo.core.diff import FieldDiff
from comparo.core.execute import Execution
from comparo.core.execute import execute_request
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionResult
from comparo.core.execution import run_execution
from comparo.core.export import RunEntry
from comparo.core.export import export_run
from comparo.core.health import Health
from comparo.core.health import HealthReport
from comparo.core.health import check_health
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import case_key
from comparo.core.matrix import expand
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
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
from comparo.core.triage import TriageError
from comparo.core.triage import profile_path
from comparo.core.triage import silence
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

# Footer key hints, ordered nav → context actions → meta, ending in help + quit.
_EXPLORER_KEYS = (
    ("↑↓", "move"),
    ("space", "fold"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)
_ENV_KEYS = (
    ("↑↓", "move"),
    ("enter", "default"),
    ("h", "health"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)
_EXEC_KEYS = (
    ("↑↓", "move"),
    ("enter", "launch"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)
_RESOLVE_KEYS = (
    ("↑↓", "move"),
    ("r", "raw/resolved"),
    ("p", "curl"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)
_PREPARE_KEYS = (
    ("↑↓", "move"),
    ("space", "fold"),
    ("enter", "select"),
    ("/", "filter"),
    ("m", "matrix"),
    ("e", "env"),
    ("x", "run"),
    ("?", "help"),
    ("q", "quit"),
)
_RUNNING_KEYS = (
    ("↑↓", "move"),
    ("enter", "open"),
    ("/", "filter"),
    ("f", "failures"),
    ("bksp", "back"),
    ("a", "abort"),
    ("?", "help"),
    ("q", "quit"),
)
_RUNNING_DONE_KEYS = (
    ("↑↓", "move"),
    ("enter", "open"),
    ("/", "filter"),
    ("f", "failures"),
    ("bksp", "back"),
    ("s", "save"),
    ("?", "help"),
    ("q", "quit"),
)
_DIFF_KEYS = (
    ("↑↓", "fields"),
    ("b/c", "baseline / candidate"),
    ("x", "run diff"),
    ("r", "fields/rules"),
    ("v", "unified/side"),
    ("i", "ignore field"),
    ("?", "help"),
    ("q", "quit"),
)
_REPORT_KEYS = (
    ("j/s/m/o", "export"),
    ("enter", "write all"),
    ("?", "help"),
    ("q", "quit"),
)
_SETTINGS_KEYS = (
    ("↑↓", "sections"),
    ("?", "help"),
    ("q", "quit"),
)
_ERROR_KEYS = (
    ("r", "re-check"),
    ("?", "help"),
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
        ("↑ ↓", "move through rows / the detail tree"),
        ("space", "PREPARE — fold a request to show its cases"),
        ("enter", "PREPARE select · RUNNING drill into the next split"),
        ("m", "PREPARE — choose matrix values (applies to every request)"),
        ("x", "run the selected cells against the current environment"),
        ("/", "filter by request or case name (shown on the panel)"),
        ("f", "RUNNING — filter the tables to failures only"),
        ("bksp", "RUNNING — collapse a split (or return to PREPARE)"),
        ("z", "RUNNING — maximize the detail panel"),
        ("a", "RUNNING — abort the run and return to PREPARE"),
        ("s", "RUNNING — save the finished run's results (secrets masked)"),
    ),
    "diff": (
        ("x", "replay every request against the baseline ⇄ candidate pair"),
        ("↑ ↓", "move through the drifted fields (grouped across cells)"),
        ("i", "silence the selected field — writes an ignore rule to its DiffProfile"),
    ),
    "report": (
        ("j", "export JUnit XML"),
        ("s", "export SARIF"),
        ("m", "export Markdown (GitHub step summary)"),
        ("o", "export JSON"),
        ("enter", "write every report format"),
    ),
    "settings": (
        ("↑ ↓", "move between config sections"),
        ("(read-only)", "the effective project configuration"),
    ),
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
        """Enter sets an environment as default, or launches an ExecutionProfile."""
        data = event.node.data
        if isinstance(data, Environment):
            cast("ComparoApp", self.app).set_default_environment(data)
        elif isinstance(data, ExecutionProfile):
            cast("ComparoApp", self.app).launch_execution(data)

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
        elif isinstance(obj, ExecutionProfile):
            keys = _EXEC_KEYS
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
    """The Run screen: a PREPARE state to pick work, a RUNNING state to watch it.

    The two states use different layouts on purpose. PREPARE is a calm checklist
    of requests (``space`` toggles, ``m`` picks matrix cases, ``x`` runs).
    RUNNING is a progress bar over three Miller columns — requests, the selected
    request's variants, and a full per-cell report — that stream live. ``a``
    aborts back to PREPARE; ``s`` saves masked results once a run finishes.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("e", "pick_env", "environment"),
        Binding("escape", "back", "back"),
        Binding("backspace", "back", "back"),
        Binding("f", "filter", "failures"),
        Binding("z", "zoom", "maximize"),
        Binding("a", "abort", "abort"),
        Binding("s", "save", "save"),
    ]

    def action_pick_env(self) -> None:
        """Choose the environment this run executes against."""
        environments = _environments(self.project)
        if not environments:
            self.app.notify("This project has no environments", severity="warning")
            return
        self.app.push_screen(
            EnvPickerModal(environments, "RUN ENVIRONMENT"),
            lambda env: self._set_run_env(env) if env is not None else None,
        )

    def _set_run_env(self, env: Environment) -> None:
        cast("ComparoApp", self.app).set_default_environment(env)
        self.app.notify(f"Runs will execute against {env.metadata.name}", title="Environment")

    def __init__(self, project: LoadedProject) -> None:
        """Build the run view.

        Args:
            project: The project whose requests are executed.
        """
        super().__init__(id="run-view", classes="view")
        self.project = project
        self._selected: set[tuple[str, str]] = set()
        self._disabled_values: set[tuple[str, int]] = set()
        self._state: dict[tuple[str, str], str] = {}
        self._exec: dict[tuple[str, str], Execution] = {}
        self._checks: dict[tuple[str, str], list[Check]] = {}
        self._prep_nodes: dict[tuple[str, str], TreeNode[object]] = {}
        self._prep_branches: dict[str, TreeNode[object]] = {}
        self._view = "requests"
        self._run_id: str | None = None
        self._max = False
        self._failures_only = False
        self.filter_query = ""
        self._focus: Request | None = None
        self._focus_cell: MatrixCell | None = None
        self._worker: Worker[None] | None = None
        self._done = False

    def compose(self) -> ComposeResult:
        """Yield the two states behind a switcher."""
        with ContentSwitcher(initial="prepare", id="run-mode"):
            with Vertical(id="prepare"), Vertical(id="prepare-panel", classes="panel hero"):
                yield Static(id="prepare-head")
                yield Tree("requests", id="prepare-tree")
                yield Static(id="prepare-cta")
            with Vertical(id="running"):
                yield Static(id="run-progress")
                with Horizontal(id="run-columns", classes="only-r"):
                    with Vertical(id="col-requests", classes="panel"):
                        yield DataTable(id="req-table", cursor_type="row")
                    with Vertical(id="col-variants", classes="panel"):
                        yield DataTable(id="var-table", cursor_type="row")
                    with Vertical(id="col-details", classes="panel"):
                        yield Tree("detail", id="detail-tree")

    def on_mount(self) -> None:
        """Select everything and build the prepare checklist."""
        for request in _requests(self.project):
            for cell in expand(self.project, request):
                self._selected.add(_run_key(request, cell))
        tree: Tree[object] = self.query_one("#prepare-tree", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        self._build_prepare()

    def refresh_screen(self) -> None:
        """Re-title and focus the primary widget of the current state."""
        if self.query_one("#run-mode", ContentSwitcher).current == "prepare":
            self._title_prepare()
            self.query_one("#prepare-tree", Tree).focus()
        else:
            self._render_progress()
            self._layout()
        self.update_footer()

    def update_footer(self) -> None:
        """Show the footer keys for the current run state."""
        if cast("ComparoApp", self.app).query_one(NavBar).active != "run":
            return
        keys: tuple[tuple[str, str], ...]
        if self.query_one("#run-mode", ContentSwitcher).current == "prepare":
            keys = _PREPARE_KEYS
            cells = len(self._plan())
            context = f"{cells} case{'' if cells == 1 else 's'} will run"
        else:
            keys = _RUNNING_DONE_KEYS if self._done else _RUNNING_KEYS
            context = f"run {self._run_id}" if self._run_id else ""
        self.app.query_one(StatusBar).show(keys, context)

    # ── PREPARE ──────────────────────────────────────────────────────────────
    def _build_prepare(self) -> None:
        tree: Tree[object] = self.query_one("#prepare-tree", Tree)
        tree.clear()
        self._prep_nodes = {}
        self._prep_branches = {}
        for request in _requests(self.project):
            if not self._matches_text(request.metadata.name):
                continue
            cells = expand(self.project, request)
            if len(cells) == 1:
                node = tree.root.add_leaf(
                    self._prep_label(request, cells[0]), data=(request, cells[0])
                )
                self._prep_nodes[_run_key(request, cells[0])] = node
            else:
                branch = tree.root.add(
                    self._prep_request_label(request), data=(request, None), expand=False
                )
                self._prep_branches[request.metadata.id or request.metadata.name] = branch
                for cell in cells:
                    leaf = branch.add_leaf(self._prep_label(request, cell), data=(request, cell))
                    self._prep_nodes[_run_key(request, cell)] = leaf
        self._title_prepare()

    def on_tree_node_selected(self, event: Tree.NodeSelected[object]) -> None:
        """Toggle the node in or out of the run on Enter."""
        request, cell = _pair(event.node)
        if request is None:
            return
        if cell is None:  # a request branch — toggle all its runnable cells
            keys = {
                _run_key(request, c) for c in expand(self.project, request) if self._cell_enabled(c)
            }
            if keys <= self._selected:
                self._selected -= keys
            else:
                self._selected |= keys
        else:
            self._selected ^= {_run_key(request, cell)}
        self._relabel_prepare(request)
        self._title_prepare()

    def open_case_picker(self) -> None:
        """Open the global matrix-value picker (applies across every request)."""
        matrices = [obj for obj in self.project.objects.values() if isinstance(obj, Matrix)]
        if not matrices:
            self.app.notify("This project has no matrices", severity="information")
            return
        self.app.push_screen(
            GlobalMatrixModal(matrices, self._disabled_values),
            self._apply_matrix_values,
        )

    def _apply_matrix_values(self, disabled: set[tuple[str, int]] | None) -> None:
        if disabled is None:
            return
        self._disabled_values = disabled
        self._build_prepare()
        self._title_prepare()

    def _relabel_prepare(self, request: Request) -> None:
        for cell in expand(self.project, request):
            node = self._prep_nodes.get(_run_key(request, cell))
            if node is not None:
                node.set_label(self._prep_label(request, cell))
        branch = self._prep_branches.get(request.metadata.id or request.metadata.name)
        if branch is not None:
            branch.set_label(self._prep_request_label(request))

    def _prep_label(self, request: Request, cell: MatrixCell) -> Text:
        key = _run_key(request, cell)
        row = Text()
        if not self._cell_enabled(cell):  # turned off globally by the matrix picker
            row.append("✕ ", style=_DIM)
            row.append(cell.key or request.metadata.name, style=_DIM)
            row.append("  matrix off", style=_WARN)
        elif key in self._selected:
            row.append("◉ ", style=_ACCENT)
            row.append(cell.key or request.metadata.name, style=_TEXT)
        else:
            row.append("○ ", style=_DIM)
            row.append(cell.key or request.metadata.name, style=_DIM)
        return row

    def _prep_request_label(self, request: Request) -> Text:
        cells = expand(self.project, request)
        will_run = len(self._plan_cells(request))
        row = Text()
        icon = "◉ " if will_run == len(cells) else "◐ " if will_run else "○ "
        row.append(icon, style=_ACCENT if will_run else _DIM)
        row.append(request.metadata.name, style=f"bold {_TEXT_HI}" if will_run else _DIM)
        method = request.spec.request.method
        row.append(f"  {method}", style=_METHOD.get(method, _DIM))
        row.append(f"  {will_run}/{len(cells)} will run", style=_AXIS if will_run else _DIM)
        return row

    def _title_prepare(self) -> None:
        environment = _app_env(self)
        env = environment.metadata.name if environment else "no environment"
        panel = self.query_one("#prepare-panel")
        panel.border_title = "PREPARE"
        filt = f' · filter "{self.filter_query}"' if self.filter_query else ""
        panel.border_subtitle = f"space select · m matrix · x run{filt}"
        head = Text()
        head.append("environment   ", style=_LABEL)
        head.append(env, style=_ACCENT if environment else _DIM)
        self.query_one("#prepare-head", Static).update(head)
        cells = len(self._plan())
        requests = len(self._plan_requests())
        cta = Text()
        cta.append("▶ ", style=f"bold {_ACCENT}")
        cta.append(f"{cells} case{'' if cells == 1 else 's'} will run", style=f"bold {_TEXT_HI}")
        cta.append(f" across {requests} request{'' if requests == 1 else 's'}", style=_TEXT)
        cta.append("   press ", style=_DIM)
        cta.append("x", style=f"bold {_ACCENT}")
        cta.append(" to run", style=_DIM)
        self.query_one("#prepare-cta", Static).update(cta)
        self.update_footer()

    # ── RUN LIFECYCLE ────────────────────────────────────────────────────────
    def execute(self) -> None:
        """Start a run of the selected cells; switch to the RUNNING state."""
        environment = _app_env(self)
        if environment is None:
            self.app.notify("Pick an environment in the Explorer first", severity="warning")
            return
        plan = self._plan()
        if not plan:
            self.app.notify("Nothing selected to run", severity="warning")
            return
        self._done = False
        self._run_id = uuid4().hex[:6]
        self._focus = None
        self._focus_cell = None
        self._view = "requests"
        for request, cell in plan:
            key = _run_key(request, cell)
            self._state[key] = "pending"
            self._exec.pop(key, None)
            self._checks.pop(key, None)
        self.query_one("#run-mode", ContentSwitcher).current = "running"
        self._populate_requests()
        self._layout()
        self._render_progress()
        self._worker = self.run_worker(self._run(environment, plan), exclusive=True, group="run")

    async def _run(self, environment: Environment, plan: list[tuple[Request, MatrixCell]]) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        client = HttpxClient()
        limit = asyncio.Semaphore(4)

        async def one(request: Request, cell: MatrixCell) -> None:
            key = _run_key(request, cell)
            self._state[key] = "running"
            self._on_progress(request, cell)
            async with limit:
                execution = await execute_request(self.project, environment, request, client, cell)
            self._exec[key] = execution
            self._checks[key] = run_checks(self.project, request, execution)
            self._state[key] = "ok" if execution.ok else "failed"
            self._on_progress(request, cell)

        try:
            await asyncio.gather(*(one(request, cell) for request, cell in plan))
        finally:
            await client.aclose()
        self._done = True
        self._render_progress()
        ok = sum(1 for request, cell in plan if self._cell_ok(request, cell))
        self.app.notify(
            f"{ok}/{len(plan)} cells passed — press s to save",
            title="Run complete",
            severity="information" if ok == len(plan) else "warning",
        )

    def action_abort(self) -> None:
        """Cancel a running run and return to PREPARE."""
        if self.query_one("#run-mode", ContentSwitcher).current != "running" or self._done:
            return
        if self._worker is not None:
            self._worker.cancel()
        for key, state in list(self._state.items()):
            if state in ("running", "pending"):
                self._state[key] = "pending"
        self.query_one("#run-mode", ContentSwitcher).current = "prepare"
        self.query_one("#prepare-tree", Tree).focus()
        self._title_prepare()
        self.app.notify("Run aborted", severity="warning")

    def action_save(self) -> None:
        """Save the completed run's results to a masked JSON file."""
        mode = self.query_one("#run-mode", ContentSwitcher).current
        if mode != "running" or not self._done or self._run_id is None:
            self.app.notify("Finish a run before saving", severity="information")
            return
        environment = _app_env(self)
        if environment is None:
            return
        entries = [
            RunEntry(request, cell, self._exec[key], self._checks.get(key, []))
            for request, cell in self._plan()
            if (key := _run_key(request, cell)) in self._exec
        ]
        try:
            path = _save_run(self.project, environment, self._run_id, entries)
        except OSError as error:
            self.app.notify(str(error), title="Could not save", severity="error")
            return
        self.app.notify(f"Saved run {self._run_id} to {path}", title="Saved")

    def action_back(self) -> None:
        """Collapse one split, or from the requests column back to PREPARE."""
        if self.query_one("#run-mode", ContentSwitcher).current != "running":
            return
        if self._view == "details":
            self._max = False
            self._view = "variants" if self._has_variants() else "requests"
            self._layout()
        elif self._view == "variants":
            self._view = "requests"
            self._layout()
        else:
            self.query_one("#run-mode", ContentSwitcher).current = "prepare"
            self.query_one("#prepare-tree", Tree).focus()

    # ── RUNNING COLUMNS ──────────────────────────────────────────────────────
    def _has_variants(self) -> bool:
        return self._focus is not None and len(self._plan_cells(self._focus)) > 1

    def _layout(self) -> None:
        if self._view == "details" and self._max:
            cls, focus = "max", "#detail-tree"
        elif self._view == "requests":
            cls, focus = "only-r", "#req-table"
        elif self._view == "variants":
            cls, focus = "r-v", "#var-table"
        elif self._view == "details":
            cls, focus = (
                ("r-v-d", "#detail-tree") if self._has_variants() else ("r-d", "#detail-tree")
            )
        else:
            cls, focus = "only-r", "#req-table"
        self.query_one("#run-columns").set_classes(cls)
        self.query_one(focus).focus()
        self.update_footer()

    def action_zoom(self) -> None:
        """Maximize (or restore) the detail panel."""
        if self.query_one("#run-mode", ContentSwitcher).current != "running":
            return
        if self._view != "details":
            return
        self._max = not self._max
        self._layout()

    def _open_request(self, request: Request) -> None:
        cells = self._plan_cells(request)
        if not cells:
            return
        self._focus = request
        if len(cells) == 1:
            self._focus_cell = cells[0]
            self._populate_details(request, cells[0])
            self._view = "details"
        else:
            self._populate_variants(request)
            self._view = "variants"
        self._layout()

    def _open_variant(self, cell: MatrixCell) -> None:
        if self._focus is None:
            return
        self._focus_cell = cell
        self._populate_details(self._focus, cell)
        self._view = "details"
        self._layout()

    def action_filter(self) -> None:
        """Toggle the failures-only filter on the run tables."""
        if self.query_one("#run-mode", ContentSwitcher).current != "running":
            return
        self._failures_only = not self._failures_only
        self._populate_requests()
        if self._focus is not None and self._view in ("variants", "details"):
            self._populate_variants(self._focus)
        self.app.notify(
            "Showing failures only" if self._failures_only else "Showing all",
            severity="information",
        )

    def open_text_filter(self) -> None:
        """Open the text filter for the current table (or the prepare tree)."""
        prepare = self.query_one("#run-mode", ContentSwitcher).current == "prepare"
        placeholder = "request name…" if prepare else "request or case…"
        self.app.push_screen(FilterModal(self, title="FILTER", placeholder=placeholder))

    def apply_filter(self, query: str) -> int:
        """Apply the live text filter; return how many rows survive.

        Args:
            query: The case-insensitive substring to match.

        Returns:
            The number of matching rows for the current state.
        """
        self.filter_query = query.strip().lower()
        if self.query_one("#run-mode", ContentSwitcher).current == "prepare":
            self._build_prepare()
            self._title_prepare()
            return len(self.query_one("#prepare-tree", Tree).root.children)
        self._populate_requests()
        if self._focus is not None and self._view in ("variants", "details"):
            self._populate_variants(self._focus)
        return sum(len(self._shown_cells(r)) for r in self._shown_requests())

    def _matches_text(self, text: str) -> bool:
        return not self.filter_query or self.filter_query in text.lower()

    def _shown_requests(self) -> list[Request]:
        requests = self._plan_requests()
        if self._failures_only:
            requests = [r for r in requests if self._request_status(r) in ("failed", "partial")]
        if self.filter_query:
            requests = [r for r in requests if self._matches_text(r.metadata.name)]
        return requests

    def _shown_cells(self, request: Request) -> list[MatrixCell]:
        cells = self._plan_cells(request)
        if self._failures_only:
            cells = [
                c
                for c in cells
                if self._state.get(_run_key(request, c)) in ("ok", "failed")
                and not self._cell_ok(request, c)
            ]
        if self.filter_query:
            cells = [c for c in cells if self._matches_text(c.key)]
        return cells

    def _filter_suffix(self) -> str:
        parts = []
        if self._failures_only:
            parts.append("failures")
        if self.filter_query:
            parts.append(f'"{self.filter_query}"')
        return f"  [{_WARN}]· {' · '.join(parts)}[/]" if parts else ""

    def _populate_requests(self) -> None:
        table = self.query_one("#req-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="state", width=3)
        table.add_column("REQUEST", key="name")
        table.add_column("VARIANTS", key="strip")
        table.add_column("P50", key="p50", width=8)
        for request in self._shown_requests():
            table.add_row(
                *self._request_row(request), key=request.metadata.id or request.metadata.name
            )
        self.query_one("#col-requests").border_title = Text.from_markup(
            f"REQUESTS{self._filter_suffix()}"
        )

    def _populate_variants(self, request: Request) -> None:
        self._focus = request
        wrap = self.query_one("#col-variants")
        wrap.border_title = Text.from_markup(
            f"VARIANTS [{_DIM}]·[/] {request.metadata.name}{self._filter_suffix()}"
        )
        table = self.query_one("#var-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="st", width=3)
        table.add_column("CASE", key="case")
        table.add_column("HTTP", key="http", width=6)
        table.add_column("TIME", key="time", width=8)
        table.add_column("RESULT", key="result")
        for cell in self._shown_cells(request):
            table.add_row(*self._variant_row(request, cell), key=cell.key)

    def _populate_details(self, request: Request, cell: MatrixCell) -> None:
        self._focus_cell = cell
        wrap = self.query_one("#col-details")
        crumb = cell.key or request.metadata.name
        wrap.border_title = Text.from_markup(f"DETAIL [{_DIM}]·[/] {crumb}")
        wrap.border_subtitle = "↑↓ navigate · z maximize"
        key = _run_key(request, cell)
        tree: Tree[object] = self.query_one("#detail-tree", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        _build_report_tree(
            tree,
            self.project,
            _app_env(self),
            request,
            cell,
            self._exec.get(key),
            self._state.get(key, "pending"),
            self._checks.get(key, []),
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Preview the child column as the cursor moves."""
        key = event.row_key.value
        if key is None:
            return
        if event.data_table.id == "req-table":
            request = self._by_id(key)
            if request is not None and self._view == "details":
                self._focus = request
                cells = self._plan_cells(request)
                if cells:
                    self._populate_details(request, cells[0])
        elif event.data_table.id == "var-table" and self._view == "details" and self._focus:
            cell = self._cell_by_key(self._focus, key)
            if cell is not None:
                self._populate_details(self._focus, cell)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Drill into the next split on Enter."""
        key = event.row_key.value
        if key is None:
            return
        if event.data_table.id == "req-table":
            request = self._by_id(key)
            if request is not None:
                self._open_request(request)
        elif event.data_table.id == "var-table" and self._focus is not None:
            cell = self._cell_by_key(self._focus, key)
            if cell is not None:
                self._open_variant(cell)

    def _on_progress(self, request: Request, cell: MatrixCell) -> None:
        request_id = request.metadata.id or request.metadata.name
        table = self.query_one("#req-table", DataTable)
        if request_id in {row.value for row in table.rows}:
            request_columns = ("state", "name", "strip", "p50")
            for column, value in zip(request_columns, self._request_row(request), strict=True):
                table.update_cell(request_id, column, value)
        if self._focus is request and self._view in ("variants", "details"):
            var_table = self.query_one("#var-table", DataTable)
            if cell.key in {row.value for row in var_table.rows}:
                variant_columns = ("st", "case", "http", "time", "result")
                for column, value in zip(
                    variant_columns, self._variant_row(request, cell), strict=True
                ):
                    var_table.update_cell(cell.key, column, value)
        if (
            self._view == "details"
            and self._focus is request
            and self._focus_cell is not None
            and self._focus_cell.key == cell.key
        ):
            self._populate_details(request, cell)
        self._render_progress()

    def _render_progress(self) -> None:
        environment = _app_env(self)
        self.query_one("#col-requests").border_title = Text.from_markup(
            f"REQUESTS{self._filter_suffix()}"
        )
        plan = self._plan()
        total = len(plan)
        done = sum(1 for r, c in plan if self._state.get(_run_key(r, c)) in ("ok", "failed"))
        ok = sum(1 for r, c in plan if self._cell_ok(r, c))
        failed = done - ok
        text = Text()
        text.append("run ", style=_DIM)
        text.append(self._run_id or "—", style=f"bold {_ACCENT}")
        text.append(f"   {environment.metadata.name if environment else '—'}", style=_TEXT_HI)
        text.append("   ", style=_DIM)
        width = 24
        filled = round(width * done / total) if total else 0
        fill_tint = _SAME if self._done and not failed else _WARN if self._done else _ACCENT
        text.append("━" * filled, style=fill_tint)
        text.append("━" * (width - filled), style=_DIM)
        text.append(f"   {done}/{total}", style=f"bold {_TEXT_HI}")
        text.append("  ·  ", style=_DIM)
        text.append(f"{ok} ✓", style=_SAME)
        text.append("  ", style=_DIM)
        text.append(f"{failed} ✗", style=_DRIFT if failed else _DIM)
        if self._done:
            text.append("    press ", style=_DIM)
            text.append("s", style=f"bold {_ACCENT}")
            text.append(" to save", style=_DIM)
        self.query_one("#run-progress", Static).update(text)
        self.update_footer()

    # ── rows ─────────────────────────────────────────────────────────────────
    def _request_row(self, request: Request) -> list[Text]:
        glyph, colour = _STATUS[self._request_status(request)]
        latencies: list[float] = []
        for cell in self._plan_cells(request):
            execution = self._exec.get(_run_key(request, cell))
            if execution is not None and execution.response is not None:
                latencies.append(execution.response.elapsed_ms)
        p50 = f"{sorted(latencies)[len(latencies) // 2]:.0f}ms" if latencies else "—"
        return [
            Text(glyph, style=colour),
            Text(request.metadata.name, style=_TEXT_HI),
            self._strip(request),
            Text(p50, style=_DIM),
        ]

    def _variant_row(self, request: Request, cell: MatrixCell) -> list[Text]:
        key = _run_key(request, cell)
        state = self._state.get(key, "pending")
        glyph, colour = _RUN_GLYPH[state]
        execution = self._exec.get(key)
        response = execution.response if execution else None
        code = str(response.status) if response else "—"
        time = f"{response.elapsed_ms:.0f}ms" if response else "—"
        return [
            Text(glyph, style=colour),
            Text(cell.key or "base", style=_AXIS),
            Text(code, style=_SAME if code.startswith("2") else _DIM if code == "—" else _WARN),
            Text(time, style=_DIM),
            self._assert_cell(request, cell),
        ]

    def _assert_cell(self, request: Request, cell: MatrixCell) -> Text:
        key = _run_key(request, cell)
        state = self._state.get(key, "pending")
        if state in ("pending", "running"):
            return Text("—", style=_DIM)
        checks = self._checks.get(key, [])
        failed = [check.name for check in checks if not check.ok]
        if failed:
            return Text("✗ " + ", ".join(failed), style=_DRIFT)
        passed = sum(1 for check in checks if check.ok)
        return Text(f"✓ {passed} passed", style=_SAME)

    def _strip(self, request: Request) -> Text:
        strip = Text()
        for cell in self._plan_cells(request):
            state = self._state.get(_run_key(request, cell), "pending")
            if state == "ok":
                mark, tint = ("✓", _SAME) if self._cell_ok(request, cell) else ("✗", _DRIFT)
            elif state == "failed":
                mark, tint = "✗", _DRIFT
            elif state == "running":
                mark, tint = "◐", _WARN
            else:
                mark, tint = "·", _DIM
            strip.append(mark, style=tint)
        return strip

    # ── model ────────────────────────────────────────────────────────────────
    def _plan(self) -> list[tuple[Request, MatrixCell]]:
        return [
            (request, cell)
            for request in _requests(self.project)
            for cell in self._plan_cells(request)
        ]

    def _plan_requests(self) -> list[Request]:
        return [r for r in _requests(self.project) if self._plan_cells(r)]

    def _plan_cells(self, request: Request) -> list[MatrixCell]:
        """The cells that will actually run: selected and not turned off by matrix."""
        return [
            c
            for c in expand(self.project, request)
            if _run_key(request, c) in self._selected and self._cell_enabled(c)
        ]

    def _cell_enabled(self, cell: MatrixCell) -> bool:
        for injection in cell.injections:
            matrix = self._matrix_for(injection.target)
            if matrix is None:
                continue
            matrix_id = matrix.metadata.id or matrix.metadata.name
            try:
                index = matrix.spec.values.index(injection.case)
            except ValueError:
                continue
            if (matrix_id, index) in self._disabled_values:
                return False
        return True

    def _matrix_for(self, target: str) -> Matrix | None:
        for obj in self.project.objects.values():
            if isinstance(obj, Matrix) and obj.spec.target == target:
                return obj
        return None

    def _cell_ok(self, request: Request, cell: MatrixCell) -> bool:
        key = _run_key(request, cell)
        return self._state.get(key) == "ok" and checks_passed(self._checks.get(key, []))

    def _request_status(self, request: Request) -> str:
        cells = self._plan_cells(request)
        if not cells:
            return "skipped"
        states = [self._state.get(_run_key(request, c), "pending") for c in cells]
        if any(s == "running" for s in states):
            return "running"
        if all(s == "pending" for s in states):
            return "pending"
        if any(s == "pending" for s in states):
            return "running"
        passing = sum(1 for c in cells if self._cell_ok(request, c))
        if passing == len(cells):
            return "success"
        return "failed" if passing == 0 else "partial"

    def _by_id(self, request_id: str) -> Request | None:
        obj = self.project.objects.get(request_id)
        if isinstance(obj, Request):
            return obj
        return next((r for r in _requests(self.project) if r.metadata.name == request_id), None)

    def _cell_by_key(self, request: Request, cell_key: str) -> MatrixCell | None:
        return next((c for c in expand(self.project, request) if c.key == cell_key), None)


class DiffView(Vertical):
    """The signature diff: replay a pair, group drift by field, silence to config.

    ``b`` / ``c`` pick the baseline / candidate environment inline; ``x`` replays
    every request against both. Drifts collapse to one row per field (a field that
    drifts on three cells is one bug, not three); ``r`` toggles that index between
    grouped-by-field and broken-rules. Selecting a field shows a git-style body
    diff — ``v`` flips it between unified and side-by-side. ``i`` silences a field
    by writing an ignore rule into the request's committed DiffProfile.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("b", "pick_baseline", "baseline"),
        Binding("c", "pick_candidate", "candidate"),
        Binding("r", "toggle_index", "fields/rules"),
        Binding("v", "toggle_view", "unified/side-by-side"),
        Binding("i", "silence", "ignore field"),
    ]

    def __init__(self, project: LoadedProject) -> None:
        """Build the diff view.

        Args:
            project: The project whose requests are diffed.
        """
        super().__init__(id="diff-view", classes="view")
        self.project = project
        self._cells: list[CellDiff] = []
        self._pair: tuple[Environment, Environment] | None = None
        self._groups: list[tuple[str, list[tuple[CellDiff, FieldDiff]]]] = []
        self._run_id: str | None = None
        self._done = False
        self._unified = True
        self._index_mode = "fields"

    def action_toggle_index(self) -> None:
        """Flip the drift index between grouped-by-field and broken-rules."""
        self._index_mode = "rules" if self._index_mode == "fields" else "fields"
        self._populate_drift()

    def action_toggle_view(self) -> None:
        """Flip the body diff between unified and side-by-side."""
        self._unified = not self._unified
        group = self._selected_group()
        if group is not None:
            self._show_field(group[0])

    def action_pick_baseline(self) -> None:
        """Choose the baseline environment for the diff."""
        self._pick_env(0, "BASELINE ENVIRONMENT")

    def action_pick_candidate(self) -> None:
        """Choose the candidate environment for the diff."""
        self._pick_env(1, "CANDIDATE ENVIRONMENT")

    def _pick_env(self, index: int, title: str) -> None:
        environments = _environments(self.project)
        if not environments:
            self.app.notify("This project has no environments", severity="warning")
            return
        self.app.push_screen(
            EnvPickerModal(environments, title),
            lambda env: self._set_env(index, env) if env is not None else None,
        )

    def _set_env(self, index: int, env: Environment) -> None:
        base, candidate = self._pair if self._pair is not None else (env, env)
        self._pair = (env, candidate) if index == 0 else (base, env)
        # The previous results no longer describe this pair — clear and re-run.
        self._cells = []
        self._groups = []
        self._done = False
        self._run_id = None
        self._render_progress()
        self._populate_drift()

    def compose(self) -> ComposeResult:
        """Yield the progress line, the drift table, and the comparison panel."""
        yield Static(id="diff-progress")
        with Horizontal(id="diff-cols"):
            with Vertical(id="col-drift", classes="panel"):
                yield DataTable(id="drift-table", cursor_type="row")
            with VerticalScroll(id="col-compare", classes="panel hero"):
                yield Static(id="compare-content")

    def on_mount(self) -> None:
        """Resolve the diff pair and render the ready state."""
        self.refresh_screen()

    def refresh_screen(self) -> None:
        """Re-resolve the pair and re-render."""
        try:
            self._pair = resolve_pair(self.project, None, None, None)
        except EnvironmentSelectionError:
            self._pair = _default_pair(self.project)
        self._render_progress()
        self._populate_drift()
        self.query_one("#drift-table", DataTable).focus()

    def execute(self) -> None:
        """Run the diff across the pair."""
        if self._pair is None:
            self.app.notify("No diffPairs configured in the project manifest", severity="warning")
            return
        self._run_id = uuid4().hex[:6]
        self._done = False
        self._render_progress()
        self.run_worker(self._run(self._pair), exclusive=True, group="diff")

    def action_silence(self) -> None:
        """Ask before writing an ignore rule for the selected field into its DiffProfile.

        The TUI never edits a version-controlled file silently, so this opens a
        confirmation overlay naming the field and the exact file(s) it would
        touch; the write only happens if the user confirms.
        """
        group = self._selected_group()
        if group is None:
            self.app.notify("Select a drifted field to silence", severity="information")
            return
        path, entries = group
        profiles = self._silence_profiles(entries)
        if not profiles:
            self.app.notify("No diff profile to write to", severity="warning")
            return
        self.app.push_screen(
            ConfirmModal(self._silence_prompt(path, profiles), title="SILENCE FIELD"),
            lambda ok: self._write_silence(path, profiles) if ok else None,
        )

    def _silence_profiles(self, entries: list[tuple[CellDiff, FieldDiff]]) -> list[DiffProfile]:
        profiles: dict[str, DiffProfile] = {}
        for cell, _ in entries:
            profile = profile_for(self.project, cell.request)
            if profile is not None and profile.metadata.id is not None:
                profiles.setdefault(profile.metadata.id, profile)
        return list(profiles.values())

    def _silence_prompt(self, path: str, profiles: list[DiffProfile]) -> Text:
        text = Text(justify="left")
        text.append("Write an ignore rule for ", style=_TEXT)
        text.append(path, style=f"bold {_TEXT_HI}")
        text.append("\ninto ", style=_TEXT)
        text.append(
            "this diff profile" if len(profiles) == 1 else "these diff profiles",
            style=_TEXT,
        )
        text.append(":\n\n", style=_TEXT)
        for profile in profiles:
            text.append("  ")
            text.append(profile.metadata.name, style=_TEXT_HI)
            identifier = profile.metadata.id or "?"
            text.append(f"  ({identifier})", style=_DIM)
            file = profile_path(self.project, identifier) if profile.metadata.id else None
            if file is not None:
                try:
                    location = str(file.relative_to(self.project.root))
                except ValueError:
                    location = str(file)
                text.append("\n  → ", style=_DIM)
                text.append(location, style=_ACCENT)
            text.append("\n")
        text.append("\ncomparo never edits your files without asking.", style=_DIM)
        return text

    def _write_silence(self, path: str, profiles: list[DiffProfile]) -> None:
        written: set[str] = set()
        for profile in profiles:
            if profile.metadata.id is None:
                continue
            try:
                file = silence(self.project, profile.metadata.id, path)
            except TriageError as error:
                self.app.notify(str(error), title="Could not silence", severity="error")
                return
            written.add(str(file))
        if not written:
            self.app.notify("No diff profile to write to", severity="warning")
            return
        self.app.notify(
            f"Ignoring {path} — wrote {', '.join(sorted(written))}. Re-run to confirm.",
            title="Silenced",
        )

    async def _run(self, pair: tuple[Environment, Environment]) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        baseline, candidate = pair
        client = HttpxClient()
        candidate_client = HttpxClient()
        try:
            self._cells = await diff_run(
                self.project, baseline, candidate, _requests(self.project), client, candidate_client
            )
        finally:
            await client.aclose()
            await candidate_client.aclose()
        report = build_report(baseline.metadata.name, candidate.metadata.name, self._cells)
        cast("ComparoApp", self.app).last_report = report
        self._done = True
        self._regroup()
        self._render_progress()
        self._populate_drift()
        drift = sum(1 for cell in self._cells if cell.drifted)
        errors = sum(1 for cell in self._cells if cell.error is not None)
        passed = drift == 0 and errors == 0
        self.app.notify(
            f"{drift} drift · {errors} error — gate {'PASS' if passed else 'FAIL'}",
            title="Diff complete",
            severity="information" if passed else "error",
        )

    def _regroup(self) -> None:
        groups: dict[str, list[tuple[CellDiff, FieldDiff]]] = {}
        for cell in self._cells:
            for field in cell.drifts:
                groups.setdefault(field.path, []).append((cell, field))
        self._groups = sorted(groups.items())

    def _populate_drift(self) -> None:
        table = self.query_one("#drift-table", DataTable)
        table.clear(columns=True)
        if not self._groups:
            table.add_column("", key="st", width=3)
            table.add_column("FIELD", key="field")
            self.query_one("#col-drift").border_title = "DRIFT"
            self.query_one("#compare-content", Static).update(_diff_ready(self._cells, self._pair))
            return
        if self._index_mode == "rules":
            self._populate_rules(table)
            subtitle = "broken rules"
        else:
            self._populate_fields(table)
            subtitle = "grouped by field"
        for cell in (c for c in self._cells if c.error is not None):
            table.add_row(
                Text("!", style=_WARN),
                Text(cell.request.metadata.name, style=_WARN),
                Text("", style=_DIM),
                Text("error", style=_WARN),
                key=f"error::{cell.cell_key}::{id(cell)}",
            )
        self.query_one("#col-drift").border_title = Text.from_markup(
            f"DRIFT [{_DIM}]· {subtitle}[/]"
        )
        self._show_field(self._groups[0][0])

    def _populate_fields(self, table: DataTable[Text]) -> None:
        table.add_column("", key="st", width=3)
        table.add_column("FIELD", key="field")
        table.add_column("CELLS", key="cells", width=7)
        table.add_column("MODE", key="mode", width=10)
        for path, entries in self._groups:
            table.add_row(
                Text("✗", style=_DRIFT),
                Text(path, style=_TEXT_HI),
                Text(f"×{len(entries)}", style=_AXIS),
                Text(entries[0][1].mode, style=_MODE.get(entries[0][1].mode, _DIM)),
                key=f"drift::{path}",
            )

    def _populate_rules(self, table: DataTable[Text]) -> None:
        # One row per fired rule: which mode flagged which field, and the change.
        table.add_column("", key="st", width=3)
        table.add_column("RULE", key="mode", width=10)
        table.add_column("FIELD", key="field")
        table.add_column("CHANGE", key="detail")
        for path, entries in self._groups:
            field = entries[0][1]
            table.add_row(
                Text("✗", style=_DRIFT),
                Text(field.mode, style=_MODE.get(field.mode, _DIM)),
                Text(path, style=_TEXT_HI),
                Text(field.detail or "differs", style=_DRIFT),
                key=f"drift::{path}",
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show the field comparison for the highlighted drift."""
        key = event.row_key.value
        if key is not None and key.startswith("drift::"):
            self._show_field(key.removeprefix("drift::"))

    def _selected_group(self) -> tuple[str, list[tuple[CellDiff, FieldDiff]]] | None:
        table = self.query_one("#drift-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if key is None or not key.startswith("drift::"):
            return None
        path = key.removeprefix("drift::")
        return next((group for group in self._groups if group[0] == path), None)

    def _show_field(self, path: str) -> None:
        group = next((g for g in self._groups if g[0] == path), None)
        wrap = self.query_one("#col-compare")
        if group is None:
            wrap.border_title = "COMPARE"
            return
        mode = "unified" if self._unified else "side-by-side"
        wrap.border_title = Text.from_markup(f"COMPARE [{_DIM}]·[/] {path} [{_DIM}]· {mode}[/]")
        self.query_one("#compare-content", Static).update(
            _diff_body_view(group, self._pair, unified=self._unified)
        )

    def _render_progress(self) -> None:
        text = Text()
        if self._pair is None:
            text.append("no diff pair configured", style=_WARN)
            self.query_one("#diff-progress", Static).update(text)
            return
        baseline, candidate = self._pair
        text.append("diff ", style=_DIM)
        if self._run_id:
            text.append(self._run_id, style=f"bold {_ACCENT}")
            text.append("  ", style=_DIM)
        text.append(baseline.metadata.name, style=_TEXT_HI)
        text.append(" ⇄ ", style=_DIM)
        text.append(candidate.metadata.name, style=_TEXT_HI)
        if self._cells:
            same = sum(1 for c in self._cells if not c.drifted and c.error is None)
            drift = sum(1 for c in self._cells if c.drifted)
            errors = sum(1 for c in self._cells if c.error is not None)
            skipped = sum(c.skipped for c in self._cells)
            text.append(f"    {same} same", style=_SAME)
            text.append(" · ", style=_DIM)
            text.append(f"{drift} drift", style=_DRIFT if drift else _DIM)
            text.append(" · ", style=_DIM)
            text.append(f"{errors} error", style=_WARN if errors else _DIM)
            text.append(" · ", style=_DIM)
            text.append(f"{skipped} skipped", style=_SKIP)
        else:
            text.append("    press ", style=_DIM)
            text.append("x", style=f"bold {_ACCENT}")
            text.append(" to run", style=_DIM)
        self.query_one("#diff-progress", Static).update(text)


class ReportView(Vertical):
    """The CI pillar: a verdict-first gate, a breakdown, and exporters.

    Reads the most recent diff run. ``j`` / ``s`` / ``m`` / ``o`` write JUnit,
    SARIF, Markdown, or JSON; ``enter`` writes them all — the same reporters the
    headless ``comparo diff --report`` uses, so the gate matches CI exactly.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "export('junit')", "JUnit"),
        Binding("s", "export('sarif')", "SARIF"),
        Binding("m", "export('markdown')", "Markdown"),
        Binding("o", "export('json')", "JSON"),
        Binding("enter", "export_all", "write all"),
    ]

    def __init__(self, project: LoadedProject) -> None:
        """Build the report view.

        Args:
            project: The project (used to locate the report output directory).
        """
        super().__init__(id="report-view", classes="view")
        self.project = project

    def compose(self) -> ComposeResult:
        """Yield the gate banner, the stat pills, and the breakdown/export columns."""
        yield Static(id="report-gate", classes="panel")
        with Horizontal(id="report-pills"):
            for stat in ("calls", "same", "drift", "error", "skipped"):
                yield Static(id=f"pill-{stat}", classes="pill")
        with Horizontal(id="report-cols"):
            with VerticalScroll(id="report-breakdown", classes="panel hero"):
                yield Static(id="report-breakdown-content")
            with VerticalScroll(id="report-export", classes="panel"):
                yield Static(id="report-export-content")

    def on_mount(self) -> None:
        """Render the last report, if any."""
        self.refresh_screen()

    def refresh_screen(self) -> None:
        """Re-render from the app's last diff report."""
        report = cast("ComparoApp", self.app).last_report
        gate = self.query_one("#report-gate")
        pills = self.query_one("#report-pills")
        cols = self.query_one("#report-cols")
        if report is None:
            gate.remove_class("pass", "fail")
            gate.border_title = "GATE"
            self.query_one("#report-gate", Static).update(
                Text("Run a diff (press x on the Diff screen) to build a report.", style=_DIM)
            )
            pills.display = False
            cols.display = False
            return
        pills.display = True
        cols.display = True
        self._render_gate(report)
        self._render_pills(report)
        self.query_one("#report-breakdown", VerticalScroll).border_title = "BREAKDOWN"
        self.query_one("#report-export", VerticalScroll).border_title = "EXPORT"
        self.query_one("#report-breakdown-content", Static).update(_report_breakdown(report))
        self.query_one("#report-export-content", Static).update(_report_export(self._output()))

    def action_export(self, fmt: str) -> None:
        """Write a single report format."""
        self._write([fmt])

    def action_export_all(self) -> None:
        """Write every report format."""
        self._write(["junit", "sarif", "markdown", "json"])

    def _write(self, formats: list[str]) -> None:
        report = cast("ComparoApp", self.app).last_report
        if report is None:
            self.app.notify("No report yet — run a diff first", severity="information")
            return
        output = self._output()
        try:
            output.mkdir(parents=True, exist_ok=True)
            written = []
            for name in formats:
                reporter = REPORTERS[name]
                (output / reporter.filename).write_text(reporter.render(report), encoding="utf-8")
                written.append(reporter.filename)
        except OSError as error:
            self.app.notify(str(error), title="Export failed", severity="error")
            return
        self.app.notify(f"Wrote {', '.join(written)} to {output}", title="Exported")

    def _output(self) -> Path:
        manifest = self.project.project
        config = manifest.spec.report if manifest else None
        output = config.get("output") if isinstance(config, dict) else None
        return self.project.root / (output if isinstance(output, str) else "reports")

    def _render_gate(self, report: RunReport) -> None:
        gate = self.query_one("#report-gate")
        gate.set_class(report.passed, "pass")
        gate.set_class(not report.passed, "fail")
        gate.border_title = "GATE"
        text = Text()
        if report.passed:
            text.append("✓  gate PASS", style=f"bold {_SAME}")
            text.append("   CI would pass", style=_DIM)
        else:
            text.append("✗  gate FAIL", style=f"bold {_DRIFT}")
            text.append(
                f"   {report.drift} drift · {report.errors} error block the run", style=_DIM
            )
        self.query_one("#report-gate", Static).update(text)

    def _render_pills(self, report: RunReport) -> None:
        stats = [
            ("calls", len(report.cells), _TEXT_HI),
            ("same", report.same, _SAME),
            ("drift", report.drift, _DRIFT if report.drift else _DIM),
            ("error", report.errors, _WARN if report.errors else _DIM),
            ("skipped", report.skipped, _SKIP),
        ]
        for name, value, colour in stats:
            pill = Text()
            pill.append(f"{value}\n", style=f"bold {colour}")
            pill.append(name, style=_DIM)
            self.query_one(f"#pill-{name}", Static).update(pill)


class SettingsView(Horizontal):
    """A navigable, read-only overview of the effective project configuration."""

    SECTIONS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("project", "Project"),
        ("environments", "Environments"),
        ("run", "Run defaults"),
        ("diff", "Diff"),
        ("report", "Report"),
        ("redaction", "Redaction"),
        ("appearance", "Appearance"),
        ("plugins", "Plugins"),
        ("engine", "Engine"),
    )

    def __init__(self, project: LoadedProject) -> None:
        """Build the settings view.

        Args:
            project: The project whose configuration is shown.
        """
        super().__init__(id="settings-view", classes="view")
        self.project = project

    def compose(self) -> ComposeResult:
        """Yield the category list and the detail panel."""
        with Vertical(id="settings-nav", classes="panel"):
            yield OptionList(*(label for _, label in self.SECTIONS), id="settings-list")
        with VerticalScroll(id="settings-detail", classes="panel hero"):
            yield Static(id="settings-content")

    def on_mount(self) -> None:
        """Title the panels and show the first section."""
        self.query_one("#settings-nav").border_title = "SETTINGS"
        self.refresh_screen()

    def refresh_screen(self) -> None:
        """Focus the category list and render the current section."""
        options = self.query_one("#settings-list", OptionList)
        options.focus()
        index = options.highlighted or 0
        self._show(index)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Render the highlighted section."""
        self._show(event.option_index)

    def _show(self, index: int) -> None:
        key, label = self.SECTIONS[index]
        detail = self.query_one("#settings-detail")
        detail.border_title = label.upper()
        detail.border_subtitle = "read-only"
        self.query_one("#settings-content", Static).update(
            _settings_section(self.project, _app_env(self), key)
        )


class FilterModal(ModalScreen[None]):
    """A narrow overlay that live-filters a view (tree or tables) as you type."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(
        self,
        target: "ExplorerView | RunView",
        *,
        title: str = "FILTER",
        placeholder: str = "filter…",
    ) -> None:
        """Build the filter modal.

        Args:
            target: The view to filter; must expose ``filter_query`` and
                ``apply_filter(query) -> int`` (the number of matches).
            title: The dialog title.
            placeholder: The input placeholder text.
        """
        super().__init__()
        self._target = target
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        """Yield the dialog: an input and a live match count."""
        with Vertical(id="filter-dialog", classes="modal"):
            yield Input(placeholder=self._placeholder, id="filter-input")
            yield Static(id="filter-count")

    def on_mount(self) -> None:
        """Title the dialog and seed the input with the current filter."""
        self.query_one("#filter-dialog").border_title = self._title
        self.query_one(Input).value = self._target.filter_query

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-filter on every keystroke."""
        count = self._target.apply_filter(event.value)
        self._show_count(count, event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Keep the filter and close."""
        self.dismiss(None)

    def action_cancel(self) -> None:
        """Clear the filter and close."""
        self._target.apply_filter("")
        self.dismiss(None)

    def _show_count(self, count: int, query: str) -> None:
        label = self.query_one("#filter-count", Static)
        if not query.strip():
            label.update(Text("everything", style=_DIM))
            return
        colour = _SAME if count else _DRIFT
        label.update(Text(f"{count} match{'' if count == 1 else 'es'}", style=colour))


class ConfirmModal(ModalScreen[bool]):
    """A small yes/no overlay that gates a write so the TUI never edits files silently.

    Returns ``True`` when the user confirms and ``False`` (or on ``escape``) when
    they decline, so the caller can act only on an explicit yes.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y,enter", "confirm", "confirm"),
        Binding("n,escape", "cancel", "cancel"),
    ]

    def __init__(
        self, body: RenderableType, *, title: str = "CONFIRM", confirm: str = "write"
    ) -> None:
        """Build the confirmation overlay.

        Args:
            body: The renderable explaining exactly what will happen.
            title: The dialog title.
            confirm: The verb shown on the confirm key hint.
        """
        super().__init__()
        self._body = body
        self._title = title
        self._confirm = confirm

    def compose(self) -> ComposeResult:
        """Yield the dialog: the explanation and the key hints."""
        with Vertical(id="confirm-dialog", classes="modal"):
            yield Static(self._body, id="confirm-body")
            yield Static(self._hints(), id="confirm-hints")

    def on_mount(self) -> None:
        """Title the dialog."""
        dialog = self.query_one("#confirm-dialog")
        dialog.border_title = self._title
        dialog.border_subtitle = "y or n"

    def _hints(self) -> Text:
        text = Text()
        text.append("y", style=f"bold {_ACCENT}")
        text.append(f" {self._confirm}     ", style=_DIM)
        text.append("n", style=f"bold {_ACCENT}")
        text.append(" cancel", style=_DIM)
        return text

    def action_confirm(self) -> None:
        """Confirm the write."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Decline; nothing is written."""
        self.dismiss(False)


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


class EnvPickerModal(ModalScreen["Environment | None"]):
    """Pick an environment for a role — a diff baseline/candidate, or the run env."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, environments: list[Environment], title: str) -> None:
        """Build the picker.

        Args:
            environments: The environments to choose from.
            title: The dialog title naming the role being set.
        """
        super().__init__()
        self._environments = environments
        self._title = title

    def compose(self) -> ComposeResult:
        """Yield the environment list."""
        with Vertical(id="picker-dialog", classes="modal"):
            yield OptionList(*(self._label(env) for env in self._environments))

    def on_mount(self) -> None:
        """Title the dialog and focus the list."""
        dialog = self.query_one("#picker-dialog")
        dialog.border_title = self._title
        dialog.border_subtitle = "↑↓ · enter · esc"
        self.query_one(OptionList).focus()

    def _label(self, env: Environment) -> Text:
        text = Text()
        text.append(env.metadata.name, style=_TEXT_HI)
        if env.metadata.id:
            text.append(f"   {env.metadata.id}", style=_DIM)
        return text

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the chosen environment."""
        self.dismiss(self._environments[event.option_index])

    def action_cancel(self) -> None:
        """Close without choosing."""
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


class GlobalMatrixModal(ModalScreen["set[tuple[str, int]] | None"]):
    """Choose which matrix values run — globally, across every request that uses them."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "apply", "apply"),
        Binding("a", "all", "all"),
        Binding("n", "none", "none"),
    ]

    def __init__(self, matrices: list[Matrix], disabled: set[tuple[str, int]]) -> None:
        """Build the global matrix-value picker.

        Args:
            matrices: Every matrix in the project.
            disabled: The currently-disabled ``(matrix_id, value_index)`` pairs.
        """
        super().__init__()
        self._matrices = matrices
        self._disabled = disabled

    def compose(self) -> ComposeResult:
        """Yield one selection entry per matrix value, labelled by matrix."""
        options: list[tuple[Text, str, bool]] = []
        for matrix in self._matrices:
            matrix_id = matrix.metadata.id or matrix.metadata.name
            for index, value in enumerate(matrix.spec.values):
                prompt = Text.assemble(
                    (f"{matrix.metadata.name}  ", _AXIS), (case_key(value), _TEXT)
                )
                options.append(
                    (prompt, f"{matrix_id}#{index}", (matrix_id, index) not in self._disabled)
                )
        with Vertical(id="mselect-dialog", classes="modal"):
            yield SelectionList[str](*options)

    def on_mount(self) -> None:
        """Title the dialog and focus the list."""
        dialog = self.query_one("#mselect-dialog")
        dialog.border_title = "MATRIX VALUES"
        dialog.border_subtitle = "space toggle · a all · n none · esc apply"
        self.query_one(SelectionList).focus()

    def action_apply(self) -> None:
        """Turn the enabled selection into the disabled set and close."""
        enabled = set(self.query_one(SelectionList).selected)
        disabled: set[tuple[str, int]] = set()
        for matrix in self._matrices:
            matrix_id = matrix.metadata.id or matrix.metadata.name
            for index in range(len(matrix.spec.values)):
                if f"{matrix_id}#{index}" not in enabled:
                    disabled.add((matrix_id, index))
        self.dismiss(disabled)

    def action_all(self) -> None:
        """Enable every value."""
        self.query_one(SelectionList).select_all()

    def action_none(self) -> None:
        """Disable every value."""
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


class ExecutionScreen(ModalScreen[None]):
    """The complete report for one launched ExecutionProfile.

    Run + Diff + Report consulted together: a cell list with per-cell assert and
    diff status, and a detail panel that drills into the baseline and candidate
    assertions plus the git-style body diff. ``v`` flips the diff view; ``esc``
    returns to the Explorer.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("v", "toggle_view", "unified/side-by-side"),
        Binding("escape", "close", "close"),
        Binding("q", "close", "close"),
    ]

    def __init__(self, result: ExecutionResult) -> None:
        """Build the screen for a finished execution.

        Args:
            result: The execution outcome to display.
        """
        super().__init__()
        self._result = result
        self._unified = True

    def compose(self) -> ComposeResult:
        """Yield the header, the cell list, the detail panel, and the gate."""
        with Vertical(id="exec-screen"):
            yield Static(id="exec-header")
            with Horizontal(id="exec-cols"):
                with Vertical(id="exec-cells", classes="panel"):
                    yield DataTable(id="exec-table", cursor_type="row")
                with VerticalScroll(id="exec-detail", classes="panel hero"):
                    yield Static(id="exec-detail-content")
            yield Static(id="exec-gate")

    def on_mount(self) -> None:
        """Render the header, populate the cell list, and show the gate."""
        result = self._result
        header = Text()
        header.append(result.profile_id, style=f"bold {_TEXT_HI}")
        header.append("    baseline ", style=_DIM)
        header.append(result.baseline, style=_TEXT_HI)
        if result.candidate is not None:
            header.append(" · candidate ", style=_DIM)
            header.append(result.candidate, style=_TEXT_HI)
        mode = " · ".join(
            part
            for part, on in (("assert", result.checked_assertions), ("diff", result.checked_diff))
            if on
        )
        header.append(f"    mode {mode or 'none'}", style=_AXIS)
        self.query_one("#exec-header", Static).update(header)
        self._populate()
        self._render_gate()
        self.query_one("#exec-table", DataTable).focus()

    def _populate(self) -> None:
        table = self.query_one("#exec-table", DataTable)
        table.clear(columns=True)
        table.add_column("REQUEST", key="request")
        table.add_column("CELL", key="cell")
        table.add_column("ASSERT", key="assert", width=8)
        table.add_column("DIFF", key="diff", width=6)
        for index, outcome in enumerate(self._result.outcomes):
            assert_glyph, diff_glyph = _exec_glyphs(outcome)
            table.add_row(
                Text(outcome.request_id, style=_TEXT_HI),
                Text(outcome.cell_key or "—", style=_AXIS if outcome.cell_key else _DIM),
                assert_glyph,
                diff_glyph,
                key=f"cell::{index}",
            )
        if self._result.outcomes:
            self._show_cell(0)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show the highlighted cell's detail."""
        key = event.row_key.value
        if key is not None and key.startswith("cell::"):
            self._show_cell(int(key.removeprefix("cell::")))

    def _show_cell(self, index: int) -> None:
        outcome = self._result.outcomes[index]
        detail = _execution_detail(
            outcome, self._result.baseline, self._result.candidate, unified=self._unified
        )
        self.query_one("#exec-detail-content", Static).update(detail)
        self.query_one("#exec-detail").border_title = Text.from_markup(
            f"DETAIL [{_DIM}]·[/] {outcome.request_id}"
        )

    def _render_gate(self) -> None:
        result = self._result
        text = Text()
        if result.passed:
            text.append(" PASS ", style=f"bold black on {_SAME}")
        else:
            text.append(" FAIL ", style=f"bold white on {_DRIFT}")
        failures = sum(1 for outcome in result.outcomes if not outcome.ok)
        text.append(
            f"   {len(result.outcomes)} cells · {failures} failing"
            f" · {result.drift} drift · {result.errors} error",
            style=_DIM,
        )
        self.query_one("#exec-gate", Static).update(text)

    def action_toggle_view(self) -> None:
        """Flip the body diff between unified and side-by-side."""
        self._unified = not self._unified
        table = self.query_one("#exec-table", DataTable)
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if key is not None and key.startswith("cell::"):
            self._show_cell(int(key.removeprefix("cell::")))

    def action_close(self) -> None:
        """Return to the Explorer."""
        self.dismiss(None)


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
                yield ReportView(project)
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
        """Open the text filter for the active screen."""
        if self.error is not None:
            return
        active = self.query_one(NavBar).active
        if active == "explorer":
            self.push_screen(
                FilterModal(self.query_one(ExplorerView), placeholder="name, kind, or tag…")
            )
        elif active == "run":
            self.query_one(RunView).open_text_filter()

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

    def launch_execution(self, profile: ExecutionProfile) -> None:
        """Run *profile* and open the Execution Screen with the result.

        Args:
            profile: The execution profile to run.
        """
        if self.project is None:
            return
        self.notify(f"Running {profile.metadata.name}…", title="Execution")
        self.run_worker(
            self._run_execution(self.project, profile), exclusive=True, group="execution"
        )

    async def _run_execution(self, project: LoadedProject, profile: ExecutionProfile) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        client = HttpxClient()
        candidate_client = HttpxClient()
        try:
            result = await run_execution(project, profile, client, candidate_client)
        except EnvironmentSelectionError as error:
            self.notify(str(error), title="Execution failed", severity="error")
            return
        finally:
            await client.aclose()
            await candidate_client.aclose()
        self.push_screen(ExecutionScreen(result))

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
        if screen == "run":
            self.query_one(RunView).update_footer()
            return
        keys, context = {
            "diff": (_DIFF_KEYS, "baseline ⇄ candidate"),
            "report": (_REPORT_KEYS, "last diff run"),
            "settings": (_SETTINGS_KEYS, "read-only"),
        }.get(screen, (_EXPLORER_KEYS, ""))
        self.query_one(StatusBar).show(keys, context)


def _environments(project: LoadedProject) -> list[Environment]:
    """Every environment in the project, sorted by id."""
    envs = [obj for obj in project.objects.values() if isinstance(obj, Environment)]
    return sorted(envs, key=lambda env: env.metadata.id or env.metadata.name)


def _default_pair(project: LoadedProject) -> tuple[Environment, Environment] | None:
    """A baseline ⇄ candidate pair to seed the Diff screen when none is configured.

    Args:
        project: The loaded project.

    Returns:
        The first two environments, the only one twice, or ``None`` if there are none.
    """
    envs = _environments(project)
    if not envs:
        return None
    return (envs[0], envs[1]) if len(envs) > 1 else (envs[0], envs[0])


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


def _pair(node: TreeNode[object] | None) -> tuple[Request | None, MatrixCell | None]:
    data = getattr(node, "data", None)
    if isinstance(data, tuple) and len(data) == 2 and isinstance(data[0], Request):
        cell = data[1] if isinstance(data[1], MatrixCell) else None
        return data[0], cell
    return None, None


def _save_run(
    project: LoadedProject, environment: Environment, run_id: str, entries: list[RunEntry]
) -> Path:
    document = export_run(project, environment, entries)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    destination = project.root / "runs" / f"{run_id}-{stamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination


def _build_report_tree(
    tree: Tree[object],
    project: LoadedProject,
    environment: Environment | None,
    request: Request,
    cell: MatrixCell,
    execution: Execution | None,
    state: str,
    checks: list[Check],
) -> None:
    tree.clear()
    root = tree.root
    resolved = (
        Resolver(project, environment).resolve_request(request, cell)
        if environment is not None
        else None
    )
    method = resolved.method if resolved else request.spec.request.method
    head = Text()
    head.append(f" {method} ", style=f"bold {_INK} on {_METHOD.get(method, _ACCENT)}")
    head.append("  ")
    head.append(resolved.url if resolved else request.spec.request.endpoint, style=_TEXT_HI)
    root.add_leaf(head)
    if cell.key:
        root.add_leaf(Text.assemble(("case    ", _LABEL), (cell.key, _AXIS)))
    glyph, colour = _RUN_GLYPH[state]
    status = Text.assemble(("status  ", _LABEL), (f"{glyph} {state}", colour))
    if execution is not None and execution.response is not None:
        response = execution.response
        status.append(f"   {response.status} · {response.elapsed_ms:.0f}ms", style=_TEXT)
    root.add_leaf(status)

    if checks:
        node = root.add(Text("CHECKS", style=f"bold {_LABEL}"), expand=True)
        for check in checks:
            mark, tint = ("✓", _SAME) if check.ok else ("✗", _DRIFT)
            node.add_leaf(Text.assemble((f"{mark} {check.name}  ", tint), (check.detail, _DIM)))

    if execution is not None and execution.response is not None:
        response = execution.response
        node = root.add(Text("METRICS", style=f"bold {_LABEL}"), expand=True)
        node.add_leaf(Text.assemble(("duration  ", _DIM), (f"{response.elapsed_ms:.0f} ms", _TEXT)))
        node.add_leaf(Text.assemble(("size      ", _DIM), (f"{len(response.body)} bytes", _TEXT)))

    if resolved is not None:
        node = root.add(Text("REQUEST", style=f"bold {_LABEL}"), expand=False)
        headers = node.add(Text("headers", style=_DIM), expand=False)
        for key, value in resolved.headers:
            masked = "••••" in str(value)
            headers.add_leaf(
                Text.assemble((f"{key}: ", _DIM), (str(value), _DRIFT if masked else _TEXT))
            )
        if resolved.body is not None:
            _value_into(node.add(Text("body", style=_DIM), expand=False), resolved.body)

    if execution is not None and execution.response is not None:
        response = execution.response
        node = root.add(Text("RESPONSE", style=f"bold {_LABEL}"), expand=True)
        headers = node.add(Text("headers", style=_DIM), expand=False)
        for key, value in response.headers[:24]:
            headers.add_leaf(Text.assemble((f"{key}: ", _DIM), (str(value), _TEXT)))
        body = node.add(Text("body", style=_DIM), expand=len(response.body) < 800)
        _body_into(body, response.body, _content_type(response.headers))
    elif execution is not None and execution.error is not None:
        root.add_leaf(Text(execution.error, style=_DRIFT))
    elif state == "pending":
        root.add_leaf(Text("not run — press x to execute", style=_DIM))


def _value_into(node: TreeNode[object], value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _value_child(node, str(key), item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _value_child(node, f"[{index}]", item)
    else:
        node.add_leaf(Text.assemble(_scalar(value)))


def _value_child(node: TreeNode[object], key: str, value: object) -> None:
    if isinstance(value, dict):
        label = Text.assemble((key, _AXIS), (f"  {{{len(value)}}}", _DIM))
        _value_into(node.add(label, expand=False), value)
    elif isinstance(value, list):
        label = Text.assemble((key, _AXIS), (f"  [{len(value)}]", _DIM))
        _value_into(node.add(label, expand=False), value)
    else:
        node.add_leaf(Text.assemble((key, _AXIS), (": ", _DIM), _scalar(value)))


def _scalar(value: object) -> tuple[str, str]:
    if value is None:
        return "null", _AXIS
    if isinstance(value, bool):
        return str(value).lower(), _WARN
    if isinstance(value, int | float):
        return str(value), _WARN
    return f'"{value}"', _SAME


def _content_type(headers: list[tuple[str, str]]) -> str:
    for key, value in headers:
        if key.lower() == "content-type":
            return value.lower()
    return ""


def _body_into(node: TreeNode[object], body: bytes, content_type: str) -> None:
    text = body.decode("utf-8", "replace")
    if "event-stream" in content_type or text.startswith(("data:", "event:", "id:", "retry:")):
        _sse_into(node, text)
        return
    if "json" in content_type or text[:1] in "{[":
        try:
            _value_into(node, json.loads(body))
            return
        except (ValueError, TypeError):
            pass
    if "html" in content_type or text.lstrip()[:1] == "<":
        _HtmlOutline(node).feed(text[:20000])
        return
    for line in text[:4000].splitlines()[:200]:
        node.add_leaf(Text(line, style=_TEXT))


def _sse_into(node: TreeNode[object], text: str) -> None:
    events = _parse_sse(text)
    if not events:
        node.add_leaf(Text("(no events)", style=_DIM))
        return
    for index, event in enumerate(events):
        label = Text.assemble((f"event {index}", _AXIS))
        if event.get("event"):
            label.append(f"  {event['event']}", style=_ACCENT)
        entry = node.add(label, expand=len(events) <= 8)
        if event.get("id"):
            entry.add_leaf(Text.assemble(("id: ", _DIM), (event["id"], _TEXT)))
        data = event.get("data", "")
        try:
            _value_into(entry.add(Text("data", style=_DIM), expand=True), json.loads(data))
        except (ValueError, TypeError):
            entry.add_leaf(Text.assemble(("data: ", _DIM), (data[:200], _TEXT)))


def _parse_sse(text: str) -> list[dict[str, str]]:
    """Parse a Server-Sent-Events stream into a list of ``field: value`` events."""
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    data: list[str] = []
    for line in text.splitlines():
        if not line:
            if data or current:
                current["data"] = "\n".join(data)
                events.append(current)
                current, data = {}, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "data":
            data.append(value)
        elif field in ("event", "id", "retry"):
            current[field] = value
    if data or current:
        current["data"] = "\n".join(data)
        events.append(current)
    return events


class _HtmlOutline(HTMLParser):
    """Streams parsed HTML into a collapsible tag tree under a node."""

    def __init__(self, root: TreeNode[object]) -> None:
        """Start the outline under *root*."""
        super().__init__(convert_charrefs=True)
        self._stack = [root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a collapsible node for the tag."""
        label = Text.assemble((f"<{tag}>", _ACCENT))
        rendered = " ".join(f"{key}={value}" for key, value in attrs if value)
        if rendered:
            label.append(f"  {rendered}", style=_DIM)
        self._stack.append(self._stack[-1].add(label, expand=False))

    def handle_endtag(self, tag: str) -> None:
        """Close the current tag."""
        if len(self._stack) > 1:
            self._stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add a self-closing tag as a leaf."""
        self._stack[-1].add_leaf(Text(f"<{tag}/>", style=_ACCENT))

    def handle_data(self, data: str) -> None:
        """Add non-empty text content as a leaf."""
        text = data.strip()
        if text:
            self._stack[-1].add_leaf(Text(text[:200], style=_TEXT))


def _diff_ready(cells: list[CellDiff], pair: tuple[Environment, Environment] | None) -> Group:
    parts: list[RenderableType] = []
    if pair is None:
        text = Text("No diff pair configured.\n\n", style=f"bold {_WARN}")
        text.append("Add one to the project manifest:\n\n", style=_DIM)
        text.append(
            "  environments:\n    diffPairs:\n      - name: local-vs-prod\n"
            "        baseline: local\n        candidate: prod",
            style=_TEXT,
        )
        return Group(text)
    baseline, candidate = pair
    if cells:
        parts.append(Text("✓ every compared field is identical — gate PASS", style=f"bold {_SAME}"))
    else:
        head = Text(style=_TEXT_HI)
        head.append(f"Ready to diff {baseline.metadata.name} ⇄ {candidate.metadata.name}.\n\n")
        head.append("Press ", style=_DIM)
        head.append("x", style=f"bold {_ACCENT}")
        head.append(" to replay every request against both and compare.", style=_DIM)
        parts.append(head)
    parts.append(_diff_legend())
    return Group(*parts)


def _diff_legend() -> Text:
    text = Text("\n")
    text.append("▏", style=_SAME)
    text.append(" compared · identical    ", style=_DIM)
    text.append("▌", style=_DRIFT)
    text.append(" compared · ", style=_DIM)
    text.append("drift", style=_DRIFT)
    text.append("    ╎", style=_SKIP)
    text.append(" not compared", style=_DIM)
    return text


def _diff_field(
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]],
    pair: tuple[Environment, Environment] | None,
) -> Group:
    path, entries = group
    baseline = pair[0].metadata.name if pair else "A"
    candidate = pair[1].metadata.name if pair else "B"
    parts: list[RenderableType] = []
    header = Text(f"{path}", style=f"bold {_DRIFT}")
    header.append(f"   drifts on {len(entries)} cell{'' if len(entries) == 1 else 's'}", style=_DIM)
    parts.append(header)
    for cell, field in entries:
        block = Text("\n")
        block.append(f"{cell.cell_key or cell.request.metadata.name}", style=_AXIS)
        block.append(f"   {field.mode}\n", style=_MODE.get(field.mode, _DIM))
        before, sep, after = field.detail.partition(" → ")
        if sep:
            block.append("  ▌ ", style=_DRIFT)
            block.append(f"{baseline:<10}", style=_DIM)
            block.append(before, style=_SAME)
            block.append("\n  ▌ ", style=_DRIFT)
            block.append(f"{candidate:<10}", style=_DIM)
            block.append(after, style=_DRIFT)
            block.append("\n")
        else:
            block.append("  ▌ ", style=_DRIFT)
            block.append(field.detail or "differs", style=_TEXT)
            block.append("\n")
        parts.append(block)
    parts.append(_diff_legend())
    hint = Text("\npress ", style=_DIM)
    hint.append("i", style=f"bold {_ACCENT}")
    hint.append(" to silence this field — writes an ignore rule to the profile", style=_DIM)
    parts.append(hint)
    return Group(*parts)


def _sv(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False)
    return rendered if len(rendered) <= 60 else f"{rendered[:57]}..."


def _is_container(value: object) -> bool:
    return isinstance(value, dict | list)


def _body_diff_lines(
    base: object,
    cand: object,
    states: dict[str, FieldDiff],
    path: str = "$",
    depth: int = 0,
    key: str | None = None,
    trailing: str = "",
) -> list[tuple[int, str, str, str, str]]:
    """Walk both response trees, yielding (depth, left, right, state, note) rows.

    ``state`` is ``same`` / ``drift`` / ``skip`` from the profile's FieldDiff at
    that path (``context`` for structural braces); ``note`` carries the skip mode.
    """
    label = f'"{key}": ' if key is not None else ""
    decided = states.get(path)
    if decided is not None and decided.state.value in ("skip", "drift") and _is_container(base):
        # The profile decided this whole node at once (e.g. an ignored $.headers,
        # or a type/length drift) — collapse it rather than recursing in.
        placeholder = "{ … }" if isinstance(base, dict) else "[ … ]"
        note = decided.mode if decided.state.value == "skip" else ""
        line = f"{label}{placeholder}{trailing}"
        return [(depth, line, line, decided.state.value, note)]
    if isinstance(base, dict) and isinstance(cand, dict):
        rows: list[tuple[int, str, str, str, str]] = [
            (depth, f"{label}{{", f"{label}{{", "context", "")
        ]
        names = sorted(set(base) | set(cand))
        for index, name in enumerate(names):
            child = f"{path}.{name}"
            tail = "," if index < len(names) - 1 else ""
            if name in base and name in cand:
                rows += _body_diff_lines(
                    base[name], cand[name], states, child, depth + 1, name, tail
                )
            elif name in base:
                rows.append((depth + 1, f'"{name}": {_sv(base[name])}{tail}', "", "drift", ""))
            else:
                rows.append((depth + 1, "", f'"{name}": {_sv(cand[name])}{tail}', "drift", ""))
        rows.append((depth, f"}}{trailing}", f"}}{trailing}", "context", ""))
        return rows
    if isinstance(base, list) and isinstance(cand, list):
        rows = [(depth, f"{label}[", f"{label}[", "context", "")]
        size = max(len(base), len(cand))
        for index in range(size):
            child = f"{path}[{index}]"
            tail = "," if index < size - 1 else ""
            if index < len(base) and index < len(cand):
                rows += _body_diff_lines(
                    base[index], cand[index], states, child, depth + 1, None, tail
                )
            elif index < len(base):
                rows.append((depth + 1, f"{_sv(base[index])}{tail}", "", "drift", ""))
            else:
                rows.append((depth + 1, "", f"{_sv(cand[index])}{tail}", "drift", ""))
        rows.append((depth, f"]{trailing}", f"]{trailing}", "context", ""))
        return rows
    field = states.get(path)
    state = field.state.value if field is not None else "same"
    note = field.mode if field is not None and state == "skip" else ""
    return [(depth, f"{label}{_sv(base)}{trailing}", f"{label}{_sv(cand)}{trailing}", state, note)]


def _diff_unified(lines: list[tuple[int, str, str, str, str]]) -> Text:
    text = Text()
    for depth, left, right, state, note in lines:
        pad = "  " * depth
        if state == "drift":
            if left:
                text.append("- ", style=f"bold {_DRIFT}")
                text.append(f"{pad}{left}\n", style=_DRIFT)
            if right:
                text.append("+ ", style=f"bold {_SAME}")
                text.append(f"{pad}{right}\n", style=_SAME)
        elif state == "skip":
            text.append("  ", style=_DIM)
            text.append(f"{pad}{left}", style=_SKIP)
            text.append(f"   ⋯ skipped · {note}\n", style=_DIM)
        else:
            text.append(f"  {pad}{left}\n", style=_DIM)
    return text


def _diff_side_by_side(
    lines: list[tuple[int, str, str, str, str]], pair: tuple[Environment, Environment] | None
) -> Table:
    baseline = pair[0].metadata.name if pair else "baseline"
    candidate = pair[1].metadata.name if pair else "candidate"
    table = Table(box=None, show_header=True, header_style=f"bold {_DIM}", padding=(0, 3, 0, 0))
    table.add_column(baseline, no_wrap=True)
    table.add_column(candidate, no_wrap=True)
    for depth, left, right, state, note in lines:
        pad = "  " * depth
        if state == "drift":
            left_cell = Text(f"{pad}{left}", style=_DRIFT) if left else Text("")
            right_cell = Text(f"{pad}{right}", style=_SAME) if right else Text("")
        elif state == "skip":
            left_cell = Text(f"{pad}{left}  ⋯ {note}", style=_SKIP)
            right_cell = Text(f"{pad}{right}", style=_SKIP)
        else:
            left_cell = Text(f"{pad}{left}", style=_DIM)
            right_cell = Text(f"{pad}{right}", style=_DIM)
        table.add_row(left_cell, right_cell)
    return table


def _diff_body_view(
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]],
    pair: tuple[Environment, Environment] | None,
    *,
    unified: bool,
) -> Group:
    path, entries = group
    cell = entries[0][0]
    if cell.baseline_body is None or cell.candidate_body is None:
        return _diff_field(group, pair)  # non-JSON / error cell — fall back
    baseline = pair[0].metadata.name if pair else "A"
    candidate = pair[1].metadata.name if pair else "B"
    header = Text()
    header.append(path, style=f"bold {_DRIFT}")
    header.append(
        f"   drifts on {len(entries)} cell{'' if len(entries) == 1 else 's'}\n", style=_DIM
    )
    githead = Text()
    githead.append(f"diff  {baseline} → {candidate}", style=_DIM)
    githead.append(f"   {cell.cell_key or cell.request.metadata.name}\n", style=_AXIS)
    states = {field.path: field for field in cell.fields}
    lines = _body_diff_lines(cell.baseline_body, cell.candidate_body, states)
    body = _diff_unified(lines) if unified else _diff_side_by_side(lines, pair)
    hint = Text("\npress ", style=_DIM)
    hint.append("v", style=f"bold {_ACCENT}")
    hint.append(f" for {'side-by-side' if unified else 'unified'}    ", style=_DIM)
    hint.append("i", style=f"bold {_ACCENT}")
    hint.append(" to silence this field", style=_DIM)
    return Group(header, githead, body, _diff_legend(), hint)


def _exec_glyphs(outcome: CellOutcome) -> tuple[Text, Text]:
    both = outcome.baseline_assertions + outcome.candidate_assertions
    if not both:
        assert_glyph = Text("—", style=_DIM)
    elif assertions_passed(outcome.baseline_assertions) and assertions_passed(
        outcome.candidate_assertions
    ):
        assert_glyph = Text("✓ pass", style=_SAME)
    else:
        assert_glyph = Text("✗ fail", style=_DRIFT)
    if outcome.diff is None:
        diff_glyph = Text("—", style=_DIM)
    elif outcome.diff.drifted:
        diff_glyph = Text("✗", style=_DRIFT)
    else:
        diff_glyph = Text("✓", style=_SAME)
    return assert_glyph, diff_glyph


def _execution_detail(
    outcome: CellOutcome, baseline: str, candidate: str | None, *, unified: bool
) -> Group:
    parts: list[RenderableType] = []
    head = Text()
    head.append(outcome.request_id, style=f"bold {_TEXT_HI}")
    if outcome.cell_key:
        head.append(f"   {outcome.cell_key}", style=_AXIS)
    parts.append(head)
    if outcome.error is not None:
        parts.append(Text(f"\n! {outcome.error}", style=_WARN))
    if outcome.baseline_assertions:
        parts.append(_assert_block(f"ASSERTIONS · {baseline}", outcome.baseline_assertions))
    if outcome.candidate_assertions and candidate is not None:
        parts.append(_assert_block(f"ASSERTIONS · {candidate}", outcome.candidate_assertions))
    if outcome.diff is not None:
        parts.append(Text("\nDIFF", style=_DIM))
        parts.append(_exec_body_diff(outcome.diff, unified=unified))
        parts.append(_diff_legend())
    return Group(*parts)


def _assert_block(title: str, results: list[AssertionResult]) -> Text:
    text = Text(f"\n{title}\n", style=_DIM)
    for result in results:
        if result.ok:
            glyph, style = "✓", _SAME
        elif result.severity == "warn":
            glyph, style = "!", _WARN
        else:
            glyph, style = "✗", _DRIFT
        text.append(f"  {glyph} ", style=style)
        text.append(f"{result.target} {result.op}", style=_TEXT_HI)
        text.append(f"   {result.detail}\n", style=_DIM)
    return text


def _exec_body_diff(diff: CellDiff, *, unified: bool) -> RenderableType:
    if diff.baseline_body is None or diff.candidate_body is None:
        return Text("(no JSON body to compare)", style=_DIM)
    states = {field.path: field for field in diff.fields}
    lines = _body_diff_lines(diff.baseline_body, diff.candidate_body, states)
    return _diff_unified(lines) if unified else _diff_side_by_side(lines, None)


def _report_breakdown(report: RunReport) -> Table:
    grouped: dict[str, dict[str, int]] = {}
    for cell in report.cells:
        tally = grouped.setdefault(cell.request_id, {"same": 0, "drift": 0, "error": 0, "skip": 0})
        tally[cell.state] = tally.get(cell.state, 0) + 1
        tally["skip"] += cell.skipped
    table = _table()
    table.add_column("REQUEST", style=_TEXT_HI, no_wrap=True)
    table.add_column("SAME", justify="right", width=6)
    table.add_column("DRIFT", justify="right", width=6)
    table.add_column("SKIP", justify="right", width=6)
    table.add_column("VERDICT", justify="right", width=9)
    for request_id, tally in grouped.items():
        if tally["error"]:
            verdict = Text("! error", style=_WARN)
        elif tally["drift"]:
            verdict = Text("✗ drift", style=_DRIFT)
        else:
            verdict = Text("✓ pass", style=_SAME)
        table.add_row(
            Text(request_id, style=_TEXT_HI),
            Text(str(tally["same"]), style=_SAME if tally["same"] else _DIM),
            Text(str(tally["drift"]), style=_DRIFT if tally["drift"] else _DIM),
            Text(str(tally["skip"]), style=_SKIP if tally["skip"] else _DIM),
            verdict,
        )
    return table


def _report_export(output: Path) -> Text:
    text = Text()
    for key, name, filename in (
        ("j", "JUnit", "junit.xml"),
        ("s", "SARIF", "comparo.sarif"),
        ("m", "Markdown", "summary.md"),
        ("o", "JSON", "report.json"),
    ):
        text.append(f"  {key}  ", style=f"bold {_ACCENT}")
        text.append(f"{name:<10}", style=_TEXT_HI)
        text.append(f"→ {output}/{filename}\n", style=_DIM)
    text.append("\n  enter", style=f"bold {_ACCENT}")
    text.append("  write every format\n\n", style=_TEXT)
    text.append("the same reporters ", style=_DIM)
    text.append("comparo diff --report", style=_ACCENT)
    text.append(" uses —\nso the gate here matches CI exactly.", style=_DIM)
    return text


def _settings_section(
    project: LoadedProject, environment: Environment | None, key: str
) -> RenderableType:
    manifest = project.project
    spec = manifest.spec if manifest else None
    if key == "project":
        text = Text()
        text.append("name       ", style=_LABEL)
        text.append(f"{manifest.metadata.name if manifest else '—'}\n", style=_TEXT_HI)
        if manifest and manifest.metadata.description:
            text.append("           ", style=_LABEL)
            text.append(f"{manifest.metadata.description}\n", style=_DIM)
        text.append("root       ", style=_LABEL)
        text.append(f"{project.root}\n", style=_TEXT)
        text.append("objects    ", style=_LABEL)
        text.append(f"{len(project.objects)}\n", style=_TEXT)
        text.append("default    ", style=_LABEL)
        text.append(f"{environment.metadata.name if environment else '—'}", style=_ACCENT)
        return text
    if key == "environments":
        envs = [obj for obj in project.objects.values() if isinstance(obj, Environment)]
        text = Text()
        for env in envs:
            remote = _is_remote(env)
            default = environment is not None and env.metadata.id == environment.metadata.id
            text.append("● ", style=_ACCENT if default else _SAME)
            text.append(f"{env.metadata.name:<14}", style=_TEXT_HI if default else _TEXT)
            text.append(f"{env.spec.base_url}", style=_DIM)
            text.append("  live" if remote else "  local", style=_DANGER if remote else _DIM)
            text.append("  default" if default else "", style=_ACCENT)
            text.append("\n")
        text.append("\nswitch the default in the Explorer (enter on an environment).", style=_DIM)
        return text
    if key in ("run", "diff", "report", "redaction"):
        value = getattr(spec, key, None) if spec else None
        if not value:
            return Text(f"no {key} config in the project manifest", style=_DIM)
        return _json(value)
    if key == "appearance":
        text = Text()
        text.append("theme      ", style=_LABEL)
        text.append("● comparo-ink", style=_SAME)
        text.append("   the built-in dark ink palette\n", style=_DIM)
        text.append("palette    ", style=_LABEL)
        text.append("~12 meaning-named tokens (theme.py)\n", style=_TEXT)
        text.append("\nColours come from a registered Textual Theme, so every\n", style=_DIM)
        text.append("widget derives from the same tokens.", style=_DIM)
        return text
    if key == "plugins":
        plugins = spec.plugins if spec else None
        if not plugins:
            return Text("no plugins — comparo is domain-agnostic by default.", style=_DIM)
        return _json(plugins)
    text = Text()
    text.append("comparo.core", style=_ACCENT)
    text.append(" is the whole engine; the TUI, CLI, and GitHub Action are\n", style=_TEXT)
    text.append("thin front-ends over it and never the reverse.\n\n", style=_TEXT)
    text.append("• core imports no HTTP library (httpx lives only in an adapter)\n", style=_DIM)
    text.append("• the layering is enforced by import-linter in CI\n", style=_DIM)
    text.append("• secrets resolve lazily and are masked in every display\n", style=_DIM)
    return text
