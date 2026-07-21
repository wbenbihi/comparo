"""The comparo terminal UI.

Built to the comparo-ink design: a top nav bar of screen tabs, a full foldable
project tree on the Explorer, and rich per-object detail (the resolved outbound
request with a syntax-highlighted body, or the config of any other object). The
Diff screen carries the signature tri-state gutter. The core never depends on
this module.
"""

import asyncio
import contextlib
import os
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import ClassVar
from typing import cast
from uuid import uuid4

from rich.console import Group
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import App
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key
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

from comparo import __version__
from comparo.adapters import updates as updates_adapter
from comparo.adapters import userconfig
from comparo.adapters.userconfig import UserConfig
from comparo.core.archive import archive_dir
from comparo.core.archive import list_records
from comparo.core.archive import save_record
from comparo.core.assertions import AssertionResult
from comparo.core.assertions import evaluate_rules
from comparo.core.assertions import passed as assertions_passed
from comparo.core.assertions import request_response_rules
from comparo.core.compare import CellDiff
from comparo.core.compare import compare_cell
from comparo.core.compare import profile_for
from comparo.core.curl import to_curl
from comparo.core.diagnostics import LoadError
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execute import execute_request
from comparo.core.execute import run_settings
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionProgress
from comparo.core.execution import ExecutionResult
from comparo.core.execution import run_execution
from comparo.core.export import RunEntry
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
from comparo.core.provenance import Trail
from comparo.core.redaction import Redactor
from comparo.core.report import diff_passed
from comparo.core.report_builder import record_from_diff
from comparo.core.report_builder import record_from_execution
from comparo.core.report_builder import record_from_run
from comparo.core.report_record import ReportRecord
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink
from comparo.core.resolve import resolve_pair
from comparo.core.secrets import SecretError
from comparo.core.triage import TriageError
from comparo.core.triage import profile_path
from comparo.core.triage import silence
from comparo.tui.render import _app_env
from comparo.tui.render import _app_redact
from comparo.tui.render import _assert_count_text
from comparo.tui.render import _assert_tally
from comparo.tui.render import _bash
from comparo.tui.render import _branch
from comparo.tui.render import _build_report_tree
from comparo.tui.render import _cell_events
from comparo.tui.render import _cell_verdict
from comparo.tui.render import _clip
from comparo.tui.render import _crash_report
from comparo.tui.render import _default_environment
from comparo.tui.render import _default_pair
from comparo.tui.render import _description
from comparo.tui.render import _diff_body_view
from comparo.tui.render import _diff_error_view
from comparo.tui.render import _diff_field
from comparo.tui.render import _diff_legend
from comparo.tui.render import _diff_ready
from comparo.tui.render import _diff_skip_view
from comparo.tui.render import _environment_detail
from comparo.tui.render import _environments
from comparo.tui.render import _envs_label
from comparo.tui.render import _error_report
from comparo.tui.render import _exec_assert_body
from comparo.tui.render import _exec_diff_legend
from comparo.tui.render import _exec_diff_summary
from comparo.tui.render import _exec_env_names
from comparo.tui.render import _exec_header
from comparo.tui.render import _exec_profile_card
from comparo.tui.render import _exec_profiles_hint
from comparo.tui.render import _exec_setup
from comparo.tui.render import _exec_stacked_diff
from comparo.tui.render import _exec_triplet
from comparo.tui.render import _executions_ledger
from comparo.tui.render import _field_drill_card
from comparo.tui.render import _gate_composition
from comparo.tui.render import _governing_path
from comparo.tui.render import _graph
from comparo.tui.render import _help_body
from comparo.tui.render import _json
from comparo.tui.render import _keys_bar
from comparo.tui.render import _leaf
from comparo.tui.render import _live_call_ledger
from comparo.tui.render import _matches
from comparo.tui.render import _object_detail
from comparo.tui.render import _ok_report
from comparo.tui.render import _outbound_header
from comparo.tui.render import _p50
from comparo.tui.render import _pair
from comparo.tui.render import _project_detail
from comparo.tui.render import _project_leaf
from comparo.tui.render import _record_kind
from comparo.tui.render import _record_markdown
from comparo.tui.render import _rel_dir
from comparo.tui.render import _relative_age
from comparo.tui.render import _render_provenance
from comparo.tui.render import _replay_banner
from comparo.tui.render import _replay_compare_well
from comparo.tui.render import _replay_detail_tree
from comparo.tui.render import _replay_drift_groups
from comparo.tui.render import _replay_drift_summary
from comparo.tui.render import _replay_run_progress
from comparo.tui.render import _replay_skip_groups
from comparo.tui.render import _report_reading_pane
from comparo.tui.render import _req_short
from comparo.tui.render import _request_detail
from comparo.tui.render import _request_latencies
from comparo.tui.render import _requests
from comparo.tui.render import _rule_detail
from comparo.tui.render import _run_key
from comparo.tui.render import _run_label
from comparo.tui.render import _running_row_from_progress
from comparo.tui.render import _running_table
from comparo.tui.render import _RunningRow
from comparo.tui.render import _save_run
from comparo.tui.render import _seg_toggle
from comparo.tui.render import _settings_body
from comparo.tui.render import _stream_body_view
from comparo.tui.render import _title
from comparo.tui.replay import ReplayRecord
from comparo.tui.replay import project
from comparo.tui.theme import COMPARO_INK
from comparo.tui.tokens import _ACCENT
from comparo.tui.tokens import _AXIS
from comparo.tui.tokens import _DANGER
from comparo.tui.tokens import _DIFF_PREPARE_KEYS
from comparo.tui.tokens import _DIFF_RESULTS_KEYS
from comparo.tui.tokens import _DIFF_RUNNING_KEYS
from comparo.tui.tokens import _DIM
from comparo.tui.tokens import _DRIFT
from comparo.tui.tokens import _ENV_KEYS
from comparo.tui.tokens import _ERROR_KEYS
from comparo.tui.tokens import _EXEC_CELL_KEYS
from comparo.tui.tokens import _EXEC_DIFF_KEYS
from comparo.tui.tokens import _EXEC_KEYS
from comparo.tui.tokens import _EXEC_LAUNCH_KEYS
from comparo.tui.tokens import _EXEC_RESULTS_KEYS
from comparo.tui.tokens import _EXEC_RUNNING_KEYS
from comparo.tui.tokens import _EXPLORER_KEYS
from comparo.tui.tokens import _GATE_COLOR
from comparo.tui.tokens import _HEALTH_LABEL
from comparo.tui.tokens import _HEALTH_SEVERITY
from comparo.tui.tokens import _INSTANCE_KEYS
from comparo.tui.tokens import _KIND_GLYPH
from comparo.tui.tokens import _KINDS
from comparo.tui.tokens import _LABEL
from comparo.tui.tokens import _METHOD
from comparo.tui.tokens import _MODE
from comparo.tui.tokens import _PREPARE_KEYS
from comparo.tui.tokens import _REPORT_DIFF_KEYS
from comparo.tui.tokens import _REPORT_LIST_KEYS
from comparo.tui.tokens import _REPORT_RUN_KEYS
from comparo.tui.tokens import _RESOLVE_KEYS
from comparo.tui.tokens import _RUN_GLYPH
from comparo.tui.tokens import _RUNNING_DONE_KEYS
from comparo.tui.tokens import _RUNNING_KEYS
from comparo.tui.tokens import _SAME
from comparo.tui.tokens import _SETTINGS_KEYS
from comparo.tui.tokens import _SETTINGS_SUBTITLE
from comparo.tui.tokens import _SKIP
from comparo.tui.tokens import _STATUS
from comparo.tui.tokens import _TAB_NAMES
from comparo.tui.tokens import _TEXT
from comparo.tui.tokens import _TEXT_HI
from comparo.tui.tokens import _WARN


class NavBar(Horizontal):
    """The top screen-tab bar: logo, tabs, and a right-aligned status."""

    TABS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("explorer", "Explorer"),
        ("run", "Run"),
        ("diff", "Diff"),
        ("execution", "Execution"),
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
        context_text = Text.from_markup(context)
        context_text.no_wrap = True
        context_text.overflow = "ellipsis"
        # Fit the hints to the width the context leaves free. When they overflow, drop
        # lower-priority hints from the middle — never the last two (help + esc/⌫/q back
        # or quit), so the back/quit hint is ALWAYS visible at 104 cols.
        width = self.app.size.width or 104
        available = max(width - context_text.cell_len - 4, 24)
        chosen = list(keys)
        bar = _keys_bar(chosen)
        while bar.cell_len > available and len(chosen) > 3:
            drop = max(1, min(len(chosen) - 3, len(chosen) // 2))
            del chosen[drop]
            bar = _keys_bar(chosen)
        self.query_one("#status-keys", Static).update(bar)
        self.query_one("#status-context", Static).update(context_text)


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
        #: When each env was last health-probed — health is manual (EXP-23), so
        #: the detail shows how fresh the last probe is and how to re-run it.
        self.health_checked: dict[str, str] = {}
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

    def refresh_screen(self) -> None:
        """Focus the tree so its nav keys work on return, and re-sync the footer."""
        self.query_one("#tree", Tree).focus()
        self._update_footer(self._selected())

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
        """Flip the selected request/instance between resolved and raw source.

        Only requests and instances have a raw ⇄ resolved view, so ``r`` is inert
        on any other selection instead of silently flipping hidden state.
        """
        if not isinstance(self._selected(), (Request, Instance)):
            return
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
        self.health_checked[env_id] = datetime.now().isoformat(timespec="seconds")
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
            if not objects:
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
            self._set_detail(
                _request_detail(
                    self.project,
                    obj,
                    resolved,
                    raw=self.raw,
                    redact=_app_redact(self),
                )
            )
            context.border_title = "PROVENANCE"
            self._set_context(_render_provenance(resolved.trail, _app_redact(self)))
        elif isinstance(obj, Environment):
            env_id = obj.metadata.id or ""
            detail.border_title = _title(obj, "ENVIRONMENT")
            detail.border_subtitle = _HEALTH_LABEL[self.health.get(env_id, Health.UNKNOWN)]
            self._set_detail(
                _environment_detail(
                    obj,
                    self.health_reports.get(env_id),
                    _app_redact(self),
                    checked=self.health_checked.get(env_id),
                )
            )
            context.border_title = "DESCRIPTION"
            self._set_context(_description(obj))
        elif isinstance(obj, Project):
            detail.border_title = _title(obj, "PROJECT")
            detail.border_subtitle = "the manifest"
            self._set_detail(_project_detail(obj, _app_redact(self)))
            context.border_title = "DESCRIPTION"
            self._set_context(_description(obj))
        elif isinstance(obj, Instance):
            value, trail = self._resolve_instance(obj)
            detail.border_title = _title(obj, "INSTANCE")
            detail.border_subtitle = self._resolve_subtitle()
            self._set_detail(
                _json(
                    obj.spec.value if self.raw else value,
                    _app_redact(self),
                )
            )
            titled, content = (
                ("PROVENANCE", _render_provenance(trail, _app_redact(self)))
                if trail and not self.raw
                else ("DESCRIPTION", _description(obj))
            )
            context.border_title = titled
            self._set_context(content)
        else:
            detail.border_title = _title(obj, type(obj).__name__.upper())
            detail.border_subtitle = ""
            self._set_detail(_object_detail(obj, _app_redact(self)))
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
        elif isinstance(obj, Request):
            keys = _RESOLVE_KEYS
        elif isinstance(obj, Instance):
            keys = _INSTANCE_KEYS
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
        Binding("t", "cycle_detail", "views"),
        Binding("z", "zoom", "maximize"),
        Binding("a", "abort", "abort"),
        Binding("s", "save", "save"),
    ]

    #: Declared here so ``action_cycle_detail`` (above ``__init__``) can read it.
    _detail_focus: str

    def action_cycle_detail(self) -> None:
        """Cycle the per-cell detail facet (RUN-27): all → request → … → raw."""
        if self._view != "details" or self._focus is None or self._focus_cell is None:
            return
        order = ("all", "request", "response", "headers", "raw")
        self._detail_focus = order[(order.index(self._detail_focus) + 1) % len(order)]
        self._populate_details(self._focus, self._focus_cell)

    def action_pick_env(self) -> None:
        """Choose the environment this run executes against (PREPARE only)."""
        if self.query_one("#run-mode", ContentSwitcher).current != "prepare":
            return
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
        self._results: dict[tuple[str, str], list[AssertionResult]] = {}
        self._prep_nodes: dict[tuple[str, str], TreeNode[object]] = {}
        self._prep_branches: dict[str, TreeNode[object]] = {}
        self._view = "requests"
        self._run_id: str | None = None
        self._max = False
        self._failures_only = False
        self.filter_query = ""
        self._focus: Request | None = None
        self._focus_cell: MatrixCell | None = None
        #: Which facet the per-cell detail shows (RUN-27): all/request/response/headers/raw.
        self._detail_focus = "all"
        self._worker: Worker[None] | None = None
        self._done = False
        #: The environment the current run executed against, pinned at launch so a
        #: later default-env change can't mislabel or mis-save the finished run.
        self._run_env: Environment | None = None

    def compose(self) -> ComposeResult:
        """Yield the two states behind a switcher."""
        with ContentSwitcher(initial="prepare", id="run-mode"):
            with Vertical(id="prepare"), Vertical(id="prepare-panel", classes="panel hero"):
                yield Static(id="prepare-head")
                yield Tree("requests", id="prepare-tree")
                yield Static(id="prepare-cta")
                yield Static(id="prepare-cli", classes="cli-preview")
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
        """Open the global matrix-value picker (PREPARE only; applies across every request)."""
        if self.query_one("#run-mode", ContentSwitcher).current != "prepare":
            return
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
        redact = _app_redact(self)
        label = redact(cell.key) or request.metadata.name
        row = Text()
        if not self._cell_enabled(cell):  # turned off globally by the matrix picker
            row.append("✕ ", style=_DIM)
            row.append(label, style=_DIM)
            row.append("  matrix off", style=_WARN)
        elif key in self._selected:
            row.append("◉ ", style=_ACCENT)
            row.append(label, style=_TEXT)
        else:
            row.append("○ ", style=_DIM)
            row.append(label, style=_DIM)
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
        # RUN-28: spell out the workload arithmetic so the run's size is legible
        # before you commit to it — reqs · cases x envs = calls (a run is one env).
        cta = Text()
        cta.append("▶ ", style=f"bold {_ACCENT}")
        cta.append(f"{requests} request{'' if requests == 1 else 's'}", style=f"bold {_TEXT_HI}")
        cta.append(" · ", style=_DIM)
        cta.append(f"{cells} case{'' if cells == 1 else 's'}", style=_TEXT)
        cta.append(" × ", style=_DIM)
        cta.append("1 env", style=_TEXT)
        cta.append(" = ", style=_DIM)
        cta.append(f"{cells} call{'' if cells == 1 else 's'}", style=f"bold {_AXIS}")
        cta.append("  ·  up to 4 in parallel", style=_DIM)
        cta.append("\npress ", style=_DIM)
        cta.append("x", style=f"bold {_ACCENT}")
        cta.append(" to run", style=_DIM)
        self.query_one("#prepare-cta", Static).update(cta)
        # PROD-90: the equivalent headless command — every screen is a command,
        # and the TUI just writes the flags. Keeps the TUI/CLI parity visible.
        cli = Text()
        cli.append("$ ", style=_DIM)
        cli.append("comparo run", style=f"bold {_TEXT}")
        if environment is not None:
            cli.append(f" --env {environment.metadata.name}", style=_TEXT)
        cli.append("\nevery screen is a command · the TUI writes the flags", style=_DIM)
        self.query_one("#prepare-cli", Static).update(cli)
        self.update_footer()

    # ── RUN LIFECYCLE ────────────────────────────────────────────────────────
    def execute(self) -> None:
        """Start a run of the selected cells; switch to the RUNNING state.

        A run already in flight is never silently discarded: pressing ``x`` mid-run
        is a no-op that points at ``a`` (abort). From PREPARE it starts a run; from
        a finished run it re-runs.
        """
        if self.query_one("#run-mode", ContentSwitcher).current == "running" and not self._done:
            self.app.notify("A run is in progress — press a to abort first", severity="warning")
            return
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
        self._run_env = environment  # pin the run's env; the default may change later
        self._focus = None
        self._focus_cell = None
        self._view = "requests"
        for request, cell in plan:
            key = _run_key(request, cell)
            self._state[key] = "pending"
            self._exec.pop(key, None)
            self._results.pop(key, None)
        self.query_one("#run-mode", ContentSwitcher).current = "running"
        self._populate_requests()
        self._layout()
        self._render_progress()
        self._worker = self.run_worker(self._run(environment, plan), exclusive=True, group="run")

    async def _run(self, environment: Environment, plan: list[tuple[Request, MatrixCell]]) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        client = HttpxClient()
        concurrency, retry = run_settings(self.project)
        limit = asyncio.Semaphore(concurrency)
        # Compile each request's rules once, up front — the run judges the exact
        # rule set it launched with, and every cell of a request shares it.
        rules = {
            id(request): request_response_rules(self.project, request)
            for request in {id(r): r for r, _ in plan}.values()
        }

        async def one(request: Request, cell: MatrixCell) -> None:
            key = _run_key(request, cell)
            self._state[key] = "running"
            self._on_progress(request, cell)
            async with limit:
                execution = await execute_request(
                    self.project, environment, request, client, cell, retry
                )
            self._exec[key] = execution
            # THE evaluation — the screen, the saved run, and the archived report
            # all read this one result set; nothing re-evaluates later. A dead
            # cell is never judged: no response means no rule ran (the spec's
            # no-fake-rows doctrine) — reachable ✗ carries the story alone.
            self._results[key] = (
                evaluate_rules(self.project, rules[id(request)], execution)
                if execution.response is not None
                else []
            )
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
        environment = self._run_env or _app_env(self)
        if environment is None:
            return
        entries = [
            RunEntry(request, cell, self._exec[key], self._results.get(key, []))
            for request, cell in self._plan()
            if (key := _run_key(request, cell)) in self._exec
        ]
        try:
            path = _save_run(self.project, environment, self._run_id, entries)
        except OSError as error:
            self.app.notify(str(error), title="Could not save", severity="error")
            return
        # Also archive an assertions report so the run shows up in the Report tab.
        # Evaluate-once: the archived report gets the SAME result objects the
        # screen showed and the run file serialized — never a second evaluation.
        run_cells = [(entry.execution, entry.results) for entry in entries]
        record = cast("ComparoApp", self.app).save_run_report(environment, run_cells)
        if record is not None:
            self.app.notify(
                f"Saved run to {path.name} · report {record.metadata.id} in the archive",
                title="Saved",
            )
        else:
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
        crumb = _app_redact(self)(cell.key) or request.metadata.name
        wrap.border_title = Text.from_markup(f"DETAIL [{_DIM}]·[/] {crumb}")
        # RUN-27: the switchable facet strip doubles as the panel subtitle.
        wrap.border_subtitle = _seg_toggle(
            ("all", "request", "response", "headers", "raw"), self._detail_focus
        )
        key = _run_key(request, cell)
        tree: Tree[object] = self.query_one("#detail-tree", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        _build_report_tree(
            tree,
            self.project,
            self._run_env or _app_env(self),
            request,
            cell,
            self._exec.get(key),
            self._state.get(key, "pending"),
            self._results.get(key, []),
            _app_redact(self),
            focus=self._detail_focus,
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
        environment = self._run_env or _app_env(self)
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
        redact = _app_redact(self)
        return [
            Text(glyph, style=colour),
            Text(redact(cell.key) or "base", style=_AXIS),
            Text(code, style=_SAME if code.startswith("2") else _DIM if code == "—" else _WARN),
            Text(time, style=_DIM),
            self._assert_cell(request, cell),
        ]

    def _assert_cell(self, request: Request, cell: MatrixCell) -> Text:
        key = _run_key(request, cell)
        state = self._state.get(key, "pending")
        if state in ("pending", "running"):
            return Text("—", style=_DIM)
        if state == "failed":
            return Text("✗ unreachable", style=_DRIFT)
        redact = _app_redact(self)
        results = self._results.get(key, [])
        failed = [
            redact(result.label or f"{result.target} {result.op}")
            for result in results
            if not result.ok and result.severity == "error"
        ]
        if failed:
            return Text("✗ " + ", ".join(failed), style=_DRIFT)
        passed = sum(1 for result in results if result.ok)
        warned = sum(1 for result in results if not result.ok and result.severity == "warn")
        cellule = Text(f"✓ {passed} passed", style=_SAME)
        if warned:
            cellule.append(f" · ~ {warned}", style=_WARN)
        return cellule

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
        return self._state.get(key) == "ok" and assertions_passed(self._results.get(key, []))

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
        Binding("o", "outbound", "outbound"),
        Binding("i", "silence", "ignore field"),
        Binding("s", "save", "save"),
        Binding("escape", "back", "back"),
        Binding("backspace", "back", "back"),
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
        #: Skipped fields grouped by path — the volatile paths the profile ignores.
        self._skip_groups: list[tuple[str, list[tuple[CellDiff, FieldDiff]]]] = []
        #: Row-key → cell, for the per-cell drift sub-rows and error rows.
        self._row_cells: dict[str, CellDiff] = {}
        self._run_id: str | None = None
        self._done = False
        self._saved = False
        #: live-progress state for the RUNNING sub-view (mirrors the Execution one)
        self._run_done = 0
        self._run_total = 0
        self._run_rows: list[_RunningRow] = []
        self._unified = True
        self._index_mode = "fields"
        #: Whether the compare panel is showing the outbound-request diff (DIFF-27).
        self._outbound_shown = False
        #: The PREPARE selection: which (request id, cell key) pairs to diff.
        self._selected: set[tuple[str, str]] = set()
        self._prep_nodes: dict[tuple[str, str], TreeNode[object]] = {}
        self._prep_branches: dict[str, TreeNode[object]] = {}
        self._disabled_values: set[tuple[str, int]] = set()

    def set_default_layout(self, diff_view: str) -> None:
        """Apply the saved default body-diff layout (unified / side-by-side)."""
        self._unified = diff_view != "side-by-side"

    def _in_results(self) -> bool:
        """Whether the diff is showing RESULTS (vs the PREPARE checklist)."""
        return self.query_one("#diff-mode", ContentSwitcher).current == "diff-results"

    def action_toggle_index(self) -> None:
        """Flip the drift index between grouped-by-field and broken-rules (RESULTS only)."""
        if not self._in_results():
            return
        self._index_mode = "rules" if self._index_mode == "fields" else "fields"
        self._populate_drift()

    def action_toggle_view(self) -> None:
        """Flip the body diff between unified and side-by-side (RESULTS only)."""
        if not self._in_results():
            return
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
        # The previous results no longer describe this pair — clear them.
        self._cells = []
        self._groups = []
        self._done = False
        self._run_id = None
        # b/c can be pressed from either state; re-title whichever is showing.
        if self.query_one("#diff-mode", ContentSwitcher).current == "diff-prepare":
            self._title_prepare()
        else:
            self._render_progress()
            self._populate_drift()

    def prime_pair(self, baseline: str, candidate: str | None) -> bool:
        """Set the pair from environment names (e.g. from a saved report). True on success."""
        environments = {env.metadata.name: env for env in _environments(self.project)}
        base = environments.get(baseline)
        cand = environments.get(candidate) if candidate else None
        if base is None or cand is None:
            return False
        self._pair = (base, cand)
        self._cells = []
        self._groups = []
        self._run_id = None
        if self.is_mounted:
            self.query_one("#diff-mode", ContentSwitcher).current = "diff-prepare"
            self._title_prepare()
        return True

    def compose(self) -> ComposeResult:
        """Yield the two states — PREPARE (pick pair + cells) and RESULTS."""
        with ContentSwitcher(initial="diff-prepare", id="diff-mode"):
            with (
                Vertical(id="diff-prepare"),
                Vertical(id="diff-prepare-panel", classes="panel hero"),
            ):
                yield Static(id="diff-prep-head")
                yield Tree("requests", id="diff-prep-tree")
                yield Static(id="diff-prep-cta")
                yield Static(id="diff-prep-cli", classes="cli-preview")
            with VerticalScroll(id="diff-running", classes="panel"):
                yield Static(id="diff-running-content")
            with Vertical(id="diff-results"):
                yield Static(id="diff-progress", classes="panel")
                with Horizontal(id="diff-cols"):
                    with Vertical(id="col-drift", classes="panel"):
                        yield DataTable(id="drift-table", cursor_type="row", show_header=False)
                        yield Static(id="drift-legend")
                    with VerticalScroll(id="col-compare", classes="panel hero"):
                        yield Static(id="compare-content")
            with VerticalScroll(id="diff-drill", classes="panel hero"):
                yield Static(id="diff-drill-content")

    def on_mount(self) -> None:
        """Resolve the diff pair, select everything, and build the checklist."""
        try:
            self._pair = resolve_pair(self.project, None, None, None)
        except EnvironmentSelectionError:
            self._pair = _default_pair(self.project)
        for request in _requests(self.project):
            for cell in expand(self.project, request):
                self._selected.add(_run_key(request, cell))
        tree: Tree[object] = self.query_one("#diff-prep-tree", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        self._build_prepare()

    def refresh_screen(self) -> None:
        """Re-title the current state and focus its primary widget."""
        if self.query_one("#diff-mode", ContentSwitcher).current == "diff-prepare":
            self._title_prepare()
            self.query_one("#diff-prep-tree", Tree).focus()
        else:
            self._render_progress()
            self._populate_drift()
            self.query_one("#drift-table", DataTable).focus()

    def execute(self) -> None:
        """From PREPARE: run the selected pair + cells. From RESULTS: re-run.

        A diff already in flight is not silently relaunched: pressing ``x`` mid-run
        is a no-op (the exclusive worker would otherwise be cancelled and restarted).
        """
        switcher = self.query_one("#diff-mode", ContentSwitcher)
        # A diff is in flight iff the RUNNING panel is showing; from RESULTS (even
        # after picking a new pair, which clears _done) a re-run must be allowed.
        if switcher.current == "diff-running":
            self.app.notify("A diff is already running…", severity="warning")
            return
        if self._pair is None:
            self.app.notify("Pick a baseline and candidate first (b / c)", severity="warning")
            return
        plan = self._plan()
        if not plan:
            self.app.notify("Nothing selected to diff", severity="warning")
            return
        self._run_id = uuid4().hex[:6]
        self._done = False
        self._saved = False
        # Show the RUNNING panel while the plan is in flight — results aren't ready,
        # so mirror the Execution running screen instead of a blank/stale results pane.
        self._run_done = 0
        self._run_total = len(plan)
        redact = _app_redact(self)
        # Seed a queued row per plan cell so the running table shows the whole plan
        # up front (names known here), filling in per side as each cell finishes.
        self._run_rows = []
        for request, cell in plan:
            outbound = request.spec.request
            self._run_rows.append(
                _RunningRow(
                    request=request.metadata.name,
                    variant=redact(cell.key) if cell.key else "",
                    method_path=f"{outbound.method} {redact(outbound.endpoint)}",
                    state="queued",
                )
            )
        self.query_one("#diff-mode", ContentSwitcher).current = "diff-running"
        baseline, candidate = self._pair
        self.query_one("#diff-running").border_title = Text.from_markup(
            f"RUNNING [{_DIM}]· {baseline.metadata.name} ⇄ {candidate.metadata.name}[/]"
        )
        self.query_one(
            "#diff-running"
        ).border_subtitle = "cancel with esc — nothing is written until you press s"
        self._render_diff_running()
        self.query_one("#diff-running").focus()
        self.refresh_footer()
        self.run_worker(self._run(self._pair, plan), exclusive=True, group="diff")

    def action_back(self) -> None:
        """Return from RUNNING / RESULTS to PREPARE (cancelling an in-flight diff)."""
        current = self.query_one("#diff-mode", ContentSwitcher).current
        if current == "diff-drill":  # the field-drill card steps back to results
            self.query_one("#diff-mode", ContentSwitcher).current = "diff-results"
            self.query_one("#drift-table", DataTable).focus()
            self.refresh_footer()
            return
        if current in ("diff-results", "diff-running"):
            if current == "diff-running":
                self.workers.cancel_group(self, "diff")
                self._done = True
            self.query_one("#diff-mode", ContentSwitcher).current = "diff-prepare"
            self._build_prepare()
            self.query_one("#diff-prep-tree", Tree).focus()
            self.refresh_footer()

    # ── PREPARE ──────────────────────────────────────────────────────────────
    def _build_prepare(self) -> None:
        tree: Tree[object] = self.query_one("#diff-prep-tree", Tree)
        tree.clear()
        self._prep_nodes = {}
        self._prep_branches = {}
        for request in _requests(self.project):
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
        """Toggle the node in or out of the diff on Enter."""
        request, cell = _pair(event.node)
        if request is None:
            return
        if cell is None:
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
        """Open the global matrix-value picker (PREPARE only; applies across every request)."""
        if self.query_one("#diff-mode", ContentSwitcher).current != "diff-prepare":
            return
        matrices = [obj for obj in self.project.objects.values() if isinstance(obj, Matrix)]
        if not matrices:
            self.app.notify("This project has no matrices", severity="information")
            return
        self.app.push_screen(
            GlobalMatrixModal(matrices, self._disabled_values), self._apply_matrix_values
        )

    def _apply_matrix_values(self, disabled: set[tuple[str, int]] | None) -> None:
        if disabled is None:
            return
        self._disabled_values = disabled
        self._build_prepare()

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
        redact = _app_redact(self)
        label = redact(cell.key) or request.metadata.name
        row = Text()
        if not self._cell_enabled(cell):
            row.append("✕ ", style=_DIM)
            row.append(label, style=_DIM)
            row.append("  matrix off", style=_WARN)
        elif key in self._selected:
            row.append("◉ ", style=_ACCENT)
            row.append(label, style=_TEXT)
        else:
            row.append("○ ", style=_DIM)
            row.append(label, style=_DIM)
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
        response = request.spec.response
        if response is not None and response.streaming:
            row.append("  streaming", style=_SKIP)
        row.append(f"  {will_run}/{len(cells)} will diff", style=_AXIS if will_run else _DIM)
        return row

    def _title_prepare(self) -> None:
        panel = self.query_one("#diff-prepare-panel")
        panel.border_title = "PREPARE — diff"
        panel.border_subtitle = "space select · b/c env · m matrix · x diff"
        head = Text()
        if self._pair is not None:
            baseline, candidate = self._pair
            head.append("baseline ", style=_LABEL)
            head.append(baseline.metadata.name, style=f"bold {_TEXT_HI}")
            head.append(" ●", style=_SAME)
            head.append("   ⇄   ", style=_AXIS)
            head.append("candidate ", style=_LABEL)
            head.append(candidate.metadata.name, style=f"bold {_TEXT_HI}")
            head.append(" ●", style=_SAME)
        else:
            head.append("no diff pair — press b and c to choose environments", style=_WARN)
        self.query_one("#diff-prep-head", Static).update(head)
        cells = len(self._plan())
        requests = len(self._plan_requests())
        # RUN-28: a diff replays every cell against BOTH environments, so the call
        # count is cells x 2 — spell it out so the doubled workload is legible.
        calls = cells * 2
        cta = Text()
        cta.append("▶ ", style=f"bold {_ACCENT}")
        cta.append(f"{requests} request{'' if requests == 1 else 's'}", style=f"bold {_TEXT_HI}")
        cta.append(" · ", style=_DIM)
        cta.append(f"{cells} cell{'' if cells == 1 else 's'}", style=_TEXT)
        cta.append(" × ", style=_DIM)
        cta.append("2 envs", style=_TEXT)
        cta.append(" = ", style=_DIM)
        cta.append(f"{calls} call{'' if calls == 1 else 's'}", style=f"bold {_AXIS}")
        cta.append("  ·  0 writes — nothing is written until you run", style=_DIM)
        cta.append("\npress ", style=_DIM)
        cta.append("x", style=f"bold {_ACCENT}")
        cta.append(" to diff", style=_DIM)
        self.query_one("#diff-prep-cta", Static).update(cta)
        # PROD-90: the equivalent headless command — the TUI writes the flags.
        cli = Text()
        cli.append("$ ", style=_DIM)
        cli.append("comparo diff", style=f"bold {_TEXT}")
        if self._pair is not None:
            baseline, candidate = self._pair
            cli.append(
                f" --baseline {baseline.metadata.name} --candidate {candidate.metadata.name}",
                style=_TEXT,
            )
        cli.append("\nevery screen is a command · the TUI writes the flags", style=_DIM)
        self.query_one("#diff-prep-cli", Static).update(cli)

    def _plan(self) -> list[tuple[Request, MatrixCell]]:
        plan: list[tuple[Request, MatrixCell]] = []
        for request in _requests(self.project):
            for cell in expand(self.project, request):
                if _run_key(request, cell) in self._selected and self._cell_enabled(cell):
                    plan.append((request, cell))
        return plan

    def _plan_requests(self) -> list[str]:
        return sorted({r.metadata.id or r.metadata.name for r, _ in self._plan()})

    def _plan_cells(self, request: Request) -> list[MatrixCell]:
        return [
            cell
            for cell in expand(self.project, request)
            if _run_key(request, cell) in self._selected and self._cell_enabled(cell)
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

    def action_silence(self) -> None:
        """Ask before writing an ignore rule for the selected field into its DiffProfile.

        The TUI never edits a version-controlled file silently, so this opens a
        confirmation overlay naming the field and the exact file(s) it would
        touch; the write only happens if the user confirms. Works from the RESULTS
        index and from the field-drill card.
        """
        current = self.query_one("#diff-mode", ContentSwitcher).current
        if current not in ("diff-results", "diff-drill"):
            return
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
        redact = _app_redact(self)
        text = Text(justify="left")
        text.append("Write an ignore rule for ", style=_TEXT)
        text.append(redact(path), style=f"bold {_TEXT_HI}")
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
        redact = _app_redact(self)
        if redact(path) != path:
            # The field's path itself carries a secret (a server echoed it as a
            # JSON key) — writing it into a tracked diff profile would commit the
            # secret to the repo. Refuse rather than leak.
            self.app.notify(
                "That field's path contains a secret value — writing it into a diff "
                "profile would commit the secret to a tracked file. Nothing written.",
                title="Refused to silence",
                severity="error",
            )
            return
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

    async def _run(
        self, pair: tuple[Environment, Environment], plan: list[tuple[Request, MatrixCell]]
    ) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        baseline, candidate = pair
        redact = _app_redact(self)
        client = HttpxClient()
        candidate_client = HttpxClient()
        concurrency, retry = run_settings(self.project)
        limit = asyncio.Semaphore(concurrency)

        async def one(index: int, request: Request, cell: MatrixCell) -> CellDiff:
            self._run_rows[index] = self._run_rows[index]._replace(state="running")
            self._render_diff_running()
            async with limit:
                base, cand = await asyncio.gather(
                    execute_request(self.project, baseline, request, client, cell, retry),
                    execute_request(
                        self.project, candidate, request, candidate_client, cell, retry
                    ),
                )
            result = compare_cell(self.project, base, cand)
            self._run_done += 1
            failed = result.drifted or result.error is not None
            drift = redact(result.drifts[0].path).rsplit(".", 1)[-1] if result.drifts else ""
            self._run_rows[index] = self._run_rows[index]._replace(
                state="done",
                baseline_status=base.response.status if base.response is not None else None,
                candidate_status=cand.response.status if cand.response is not None else None,
                baseline_ms=round(base.response.elapsed_ms) if base.response is not None else None,
                candidate_ms=round(cand.response.elapsed_ms) if cand.response is not None else None,
                drift=drift,
                failed=failed,
            )
            self._render_diff_running()
            return result

        try:
            self._cells = list(
                await asyncio.gather(*(one(i, r, c) for i, (r, c) in enumerate(plan)))
            )
        finally:
            await client.aclose()
            await candidate_client.aclose()
        self._finish(self._cells)

    def _render_diff_running(self) -> None:
        if not self.is_mounted:
            return
        label = "diff"
        base_name, cand_name = "baseline", "candidate"
        if self._pair is not None:
            base_name, cand_name = self._pair[0].metadata.name, self._pair[1].metadata.name
            label = f"{base_name} ⇄ {cand_name}"
        self.query_one("#diff-running-content", Static).update(
            _running_table(
                label,
                self._run_done,
                self._run_total,
                self._run_rows,
                base_name=base_name,
                cand_name=cand_name,
            )
        )

    def _finish(self, cells: list[CellDiff]) -> None:
        """Land the finished diff on the RESULTS pane (results are saved only on ``s``)."""
        self._cells = cells
        self._done = True
        self.query_one("#diff-mode", ContentSwitcher).current = "diff-results"
        self._regroup()
        self._render_progress()
        self._populate_drift()
        # Only grab focus + the footer if the Diff tab is actually showing — a diff
        # that finishes while the user is on another tab must not steal their keys
        # (returning to the Diff tab re-focuses via refresh_screen).
        if cast("ComparoApp", self.app).query_one(NavBar).active == "diff":
            self.query_one("#drift-table", DataTable).focus()
            self.refresh_footer()
        drift = sum(1 for cell in self._cells if cell.drifted)
        errors = sum(1 for cell in self._cells if cell.error is not None)
        passed = diff_passed(len(self._cells), drift, errors)
        gate = "PASS" if passed else "FAIL"
        self.app.notify(
            f"{drift} drift · {errors} error — gate {gate} · press s to save",
            title="Diff complete",
            severity="information" if passed else "error",
        )

    def action_save(self) -> None:
        """``s`` — archive the finished diff as a saved report, then confirm with a toast."""
        if not self._in_results() or not self._done:
            return
        if not self._cells or self._pair is None:
            self.app.notify("Nothing to save yet — run a diff first", severity="information")
            return
        baseline, candidate = self._pair
        record = cast("ComparoApp", self.app).save_diff_report(baseline, candidate, self._cells)
        if record is None:
            self.app.notify("No project archive to save into", severity="warning")
            return
        self._run_id = record.metadata.id
        self._saved = True
        self.refresh_footer()
        self.app.notify(f"Saved report {record.metadata.id} to the archive", title="Report")

    def _regroup(self) -> None:
        groups: dict[str, list[tuple[CellDiff, FieldDiff]]] = {}
        skips: dict[str, list[tuple[CellDiff, FieldDiff]]] = {}
        for cell in self._cells:
            for field in cell.fields:
                if field.state is State.DRIFT:
                    groups.setdefault(field.path, []).append((cell, field))
                elif field.state is State.SKIP:
                    skips.setdefault(field.path, []).append((cell, field))
        self._groups = sorted(groups.items())
        self._skip_groups = sorted(skips.items())

    def _populate_drift(self) -> None:
        table = self.query_one("#drift-table", DataTable)
        table.clear(columns=True)
        self._row_cells = {}
        errors = [cell for cell in self._cells if cell.error is not None]
        if not self._groups and not errors and not self._skip_groups:
            # Nothing drifted, errored, or was skipped — the only bare PASS state.
            table.add_column("", key="st", width=3)
            table.add_column("FIELD", key="field")
            self.query_one("#col-drift").border_title = "DRIFT INDEX"
            self.query_one("#drift-legend", Static).update(Text(""))
            self.query_one("#compare-content", Static).update(_diff_ready(self._cells, self._pair))
            return
        if self._index_mode == "rules":
            self._populate_rules(table)
        else:
            self._populate_fields(table)
        redact = _app_redact(self)
        for cell in errors:
            key = f"error::{cell.cell_key}::{id(cell)}"
            self._row_cells[key] = cell
            label = Text(cell.request.metadata.name, style=_WARN)
            if cell.cell_key:
                label.append(f" · {redact(cell.cell_key)}", style=_DIM)
            table.add_row(
                Text("!", style=_WARN), label, Text("error", style=f"bold {_WARN}"), key=key
            )
        # The mockup's segmented toggle → a pill control in the panel title.
        active = "grouped" if self._index_mode != "rules" else "broken rules"
        title = Text("DRIFT INDEX  ", style=_DIM)
        title.append(_seg_toggle(("grouped", "broken rules"), active))
        self.query_one("#col-drift").border_title = title
        self._render_drift_legend(errors)
        # Select the first drift if any, else the first error; skips are visible but
        # never a failure, so a skip-only run still reads its clean PASS on the right.
        rules = self._rule_order()
        if self._index_mode == "rules" and rules:
            self._show_rule(rules[0])
        elif self._groups:
            self._show_field(self._groups[0][0])
        elif errors:
            self._show_error(errors[0])
        else:
            self.query_one("#compare-content", Static).update(_diff_ready(self._cells, self._pair))

    def _render_drift_legend(self, errors: list[CellDiff]) -> None:
        legend = Text()
        if self._groups:
            biggest = max(len(entries) for _, entries in self._groups)
            word = "cell" if biggest == 1 else "cells"
            legend.append(
                f"one field drifting on {biggest} {word} is one bug, not {biggest}.", style=_DIM
            )
        elif errors:
            legend.append(f"{len(errors)} request(s) failed to execute — select one.", style=_DIM)
        else:
            legend.append("no drift — the skipped paths below are ignored by design.", style=_DIM)
        example = self._rule_example()
        legend.append("\ntoggle ", style=_DIM)
        legend.append("r", style=f"bold {_ACCENT}")
        legend.append(" → broken rules: ", style=_DIM)
        legend.append(example, style=_DIM)
        self.query_one("#drift-legend", Static).update(legend)

    def _rule_example(self) -> str:
        redact = _app_redact(self)
        for path, entries in self._groups:
            field = entries[0][1]
            return f"{redact(path)} · {field.mode} · {_clip(redact(field.detail)) or 'differs'}"
        return "the DiffProfile rules that fired"

    def _populate_fields(self, table: DataTable[Text]) -> None:
        redact = _app_redact(self)
        table.add_column("", key="st", width=3)
        table.add_column("FIELD", key="field")
        table.add_column("META", key="meta", width=18)
        for path, entries in self._groups:
            meta = Text(f"×{len(entries)}", style=_AXIS)
            meta.append(f" · {entries[0][1].mode}", style=_MODE.get(entries[0][1].mode, _DIM))
            table.add_row(
                Text("✗", style=_DRIFT),
                Text(redact(path), style=_DRIFT),
                meta,
                key=f"drift::{path}",
            )
            # Name which request/cell each drift came from — one dim sub-row apiece.
            for index, (cell, _) in enumerate(entries):
                key = f"cell::{path}::{index}"
                self._row_cells[key] = cell
                label = Text(f"  ↳ {cell.request.metadata.name}", style=_DIM)
                if cell.cell_key:
                    label.append(f" · {redact(cell.cell_key)}", style=_AXIS)
                table.add_row(Text(""), label, Text(""), key=key)
        # Skipped fields stay visible — green never means full coverage.
        for path, entries in self._skip_groups:
            mode = entries[0][1].mode
            table.add_row(
                Text("◐", style=_SKIP),
                Text(redact(path), style=_SKIP),
                Text(f"skipped · {mode}", style=_DIM),
                key=f"skip::{path}",
            )
            requests = sorted({cell.request.metadata.name for cell, _ in entries})
            who = "all requests" if len(requests) > 1 else requests[0]
            sub = Text(f"  ↳ {who}", style=_DIM)
            # Name the exact ignore rule that carved this hole, so a green cell says
            # out loud *which* rule chose not to check the field — not just "skipped".
            rule = _governing_path(entries[0][1])
            if rule:
                sub.append(" · ignored by ", style=_DIM)
                sub.append(redact(rule), style=_SKIP)
            else:
                sub.append(" · volatile", style=_SKIP)
            table.add_row(Text(""), sub, Text(""), key=f"skipsub::{path}")

    def _populate_rules(self, table: DataTable[Text]) -> None:
        # The "broken rules" view: one row per SILENCING rule that fired (ignore /
        # tolerance), with how many fields and requests it silenced — not the drifted
        # fields (that is the "grouped by field" view). Selecting a rule shows its
        # detail, so a skip is auditable, never a silent pass.
        redact = _app_redact(self)
        table.add_column("", key="st", width=3)
        table.add_column("RULE", key="rule")
        table.add_column("SILENCED", key="meta", width=18)
        for rule in self._rule_order():
            groups = self._rule_groups(rule)
            mode = groups[0][1][0][1].mode
            requests = {cell.request.metadata.name for _, ents in groups for cell, _ in ents}
            rule_text = Text(redact(rule), style=_SKIP)
            rule_text.append(f"  {mode}", style=_DIM)
            count = len(groups)
            plural = "" if count == 1 else "s"
            meta = Text(f"{count} field{plural} · {len(requests)} req", style=_DIM)
            table.add_row(Text("◐", style=_SKIP), rule_text, meta, key=f"rule::{rule}")
        if not self._skip_groups:
            table.add_row(
                Text("✓", style=_SAME), Text("no silencing rules fired", style=_DIM), Text("")
            )

    def _rule_order(self) -> list[str]:
        """The silencing rules that fired, in first-seen order."""
        order: list[str] = []
        for path, entries in self._skip_groups:
            rule = _governing_path(entries[0][1]) or path
            if rule not in order:
                order.append(rule)
        return order

    def _rule_groups(self, rule: str) -> list[tuple[str, list[tuple[CellDiff, FieldDiff]]]]:
        """The skipped-field groups a given silencing *rule* carved out."""
        return [(p, e) for p, e in self._skip_groups if (_governing_path(e[0][1]) or p) == rule]

    def _show_rule(self, rule: str) -> None:
        """Render a silencing rule's detail — its mode, why, and every field it hid."""
        redact = _app_redact(self)
        groups = self._rule_groups(rule)
        if not groups:
            return
        mode = groups[0][1][0][1].mode
        wrap = self.query_one("#col-compare")
        wrap.border_title = Text.from_markup(f"RULE [{_DIM}]· {redact(rule)}[/]")
        wrap.border_subtitle = f"{mode} · {len(groups)} silenced field(s)"
        silenced = [
            (redact(path), sorted({cell.request.metadata.name for cell, _ in entries}))
            for path, entries in groups
        ]
        detail = _rule_detail(redact(rule), mode, silenced)
        self.query_one("#compare-content", Static).update(detail)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show the comparison (or error) for the highlighted row.

        The OUTBOUND header's expand/collapse state persists across rows (DIFF-27);
        it is a layer of the compare panel now, not a separate overlay mode.
        """
        self._render_row(event.row_key.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """``enter`` on a drifted field opens the focused field-drill card (d-drill)."""
        if event.data_table.id != "drift-table":
            return
        key = event.row_key.value
        if key is None:
            return
        if key.startswith("drift::"):
            self._open_drill(key.removeprefix("drift::"))
        elif key.startswith("cell::"):
            self._open_drill(key.split("::")[1])

    def _open_drill(self, path: str) -> None:
        group = next((g for g in self._groups if g[0] == path), None)
        if group is None:
            return
        redact = _app_redact(self)
        wrap = self.query_one("#diff-drill")
        wrap.border_title = Text.from_markup(f"FIELD DRILL [{_DIM}]· {redact(path)}[/]")
        wrap.border_subtitle = "esc back · i ignore"
        self.query_one("#diff-drill-content", Static).update(
            _field_drill_card(path, group[1], redact)
        )
        self.query_one("#diff-mode", ContentSwitcher).current = "diff-drill"
        self.query_one("#diff-drill").focus()

    def _render_row(self, key: str | None) -> None:
        """Render the compare panel for a drift-table row key."""
        if key is None:
            return
        if key.startswith("rule::"):
            self._show_rule(key.removeprefix("rule::"))
        elif key.startswith("drift::"):
            self._show_field(key.removeprefix("drift::"))
        elif key.startswith("cell::"):
            cell = self._row_cells.get(key)
            path = key.split("::")[1]
            if cell is not None:
                self._show_field(path, cell)
        elif key.startswith(("skip::", "skipsub::")):
            self._show_skip(key.split("::", 1)[1])
        elif key.startswith("error::"):
            cell = self._row_cells.get(key)
            if cell is not None:
                self._show_error(cell)

    def _current_row_key(self) -> str | None:
        """The drift-table row key under the cursor, or None."""
        table = self.query_one("#drift-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_outbound(self) -> None:
        """Expand or collapse the persistent OUTBOUND header on the compare panel (DIFF-27).

        comparo replays the *same* request against both environments, so the
        outbound only differs where env config does — a different base URL, a
        per-env auth token, an env-specific header. The header sits above the
        body diff and answers the first triage question: is the drift the
        service's, or did we send two different requests? ``o`` toggles it between
        the one-line summary and the full request diff, staying on the field diff.
        """
        if not self._in_results() or self._pair is None:
            return
        self._outbound_shown = not self._outbound_shown
        self._render_row(self._current_row_key())

    def _selected_group(self) -> tuple[str, list[tuple[CellDiff, FieldDiff]]] | None:
        table = self.query_one("#drift-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if key is None:
            return None
        if key.startswith("drift::"):
            path = key.removeprefix("drift::")
        elif key.startswith("cell::"):
            path = key.split("::")[1]
        else:
            return None
        return next((group for group in self._groups if group[0] == path), None)

    def _show_field(self, path: str, cell: CellDiff | None = None) -> None:
        group = next((g for g in self._groups if g[0] == path), None)
        wrap = self.query_one("#col-compare")
        if group is None:
            wrap.border_title = "COMPARE"
            return
        entries = [(c, f) for c, f in group[1] if c is cell] if cell is not None else group[1]
        mode = "unified" if self._unified else "side-by-side"
        request = entries[0][0].request.metadata.name if entries else ""
        envs = ""
        if self._pair is not None:
            envs = f" · {self._pair[0].metadata.name} ⇄ {self._pair[1].metadata.name}"
        redact = _app_redact(self)
        wrap.border_title = Text.from_markup(
            f"COMPARE [{_DIM}]·[/] {redact(path)} [{_DIM}]· {request}{envs}[/]"
        )
        cell = entries[0][0] if entries else None
        # A streamed response diffs its event SEQUENCE (numbered ✓/✗), never one
        # assembled blob; a normal response gets the git-style body diff.
        base_events, cand_events = _cell_events(cell) if cell is not None else (None, None)
        if path == "$status" or path.startswith("$headers"):
            # Status and header drifts have no home in the body tree — render the
            # per-cell before/after card so the evidence is on screen, not hidden
            # behind an empty body well. (The dedicated headers diff well is the
            # Results-rework's job; this keeps the live view honest until then.)
            wrap.border_subtitle = "response envelope · before/after"
            body: RenderableType = _diff_field((path, entries), self._pair, redact)
        elif base_events is not None or cand_events is not None:
            wrap.border_subtitle = "streaming · per-event diff"
            body = _stream_body_view(base_events or [], cand_events or [], redact)
        else:
            wrap.border_subtitle = _seg_toggle(("unified", "side-by-side"), mode)
            body = _diff_body_view(
                (path, entries), self._pair, unified=self._unified, redact=redact
            )
        # The compare panel is three stacked layers: call ledger → outbound → body,
        # so a latency/size regression and the "did we send the same request" answer
        # sit above the response diff (mockup d-results).
        ledger = _live_call_ledger(cell) if cell is not None else None
        header = self._outbound_layer(cell, redact)
        layers: list[RenderableType] = []
        for part in (ledger, header):
            if part is not None:
                layers.extend((part, Text()))
        layers.append(body)
        content = Group(*layers) if len(layers) > 1 else body
        self.query_one("#compare-content", Static).update(content)

    def _outbound_layer(
        self, cell: CellDiff | None, redact: Callable[[str], str]
    ) -> RenderableType | None:
        """The persistent OUTBOUND header for *cell*, collapsed unless ``o`` expanded it.

        Reuses the resolved requests already captured on the executed cell (no
        re-resolve, so no live-secret exposure and no interpolation cost on cursor
        moves); returns ``None`` when the pair or either resolved request is absent.
        """
        if cell is None or self._pair is None:
            return None
        baseline = cell.baseline.resolved if cell.baseline is not None else None
        candidate = cell.candidate.resolved if cell.candidate is not None else None
        if baseline is None or candidate is None:
            return None
        base_env, cand_env = self._pair
        return _outbound_header(
            baseline,
            candidate,
            base_env.metadata.name,
            cand_env.metadata.name,
            expanded=self._outbound_shown,
            redact=redact,
        )

    def _show_error(self, cell: CellDiff) -> None:
        """Render the transport/execution error for a cell — request, env, message."""
        wrap = self.query_one("#col-compare")
        wrap.border_title = Text.from_markup(f"COMPARE [{_DIM}]· error[/]")
        redact = _app_redact(self)
        self.query_one("#compare-content", Static).update(
            _diff_error_view(cell, self._pair, redact=redact)
        )

    def _show_skip(self, path: str) -> None:
        """Explain a field the profile deliberately skips — what and why."""
        group = next((g for g in self._skip_groups if g[0] == path), None)
        wrap = self.query_one("#col-compare")
        wrap.border_title = Text.from_markup(f"COMPARE [{_DIM}]· skipped[/]")
        redact = _app_redact(self)
        self.query_one("#compare-content", Static).update(_diff_skip_view(path, group, redact))

    def _render_progress(self) -> None:
        """Render the summary bar: counts, the gate verdict, and the env selector."""
        panel = self.query_one("#diff-progress", Static)
        self.query_one("#diff-progress").border_title = "SUMMARY"
        if self._pair is None:
            panel.update(Text("no diff pair configured", style=_WARN))
            return
        baseline, candidate = self._pair
        bar = Table(box=None, expand=True, show_header=False, padding=0)
        bar.add_column(justify="left")
        bar.add_column(justify="right")
        left = Text()
        if self._cells:
            same = sum(1 for c in self._cells if not c.drifted and c.error is None)
            drift = sum(1 for c in self._cells if c.drifted)
            errors = sum(1 for c in self._cells if c.error is not None)
            # Tri-state axes stay colored regardless of count (bold only when live).
            left.append(f"{same} same", style=f"bold {_SAME}")
            left.append(" · ", style=_DIM)
            left.append(f"{drift} drift", style=f"bold {_DRIFT}" if drift else _DRIFT)
            left.append(" · ", style=_DIM)
            left.append(f"{errors} error", style=f"bold {_WARN}" if errors else _WARN)
            left.append("    │  ", style=_DIM)
            left.append("gate ", style=_DIM)
            passed = drift == 0 and errors == 0
            left.append("PASS" if passed else "FAIL", style=f"bold {_SAME if passed else _DANGER}")
            if not passed:
                untriaged = drift + errors
                noun = "drift" if untriaged == 1 else "drifts"
                if errors == 0:
                    what = f"{untriaged} untriaged {noun}"
                else:
                    what = f"{drift} drift · {errors} error"
                left.append(f"  {what}", style=_DIM)
        else:
            left.append("press ", style=_DIM)
            left.append("x", style=f"bold {_ACCENT}")
            left.append(" to diff the selected requests against both", style=_DIM)
        env = Text()
        env.append("baseline ", style=_DIM)
        env.append(baseline.metadata.name, style=f"bold {_TEXT_HI}")
        env.append(" ●", style=_SAME)
        env.append("  ⇄  ", style=_AXIS)
        env.append("candidate ", style=_DIM)
        env.append(candidate.metadata.name, style=f"bold {_TEXT_HI}")
        env.append(" ●", style=_SAME)
        env.append("  ▾", style=_DIM)
        bar.add_row(left, env)
        panel.update(bar)

    def footer_keys(self) -> tuple[tuple[str, str], ...]:
        """The footer hints for the current state: PREPARE / RUNNING / RESULTS differ."""
        switcher = self.query_one("#diff-mode", ContentSwitcher) if self.is_mounted else None
        current = switcher.current if switcher is not None else None
        if current == "diff-results":
            return _DIFF_RESULTS_KEYS
        if current == "diff-running":
            return _DIFF_RUNNING_KEYS
        return _DIFF_PREPARE_KEYS

    def refresh_footer(self) -> None:
        """Re-show the status bar for the current diff state."""
        self.app.query_one(StatusBar).show(self.footer_keys(), self.footer_context())

    def footer_context(self) -> str:
        """The right-hand footer caption: the run id and the env direction."""
        switcher = self.query_one("#diff-mode", ContentSwitcher) if self.is_mounted else None
        current = switcher.current if switcher is not None else None
        if current == "diff-prepare":
            cells = len(self._plan())
            return f"{cells} cell{'' if cells == 1 else 's'} selected to diff"
        if self._pair is None:
            return "no diff pair"
        baseline, candidate = self._pair
        arrow = f"{baseline.metadata.name} → {candidate.metadata.name}"
        if current == "diff-running":
            return f"{self._run_done}/{self._run_total} · {arrow}"
        if self._saved and self._run_id:
            return f"saved {self._run_id} · {arrow}"
        return f"{baseline.metadata.name} ⇄ {candidate.metadata.name}"


class ReportView(Vertical):
    """The Report tab: a browser over saved runs, replayed with the live panels.

    The left column lists every archived diff / run / execution — id, age, envs,
    gate, and a drift/error tally — newest first; the right reading pane shows the
    selected report's gate, stat pills, assertion roll-ups, and per-request diff
    breakdown. ``enter`` opens the full analysis **inside** the Report tab: a
    saved diff reopens with the Diff screen's own drift-index + git-diff well
    (read-only), a saved run with the Run screen's Miller rows + detail tree.
    ``esc`` returns to the list; ``o`` exports Markdown, ``d`` deletes.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("o", "export", "export"),
        Binding("d", "delete", "delete"),
        Binding("v", "toggle_view", "unified/side"),
        Binding("slash", "find", "find"),
        Binding("escape", "back", "back"),
        Binding("backspace", "back", "back"),
    ]

    def __init__(self, project: LoadedProject) -> None:
        """Build the report view.

        Args:
            project: The project (used to locate the report archive directory).
        """
        super().__init__(id="report-view", classes="view")
        self.project = project
        self._records: list[ReplayRecord] = []
        self._filtered: list[ReplayRecord] = []
        self.filter_query: str = ""
        self._analyzed: ReplayRecord | None = None
        self._unified = True

    def action_find(self) -> None:
        """Open the archive filter (browse list only)."""
        if self._current_view() == "report-browse":
            self.app.push_screen(FilterModal(self, placeholder="id, envs, kind, or gate…"))

    def apply_filter(self, query: str) -> int:
        """Filter the saved-run list by id / envs / kind / gate / execution; returns the count."""
        self.filter_query = query
        needle = query.strip().lower()
        self._filtered = [record for record in self._records if self._matches(record, needle)]
        self._populate_list()
        if self._filtered:
            self._show(self._filtered[0])
        return len(self._filtered)

    def _matches(self, record: ReplayRecord, needle: str) -> bool:
        """Whether *record* matches the lower-cased *needle* across every filtered field.

        The one predicate both :meth:`apply_filter` and :meth:`refresh_screen` use —
        so returning to the tab can never re-filter on a narrower set of fields and
        silently collapse a gate / envs / kind / execution filter to nothing.
        """
        return (
            not needle
            or needle in record.id.lower()
            or needle in _envs_label(record).lower()
            or needle in _record_kind(record)
            or needle in record.gate.lower()
            or needle in (record.execution or "").lower()
        )

    def compose(self) -> ComposeResult:
        """Yield the browser, the saved-diff replay, and the saved-run replay."""
        with ContentSwitcher(initial="report-browse", id="report-switch"):
            with Horizontal(id="report-browse"):
                with Vertical(id="report-list", classes="panel"):
                    yield DataTable(id="report-table", cursor_type="row")
                    yield Static(id="report-kind")
                    yield Static(id="report-list-legend")
                with VerticalScroll(id="report-read", classes="panel"):
                    yield Static(id="report-read-content")
            with VerticalScroll(id="report-diff"):
                yield Static(id="report-diff-banner")
                with Horizontal(id="report-diff-cols"):
                    with Vertical(id="report-drift", classes="panel"):
                        yield DataTable(
                            id="report-drift-table",
                            cursor_type="row",
                            show_header=True,
                            cell_padding=0,
                        )
                        yield Static(id="report-drift-legend")
                    with VerticalScroll(id="report-compare", classes="panel"):
                        yield Static(id="report-compare-content")
            with Vertical(id="report-run"):
                yield Static(id="report-run-banner")
                yield Static(id="report-run-progress")
                with Horizontal(id="report-run-cols"):
                    with Vertical(id="report-req", classes="panel"):
                        yield DataTable(id="report-req-table", cursor_type="row")
                    with VerticalScroll(id="report-detail", classes="panel"):
                        yield Tree("detail", id="report-detail-tree")

    def on_mount(self) -> None:
        """List the archive and render the newest saved run."""
        self._records = self._load_records()
        self._filtered = list(self._records)
        self._populate_list()
        if self._filtered:
            self._show(self._filtered[0])
        else:
            self._show_empty()

    def _load_records(self) -> list[ReplayRecord]:
        directory = cast("ComparoApp", self.app).archive_directory()
        if directory is None:
            return []
        return [project(record) for record in list_records(directory)]

    def _current_view(self) -> str:
        if not self.is_mounted:
            return "report-browse"
        return self.query_one("#report-switch", ContentSwitcher).current or "report-browse"

    def refresh_screen(self) -> None:
        """Re-read the archive and render the browser (or the current replay)."""
        view = self._current_view()
        if view == "report-browse":
            selected = self._selected()
            self._records = self._load_records()
            needle = self.filter_query.strip().lower()
            self._filtered = [record for record in self._records if self._matches(record, needle)]
            self._populate_list()
            self.query_one("#report-table", DataTable).focus()
            if self._filtered:
                target = next(
                    (r for r in self._filtered if selected is not None and r.id == selected.id),
                    self._filtered[0],
                )
                self._show(target)
            else:
                self._show_empty()
        self._update_nav()
        self.refresh_footer()

    def _update_nav(self) -> None:
        app = cast("ComparoApp", self.app)
        if app.query_one(NavBar).active != "report":
            return
        app.query_one(NavBar).set_status(self._nav_status())

    def _nav_status(self) -> str:
        view = self._current_view()
        if view != "report-browse" and self._analyzed is not None:
            return f"[{_AXIS}]replay [/][{_TEXT_HI}]{self._analyzed.id}[/][{_DIM}]  ·  read-only[/]"
        count = len(self._records)
        return f"[{_TEXT_HI}].reports [/][{_DIM}]· {count} saved run{'' if count == 1 else 's'}[/]"

    # ── footer ────────────────────────────────────────────────────────────────
    def footer_keys(self) -> tuple[tuple[str, str], ...]:
        """The footer hints for the active sub-view."""
        view = self._current_view()
        if view == "report-diff":
            return _REPORT_DIFF_KEYS
        if view == "report-run":
            return _REPORT_RUN_KEYS
        return _REPORT_LIST_KEYS

    def footer_context(self) -> str:
        """The right-hand footer caption: the selected report's file."""
        record = self._analyzed if self._current_view() != "report-browse" else self._selected()
        if record is not None:
            suffix = " · read-only" if self._current_view() != "report-browse" else ""
            return f"reports/{record.id}.json{suffix}"
        return "saved runs · <data>/.reports"

    def refresh_footer(self) -> None:
        """Re-show the status bar for the active sub-view."""
        if cast("ComparoApp", self.app).query_one(NavBar).active != "report":
            return
        self.app.query_one(StatusBar).show(self.footer_keys(), self.footer_context())

    # ── the browser list (step 1) ─────────────────────────────────────────────
    def _populate_list(self) -> None:
        table = self.query_one("#report-table", DataTable)
        table.clear(columns=True)
        # The RUN cell carries a leading per-kind glyph (◆ execution · ◇ diff · ● run)
        # so every row shows its own kind at a glance, not only the kind legend below.
        table.add_column("RUN", key="run", width=8)
        table.add_column("WHEN", key="when", width=4)
        table.add_column("ENVS", key="envs")
        table.add_column("GATE", key="gate", width=6)
        table.add_column("D/E", key="de", width=4)
        self.query_one("#report-list").border_title = Text.from_markup(
            f"[{_ACCENT}]SAVED REPORTS[/] [{_DIM}]· {len(self._filtered)}[/]"
        )
        directory = cast("ComparoApp", self.app).archive_directory()
        rel = _rel_dir(self.project, directory) if directory is not None else ".reports"
        self.query_one("#report-list").border_subtitle = rel
        for index, record in enumerate(self._filtered):
            glyph, colour = _KIND_GLYPH.get(_record_kind(record), ("·", _DIM))
            run = Text.assemble((f"{glyph} ", colour), (record.id, f"bold {_TEXT_HI}"))
            table.add_row(
                run,
                Text(_relative_age(record.created), style=_DIM),
                Text(_envs_label(record), style=_DIM),
                Text(record.gate, style=f"bold {_GATE_COLOR.get(record.gate, _DIM)}"),
                Text(f"{record.drift}/{record.error}", style=_TEXT),
                key=f"rec::{index}",
            )
        self._render_kind()
        legend = Text("fed by ", style=_DIM)
        legend.append("--save", style=_ACCENT)
        legend.append(" · ", style=_DIM)
        legend.append("⏎", style=f"bold {_TEXT_HI}")
        legend.append(" analyze in place", style=_DIM)
        self.query_one("#report-list-legend", Static).update(legend)
        # Keep the nav count in sync with the loaded records whenever the list is built.
        self._update_nav()

    def _render_kind(self) -> None:
        kinds = [_record_kind(record) for record in self._records]
        text = Text("kind\n", style=f"bold {_DIM}")
        for name in ("execution", "diff", "run"):
            glyph, colour = _KIND_GLYPH[name]
            present = name in kinds
            text.append(f"  {glyph} ", style=colour if present else _DIM)
            text.append(f"{name}", style=_TEXT if present else _DIM)
        self.query_one("#report-kind", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Render the highlighted saved run in the reading pane / detail tree."""
        if event.data_table.id == "report-table":
            key = event.row_key.value
            if key is not None and key.startswith("rec::"):
                index = int(key.removeprefix("rec::"))
                if 0 <= index < len(self._filtered):
                    self._show(self._filtered[index])
                    self.refresh_footer()  # show reports/<id>.json, freeing footer width
        elif event.data_table.id == "report-req-table":
            self._render_run_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a saved run analyzes it in place; Enter on a request drills in."""
        if event.data_table.id == "report-table":
            self.action_analyze()

    def _selected(self) -> ReplayRecord | None:
        table = self.query_one("#report-table", DataTable) if self.is_mounted else None
        if table is None or table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if key is None or not key.startswith("rec::"):
            return None
        index = int(key.removeprefix("rec::"))
        return self._filtered[index] if 0 <= index < len(self._filtered) else None

    def _show_empty(self) -> None:
        read = self.query_one("#report-read")
        read.remove_class("pass", "fail")
        read.border_title = "REPORT"
        read.border_subtitle = ""
        self.query_one("#report-read-content", Static).update(
            Text(
                "No saved reports yet.\n\nLaunch an ExecutionProfile from the Execution tab, "
                "or run a diff and press s — each run is archived under <data>/.reports/.",
                style=_DIM,
            )
        )

    def _show(self, record: ReplayRecord) -> None:
        read = self.query_one("#report-read")
        passed = record.gate == "PASS"
        read.set_class(passed, "pass")
        read.set_class(not passed, "fail")
        read.border_title = Text.from_markup(f"REPORT [{_DIM}]· {_run_label(record.id)}[/]")
        code = 0 if passed else 1
        read.border_subtitle = f"{record.gate} · exit {code}"
        self.query_one("#report-read-content", Static).update(_report_reading_pane(record))

    # ── analyze in place (steps 2 / 3) ────────────────────────────────────────
    def action_analyze(self) -> None:
        """Open the selected report's full analysis in place — never a tab jump."""
        record = self._selected()
        if record is None:
            self.app.notify("No saved report selected", severity="information")
            return
        self._analyzed = record
        self._unified = True
        if _record_kind(record) == "diff":
            self._show_diff_replay(record)
        else:
            self._show_run_replay(record)

    def _show_diff_replay(self, record: ReplayRecord) -> None:
        self.query_one("#report-switch", ContentSwitcher).current = "report-diff"
        self.query_one("#report-diff-banner", Static).update(_replay_banner(record, "diff"))
        self.query_one("#report-drift").border_title = Text.from_markup(
            f"DRIFT INDEX [{_DIM}]· saved[/]"
        )
        drift = sum(row.drift for row in record.requests)
        skip = sum(row.skip for row in record.requests)
        self.query_one("#report-drift").border_subtitle = f"{drift} drift · {skip} skip"
        self._populate_replay_drift(record)
        self.query_one("#report-drift-legend", Static).update(
            Group(_diff_legend(), _replay_drift_summary(record))
        )
        compare = self.query_one("#report-compare")
        compare.border_title = Text.from_markup(f"[{_AXIS}]COMPARE · read-only[/]")
        compare.border_subtitle = Text.from_markup(f"[{_AXIS}]unified · v side-by-side[/]")
        self._render_replay_compare(record)
        self.query_one("#report-drift-table", DataTable).focus()
        self._update_nav()
        self.refresh_footer()

    def _populate_replay_drift(self, record: ReplayRecord) -> None:
        table = self.query_one("#report-drift-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="st", width=3)
        table.add_column("FIELD", key="field")
        table.add_column("META", key="meta", width=17)
        redact = _app_redact(self)
        groups = _replay_drift_groups(record)
        for path, requests in groups:
            meta = Text(f" ×{len(requests)}", style=_AXIS)
            meta.append(" · exact", style=_SAME)
            table.add_row(
                Text("✗", style=_DRIFT),
                Text(redact(path), style=_DRIFT),
                meta,
                key=f"drift::{path}",
            )
            for request in requests:
                sub = Text("  ↳ ", style=_DIM)
                sub.append(request, style=_DIM)
                table.add_row(Text(""), sub, Text(""), key=f"cell::{path}::{request}")
        # Skipped paths join the index so the listed rows match the `N skip` subtitle.
        for path in (p for p, _ in _replay_skip_groups(record)):
            table.add_row(
                Text("◐", style=_SKIP),
                Text(redact(path), style=_SKIP),
                Text(" skipped · ignore", style=_DIM),
                key=f"skip::{path}",
            )
            sub = Text("  ↳ ", style=_DIM)
            sub.append("all requests · volatile", style=_DIM)
            table.add_row(Text(""), sub, Text(""), key=f"skipcell::{path}")
        if not groups and not record.cells:
            table.add_row(
                Text("✓", style=_SAME), Text("no drift under compared paths", style=_DIM), Text("")
            )

    def _render_replay_compare(self, record: ReplayRecord) -> None:
        self.query_one("#report-compare-content", Static).update(
            _replay_compare_well(record, self._unified, _app_redact(self))
        )

    def _show_run_replay(self, record: ReplayRecord) -> None:
        self.query_one("#report-switch", ContentSwitcher).current = "report-run"
        kind = _record_kind(record)
        self.query_one("#report-run-banner", Static).update(_replay_banner(record, kind))
        self.query_one("#report-run-progress", Static).update(_replay_run_progress(record))
        self.query_one("#report-req").border_title = Text.from_markup(
            f"[{_AXIS}]REQUESTS · replay[/]"
        )
        self.query_one(
            "#report-req"
        ).border_subtitle = f"{len(record.requests)} requests · {record.calls} cells"
        table = self.query_one("#report-req-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="st", width=3)
        table.add_column("REQUEST", key="req", width=15)
        table.add_column("VARIANTS", key="var", width=10)
        table.add_column("P50", key="p50")
        latencies = _request_latencies(record)
        for index, row in enumerate(record.requests):
            glyph, colour = (
                ("✗", _DRIFT) if row.verdict in ("fail", "drift", "error") else ("✓", _SAME)
            )
            variants = "✓" * max(row.same + row.drift, 1)
            p50 = _p50(latencies.get(row.request, []))
            table.add_row(
                Text(f" {glyph} ", style=colour),
                Text(row.request, style=f"bold {_TEXT_HI}"),
                Text(variants, style=_SAME),
                Text(f"{p50}ms" if p50 is not None else "—", style=_DIM),
                key=f"req::{index}",
            )
        self._render_run_detail()
        table.focus()
        self._update_nav()
        self.refresh_footer()

    def _render_run_detail(self) -> None:
        record = self._analyzed
        if record is None:
            return
        table = self.query_one("#report-req-table", DataTable)
        row = None
        if table.row_count:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            if key is not None and key.startswith("req::"):
                index = int(key.removeprefix("req::"))
                if 0 <= index < len(record.requests):
                    row = record.requests[index]
        detail = self.query_one("#report-detail")
        crumb = row.request if row is not None else "—"
        detail.border_title = Text.from_markup(f"DETAIL [{_DIM}]· {crumb}[/]")
        detail.border_subtitle = "↑↓ navigate · z maximize"
        tree: Tree[object] = self.query_one("#report-detail-tree", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        _replay_detail_tree(tree, record, row)

    # ── back ──────────────────────────────────────────────────────────────────
    def action_back(self) -> None:
        """``esc`` / ``⌫`` — return from a replay to the saved-report list."""
        if self._current_view() == "report-browse":
            return
        self.query_one("#report-switch", ContentSwitcher).current = "report-browse"
        self._analyzed = None
        self.query_one("#report-table", DataTable).focus()
        self._update_nav()
        self.refresh_footer()

    def action_toggle_view(self) -> None:
        """``v`` — flip the saved-diff replay's body well unified ⇄ side-by-side."""
        if self._current_view() == "report-diff" and self._analyzed is not None:
            self._unified = not self._unified
            self._render_replay_compare(self._analyzed)
            sub = "unified · v side-by-side" if self._unified else "side-by-side · v unified"
            self.query_one("#report-compare").border_subtitle = Text.from_markup(
                f"[{_AXIS}]{sub}[/]"
            )

    # ── list actions ──────────────────────────────────────────────────────────
    def action_reload(self) -> None:
        """Re-read the archive directory from disk, keeping the selection if it survives."""
        selected = self._selected()
        self.query_one("#report-switch", ContentSwitcher).current = "report-browse"
        self._analyzed = None
        self._records = self._load_records()
        self._filtered = list(self._records)
        self._populate_list()
        self.query_one("#report-table", DataTable).focus()
        if self._filtered:
            index = next(
                (
                    i
                    for i, record in enumerate(self._filtered)
                    if selected is not None and record.id == selected.id
                ),
                0,
            )
            self.query_one("#report-table", DataTable).move_cursor(row=index)
            self._show(self._filtered[index])
        else:
            self._show_empty()
        self._update_nav()
        self.refresh_footer()
        self.app.notify(f"Reloaded {len(self._records)} saved report(s)", title="Report")

    def action_export(self) -> None:
        """Write a Markdown summary of the selected run to the reports directory."""
        record = self._analyzed if self._current_view() != "report-browse" else self._selected()
        if record is None:
            self.app.notify("No saved report selected", severity="information")
            return
        cast("ComparoApp", self.app).export_record_markdown(record)

    def action_delete(self) -> None:
        """Ask before deleting the selected saved run from the archive."""
        if self._current_view() != "report-browse":
            return
        record = self._selected()
        if record is None:
            self.app.notify("No saved report selected", severity="information")
            return
        directory = cast("ComparoApp", self.app).archive_directory()
        if directory is None:
            return
        path = directory / f"{record.id}.json"
        prompt = Text()
        prompt.append("Delete saved report ", style=_TEXT)
        prompt.append(record.id, style=f"bold {_TEXT_HI}")
        prompt.append(f"\n({record.baseline} ⇄ {record.candidate or '—'}) from the archive?\n\n")
        prompt.append("→ ", style=_DIM)
        prompt.append(str(path), style=_ACCENT)
        prompt.append("\n\nThis removes the file from disk.", style=_DIM)
        self.app.push_screen(
            ConfirmModal(prompt, title="DELETE REPORT"),
            lambda ok: self._delete(path) if ok else None,
        )

    def _delete(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError as error:
            self.app.notify(str(error), title="Delete failed", severity="error")
            return
        self.app.notify(f"Deleted {path.name}", title="Report")
        self.action_reload()


class SettingsView(Horizontal):
    """App-level settings — about, security self-check, updates, engine, prefs.

    A left ``OptionList`` of sections beside a right detail panel. Most sections
    are read-only; a few carry one interactive control (``enter``/``space``
    toggles it) and Security runs the never-leak self-check on ``t``. Preferences
    persist to ``~/.config/comparo/config.toml`` via the app.
    """

    SECTIONS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("about", "About"),
        ("project", "Project"),
        ("security", "Security & Redaction"),
        ("appearance", "Appearance"),
        ("keybindings", "Keybindings"),
        ("updates", "Updates & Privacy"),
        ("plugins", "Plugins"),
        ("engine", "Engine"),
        ("behavior", "Behavior"),
    )

    BINDINGS: ClassVar[list[BindingType]] = [
        # enter/space are consumed by the focused OptionList and arrive as
        # OptionSelected (see on_option_list_option_selected); t bubbles here.
        Binding("t", "selfcheck", "self-check"),
    ]

    def __init__(self, project: LoadedProject) -> None:
        """Build the settings view.

        Args:
            project: The loaded project (its summary appears in the Project section).
        """
        super().__init__(id="settings-view", classes="view")
        self.project = project
        #: Self-check rows (name, detail, ok) once ``t`` has run — ``None`` until then.
        self._selfcheck: list[tuple[str, str, bool]] | None = None
        self._checking = False

    def compose(self) -> ComposeResult:
        """Yield the section list and the detail panel."""
        with Vertical(id="settings-nav", classes="panel"):
            yield OptionList(*(label for _, label in self.SECTIONS), id="settings-list")
        with VerticalScroll(id="settings-detail", classes="panel hero"):
            yield Static(id="settings-content")

    def on_mount(self) -> None:
        """Title the panels and show the first section."""
        nav = self.query_one("#settings-nav")
        nav.border_title = "SETTINGS"
        nav.border_subtitle = str(len(self.SECTIONS))
        self.refresh_screen()

    def refresh_screen(self) -> None:
        """Focus the section list and render the current section."""
        options = self.query_one("#settings-list", OptionList)
        options.focus()
        self._show(options.highlighted or 0)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Render the highlighted section."""
        self._show(event.option_index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter on a section toggles its control (the list consumes the key)."""
        self.action_activate()

    def _config(self) -> UserConfig:
        return cast("ComparoApp", self.app).user_config

    def _current_key(self) -> str:
        index = self.query_one("#settings-list", OptionList).highlighted or 0
        return self.SECTIONS[index][0]

    def _show(self, index: int) -> None:
        key, label = self.SECTIONS[index]
        detail = self.query_one("#settings-detail")
        detail.border_title = label.upper()
        detail.border_subtitle = _SETTINGS_SUBTITLE.get(key, "read-only")
        self.query_one("#settings-content", Static).update(
            _settings_body(
                self.project,
                self._config(),
                key,
                self._selfcheck,
                self._checking,
                _app_redact(self),
            )
        )

    def _reshow(self) -> None:
        self._show(self.query_one("#settings-list", OptionList).highlighted or 0)

    def action_activate(self) -> None:
        """Toggle the current section's control (Updates / Appearance / Behavior)."""
        app = cast("ComparoApp", self.app)
        key = self._current_key()
        if key == "updates":
            enabled = not app.user_config.update_check
            app.user_config = app.user_config.with_(update_check=enabled)
            app.save_user_config()
            if enabled:
                app.check_for_updates_now()
            app.notify(
                f"Update check {'enabled' if enabled else 'disabled'}", severity="information"
            )
        elif key == "appearance":
            nxt = "side-by-side" if app.user_config.diff_view == "unified" else "unified"
            app.user_config = app.user_config.with_(diff_view=nxt)
            app.save_user_config()
            app.query_one(DiffView).set_default_layout(nxt)
            app.notify(f"Default diff layout: {nxt}", severity="information")
        elif key == "behavior":
            confirm = not app.user_config.confirm_quit
            app.user_config = app.user_config.with_(confirm_quit=confirm)
            app.save_user_config()
            app.notify(
                f"Confirm on quit {'enabled' if confirm else 'disabled'}", severity="information"
            )
        else:
            return
        self._reshow()

    def action_selfcheck(self) -> None:
        """Run the never-leak self-check (Security section only)."""
        if self._current_key() != "security" or self._checking:
            return
        self._checking = True
        self._reshow()
        self.run_worker(self._run_selfcheck(), group="selfcheck", exclusive=True)

    async def _run_selfcheck(self) -> None:
        from comparo.adapters import doctor

        try:
            results = await asyncio.to_thread(doctor.run_selfcheck)
        finally:
            self._checking = False
        self._selfcheck = [(check.name, check.detail, check.ok) for check in results]
        self._reshow()
        passed = sum(1 for _, _, ok in self._selfcheck if ok)
        total = len(self._selfcheck)
        self.app.notify(
            f"redaction self-check — {passed}/{total} sinks masked the canary",
            title="Self-check",
            severity="information" if passed == total else "error",
        )


class _FilterInput(Input):
    """The filter's text input, but ``?`` opens help instead of typing a literal.

    A focused ``Input`` consumes every printable key (inserting it into the
    value) before any screen binding is consulted, so a plain ``question_mark``
    binding — even ``priority=True`` — never fires. Intercepting the key on the
    Input itself is the only reliable way to keep ``?`` help reachable here.
    """

    async def _on_key(self, event: Key) -> None:
        if event.key == "question_mark":
            event.prevent_default()
            event.stop()
            self.app.push_screen(HelpModal("filter"))
            return
        await super()._on_key(event)


class FilterModal(ModalScreen[None]):
    """A narrow overlay that live-filters a view (tree or tables) as you type."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel"),
        # A fallback for when focus is not on the Input; the Input itself
        # intercepts '?' (see _FilterInput) since it would otherwise eat the key.
        Binding("question_mark", "help", "help"),
    ]

    def action_help(self) -> None:
        """Show this overlay's key help."""
        self.app.push_screen(HelpModal("filter"))

    def __init__(
        self,
        target: "ExplorerView | RunView | ReportView",
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
            yield _FilterInput(placeholder=self._placeholder, id="filter-input")
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
        Binding("question_mark", "help", "help"),
    ]

    def action_help(self) -> None:
        """Show this overlay's key help."""
        self.app.push_screen(HelpModal("confirm"))

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
        Binding("q", "app.quit", "quit"),
        Binding("question_mark", "help", "help"),
    ]

    def action_help(self) -> None:
        """Show this overlay's key help."""
        self.app.push_screen(HelpModal("graph"))

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
        Binding("q", "app.quit", "quit"),
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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel"),
        Binding("question_mark", "help", "help"),
    ]

    def action_help(self) -> None:
        """Show this overlay's key help."""
        self.app.push_screen(HelpModal("picker"))

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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "cancel"),
        Binding("question_mark", "help", "help"),
    ]

    def action_help(self) -> None:
        """Show this overlay's key help."""
        self.app.push_screen(HelpModal("picker"))

    def __init__(self, cells: list[MatrixCell]) -> None:
        """Build the picker over a request's expanded matrix cells.

        Args:
            cells: Every matrix combination for the request.
        """
        super().__init__()
        self._cells = cells

    def compose(self) -> ComposeResult:
        """Yield the option list of matrix cases."""
        redact = _app_redact(self)
        with Vertical(id="picker-dialog", classes="modal"):
            yield OptionList(*(redact(cell.key) or "base (no matrix)" for cell in self._cells))

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
        Binding("q", "app.quit", "quit"),
        Binding("c", "copy", "copy"),
        Binding("question_mark", "help", "help"),
    ]

    def action_help(self) -> None:
        """Show this overlay's key help."""
        self.app.push_screen(HelpModal("curl"))

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
        case = _app_redact(self)(self._cell.key)
        dialog.border_title = "CURL" + (f" · {case}" if self._cell.key else "")
        dialog.border_subtitle = "c copy with real secrets · esc close"
        # DISPLAY masks $secret refs; the backstop also masks a declared secret a
        # matrix injects untainted into the body/headers of the previewed curl.
        curl = _app_redact(self)(self._curl(Sink.DISPLAY))
        self.query_one("#curl-content", Static).update(_bash(curl))

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
        Binding("question_mark", "help", "help"),
    ]

    def action_help(self) -> None:
        """Show this overlay's key help."""
        self.app.push_screen(HelpModal("matrix"))

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
        redact = _app_redact(self)
        options: list[tuple[Text, str, bool]] = []
        for matrix in self._matrices:
            matrix_id = matrix.metadata.id or matrix.metadata.name
            for index, value in enumerate(matrix.spec.values):
                prompt = Text.assemble(
                    (f"{matrix.metadata.name}  ", _AXIS), (redact(case_key(value)), _TEXT)
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


class ExecutionView(Vertical):
    """The Execution tab: launch → run → gate → cell → diff, all in one tab.

    A self-contained tab with an inner ``ContentSwitcher`` over five sub-views —
    the launch profile picker, the live running transition, the results overview
    (assertions ∧ diff gate), the per-cell drill-in, and the run's scoped body
    diff. Navigation stays inside the tab: pressing ``enter`` on a profile runs
    it in place, ``enter`` on a drifted cell drills in, ``d`` opens the diff.
    ``esc`` / ``backspace`` step back one sub-view; ``q`` always quits.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("space", "plan_toggle", "toggle"),
        Binding("t", "tags", "tags"),
        Binding("m", "mode", "mode"),
        Binding("d", "open_diff", "diff"),
        Binding("e", "report", "report"),
        Binding("s", "save", "save"),
        Binding("r", "rerun", "re-run"),
        Binding("v", "toggle_view", "unified/side"),
        Binding("i", "silence", "ignore field"),
        Binding("escape", "back", "back"),
        Binding("backspace", "back", "back"),
    ]

    def __init__(self, project: LoadedProject) -> None:
        """Build the execution view.

        Args:
            project: The project whose execution profiles are launched.
        """
        super().__init__(id="execution-view", classes="view")
        self.project = project
        self._profiles = sorted(
            (obj for obj in project.objects.values() if isinstance(obj, ExecutionProfile)),
            key=lambda profile: profile.metadata.id or profile.metadata.name,
        )
        self._profile: ExecutionProfile | None = None
        self._result: ExecutionResult | None = None
        self._record: ReportRecord | None = None
        self._drifted: list[CellOutcome] = []
        self._cell: CellOutcome | None = None
        #: the sub-view ``d`` was opened from, so ``esc`` returns there exactly
        #: (never a stale cell detail left over from an earlier drill).
        self._diff_origin = "exec-results"
        self._unified = True
        self._run_id: str | None = None
        # live-progress state for the running sub-view
        self._done = 0
        self._total = 0
        self._run_rows: list[_RunningRow] = []
        self._worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        """Yield the five execution sub-views behind an inner switcher."""
        with ContentSwitcher(initial="exec-launch", id="exec-switch"):
            with Horizontal(id="exec-launch"):
                with Vertical(id="exec-profiles", classes="panel"):
                    yield Static(id="exec-profiles-hint")
                    yield OptionList(id="exec-profile-list")
                with VerticalScroll(id="exec-setup", classes="panel"):
                    yield Static(id="exec-setup-content")
            with VerticalScroll(id="exec-running", classes="panel"):
                yield Static(id="exec-running-content")
            with VerticalScroll(id="exec-results"):
                yield Static(id="exec-header", classes="panel")
                yield Static(id="exec-gate", classes="panel")
                with Vertical(id="exec-diff", classes="panel"):
                    yield Static(id="exec-diff-summary")
                    yield DataTable(id="exec-drift-table", cursor_type="row", show_header=True)
                    yield Static(id="exec-diff-legend")
            with Vertical(id="exec-cell"):
                yield Static(id="cell-header", classes="panel")
                with Horizontal(id="cell-cols"):
                    with Vertical(id="cell-assert"):
                        yield Static(id="cell-ledger", classes="panel")
                        yield Static(id="cell-assert-base", classes="panel")
                        yield Static(id="cell-assert-cand", classes="panel")
                        yield Static(id="cell-verdict", classes="panel")
                    with VerticalScroll(id="cell-body", classes="panel hero"):
                        yield Static(id="cell-body-content")
            with VerticalScroll(id="exec-diff-screen", classes="panel"):
                yield Static(id="exec-diff-screen-content")

    def on_mount(self) -> None:
        """Populate the profile picker."""
        self._populate_profiles()

    # ── navigation ────────────────────────────────────────────────────────────
    def _current_view(self) -> str:
        return self.query_one("#exec-switch", ContentSwitcher).current or "exec-launch"

    def _show(self, view: str) -> None:
        self.query_one("#exec-switch", ContentSwitcher).current = view
        self._focus_view(view)
        self._update_nav()
        self.refresh_footer()

    def _focus_view(self, view: str) -> None:
        target = {
            "exec-launch": "#exec-profile-list",
            "exec-running": "#exec-running",
            "exec-results": "#exec-drift-table",
            "exec-cell": "#cell-body",
            "exec-diff-screen": "#exec-diff-screen",
        }[view]
        with contextlib.suppress(NoMatches):
            self.query_one(target).focus()

    def refresh_screen(self) -> None:
        """Re-render the current sub-view when the tab is (re)activated."""
        view = self._current_view()
        if view == "exec-launch":
            self._render_launch()
        self._focus_view(view)
        self._update_nav()
        self.refresh_footer()

    def _update_nav(self) -> None:
        app = cast("ComparoApp", self.app)
        if app.query_one(NavBar).active != "execution":
            return
        app.query_one(NavBar).set_status(self._nav_status())

    def _nav_status(self) -> str:
        view = self._current_view()
        if view == "exec-running":
            return f"[{_WARN}]running [/][bold {_ACCENT}]{_run_label(self._run_id)}[/]"
        result = self._result
        if view == "exec-results" and result is not None:
            gate = "PASS" if result.passed else "FAIL"
            colour = _SAME if result.passed else _DRIFT
            code = 0 if result.passed else 1
            return f"[{_DIM}]gate [/][bold {colour}]{gate}[/][{_DIM}] · exit {code}[/]"
        if view == "exec-cell" and self._cell is not None:
            drift = self._cell.diff is not None and self._cell.diff.drifted
            verdict = f"[{_DRIFT}]✗ drift[/]" if drift else f"[{_SAME}]✓ same[/]"
            key = _app_redact(self)(self._cell.cell_key) or "base"
            return f"[{_DIM}]cell [/][{_AXIS}]{key}[/][{_DIM}] · [/]{verdict}"
        if view == "exec-diff-screen":
            return f"[{_DIM}]diff [/][{_DRIFT}]{len(self._drifted)} drifted cells[/]"
        # launch — echo the candidate env and the project scope (mockup A/1)
        redact = _app_redact(self)
        reqs = sum(1 for obj in self.project.objects.values() if isinstance(obj, Request))
        envs = sum(1 for obj in self.project.objects.values() if isinstance(obj, Environment))
        profile = self._selected_profile()
        target = "—"
        if profile is not None:
            baseline, candidate = _exec_env_names(self.project, profile)
            target = redact(candidate or baseline)
        return f"[{_TEXT_HI}]{target}[/][{_DIM}]  ·  {reqs} reqs · {envs} envs[/]"

    def footer_keys(self) -> tuple[tuple[str, str], ...]:
        """The footer hints for the active sub-view."""
        return {
            "exec-launch": _EXEC_LAUNCH_KEYS,
            "exec-running": _EXEC_RUNNING_KEYS,
            "exec-results": _EXEC_RESULTS_KEYS,
            "exec-cell": _EXEC_CELL_KEYS,
            "exec-diff-screen": _EXEC_DIFF_KEYS,
        }[self._current_view()]

    def footer_context(self) -> str:
        """The right-hand footer caption for the active sub-view."""
        view = self._current_view()
        if view == "exec-launch":
            profile = self._selected_profile()
            return (
                profile.metadata.id or profile.metadata.name if profile is not None else ".reports"
            )
        if view == "exec-running":
            return f"[{_DIM}]{_run_label(self._run_id)} · in flight[/]"
        result = self._result
        if view == "exec-results" and result is not None:
            colour = _SAME if result.passed else _DRIFT
            code = 0 if result.passed else 1
            saved = f"[{_DIM}] · saved[/]" if self._record is not None else ""
            return f"[bold {colour}]exit {code}[/]{saved}"
        if view == "exec-cell" and self._cell is not None:
            crumb = (
                self._cell.diff.request.metadata.name
                if self._cell.diff is not None
                else _req_short(self._cell.request_id)
            )
            key = _app_redact(self)(self._cell.cell_key)
            return f"{crumb} › {key}" if key else crumb
        if view == "exec-diff-screen":
            return f"[{_DIM}]scoped to {_run_label(self._run_id)} · stays in tab[/]"
        return ".reports"

    def refresh_footer(self) -> None:
        """Re-show the status bar for the active sub-view."""
        if cast("ComparoApp", self.app).query_one(NavBar).active != "execution":
            return
        self.app.query_one(StatusBar).show(self.footer_keys(), self.footer_context())

    # ── launch (step 1) ───────────────────────────────────────────────────────
    def _populate_profiles(self) -> None:
        options = self.query_one("#exec-profile-list", OptionList)
        options.clear_options()
        redact = _app_redact(self)
        for index, profile in enumerate(self._profiles):
            options.add_option(_exec_profile_card(self.project, profile, redact, caret=index == 0))
        self.query_one("#exec-profiles").border_title = Text.from_markup(f"[{_ACCENT}]PROFILES[/]")
        self.query_one("#exec-profiles-hint", Static).update(_exec_profiles_hint())
        self.query_one(
            "#exec-profiles"
        ).border_subtitle = (
            f"{len(self._profiles)} profile{'' if len(self._profiles) == 1 else 's'}"
        )
        if self._profiles:
            options.highlighted = 0

    def _sync_profile_caret(self, highlighted: int) -> None:
        """Move the accent ``▸`` caret to the highlighted profile card."""
        options = self.query_one("#exec-profile-list", OptionList)
        redact = _app_redact(self)
        for index, profile in enumerate(self._profiles):
            options.replace_option_prompt_at_index(
                index,
                _exec_profile_card(self.project, profile, redact, caret=index == highlighted),
            )

    def _render_launch(self) -> None:
        self._populate_profiles()
        self._render_setup()

    def _selected_profile(self) -> ExecutionProfile | None:
        options = self.query_one("#exec-profile-list", OptionList) if self.is_mounted else None
        if options is None or options.highlighted is None:
            return self._profiles[0] if self._profiles else None
        index = options.highlighted
        return self._profiles[index] if 0 <= index < len(self._profiles) else None

    def _render_setup(self) -> None:
        profile = self._selected_profile()
        setup = self.query_one("#exec-setup")
        if profile is None:
            setup.border_title = "SETUP"
            self.query_one("#exec-setup-content", Static).update(
                Text("This project has no execution profiles.", style=_DIM)
            )
            return
        setup.border_title = Text.from_markup(f"SETUP [{_DIM}]· {profile.metadata.name}[/]")
        setup.border_subtitle = "space toggle"
        self.query_one("#exec-setup-content", Static).update(
            _exec_setup(self.project, profile, _app_redact(self))
        )

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Update the SETUP panel for the highlighted profile."""
        if event.option_list.id == "exec-profile-list":
            self._sync_profile_caret(event.option_index)
            self._render_setup()
            self.refresh_footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter on a profile launches it in place."""
        if event.option_list.id == "exec-profile-list":
            profile = self._selected_profile()
            if profile is not None:
                self.launch(profile)

    def action_plan_toggle(self) -> None:
        """``space`` — reserved for tuning the plan (matrix/select) before a launch."""
        if self._current_view() == "exec-launch":
            self.app.notify(
                "Tune selection in the profile's YAML — the plan preview reflects it live.",
                severity="information",
            )

    def action_tags(self) -> None:
        """``t`` — the launch tag filter (defined by the profile's select clause)."""
        if self._current_view() == "exec-launch":
            self.app.notify(
                "Tags are set by the ExecutionProfile's select clause.",
                severity="information",
            )

    def action_mode(self) -> None:
        """``m`` — the assert/diff/both mode (defined by the profile's check clause)."""
        if self._current_view() == "exec-launch":
            self.app.notify(
                "Mode is set by the ExecutionProfile's check clause (assert / diff / both).",
                severity="information",
            )

    # ── running (step 2) ──────────────────────────────────────────────────────
    def launch(self, profile: ExecutionProfile) -> None:
        """Run *profile* in place: show the running transition, then the results."""
        self._profile = profile
        self._result = None
        self._record = None
        # A re-run starts from a clean slate: drop any cell drilled into on the
        # previous run so a stale CellOutcome can never be rendered against the
        # new result (its drift index is rebuilt by _show_results).
        self._cell = None
        self._drifted = []
        self._run_id = uuid4().hex[:4]
        self._done = 0
        self._total = 0
        self._run_rows = []
        self.query_one("#exec-running").border_title = Text.from_markup(
            f"RUNNING [{_DIM}]· {profile.metadata.name}[/]"
        )
        self.query_one(
            "#exec-running"
        ).border_subtitle = "cancel with esc — nothing is written until it finishes"
        self._render_running()
        self._show("exec-running")
        self._worker = self.run_worker(
            self._run(self.project, profile), exclusive=True, group="execution"
        )

    def update_progress(self, event: ExecutionProgress) -> None:
        """Advance the running table for one queued/start/finish tick from the engine."""
        redact = _app_redact(self)
        self._total = event.total
        while len(self._run_rows) < event.total:
            self._run_rows.append(_RunningRow(request=""))
        if 0 <= event.index < len(self._run_rows):
            self._run_rows[event.index] = _running_row_from_progress(event, redact)
        self._done = sum(1 for row in self._run_rows if row.state == "done")
        if self.is_mounted:
            self._render_running()

    def _render_running(self) -> None:
        label = self._profile.metadata.name if self._profile is not None else "execution"
        base, cand = "baseline", "candidate"
        if self._profile is not None:
            names = _exec_env_names(self.project, self._profile)
            base, cand = names[0], names[1] or "candidate"
        self.query_one("#exec-running-content", Static).update(
            _running_table(
                label,
                self._done,
                self._total,
                self._run_rows,
                base_name=base,
                cand_name=cand,
                exec_mode=True,
            )
        )

    async def _run(self, project: LoadedProject, profile: ExecutionProfile) -> None:
        from comparo.adapters.httpx_client import HttpxClient

        client = HttpxClient()
        candidate_client = HttpxClient()
        try:
            result = await run_execution(
                project,
                profile,
                client,
                candidate_client,
                on_progress=self.update_progress,
            )
        except EnvironmentSelectionError as error:
            self.app.notify(str(error), title="Execution failed", severity="error")
            self._show("exec-launch")
            return
        finally:
            await client.aclose()
            await candidate_client.aclose()
        self._result = result
        self._record = None  # results are archived only when you press s
        self._show_results()
        self.app.notify("Run finished — press s to archive it as a report", title="Execution")

    # ── results (step 3) ──────────────────────────────────────────────────────
    def show_result(
        self,
        result: ExecutionResult,
        profile: ExecutionProfile,
        record: "ReportRecord | None" = None,
    ) -> None:
        """Adopt a finished execution and open the results overview (used by tests)."""
        self._profile = profile
        self._result = result
        self._record = record
        self._run_id = self._run_id or (
            record.metadata.id if record is not None else uuid4().hex[:4]
        )
        self._show_results()

    def _show_results(self) -> None:
        result = self._result
        if result is None or self._profile is None:
            return
        self._cell = None  # leaving any cell detail — do not keep a stale selection
        self._drifted = [
            outcome
            for outcome in result.outcomes
            if outcome.error is not None or (outcome.diff is not None and outcome.diff.drifted)
        ]
        redact = _app_redact(self)
        # The banner leads with the gate verdict; its border echoes pass/fail.
        banner = self.query_one("#exec-header")
        banner.set_class(result.passed, "pass")
        banner.set_class(not result.passed, "fail")
        banner.border_title = Text.from_markup(f"GATE [{_DIM}]· assertions ∧ diff[/]")
        banner.border_subtitle = f"{self._profile.metadata.id or self._profile.metadata.name}"
        self.query_one("#exec-header", Static).update(_exec_header(self._profile, result, redact))
        self._render_results_gate()
        self._render_results_diff()
        self._show("exec-results")
        if result.outcomes:  # focus the per-cell table so ↑↓/⏎ work immediately
            self.query_one("#exec-drift-table", DataTable).focus()

    def _render_results_diff(self) -> None:
        result = self._result
        if result is None:
            return
        redact = _app_redact(self)
        self.query_one("#exec-diff").border_title = Text.from_markup(
            f"DIFF [{_DIM}]· {result.baseline} ⇄ {result.candidate or '—'}[/]"
        )
        self.query_one(
            "#exec-diff"
        ).border_subtitle = f"{result.drift} drift · {len(result.outcomes)} cells"
        self.query_one("#exec-diff-summary", Static).update(_exec_diff_summary(result, redact))
        table = self.query_one("#exec-drift-table", DataTable)
        table.clear(columns=True)
        table.add_column("CELL", key="cell")
        table.add_column("BASELINE", key="b", width=10)
        table.add_column("CANDIDATE", key="c", width=10)
        table.add_column("DIFF", key="diff", width=10)
        table.add_column("VERDICT", key="verdict")
        # Every cell, not just the drifted ones — a per-cell row (both sides' asserts ·
        # diff · verdict-with-reason) so the whole run reads at a glance; Enter drills in.
        for index, outcome in enumerate(result.outcomes):
            label = Text(_req_short(outcome.request_id), style=f"bold {_TEXT_HI}")
            if outcome.cell_key:
                label.append(f" · {redact(outcome.cell_key)}", style=_AXIS)
            table.add_row(*_exec_triplet(outcome, label), key=f"cell::{index}")
        self.query_one("#exec-diff-legend", Static).update(_exec_diff_legend(result, redact))

    def _render_results_gate(self) -> None:
        result = self._result
        if result is None:
            return
        redact = _app_redact(self)
        gate = self.query_one("#exec-gate")
        gate.set_class(result.passed, "pass")
        gate.set_class(not result.passed, "fail")
        gate.border_title = Text.from_markup(
            f"GATE COMPOSITION [{_DIM}]· baseline ∧ candidate ∧ diff[/]"
        )
        self.query_one("#exec-gate", Static).update(_gate_composition(result, redact))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a drifted cell drills in (the table eats enter first)."""
        if event.data_table.id == "exec-drift-table":
            self._drill()

    def _drill(self) -> None:
        result = self._result
        table = self.query_one("#exec-drift-table", DataTable)
        if result is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if key is None or not key.startswith("cell::"):
            return
        self._cell = result.outcomes[int(key.removeprefix("cell::"))]
        self._unified = True
        self._render_cell()
        self._show("exec-cell")

    def action_rerun(self) -> None:
        """``r`` — re-run the same execution profile from the results overview."""
        if self._current_view() == "exec-results" and self._profile is not None:
            self.launch(self._profile)

    def action_save(self) -> None:
        """``s`` — archive this execution's results as a saved report, then toast."""
        if self._current_view() != "exec-results" or self._result is None:
            return
        if self._record is not None:
            self.app.notify(
                f"Already archived as report {self._record.metadata.id}", title="Report"
            )
            self.refresh_footer()
            return
        if self._profile is None:
            return
        record = cast("ComparoApp", self.app).save_execution_report(self._result, self._profile)
        if record is None:
            self.app.notify("No project archive to save into", severity="warning")
            return
        self._record = record
        self.refresh_footer()
        self.app.notify(f"Saved report {record.metadata.id} to the archive", title="Report")

    def action_report(self) -> None:
        """``e`` — point at this run's saved report without leaving the tab.

        If the run has been archived (``s``), this names the saved record so it can
        be reopened from the Report tab (5); otherwise it nudges you to save first —
        never jumping to another tab (tab self-containment).
        """
        if self._current_view() != "exec-results":
            return
        if self._record is None:
            self.app.notify(
                "Press s to archive this run first, then browse it in the Report tab (5).",
                title="Report",
            )
            return
        self.app.notify(
            f"This run is archived as report {self._record.metadata.id} — "
            "browse it in the Report tab (5).",
            title="Report",
        )

    # ── cell (step 4) ─────────────────────────────────────────────────────────
    def _render_cell(self) -> None:
        outcome = self._cell
        if outcome is None or self._result is None:
            return
        result = self._result
        redact = _app_redact(self)
        crumb = Text()
        crumb.append("▸ ", style=_ACCENT)
        crumb.append(self._profile.metadata.name if self._profile else "", style=f"bold {_TEXT_HI}")
        crumb.append(" › ", style=_DIM)
        req_name = (
            outcome.diff.request.metadata.name
            if outcome.diff is not None
            else _req_short(outcome.request_id)
        )
        crumb.append(req_name, style=f"bold {_TEXT_HI}")
        if outcome.cell_key:
            crumb.append(" › ", style=_DIM)
            crumb.append(redact(outcome.cell_key), style=_AXIS)
        request = outcome.diff.request if outcome.diff is not None else None
        if request is not None:
            method = request.spec.request.method
            endpoint = redact(request.spec.request.endpoint)
            crumb.append(f"    {method} {endpoint}", style=_DIM)
        crumb.append("    ", style=_DIM)
        crumb.append(result.baseline, style=_TEXT_HI)
        crumb.append(" ● ⇄ ", style=_SAME)
        crumb.append(result.candidate or "—", style=_TEXT_HI)
        crumb.append(" ●", style=_SAME)
        drifted = outcome.diff is not None and outcome.diff.drifted
        crumb.append("    ✗ drift" if drifted else "    ✓ same", style=_DRIFT if drifted else _SAME)
        self.query_one("#cell-header", Static).update(crumb)
        self.query_one("#cell-header").border_title = "CELL"
        ledger = _executions_ledger(outcome.baseline, outcome.candidate)
        ledger_panel = self.query_one("#cell-ledger", Static)
        ledger_panel.border_title = Text.from_markup(f"CALL LEDGER [{_DIM}]· per side[/]")
        ledger_panel.display = ledger is not None
        ledger_panel.update(ledger or Text())
        for ident, env, results in (
            ("#cell-assert-base", result.baseline, outcome.baseline_assertions),
            ("#cell-assert-cand", result.candidate or "—", outcome.candidate_assertions),
        ):
            panel = self.query_one(ident, Static)
            panel.border_title = Text.from_markup(f"ASSERTIONS [{_DIM}]·[/] {env}")
            panel.border_subtitle = _assert_count_text(_assert_tally(results))
            panel.update(_exec_assert_body([(outcome.request_id, r) for r in results], redact))
        self.query_one("#cell-verdict", Static).update(_cell_verdict(outcome, redact))
        self.query_one("#cell-verdict").border_title = "VERDICT"
        self._render_cell_body()

    def _render_cell_body(self) -> None:
        outcome = self._cell
        if outcome is None or self._result is None:
            return
        result = self._result
        body = self.query_one("#cell-body")
        req_name = (
            outcome.diff.request.metadata.name
            if outcome.diff is not None
            else _req_short(outcome.request_id)
        )
        body.border_title = Text.from_markup(f"BODY DIFF [{_DIM}]· {req_name}[/]")
        sub = "unified · v for side-by-side" if self._unified else "side-by-side · v for unified"
        body.border_subtitle = Text.from_markup(f"[{_DIM}]{sub}[/]")
        names = (result.baseline, result.candidate or "candidate")
        redact = _app_redact(self)
        content = self.query_one("#cell-body-content", Static)
        if outcome.error is not None and outcome.diff is not None:
            content.update(_diff_error_view(outcome.diff, None, names=names, redact=redact))
        elif outcome.diff is not None:
            entries = [(outcome.diff, field) for field in outcome.diff.drifts]
            path = entries[0][1].path if entries else "$"
            well = _diff_body_view(
                (path, entries),
                None,
                unified=self._unified,
                names=names,
                redact=redact,
                chrome=False,
            )
            # Step-4 cell view: the well, a compact two-item legend, and the short
            # insight — the v/i affordances already live in the footer + subtitle.
            legend = Text("\n")
            legend.append("− ", style=f"bold {_DRIFT}")
            legend.append("baseline ", style=_DIM)
            legend.append(names[0], style=_TEXT)
            legend.append("    + ", style=f"bold {_SAME}")
            legend.append("candidate ", style=_DIM)
            legend.append(names[1], style=_TEXT)
            # Don't assert the drift is the service's — the outbound can differ
            # across environments (per-env variables/secrets), which would explain
            # some drift. Point at the Diff tab's outbound view to confirm.
            insight = Text(
                "\nsame request replayed against both — open the Diff tab (o) to confirm "
                "the outbound matches before blaming the service",
                style=_DIM,
            )
            content.update(Group(well, legend, insight))
        else:
            content.update(Text("no diff computed for this cell", style=_DIM))

    def action_toggle_view(self) -> None:
        """``v`` — flip the body diff(s) between unified and side-by-side."""
        view = self._current_view()
        if view == "exec-cell":
            self._unified = not self._unified
            self._render_cell_body()
        elif view == "exec-diff-screen":
            self._unified = not self._unified
            self._render_exec_diff()

    def action_silence(self) -> None:
        """``i`` — silence the drilled cell's drifted field (after confirmation)."""
        if self._current_view() != "exec-cell":
            return
        outcome = self._cell
        if outcome is None or outcome.diff is None or not outcome.diff.drifts:
            self.app.notify("No drifted field here to silence", severity="information")
            return
        project = self.project
        profile = profile_for(project, outcome.diff.request)
        if profile is None or profile.metadata.id is None:
            self.app.notify("No committed diff profile to write to", severity="warning")
            return
        path = outcome.diff.drifts[0].path
        profile_id = profile.metadata.id
        file = profile_path(project, profile_id)
        redact = _app_redact(self)
        prompt = Text()
        prompt.append("Write an ignore rule for ", style=_TEXT)
        prompt.append(redact(path), style=f"bold {_TEXT_HI}")
        prompt.append(f"\ninto {profile.metadata.name} ({profile_id})\n\n", style=_TEXT)
        if file is not None:
            prompt.append("→ ", style=_DIM)
            prompt.append(str(file), style=_ACCENT)
        prompt.append("\n\ncomparo never edits your files without asking.", style=_DIM)
        self.app.push_screen(
            ConfirmModal(prompt, title="SILENCE FIELD"),
            lambda ok: self._do_silence(project, profile_id, path) if ok else None,
        )

    def _do_silence(self, project: LoadedProject, profile_id: str, path: str) -> None:
        if _app_redact(self)(path) != path:
            self.app.notify(
                "That field's path contains a secret value — writing it into a diff "
                "profile would commit the secret to a tracked file. Nothing written.",
                title="Refused to silence",
                severity="error",
            )
            return
        try:
            file = silence(project, profile_id, path)
        except TriageError as error:
            self.app.notify(str(error), title="Could not silence", severity="error")
            return
        self.app.notify(f"Ignoring {path} — wrote {file}. Re-run to confirm.", title="Silenced")

    # ── in-flow diff (step 5) ─────────────────────────────────────────────────
    def action_open_diff(self) -> None:
        """``d`` — open the run's drift as a body diff, without leaving the tab.

        Remembers which sub-view it was opened from so ``esc`` returns there
        exactly — the gate overview or the cell you drilled into.
        """
        view = self._current_view()
        if view not in ("exec-results", "exec-cell"):
            return
        if not self._drifted:
            self.app.notify("No drift to diff in this run", severity="information")
            return
        self._diff_origin = view
        self._unified = True
        self._render_exec_diff()
        self._show("exec-diff-screen")

    def _render_exec_diff(self) -> None:
        result = self._result
        if result is None:
            return
        dialog = self.query_one("#exec-diff-screen")
        dialog.border_title = Text.from_markup(
            f"EXECUTION DIFF [{_DIM}]· {self._profile.metadata.name if self._profile else ''}[/]"
        )
        back = "the cell" if self._diff_origin == "exec-cell" else "the gate"
        dialog.border_subtitle = f"v unified/side · esc → {back} · never leaves the Execution tab"
        self.query_one("#exec-diff-screen-content", Static).update(
            _exec_stacked_diff(
                self._drifted,
                result.baseline,
                result.candidate,
                unified=self._unified,
                redact=_app_redact(self),
            )
        )

    # ── back ──────────────────────────────────────────────────────────────────
    def action_back(self) -> None:
        """``esc`` / ``⌫`` — step back one sub-view (never quit)."""
        view = self._current_view()
        if view == "exec-running":
            if self._worker is not None:
                self._worker.cancel()
            self._show("exec-launch")
        elif view == "exec-results":
            self._show("exec-launch")
        elif view == "exec-cell":
            self._show_results()
        elif view == "exec-diff-screen":
            # Return to whichever sub-view opened the diff, not a stale selection.
            if self._diff_origin == "exec-cell" and self._cell is not None:
                self._render_cell()
                self._show("exec-cell")
            else:
                self._show_results()


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
        Binding("4,apostrophe", "screen('execution')", "Execution"),
        Binding("5,left_parenthesis", "screen('report')", "Report"),
        # 6 needs shift on French AZERTY; bind the unshifted char too — `minus` on a
        # PC keyboard, `section_sign` (§) on an Apple keyboard — so it works on a MacBook.
        Binding("6,minus,section_sign", "screen('settings')", "Settings"),
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
        #: Persisted app preferences (theme, opt-in update check, diff default…).
        self.user_config: UserConfig = userconfig.load()
        #: Guards the confirm-on-quit dialog against re-entrancy (a second ``q``).
        self._quitting = False

    @cached_property
    def redactor(self) -> Redactor:
        """The project's secret-redactor, built once and reused across renders.

        The app holds one project for its whole lifetime (``_reload`` re-checks a
        failed load but never swaps a live project), so this is safe to cache —
        every view redacts through the same instance instead of rebuilding it, and
        re-reading every declared secret file, on each render frame. Only valid
        once a project is loaded; use ``_app_redact`` at sites that tolerate none.
        """
        assert self.project is not None
        return Redactor.for_project(self.project)

    def _handle_exception(self, error: Exception) -> None:
        """On an unhandled crash, show a redacted report with a prefilled issue.

        Disabled while ``COMPARO_DEV`` is set, so developers still get the raw
        Textual traceback. A failure inside this handler falls back to the default
        so the crash handler can never mask the crash it is reporting.
        """
        if os.environ.get("COMPARO_DEV"):
            super()._handle_exception(error)
            return
        try:
            # Exit through the public API — a redacted crash report as the exit
            # message and a non-zero return code — rather than reaching into Textual's
            # private crash bookkeeping (_return_code / _exception / _exception_event),
            # which could be renamed between versions (M-10). Not re-raising is
            # deliberate: we show the redacted report, never the raw traceback.
            self.exit(return_code=1, message=_crash_report(error, _app_redact(self)))
        except Exception:
            # Never let the reporter hide the real crash it is reporting.
            super()._handle_exception(error)

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
                yield ExecutionView(project)
                yield ReportView(project)
                yield SettingsView(project)
        yield StatusBar()

    def on_mount(self) -> None:
        """Register the theme, apply saved prefs, and focus the landing screen."""
        self.register_theme(COMPARO_INK)
        self.theme = (
            self.user_config.theme
            if self.user_config.theme in self.available_themes
            else "comparo-ink"
        )
        self._status("error" if self.error is not None else "explorer")
        # Every view's on_mount ran already (children mount before the app); the last
        # one to focus would otherwise win, so claim focus for the Explorer landing tree.
        if self.error is None:
            # Apply the saved default body-diff layout before the Diff screen renders.
            self.query_one(DiffView).set_default_layout(self.user_config.diff_view)
            self.query_one(ExplorerView).refresh_screen()
            landing = self.user_config.default_tab
            if landing in _TAB_NAMES and landing != "explorer":
                self.action_screen(landing)
            # Opt-in, throttled version check → a toast when a newer release is out.
            if self.user_config.update_check:
                self.run_worker(self._version_check(), group="update-check")

    def action_quit(self) -> None:  # type: ignore[override]
        """Quit the app — ``q`` always quits, optionally behind a confirmation.

        Confirm-on-quit is an opt-in Behavior pref; it never turns ``q`` into
        "back/close" (the hard rule), only asks before the app exits.
        """
        if self.user_config.confirm_quit and self.error is None and not self._quitting:
            self._quitting = True

            def done(ok: bool | None) -> None:
                self._quitting = False
                if ok:
                    self.exit()

            self.push_screen(ConfirmModal(Text("Quit comparo?"), title="QUIT"), done)
        else:
            # A second q while the prompt is open (or no confirmation) exits directly —
            # q always quits, and never stacks a second dialog.
            self.exit()

    def save_user_config(self) -> None:
        """Persist the current preferences to ``~/.config/comparo/config.toml``."""
        userconfig.save(self.user_config)

    def check_for_updates_now(self) -> None:
        """Force a version check now (e.g. just after the toggle is enabled)."""
        self.run_worker(self._version_check(force=True), group="update-check")

    async def _version_check(self, *, force: bool = False) -> None:
        """Ask PyPI whether a newer comparo exists; toast if so. Throttled to once a day."""
        today = datetime.now().strftime("%Y-%m-%d")
        if not force and self.user_config.update_last_checked == today:
            seen = self.user_config.update_latest_seen
            if seen and updates_adapter.is_newer(seen, __version__):
                self._notify_update(seen)
            return
        latest = await updates_adapter.check_latest(__version__)
        self.user_config = self.user_config.with_(
            update_last_checked=today, update_latest_seen=latest or ""
        )
        userconfig.save(self.user_config)
        if latest:
            self._notify_update(latest)

    def _notify_update(self, latest: str) -> None:
        self.notify(
            f"comparo {latest} is available — you have {__version__}."
            "\nUpgrade: pipx upgrade comparo",
            title="Update available",
            severity="information",
            timeout=10,
        )

    def action_screen(self, name: str) -> None:
        """Switch to a named screen.

        Args:
            name: The screen id (``explorer``, ``diff``, …).
        """
        if self.error is not None:
            return
        self.query_one("#content", ContentSwitcher).current = f"{name}-view"
        self.query_one(NavBar).active = name
        # Reset the nav status to the project/env default; a tab that owns a richer
        # status (Execution, Report) re-sets it inside its refresh_screen.
        self.query_one(NavBar).set_status(self._nav_status())
        view = self.query_one(f"#{name}-view")
        if isinstance(
            view, (ExplorerView, RunView, DiffView, ExecutionView, ReportView, SettingsView)
        ):
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
        """Open the matrix-case picker on the Run or Diff screen."""
        if self.error is not None:
            return
        active = self.query_one(NavBar).active
        if active == "run":
            self.query_one(RunView).open_case_picker()
        elif active == "diff":
            self.query_one(DiffView).open_case_picker()

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
        """Open the reference-graph overlay (Explorer only, where ``g`` is shown)."""
        if self.error is not None or self.project is None:
            return
        if self.query_one(NavBar).active != "explorer":
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
        """``r`` re-checks a failed project, toggles Explorer raw, or reloads Report."""
        if self.error is not None:
            self._reload()
            return
        active = self.query_one(NavBar).active
        if active == "explorer":
            self.query_one(ExplorerView).toggle_raw()
        elif active == "report":
            # Reload works even when the archive is empty and the table is unfocused,
            # so a run saved to disk while Report is open surfaces on 'r'.
            self.query_one(ReportView).action_reload()

    def set_default_environment(self, environment: Environment) -> None:
        """Adopt *environment* as the default requests resolve against.

        Args:
            environment: The environment to make default.
        """
        self.environment = environment
        self.query_one(ExplorerView).set_default(environment)
        self.query_one(NavBar).set_status(self._nav_status())

    def launch_execution(self, profile: ExecutionProfile) -> None:
        """Switch to the Execution tab and run *profile* in place.

        Launching from the Explorer opens the self-contained Execution tab and
        starts the run there (the running transition, then the results), rather
        than pushing a modal.

        Args:
            profile: The execution profile to run.
        """
        if self.project is None:
            return
        self.action_screen("execution")
        self.query_one(ExecutionView).launch(profile)

    def archive_directory(self) -> Path | None:
        """The ``<data>/.reports`` directory this project's runs are saved to."""
        if self.project is None:
            return None
        manifest = self.project.project
        data = manifest.spec.data if manifest else None
        report_config = manifest.spec.report if manifest else None
        return archive_dir(self.project.root, data, report_config)

    def _report_retention(self) -> int | None:
        """How many saved reports to keep — ``spec.report.retention`` or unlimited."""
        manifest = self.project.project if self.project is not None else None
        config = manifest.spec.report if manifest is not None else None
        return config.retention if config is not None else None

    def _record_env(self) -> tuple[str, str, str, str | None, int]:
        """Common ``(id, created, tool, project, concurrency)`` for a saved record."""
        manifest = self.project.project if self.project is not None else None
        project_name = manifest.metadata.name if manifest is not None else None
        concurrency = run_settings(self.project)[0] if self.project is not None else 4
        return (
            uuid4().hex[:6],
            datetime.now(UTC).isoformat(timespec="seconds"),
            f"comparo {__version__}",
            project_name,
            concurrency,
        )

    def execution_record(
        self, result: ExecutionResult, profile: ExecutionProfile
    ) -> ReportRecord | None:
        """Build a redacted report record for an execution, without saving it."""
        if self.project is None:
            return None
        record_id, created, tool, project, concurrency = self._record_env()
        return record_from_execution(
            profile,
            result,
            record_id=record_id,
            created=created,
            tool=tool,
            project=project,
            concurrency=concurrency,
            redact=_app_redact(self),
        )

    def save_execution_report(
        self, result: ExecutionResult, profile: ExecutionProfile
    ) -> ReportRecord | None:
        """Archive an execution result; returns the saved record, or ``None``."""
        directory = self.archive_directory()
        record = self.execution_record(result, profile)
        if directory is None or record is None:
            return None
        try:
            save_record(directory, record, keep=self._report_retention())
        except OSError as error:
            self.notify(str(error), title="Could not save report", severity="error")
            return None
        return record

    def export_record_markdown(self, record: ReplayRecord) -> None:
        """Write a Markdown summary of *record* to the project's reports output dir."""
        if self.project is None:
            return
        manifest = self.project.project
        config = manifest.spec.report if manifest else None
        output_name = config.output if config is not None else None
        output = self.project.root / (output_name if isinstance(output_name, str) else "reports")
        try:
            output.mkdir(parents=True, exist_ok=True)
            path = output / f"report-{record.id}.md"
            path.write_text(_record_markdown(record), encoding="utf-8")
        except OSError as error:
            self.notify(str(error), title="Export failed", severity="error")
            return
        self.notify(f"Wrote {path.name}", title="Exported")

    def save_diff_report(
        self, baseline: Environment, candidate: Environment, diffs: list[CellDiff]
    ) -> ReportRecord | None:
        """Archive an ad-hoc diff run; returns the saved record, or ``None``."""
        directory = self.archive_directory()
        if directory is None or self.project is None:
            return None
        record_id, created, tool, project, concurrency = self._record_env()
        record = record_from_diff(
            baseline,
            candidate,
            diffs,
            record_id=record_id,
            created=created,
            tool=tool,
            project=project,
            concurrency=concurrency,
            redact=_app_redact(self),
        )
        try:
            save_record(directory, record, keep=self._report_retention())
        except OSError as error:
            self.notify(str(error), title="Could not save report", severity="error")
            return None
        return record

    def save_run_report(
        self, environment: Environment, cells: list[tuple[Execution, list[AssertionResult]]]
    ) -> ReportRecord | None:
        """Archive a single-environment run as an assertions report; returns it or ``None``."""
        directory = self.archive_directory()
        if directory is None or self.project is None:
            return None
        record_id, created, tool, project, concurrency = self._record_env()
        record = record_from_run(
            environment,
            cells,
            record_id=record_id,
            created=created,
            tool=tool,
            project=project,
            concurrency=concurrency,
            redact=_app_redact(self),
        )
        try:
            save_record(directory, record, keep=self._report_retention())
        except OSError as error:
            self.notify(str(error), title="Could not save report", severity="error")
            return None
        return record

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
        if screen == "diff":
            self.query_one(DiffView).refresh_footer()
            return
        if screen == "execution":
            self.query_one(ExecutionView).refresh_footer()
            return
        if screen == "report":
            self.query_one(ReportView).refresh_footer()
            return
        keys, context = {
            "settings": (_SETTINGS_KEYS, "app settings"),
        }.get(screen, (_EXPLORER_KEYS, ""))
        self.query_one(StatusBar).show(keys, context)
