"""The comparo terminal UI.

Built to the comparo-ink design: a top nav bar of screen tabs, a full foldable
project tree on the Explorer, and rich per-object detail (the resolved outbound
request with a syntax-highlighted body, or the config of any other object). The
Diff screen carries the signature tri-state gutter. The core never depends on
this module.
"""

import asyncio
import contextlib
import json
import os
import traceback
from collections.abc import Callable
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar
from typing import Literal
from typing import NamedTuple
from typing import cast
from urllib.parse import urlencode
from uuid import uuid4

import msgspec
from rich.box import ROUNDED
from rich.cells import cell_len
from rich.console import Group
from rich.console import RenderableType
from rich.panel import Panel
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
from textual.css.query import NoMatches
from textual.events import Key
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
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
from comparo.core.archive import AssertionSummary
from comparo.core.archive import CellRecord
from comparo.core.archive import ReportRecord
from comparo.core.archive import RequestBreakdown
from comparo.core.archive import archive_dir
from comparo.core.archive import list_records
from comparo.core.archive import record_from_diff
from comparo.core.archive import record_from_execution
from comparo.core.archive import record_from_run
from comparo.core.archive import save_record
from comparo.core.assertions import AssertionResult
from comparo.core.checks import Check
from comparo.core.checks import passed as checks_passed
from comparo.core.checks import run_checks
from comparo.core.compare import CellDiff
from comparo.core.compare import compare_cell
from comparo.core.compare import profile_for
from comparo.core.curl import to_curl
from comparo.core.diagnostics import Diagnostic
from comparo.core.diagnostics import LoadError
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execute import execute_request
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionProgress
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
from comparo.core.models import AssertionProfile
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.models import Header
from comparo.core.models import Instance
from comparo.core.models import Matrix
from comparo.core.models import Project
from comparo.core.models import Request
from comparo.core.models import Schema
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail
from comparo.core.redaction import Redactor
from comparo.core.report import RunReport
from comparo.core.report import build_report
from comparo.core.report import diff_passed
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
#: The git-diff well and its muted line-background bands (mockup .diff / .dl.*).
_DIFF_BG = "#090c11"  # a recessed well, darker than the panels
_DEL_BG = "#2b161c"  # muted red band behind deleted (baseline) lines
_ADD_BG = "#122a20"  # muted green band behind added (candidate) lines
_HUNK_BG = "#1d1836"  # muted purple band behind the hunk header
_WELL_BORDER = "#3f3960"  # the diff well's rounded outline (a muted axis-purple)

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
    ("Assertion Profiles", AssertionProfile),
    ("Execution Profiles", ExecutionProfile),
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
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)
_EXEC_KEYS = (
    ("↑↓", "move"),
    ("enter", "launch"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)
_RESOLVE_KEYS = (
    ("↑↓", "move"),
    ("r", "raw/resolved"),
    ("p", "curl"),
    ("tab", "panel"),
    ("/", "filter"),
    ("g", "graph"),
    ("?", "help"),
    ("q", "quit"),
)
#: Instances resolve like requests but have no HTTP call, so no 'p curl'.
_INSTANCE_KEYS = (
    ("↑↓", "move"),
    ("r", "raw/resolved"),
    ("tab", "panel"),
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
    ("t", "views"),
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
    ("t", "views"),
    ("x", "re-run"),
    ("bksp", "back"),
    ("s", "save"),
    ("?", "help"),
    ("q", "quit"),
)
_DIFF_PREPARE_KEYS = (
    ("↑↓", "move"),
    ("space", "fold"),
    ("enter", "select"),
    ("b/c", "baseline / candidate"),
    ("m", "matrix"),
    ("x", "run diff"),
    ("?", "help"),
    ("q", "quit"),
)
_DIFF_RUNNING_KEYS = (
    ("?", "help"),
    ("esc/⌫", "cancel diff"),
    ("q", "quit"),
)
_DIFF_RESULTS_KEYS = (
    ("↑↓", "fields"),
    ("b/c", "baseline / candidate"),
    ("x", "re-run"),
    ("r", "fields/rules"),
    ("v", "unified/side"),
    ("o", "outbound"),
    ("i", "ignore field"),
    ("s", "save"),
    ("?", "help"),
    ("q", "quit"),
)
#: The nav tabs, in order — used to validate the saved default-tab preference.
_TAB_NAMES = ("explorer", "run", "diff", "execution", "report", "settings")
_SETTINGS_KEYS = (
    ("↑↓", "sections"),
    ("enter/t", "activate"),
    ("?", "help"),
    ("q", "quit"),
)
_ERROR_KEYS = (
    ("r", "re-check"),
    ("?", "help"),
    ("q", "quit"),
)
# Execution tab — each sub-view advertises its own footer keys (mockups A, steps 1-5).
_EXEC_LAUNCH_KEYS = (
    ("⏎", "launch"),
    ("space", "toggle"),
    ("t", "tags"),
    ("m", "mode"),
    ("?", "help"),
    ("esc/⌫/q", "close"),
)
_EXEC_RUNNING_KEYS = (
    ("?", "help"),
    ("esc/⌫/q", "cancel run"),
)
_EXEC_RESULTS_KEYS = (
    ("↑↓", "section"),
    ("⏎", "cell"),
    ("d", "diff"),
    ("e", "report"),
    ("s", "save"),
    ("r", "re-run"),
    ("?", "help"),
    ("esc/⌫/q", "close"),
)
_EXEC_CELL_KEYS = (
    ("v", "unified/side"),
    ("i", "ignore field"),
    ("⏎", "open diff"),
    ("?", "help"),
    ("esc/⌫/q", "back"),
)
_EXEC_DIFF_KEYS = (
    ("v", "unified/side-by-side"),
    ("↑↓", "scroll"),
    ("?", "help"),
    ("esc/⌫/q", "back"),
)
# Report tab — the saved-report list, and the read-only in-tab replays.
_REPORT_LIST_KEYS = (
    ("⏎", "analyze"),
    ("/", "filter"),
    ("o", "export md"),
    ("r", "reload"),
    ("d", "delete"),
    ("?", "help"),
    ("esc/⌫/q", "close"),
)
_REPORT_DIFF_KEYS = (
    ("v", "unified/side"),
    ("↑↓", "field"),
    ("o", "export"),
    ("?", "help"),
    ("esc/⌫/q", "back"),
)
_REPORT_RUN_KEYS = (
    ("↑↓", "requests"),
    ("⏎", "drill"),
    ("z", "maximize"),
    ("o", "export"),
    ("?", "help"),
    ("esc/⌫/q", "back"),
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
    AssertionProfile: _SAME,
    ExecutionProfile: _ACCENT,
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
    "report": "REPORT — browse saved runs, replayed with the live panels",
    "report-detail": "REPORT — deep dive into a saved run",
    "execution": "EXECUTION — launch, run, gate, drill, diff — all in one tab",
    "execution-diff": "EXECUTION — the run's scoped diff",
    "execution-running": "EXECUTION — running the plan",
    "settings": "SETTINGS",
}
_HELP_SCREEN: dict[str, tuple[tuple[str, str], ...]] = {
    "explorer": (
        ("↑ ↓", "move through the project tree"),
        ("space", "fold / unfold a section"),
        ("tab", "switch the active panel — tree, detail, provenance"),
        ("enter", "on an environment: set it default · on an execution profile: launch it"),
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
        ("e", "PREPARE — choose the environment this run executes against"),
        ("m", "PREPARE — choose matrix values (applies to every request)"),
        ("x", "run the selected cells against the current environment"),
        ("/", "filter by request or case name (shown on the panel)"),
        ("f", "RUNNING — filter the tables to failures only"),
        ("esc / bksp", "RUNNING — collapse a split (or return to PREPARE)"),
        ("z", "RUNNING — maximize the detail panel"),
        ("a", "RUNNING — abort the run and return to PREPARE"),
        ("s", "RUNNING — save the finished run (masked) + archive it as a report"),
    ),
    "diff": (
        ("b / c", "pick the baseline / candidate environment in place (PREPARE and RESULTS)"),
        ("space", "PREPARE — fold a request to show its matrix cases"),
        ("enter", "PREPARE — toggle a request or matrix case in / out of the diff"),
        ("m", "PREPARE — choose matrix values (applies to every request)"),
        ("x", "diff the selected requests against the baseline ⇄ candidate pair"),
        ("↑ ↓", "PREPARE — move the request tree · RESULTS — move through drifted fields"),
        ("r", "RESULTS — toggle the index grouped ⇄ broken rules"),
        ("v", "RESULTS — toggle the body diff unified ⇄ side-by-side"),
        ("i", "silence the selected field — writes an ignore rule to its DiffProfile"),
        ("s", "RESULTS — archive this diff as a saved report"),
        ("esc / bksp", "RUNNING — cancel the diff · RESULTS — return to PREPARE"),
    ),
    "report": (
        ("↑ ↓", "move through the saved runs"),
        (
            "enter",
            "analyze the run in place — a diff replays the Diff panels, a run the Run panels",
        ),
        ("v", "in a saved diff: toggle the body well unified ⇄ side-by-side"),
        ("o", "export a Markdown summary of the run"),
        ("d", "delete the saved run (after confirmation)"),
        ("r", "reload the archive directory from disk"),
        ("/", "find by id, envs, or gate"),
        ("esc / bksp", "return from a replay to the saved-report list"),
    ),
    "report-detail": (
        ("↑ ↓", "scroll the run's full detail"),
        ("o", "export this run as a Markdown summary"),
        ("esc / bksp / q", "close and return to the saved-run list"),
    ),
    "execution": (
        ("enter", "on a profile: launch it in place · on a drifted cell: drill in"),
        ("space / t / m", "the plan's selection, tags, and mode (set by the profile's YAML)"),
        ("↑ ↓", "move through the profiles / the drifted cells"),
        ("d", "open the run's scoped body diff in place"),
        ("s", "archive this run's results as a saved report"),
        ("e", "name this run's archived report (browse it in the Report tab)"),
        ("v", "in a cell / diff: toggle the body diff unified ⇄ side-by-side"),
        ("i", "in a cell: silence the drifted field (after confirmation)"),
        ("r", "re-run this execution profile"),
        ("esc / bksp", "step back one sub-view (launch ← results ← cell ← diff)"),
    ),
    "execution-cell": (
        ("↑ ↓", "scroll the assertions / body diff"),
        ("v", "toggle the body diff unified ⇄ side-by-side"),
        ("i", "silence this drifted field (after confirmation)"),
        ("esc / bksp / q", "return to the execution"),
    ),
    "execution-diff": (
        ("↑ ↓", "scroll through the run's drifted cells"),
        ("v", "toggle every body diff unified ⇄ side-by-side"),
        ("esc / bksp / q", "return to the execution"),
    ),
    "execution-running": (
        ("(running)", "comparo is executing the plan; this view closes when the run finishes"),
    ),
    "settings": (
        ("↑ ↓", "move between settings sections"),
        ("enter", "toggle the section's control (Updates, Appearance, Behavior)"),
        ("t", "run the never-leak self-check (Security section)"),
    ),
    "error": (("r", "re-check the project after editing the files"),),
    "filter": (
        ("type", "filter the list as you type — it stays live behind the overlay"),
        ("enter", "keep this filter and close"),
        ("esc", "clear the filter and close"),
    ),
    "confirm": (
        ("y / enter", "confirm the action (comparo names the exact file first)"),
        ("n / esc", "cancel — nothing is written"),
    ),
    "graph": (
        ("↑ ↓", "scroll the reference graph"),
        ("g / esc / q", "close the overlay"),
    ),
    "picker": (
        ("↑ ↓", "move through the choices"),
        ("enter", "choose the highlighted item"),
    ),
    "curl": (
        ("↑ ↓", "scroll the curl"),
        ("c", "copy the real (secret-bearing) curl to the clipboard"),
        ("esc / q", "close — the shown curl is masked"),
    ),
    "matrix": (
        ("↑ ↓", "move through the matrix values"),
        ("space", "toggle a matrix value in / out of the run"),
        ("a / n", "select all / none"),
        ("esc", "apply the selection"),
    ),
}
_HELP_GLOBAL = (
    ("1 … 6", "switch screens — Explorer, Run, Diff, Execution, Report, Settings"),
    ("&é\"'(-", "same tabs on an AZERTY top row (no Shift needed)"),
    ("?", "show this help"),
    ("q", "quit comparo"),
)


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


def _keys_bar(keys: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> Text:
    """Render ``(key, action)`` hints as a single no-wrap line of pills."""
    bar = Text(no_wrap=True, overflow="ellipsis")
    for index, (key, action) in enumerate(keys):
        if index:
            bar.append(" ")
        bar.append(f" {key} ", style=f"bold {_INK} on {_ACCENT}")
        bar.append(f" {action}", style=_DIM)
    return bar


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
                    redact=Redactor.for_project(self.project).text,
                )
            )
            context.border_title = "PROVENANCE"
            self._set_context(
                _render_provenance(resolved.trail, Redactor.for_project(self.project).text)
            )
        elif isinstance(obj, Environment):
            env_id = obj.metadata.id or ""
            detail.border_title = _title(obj, "ENVIRONMENT")
            detail.border_subtitle = _HEALTH_LABEL[self.health.get(env_id, Health.UNKNOWN)]
            self._set_detail(
                _environment_detail(
                    obj,
                    self.health_reports.get(env_id),
                    Redactor.for_project(self.project).text,
                    checked=self.health_checked.get(env_id),
                )
            )
            context.border_title = "DESCRIPTION"
            self._set_context(_description(obj))
        elif isinstance(obj, Project):
            detail.border_title = _title(obj, "PROJECT")
            detail.border_subtitle = "the manifest"
            self._set_detail(_project_detail(obj, Redactor.for_project(self.project).text))
            context.border_title = "DESCRIPTION"
            self._set_context(_description(obj))
        elif isinstance(obj, Instance):
            value, trail = self._resolve_instance(obj)
            detail.border_title = _title(obj, "INSTANCE")
            detail.border_subtitle = self._resolve_subtitle()
            self._set_detail(
                _json(
                    obj.spec.value if self.raw else value,
                    Redactor.for_project(self.project).text,
                )
            )
            titled, content = (
                ("PROVENANCE", _render_provenance(trail, Redactor.for_project(self.project).text))
                if trail and not self.raw
                else ("DESCRIPTION", _description(obj))
            )
            context.border_title = titled
            self._set_context(content)
        else:
            detail.border_title = _title(obj, type(obj).__name__.upper())
            detail.border_subtitle = ""
            self._set_detail(_object_detail(obj, Redactor.for_project(self.project).text))
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
        #: Which facet the per-cell detail shows (RUN-27): all/request/response/headers/raw.
        self._detail_focus = "all"
        self._worker: Worker[None] | None = None
        self._done = False

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
        redact = Redactor.for_project(self.project).text
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
        # Also archive an assertions report so the run shows up in the Report tab.
        run_cells = [
            (
                entry.request.metadata.id or entry.request.metadata.name,
                [_check_result(check) for check in entry.checks],
            )
            for entry in entries
        ]
        record = cast("ComparoApp", self.app).save_run_report(environment.metadata.name, run_cells)
        if record is not None:
            self.app.notify(
                f"Saved run to {path.name} · report {record.id} in the archive", title="Saved"
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
        crumb = Redactor.for_project(self.project).text(cell.key) or request.metadata.name
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
            _app_env(self),
            request,
            cell,
            self._exec.get(key),
            self._state.get(key, "pending"),
            self._checks.get(key, []),
            Redactor.for_project(self.project).text,
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
        redact = Redactor.for_project(self.project).text
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
        Binding("o", "outbound", "outbound diff"),
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
        self._run_current: _RunningRow | None = None
        self._run_recent: list[_RunningRow] = []
        self._run_glyphs: list[str] = []
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
        if switcher.current in ("diff-results", "diff-running") and not self._done:
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
        self._run_current = None
        self._run_recent = []
        self._run_glyphs = ["○"] * len(plan)
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
        redact = Redactor.for_project(self.project).text
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
        cta.append("  ·  up to 4 in parallel", style=_DIM)
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
        touch; the write only happens if the user confirms. RESULTS only.
        """
        if not self._in_results():
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
        redact = Redactor.for_project(self.project).text
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
        redact = Redactor.for_project(self.project).text
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
        redact = Redactor.for_project(self.project).text
        client = HttpxClient()
        candidate_client = HttpxClient()
        limit = asyncio.Semaphore(4)

        async def one(index: int, request: Request, cell: MatrixCell) -> CellDiff:
            method_path = f"{request.spec.request.method} {redact(request.spec.request.endpoint)}"
            variant = redact(cell.key) if cell.key else ""
            self._run_current = _RunningRow(request.metadata.name, variant, method_path)
            self._run_glyphs[index] = "◐"
            self._render_diff_running()
            async with limit:
                base, cand = await asyncio.gather(
                    execute_request(self.project, baseline, request, client, cell),
                    execute_request(self.project, candidate, request, candidate_client, cell),
                )
            result = compare_cell(self.project, base, cand)
            self._run_done += 1
            self._run_glyphs[index] = "●"
            drift = redact(result.drifts[0].path).rsplit(".", 1)[-1] if result.drifts else ""
            self._run_recent.append(
                _RunningRow(
                    request.metadata.name,
                    variant,
                    method_path,
                    status=base.response.status if base.response is not None else None,
                    baseline_ms=(
                        round(base.response.elapsed_ms) if base.response is not None else None
                    ),
                    candidate_ms=(
                        round(cand.response.elapsed_ms) if cand.response is not None else None
                    ),
                    drift=drift,
                )
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
        report = build_report(baseline.metadata.name, candidate.metadata.name, self._cells, redact)
        cast("ComparoApp", self.app).last_report = report
        self._finish(self._cells)

    def _render_diff_running(self) -> None:
        if not self.is_mounted:
            return
        label = "diff"
        if self._pair is not None:
            label = f"{self._pair[0].metadata.name} ⇄ {self._pair[1].metadata.name}"
        self.query_one("#diff-running-content", Static).update(
            _running_body(
                label,
                self._run_done,
                self._run_total,
                self._run_current,
                self._run_recent,
                self._run_glyphs,
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
        # Focus the results pane so the RESULTS keys (↑↓/r/v/i/s/esc) fire on landing.
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
        record = cast("ComparoApp", self.app).save_diff_report(
            baseline.metadata.name, candidate.metadata.name, self._cells
        )
        if record is None:
            self.app.notify("No project archive to save into", severity="warning")
            return
        self._run_id = record.id
        self._saved = True
        self.refresh_footer()
        self.app.notify(f"Saved report {record.id} to the archive", title="Report")

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
        redact = Redactor.for_project(self.project).text
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
        if self._groups:
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
        redact = Redactor.for_project(self.project).text
        for path, entries in self._groups:
            field = entries[0][1]
            return f"{redact(path)} · {field.mode} · {_clip(redact(field.detail)) or 'differs'}"
        return "the DiffProfile rules that fired"

    def _populate_fields(self, table: DataTable[Text]) -> None:
        redact = Redactor.for_project(self.project).text
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
            sub.append(" · volatile", style=_SKIP)
            table.add_row(Text(""), sub, Text(""), key=f"skipsub::{path}")

    def _populate_rules(self, table: DataTable[Text]) -> None:
        # One row per fired rule: which mode flagged which field, and the change.
        redact = Redactor.for_project(self.project).text
        table.add_column("", key="st", width=3)
        table.add_column("FIELD", key="field")
        table.add_column("RULE · CHANGE", key="meta")
        for path, entries in self._groups:
            field = entries[0][1]
            meta = Text(field.mode, style=_MODE.get(field.mode, _DIM))
            meta.append(f" · {_clip(redact(field.detail)) or 'differs'}", style=_DRIFT)
            table.add_row(
                Text("✗", style=_DRIFT),
                Text(redact(path), style=_DRIFT),
                meta,
                key=f"drift::{path}",
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Show the comparison (or error) for the highlighted row."""
        # Moving the cursor leaves the outbound-request overlay (DIFF-27).
        self._outbound_shown = False
        self._render_row(event.row_key.value)

    def _render_row(self, key: str | None) -> None:
        """Render the compare panel for a drift-table row key."""
        if key is None:
            return
        if key.startswith("drift::"):
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

    def _current_cell_diff(self) -> CellDiff | None:
        """Return the CellDiff for the highlighted row.

        A per-cell row names its own cell; a grouped-field row falls back to the
        first cell that drifts on that field.
        """
        key = self._current_row_key()
        if key is None:
            return None
        if key in self._row_cells:  # cell:: / error:: rows carry their cell
            return self._row_cells[key]
        group = self._selected_group()
        if group is not None and group[1]:
            return group[1][0][0]
        return None

    def action_outbound(self) -> None:
        """Diff the OUTBOUND request itself across the pair (DIFF-27).

        comparo replays the *same* request against both environments, so the
        outbound only differs where env config does — a different base URL, a
        per-env auth token, an env-specific header. Showing it answers the first
        triage question: is the drift the service's, or did we send two different
        requests? Press ``o`` again to return to the field diff.
        """
        if not self._in_results() or self._pair is None:
            return
        if self._outbound_shown:  # toggle back to whatever row is selected
            self._outbound_shown = False
            self._render_row(self._current_row_key())
            return
        cell = self._current_cell_diff()
        if cell is None:
            self.app.notify("Select a drifted field first", severity="information")
            return
        self._outbound_shown = True
        matrix_cell = next(
            (c for c in expand(self.project, cell.request) if c.key == cell.cell_key), None
        )
        baseline_env, candidate_env = self._pair
        baseline = Resolver(self.project, baseline_env).resolve_request(cell.request, matrix_cell)
        candidate = Resolver(self.project, candidate_env).resolve_request(cell.request, matrix_cell)
        redact = Redactor.for_project(self.project).text
        wrap = self.query_one("#col-compare")
        wrap.border_title = Text.from_markup(
            f"COMPARE [{_DIM}]· outbound request · {redact(cell.request.metadata.name)}[/]"
        )
        wrap.border_subtitle = "press o to return to the field diff"
        self.query_one("#compare-content", Static).update(
            _outbound_diff_view(baseline, candidate, baseline_env, candidate_env, redact=redact)
        )

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
        redact = Redactor.for_project(self.project).text
        wrap.border_title = Text.from_markup(
            f"COMPARE [{_DIM}]·[/] {redact(path)} [{_DIM}]· {request}{envs}[/]"
        )
        wrap.border_subtitle = _seg_toggle(("unified", "side-by-side"), mode)
        self.query_one("#compare-content", Static).update(
            _diff_body_view((path, entries), self._pair, unified=self._unified, redact=redact)
        )

    def _show_error(self, cell: CellDiff) -> None:
        """Render the transport/execution error for a cell — request, env, message."""
        wrap = self.query_one("#col-compare")
        wrap.border_title = Text.from_markup(f"COMPARE [{_DIM}]· error[/]")
        redact = Redactor.for_project(self.project).text
        self.query_one("#compare-content", Static).update(
            _diff_error_view(cell, self._pair, redact=redact)
        )

    def _show_skip(self, path: str) -> None:
        """Explain a field the profile deliberately skips — what and why."""
        group = next((g for g in self._skip_groups if g[0] == path), None)
        wrap = self.query_one("#col-compare")
        wrap.border_title = Text.from_markup(f"COMPARE [{_DIM}]· skipped[/]")
        redact = Redactor.for_project(self.project).text
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


_GATE_COLOR = {"PASS": _SAME, "FAIL": _DRIFT, "ERROR": _WARN}
#: Per-kind glyph + colour for the saved-report list (also used in the kind legend),
#: so each row shows its own kind (execution / diff / run) at a glance.
_KIND_GLYPH: dict[str, tuple[str, str]] = {
    "execution": ("◆", _AXIS),
    "diff": ("◇", _ACCENT),
    "run": ("◇", _SAME),
}


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
        self._records: list[ReportRecord] = []
        self._filtered: list[ReportRecord] = []
        self.filter_query: str = ""
        self._analyzed: ReportRecord | None = None
        self._unified = True

    def action_find(self) -> None:
        """Open the archive filter (browse list only)."""
        if self._current_view() == "report-browse":
            self.app.push_screen(FilterModal(self, placeholder="id, envs, kind, or gate…"))

    def apply_filter(self, query: str) -> int:
        """Filter the saved-run list by id / envs / kind / gate / execution; returns the count."""
        self.filter_query = query
        needle = query.strip().lower()
        self._filtered = [
            record
            for record in self._records
            if not needle
            or needle in record.id.lower()
            or needle in _envs_label(record).lower()
            or needle in _record_kind(record)
            or needle in record.gate.lower()
            or needle in (record.execution or "").lower()
        ]
        self._populate_list()
        if self._filtered:
            self._show(self._filtered[0])
        return len(self._filtered)

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

    def _load_records(self) -> list[ReportRecord]:
        directory = cast("ComparoApp", self.app).archive_directory()
        return list_records(directory) if directory is not None else []

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
            self._filtered = [
                record
                for record in self._records
                if not self.filter_query.strip()
                or self.filter_query.strip().lower() in record.id.lower()
            ]
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

    def _selected(self) -> ReportRecord | None:
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

    def _show(self, record: ReportRecord) -> None:
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

    def _show_diff_replay(self, record: ReportRecord) -> None:
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

    def _populate_replay_drift(self, record: ReportRecord) -> None:
        table = self.query_one("#report-drift-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="st", width=3)
        table.add_column("FIELD", key="field")
        table.add_column("META", key="meta", width=17)
        redact = Redactor.for_project(self.project).text
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

    def _render_replay_compare(self, record: ReportRecord) -> None:
        self.query_one("#report-compare-content", Static).update(
            _replay_compare_well(record, self._unified, Redactor.for_project(self.project).text)
        )

    def _show_run_replay(self, record: ReportRecord) -> None:
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
            _settings_body(self.project, self._config(), key, self._selfcheck, self._checking)
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
        self._unified = True
        self._run_id: str | None = None
        # live-progress state for the running sub-view
        self._done = 0
        self._total = 0
        self._current: _RunningRow | None = None
        self._recent: list[_RunningRow] = []
        self._plan_glyphs: list[str] = []
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
                with Horizontal(id="exec-assert"):
                    yield Static(id="exec-assert-base", classes="panel")
                    yield Static(id="exec-assert-cand", classes="panel")
                with Vertical(id="exec-diff", classes="panel"):
                    yield Static(id="exec-diff-summary")
                    yield DataTable(id="exec-drift-table", cursor_type="row", show_header=False)
                    yield Static(id="exec-diff-legend")
                yield Static(id="exec-gate", classes="panel")
            with Vertical(id="exec-cell"):
                yield Static(id="cell-header", classes="panel")
                with Horizontal(id="cell-cols"):
                    with Vertical(id="cell-assert"):
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
        redact = Redactor.for_project(self.project).text
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
        redact = Redactor.for_project(self.project).text
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
            _exec_setup(self.project, profile, Redactor.for_project(self.project).text)
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
        self._run_id = uuid4().hex[:4]
        self._done = 0
        self._total = 0
        self._current = None
        self._recent = []
        self._plan_glyphs = []
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
        """Advance the running display for one start/finish tick from the engine."""
        redact = Redactor.for_project(self.project).text
        self._total = event.total
        while len(self._plan_glyphs) < event.total:
            self._plan_glyphs.append("○")
        row = _running_row_from_progress(event, redact)
        if event.done:
            self._done += 1
            if event.index < len(self._plan_glyphs):
                self._plan_glyphs[event.index] = "●"
            self._recent.append(row)
        else:
            if event.index < len(self._plan_glyphs):
                self._plan_glyphs[event.index] = "◐"
            self._current = row
        if self.is_mounted:
            self._render_running()

    def _render_running(self) -> None:
        label = self._profile.metadata.name if self._profile is not None else "execution"
        self.query_one("#exec-running-content", Static).update(
            _running_body(
                label,
                self._done,
                self._total,
                self._current,
                self._recent,
                self._plan_glyphs,
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
        self._run_id = self._run_id or (record.id if record is not None else uuid4().hex[:4])
        self._show_results()

    def _show_results(self) -> None:
        result = self._result
        if result is None or self._profile is None:
            return
        self._drifted = [
            outcome
            for outcome in result.outcomes
            if outcome.error is not None or (outcome.diff is not None and outcome.diff.drifted)
        ]
        redact = _app_redact(self)
        self.query_one("#exec-header", Static).update(_exec_header(self._profile, result, redact))
        self.query_one("#exec-header").border_title = "EXECUTION"
        self.query_one(
            "#exec-header"
        ).border_subtitle = (
            f"{len(result.outcomes)} cells · {result.baseline} ⇄ {result.candidate or '—'}"
        )
        self._render_results_assertions()
        self._render_results_diff()
        self._render_results_gate()
        self._show("exec-results")
        if self._drifted:
            self.query_one("#exec-drift-table", DataTable).focus()

    def _render_results_assertions(self) -> None:
        result = self._result
        if result is None:
            return
        redact = _app_redact(self)
        blocks = (
            ("#exec-assert-base", "baseline", result.baseline),
            ("#exec-assert-cand", "candidate", result.candidate or "—"),
        )
        for ident, side, env in blocks:
            tally, rows = _exec_assert_rows(result.outcomes, side)
            panel = self.query_one(ident, Static)
            panel.border_title = Text.from_markup(f"ASSERTIONS [{_DIM}]· {env}[/]")
            panel.border_subtitle = _assert_count_text(tally)
            kind = "BASELINE" if side == "baseline" else "CANDIDATE"
            header = Text(f"{kind} ASSERTIONS ", style=_DIM)
            header.append("· ", style=_DIM)
            header.append(env, style=f"bold {_TEXT_HI}")
            panel.update(Group(header, Text(), _exec_assert_body(rows, redact)))

    def _render_results_diff(self) -> None:
        result = self._result
        if result is None:
            return
        redact = _app_redact(self)
        self.query_one("#exec-diff").border_title = Text.from_markup(
            f"DIFF [{_DIM}]· {result.baseline} ⇄ {result.candidate or '—'}[/]"
        )
        self.query_one("#exec-diff").border_subtitle = f"{result.drift} drift"
        self.query_one("#exec-diff-summary", Static).update(_exec_diff_summary(result, redact))
        table = self.query_one("#exec-drift-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="st", width=3)
        table.add_column("cell", key="cell")
        table.add_column("change", key="change")
        for index, outcome in enumerate(self._drifted):
            label = Text(_req_short(outcome.request_id), style=f"bold {_TEXT_HI}")
            if outcome.cell_key:
                label.append(f" · {redact(outcome.cell_key)}", style=_AXIS)
            if outcome.error is not None:
                glyph, change = Text("!", style=_WARN), Text("error", style=_WARN)
            else:
                glyph = Text("✗", style=_DRIFT)
                change = _drift_change(outcome, redact)
            table.add_row(glyph, label, change, key=f"drift::{index}")
        self.query_one("#exec-diff-legend", Static).update(_exec_diff_legend(result, redact))

    def _render_results_gate(self) -> None:
        result = self._result
        if result is None:
            return
        gate = self.query_one("#exec-gate")
        gate.set_class(result.passed, "pass")
        gate.set_class(not result.passed, "fail")
        gate.border_title = Text.from_markup(f"GATE [{_DIM}]· assertions ∧ diff[/]")
        self.query_one("#exec-gate", Static).update(_exec_gate_body(result))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a drifted cell drills in (the table eats enter first)."""
        if event.data_table.id == "exec-drift-table":
            self._drill()

    def _drill(self) -> None:
        table = self.query_one("#exec-drift-table", DataTable)
        if table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if key is None or not key.startswith("drift::"):
            return
        self._cell = self._drifted[int(key.removeprefix("drift::"))]
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
            self.app.notify(f"Already archived as report {self._record.id}", title="Report")
            self.refresh_footer()
            return
        record = cast("ComparoApp", self.app).save_execution_report(
            self._result, self._profile.metadata.name if self._profile else None
        )
        if record is None:
            self.app.notify("No project archive to save into", severity="warning")
            return
        self._record = record
        self.refresh_footer()
        self.app.notify(f"Saved report {record.id} to the archive", title="Report")

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
            f"This run is archived as report {self._record.id} — browse it in the Report tab (5).",
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
            insight = Text("\noutbound identical → the drift is the service's", style=_SAME)
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
        redact = Redactor.for_project(project).text
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
        if Redactor.for_project(project).text(path) != path:
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
        """``d`` — open the run's scoped body diff, stacked, without leaving the tab."""
        view = self._current_view()
        if view not in ("exec-results", "exec-cell"):
            return
        if not self._drifted:
            self.app.notify("No drift to diff in this run", severity="information")
            return
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
        dialog.border_subtitle = "v unified/side · stays in the Execution tab"
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
            if self._cell is not None:
                self._render_cell()
                self._show("exec-cell")
            else:
                self._show_results()


_ISSUES_URL = "https://github.com/wbenbihi/comparo/issues/new"


def _crash_report(error: Exception, redact: Callable[[str], str]) -> Group:
    """A friendly, secret-redacted crash panel with a prefilled GitHub issue link.

    The traceback is masked with the project's secret values before it is shown or
    put into the issue URL, so a crash can never leak a secret.
    """
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    safe_tb = redact(tb)
    title = f"crash: {type(error).__name__}: {redact(str(error))}"
    # GitHub caps issue URLs, so the prefilled body carries only the tail.
    body = (
        "**What I was doing:** \n\n"
        f"**comparo version:** {__version__}\n\n"
        "**Traceback** (secrets already masked):\n\n"
        f"```\n{safe_tb[-3500:]}\n```\n"
    )
    url = f"{_ISSUES_URL}?{urlencode({'title': title[:200], 'body': body})}"
    text = Text()
    text.append("comparo hit an unexpected error and has to stop.\n\n", style=f"bold {_DRIFT}")
    text.append("This is a bug — nothing you did is at fault, and your files were not changed.\n")
    text.append("The traceback below has your secrets masked. Please report it:\n\n", style=_DIM)
    text.append(f"{url}\n", style=_ACCENT)
    body_panel = Text("\n")
    body_panel.append(safe_tb, style=_DIM)
    return Group(text, body_panel)


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
        self.last_report: RunReport | None = None
        #: Persisted app preferences (theme, opt-in update check, diff default…).
        self.user_config: UserConfig = userconfig.load()
        #: Guards the confirm-on-quit dialog against re-entrancy (a second ``q``).
        self._quitting = False

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
            self._return_code = 1
            if self._exception is None:
                self._exception = error
                self._exception_event.set()
            redact = Redactor.for_project(self.project).text if self.project is not None else str
            self.panic(_crash_report(error, redact))
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

    def execution_record(self, result: ExecutionResult, name: str | None) -> ReportRecord | None:
        """Build a redacted report record for an execution, without saving it."""
        if self.project is None:
            return None
        return record_from_execution(
            result,
            run_id=uuid4().hex[:6],
            created=datetime.now().isoformat(timespec="seconds"),
            name=name,
            redact=Redactor.for_project(self.project).text,
        )

    def save_execution_report(
        self, result: ExecutionResult, name: str | None
    ) -> ReportRecord | None:
        """Archive an execution result; returns the saved record, or ``None``."""
        directory = self.archive_directory()
        record = self.execution_record(result, name)
        if directory is None or record is None:
            return None
        try:
            save_record(directory, record)
        except OSError as error:
            self.notify(str(error), title="Could not save report", severity="error")
            return None
        return record

    def export_record_markdown(self, record: ReportRecord) -> None:
        """Write a Markdown summary of *record* to the project's reports output dir."""
        if self.project is None:
            return
        manifest = self.project.project
        config = manifest.spec.report if manifest else None
        output_name = config.get("output") if isinstance(config, dict) else None
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
        self, baseline: str, candidate: str, diffs: list[CellDiff]
    ) -> ReportRecord | None:
        """Archive an ad-hoc diff run; returns the saved record, or ``None``."""
        directory = self.archive_directory()
        if directory is None or self.project is None:
            return None
        record = record_from_diff(
            baseline,
            candidate,
            diffs,
            run_id=uuid4().hex[:6],
            created=datetime.now().isoformat(timespec="seconds"),
            redact=Redactor.for_project(self.project).text,
        )
        try:
            save_record(directory, record)
        except OSError as error:
            self.notify(str(error), title="Could not save report", severity="error")
            return None
        return record

    def save_run_report(
        self, environment: str, cells: list[tuple[str, list[AssertionResult]]]
    ) -> ReportRecord | None:
        """Archive a single-environment run as an assertions report; returns it or ``None``."""
        directory = self.archive_directory()
        if directory is None or self.project is None:
            return None
        record = record_from_run(
            environment,
            cells,
            run_id=uuid4().hex[:6],
            created=datetime.now().isoformat(timespec="seconds"),
            redact=Redactor.for_project(self.project).text,
        )
        try:
            save_record(directory, record)
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
    elif isinstance(obj, ExecutionProfile):
        row.append("▸ ", style=_ACCENT)
        row.append(name, style=_TEXT_HI)
        row.append("  enter to run", style=_DIM)
    elif isinstance(obj, AssertionProfile):
        row.append(name, style=_TEXT)
        count = len(obj.spec.rules or [])
        if count:
            row.append(f"  ×{count}", style=_DIM)
    else:
        row.append(name, style=_TEXT)
    return row


def _project_leaf(manifest: Project) -> Text:
    row = Text()
    row.append("◆ ", style=_ACCENT)
    row.append(str(manifest.metadata.name or "project"), style=f"bold {_TEXT_HI}")
    row.append("  project", style=_DIM)
    return row


def _project_detail(manifest: Project, redact: Callable[[str], str] = str) -> Group:
    spec = manifest.spec
    parts: list[RenderableType] = []
    head = Text()
    if spec.data:
        head.append("data       ", style=_LABEL)
        head.append(f"{redact(str(spec.data))}\n", style=_TEXT)
    environments = spec.environments if isinstance(spec.environments, dict) else {}
    default = environments.get("default")
    if isinstance(default, str):
        head.append("default    ", style=_LABEL)
        head.append(f"{redact(default)}\n", style=_ACCENT)
    parts.append(head)
    pairs = environments.get("diffPairs")
    if isinstance(pairs, list) and pairs:
        block = Text("\nDIFF PAIRS", style=_LABEL)
        for pair in pairs:
            if isinstance(pair, dict):
                block.append(f"\n  {redact(str(pair.get('name', ''))):<16}", style=_TEXT)
                base = redact(str(pair.get("baseline", "")))
                cand = redact(str(pair.get("candidate", "")))
                block.append(f"{base} ⇄ {cand}", style=_AXIS)
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
            parts.append(_json(value, redact))
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
    project: LoadedProject,
    request: Request,
    resolved: ResolvedRequest,
    *,
    raw: bool = False,
    redact: Callable[[str], str] = str,
) -> Group:
    outbound = request.spec.request
    parts: list[RenderableType] = []
    head = Text()
    head.append(
        f" {resolved.method} ", style=f"bold {_INK} on {_METHOD.get(resolved.method, _ACCENT)}"
    )
    head.append("  ")
    head.append(redact(outbound.endpoint if raw else resolved.url), style=_TEXT_HI)
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
    for key, rendered in _header_rows(outbound.headers, resolved.headers, raw=raw, redact=redact):
        headers.append(f"\n  {key:<18}", style=_DIM)
        headers.append(rendered)
    parts.append(headers)
    query_source = (outbound.query or {}) if raw else resolved.query
    if query_source:
        query = Text("\n\nQUERY", style=_LABEL)
        for key, value in query_source.items():
            shown = _hole_str(value) if raw else str(value)
            query.append(f"\n  {redact(key):<18}", style=_DIM)
            query.append(redact(shown), style=_AXIS)
        parts.append(query)
    body_source = outbound.body if raw else resolved.body
    if body_source is not None:
        parts.append(Text("\n\nBODY", style=_LABEL))
        parts.append(_json(body_source, redact))
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
    raw_headers: object,
    resolved_headers: list[tuple[str, object]],
    *,
    raw: bool,
    redact: Callable[[str], str] = str,
) -> list[tuple[str, Text]]:
    if raw:
        pairs = _raw_header_pairs(raw_headers)
        return [(redact(key), Text(redact(_hole_str(value)), style=_AXIS)) for key, value in pairs]
    rows: list[tuple[str, Text]] = []
    for key, value in resolved_headers:
        shown = redact(str(value))
        masked = "••••" in shown
        rows.append((redact(key), Text(shown, style=_DRIFT if masked else _TEXT)))
    return rows


def _raw_header_pairs(headers: object) -> list[tuple[str, object]]:
    if isinstance(headers, dict):
        target = headers.get("$val")
        if isinstance(target, str):
            return [("(reference)", {"$val": target})]
        # Mapping form: ``{Header-Name: value}`` (skip any ``$``-sigil hole).
        return [(str(key), value) for key, value in headers.items() if not str(key).startswith("$")]
    pairs: list[tuple[str, object]] = []
    if isinstance(headers, list):
        for item in headers:
            if isinstance(item, Header):
                pairs.append((item.key, item.value))
            elif isinstance(item, dict) and "key" in item:
                pairs.append((str(item["key"]), item.get("value")))
    return pairs


def _hole_str(value: object) -> str:
    if isinstance(value, dict) and len(value) == 1:
        key, target = next(iter(value.items()))
        return f"{key} {target}"
    return str(value)


def _object_detail(obj: object, redact: Callable[[str], str] = str) -> RenderableType:
    if isinstance(obj, Environment):
        return _environment_detail(obj, None, redact)
    if isinstance(obj, Matrix):
        return Group(_matrix_head(obj, redact), _json(obj.spec.values, redact))
    if isinstance(obj, DiffProfile):
        return _diffprofile_detail(obj, redact)
    if isinstance(obj, AssertionProfile):
        return _assertion_profile_detail(obj, redact)
    if isinstance(obj, ExecutionProfile):
        return _execution_profile_detail(obj, redact)
    if isinstance(obj, Schema):
        return _json(obj.spec, redact)
    if isinstance(obj, Instance):
        return _json(obj.spec.value, redact)
    return Text(str(obj), style=_TEXT)


def _assertion_profile_detail(
    profile: AssertionProfile, redact: Callable[[str], str] = str
) -> Group:
    spec = profile.spec
    parts: list[RenderableType] = []
    if profile.metadata.description:
        parts.append(Text(profile.metadata.description, style=_DIM))
    for reference in spec.include or []:
        line = Text("\ninclude    ", style=_LABEL)
        line.append(_ref_id(reference) or _hole_str(reference), style=_ACCENT)
        parts.append(line)
    rules = Text("\n\nRULES", style=_LABEL)
    for rule in spec.rules or []:
        tint = _WARN if rule.severity == "warn" else _TEXT
        # A rule's expected value can equal a declared secret (asserting against a
        # credential); mask it here as the label/detail sinks do.
        rules.append(f"\n  {redact(rule.target):<24}", style=_TEXT_HI)
        rules.append(f"{rule.op:<8}", style=_AXIS)
        if rule.value is not None:
            rules.append(_sv(rule.value, redact), style=tint)
        if rule.severity == "warn":
            rules.append("   warn", style=_WARN)
    parts.append(rules)
    parts.append(Text("\n\nRuns on both environments.", style=_DIM))
    return Group(*parts)


def _execution_profile_detail(
    profile: ExecutionProfile, redact: Callable[[str], str] = str
) -> Group:
    spec = profile.spec
    parts: list[RenderableType] = []
    if profile.metadata.description:
        parts.append(Text(profile.metadata.description, style=_DIM))
    envs = spec.environments
    body = Text()
    if envs is not None:
        body.append("\nbaseline   ", style=_LABEL)
        body.append(f"{redact(envs.baseline or '—')}", style=_SAME)
        if envs.candidate:
            body.append("\ncandidate  ", style=_LABEL)
            body.append(redact(envs.candidate), style=_DRIFT)
    select = spec.select
    if select is not None and (select.tags or select.requests):
        body.append("\nselect     ", style=_LABEL)
        chosen = list(select.tags or []) + list(select.requests or [])
        body.append(" · ".join(redact(item) for item in chosen), style=_AXIS)
    check = spec.check
    body.append("\nchecks     ", style=_LABEL)
    do_assert = check.assertions if check is not None else True
    do_diff = check.diff if check is not None else True
    body.append("assert " + ("on" if do_assert else "off"), style=_SAME if do_assert else _DIM)
    body.append("  ·  ", style=_DIM)
    body.append("diff " + ("on" if do_diff else "off"), style=_SAME if do_diff else _DIM)
    parts.append(body)
    profiles = spec.profiles
    for key, block in (
        ("assert", profiles.assert_ if profiles else None),
        ("diff", profiles.diff if profiles else None),
    ):
        for reference in block if isinstance(block, list) else ([block] if block else []):
            line = Text(f"\n{key:<10} ", style=_LABEL)
            line.append(_ref_id(reference) or "inline", style=_ACCENT)
            parts.append(line)
    if isinstance(spec.matrix, dict) and spec.matrix:
        matrix = Text("\n\nMATRIX SCOPE", style=_LABEL)
        for name, scope in spec.matrix.items():
            matrix.append(f"\n  {name}  ", style=_TEXT_HI)
            for verb, cases in (("+", scope.include), ("−", scope.exclude), ("~", scope.override)):
                for case in cases or []:
                    matrix.append(f"{verb}{_sv(case, redact)} ", style=_DIM)
        parts.append(matrix)
    parts.append(Text("\n\npress enter to run this execution", style=f"bold {_ACCENT}"))
    return Group(*parts)


def _environment_detail(
    env: Environment,
    report: HealthReport | None,
    redact: Callable[[str], str] = str,
    *,
    checked: str | None = None,
) -> Text:
    spec = env.spec
    text = Text()
    remote = _is_remote(env)
    text.append("baseUrl    ", style=_LABEL)
    # base_url can embed a credential (https://user:<secret>@host); a variable's
    # value can equal a declared secret (the untainted vector) — mask both.
    text.append(f"{redact(spec.base_url)}", style=_ACCENT)
    text.append("   live\n" if remote else "   local\n", style=_DANGER if remote else _DIM)
    if spec.timeout is not None:
        text.append("timeout    ", style=_LABEL)
        text.append(f"connect {spec.timeout.connect} · read {spec.timeout.read}\n", style=_TEXT)
    for section, mapping in (("VARIABLES", spec.variables), ("SECRETS", spec.secrets)):
        if mapping:
            text.append(f"\n{section}\n", style=_LABEL)
            for key in mapping:
                text.append(f"  {redact(key):<22}", style=_DIM)
                text.append(
                    "••••••\n" if section == "SECRETS" else f"{redact(str(mapping[key]))}\n",
                    style=_DRIFT if section == "SECRETS" else _TEXT,
                )
    if spec.health:
        text.append("\nHEALTH", style=_LABEL)
        if report is not None:
            text.append(f"   {report.status.value}", style=_HEALTH_COLOR[report.status])
        # EXP-23: health is a point-in-time probe you trigger — never fired on
        # focus, since that would hammer a live env on every cursor move. Show
        # how fresh the last probe is (or that there isn't one) and how to re-run.
        if checked is not None:
            age = _relative_age(checked)
            text.append(
                f"   checked {age} ago · press h to re-check" if age else "   press h to re-check",
                style=_DIM,
            )
        else:
            text.append("   not checked yet · press h", style=_DIM)
        text.append("\n", style=_LABEL)
        outcomes = {result.endpoint: result for result in (report.results if report else [])}
        for check in spec.health:
            result = outcomes.get(check.endpoint)
            if result is None:
                text.append(f"  ○ {check.method} {redact(check.endpoint)}\n", style=_DIM)
            else:
                glyph, colour = ("✓", _SAME) if result.ok else ("✗", _DRIFT)
                text.append(f"  {glyph} {check.method} {redact(check.endpoint)}", style=colour)
                text.append(f"   {redact(result.detail)}\n", style=_DIM)
    return text


def _matrix_head(matrix: Matrix, redact: Callable[[str], str] = str) -> Text:
    spec = matrix.spec
    text = Text()
    text.append("target   ", style=_LABEL)
    text.append(f"{redact(spec.target)}\n", style=_TEXT)
    text.append("mode     ", style=_LABEL)
    text.append(f"{spec.mode}\n", style=_TEXT)
    text.append(f"\nVALUES  ×{len(spec.values)}\n", style=_LABEL)
    return text


def _diffprofile_detail(profile: DiffProfile, redact: Callable[[str], str] = str) -> Text:
    spec = profile.spec
    text = Text()
    text.append("default  ", style=_LABEL)
    text.append(f"{spec.default}\n", style=_MODE.get(spec.default, _TEXT))
    if spec.rules:
        text.append("\nRULES\n", style=_LABEL)
        for rule in spec.rules:
            text.append(f"  {redact(rule.path):<30}", style=_TEXT)
            text.append(f"{rule.mode}\n", style=_MODE.get(rule.mode, _TEXT))
    return text


def _render_provenance(trail: list[Trail], redact: Callable[[str], str] = str) -> Text:
    if not trail:
        return Text("all literal — nothing resolved", style=_DIM)
    text = Text()
    for entry in trail:
        colour = _DRIFT if entry.tainted else _AXIS
        # A MATRIX-origin trail detail is a case_key (``token=<value>``) that can
        # carry a declared secret; the backstop is a no-op on ref-name details.
        text.append(f"{redact(entry.path):<22}", style=_TEXT)
        text.append("← ", style=_DIM)
        text.append(redact(entry.detail), style=colour)
        if entry.tainted:
            text.append("  · masked", style=_DIM)
        elif entry.origin is Origin.VARIABLE:
            text.append("  · variable", style=_DIM)
        elif entry.origin is Origin.INSTANCE:
            text.append("  · instance", style=_DIM)
        text.append("\n")
    return text


#: Overlays (pushed modals) shadow the app tab keys, so their help lists only
#: the modal-scoped global keys, never the tab-switch / quit block.
_MODAL_HELP_SCREENS = frozenset(
    {
        "execution",
        "execution-cell",
        "execution-diff",
        "report-detail",
        "filter",
        "confirm",
        "graph",
        "picker",
        "curl",
        "matrix",
    }
)
_HELP_MODAL_GLOBAL = (("?", "show / close this help"), ("esc", "close this overlay"))
#: The fatal-error screen has no tab bar and no ContentSwitcher, so the 1-5 /
#: AZERTY tab keys are inert there; its help lists only the keys that work.
_HELP_ERROR_GLOBAL = (("?", "show / close this help"), ("q", "quit comparo"))
#: The launch transition binds only '?'; it closes itself when the run finishes.
_HELP_RUNNING_GLOBAL = (("?", "show / close this help"),)


def _help_body(screen: str) -> Text:
    text = Text()
    text.append(f"{_HELP_TITLE.get(screen, screen.upper())}\n\n", style=f"bold {_TEXT_HI}")
    # A screen key can be a combined token like "esc / bksp / q"; collect every
    # sub-token so a global row that repeats one of them is suppressed.
    shown = {part.strip() for key, _ in _HELP_SCREEN.get(screen, ()) for part in key.split("/")}
    for key, description in _HELP_SCREEN.get(screen, ()):
        _help_row(text, key, description)
    text.append("\nEVERYWHERE\n", style=f"bold {_LABEL}")
    globals_: tuple[tuple[str, str], ...]
    if screen == "error":
        globals_ = _HELP_ERROR_GLOBAL
    elif screen == "execution-running":
        globals_ = _HELP_RUNNING_GLOBAL
    elif screen in _MODAL_HELP_SCREENS:
        globals_ = _HELP_MODAL_GLOBAL
    else:
        globals_ = _HELP_GLOBAL
    for key, description in globals_:
        # Don't repeat a key the screen block already documented with a specific
        # meaning (e.g. matrix/filter 'esc' apply/clear vs the generic 'close').
        if key not in shown:
            _help_row(text, key, description)
    return text


def _help_row(text: Text, key: str, description: str) -> None:
    text.append(f"  {key:<8}", style=f"bold {_ACCENT}")
    text.append(f"  {description}\n", style=_TEXT)


def _json(value: object, redact: Callable[[str], str] = str) -> Syntax:
    # redact is the string-match backstop: a value equal to a declared secret can
    # arrive untainted (a plain literal or a non-secret variable), so the DISPLAY
    # sink alone would not mask it — mask the rendered text before it is shown.
    rendered = redact(json.dumps(value, indent=2, ensure_ascii=False))
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


def _check_result(check: Check) -> AssertionResult:
    """Adapt a run ``Check`` to an ``AssertionResult`` for the report roll-up."""
    return AssertionResult(check.name, "", check.ok, "error", check.detail, check.name)


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
    redact: Callable[[str], str] = str,
    *,
    focus: str = "all",
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
    head.append(redact(resolved.url if resolved else request.spec.request.endpoint), style=_TEXT_HI)
    root.add_leaf(head)
    if cell.key:
        root.add_leaf(Text.assemble(("case    ", _LABEL), (redact(cell.key), _AXIS)))
    glyph, colour = _RUN_GLYPH[state]
    status = Text.assemble(("status  ", _LABEL), (f"{glyph} {state}", colour))
    if execution is not None and execution.response is not None:
        response = execution.response
        status.append(f"   {response.status} · {response.elapsed_ms:.0f}ms", style=_TEXT)
    root.add_leaf(status)

    # RUN-27: the detail is switchable — Request · Response · Headers · Raw (and
    # the default "all" overview). Each mode carves the tree to one facet; the
    # RAW view dumps the unparsed request line and response body verbatim.
    if focus == "raw":
        _raw_detail_into(root, resolved, execution, redact)
        return
    want_request = focus in ("all", "request", "headers")
    want_response = focus in ("all", "response", "headers")
    want_meta = focus in ("all", "response")  # checks + metrics ride with the response
    headers_only = focus == "headers"

    if checks and want_meta:
        node = root.add(Text("CHECKS", style=f"bold {_LABEL}"), expand=True)
        for check in checks:
            mark, tint = ("✓", _SAME) if check.ok else ("✗", _DRIFT)
            detail = redact(check.detail)
            node.add_leaf(Text.assemble((f"{mark} {check.name}  ", tint), (detail, _DIM)))

    if execution is not None and execution.response is not None and want_meta:
        response = execution.response
        node = root.add(Text("METRICS", style=f"bold {_LABEL}"), expand=True)
        node.add_leaf(Text.assemble(("duration  ", _DIM), (f"{response.elapsed_ms:.0f} ms", _TEXT)))
        node.add_leaf(Text.assemble(("size      ", _DIM), (f"{len(response.body)} bytes", _TEXT)))

    if resolved is not None and want_request:
        node = root.add(Text("REQUEST", style=f"bold {_LABEL}"), expand=focus != "all")
        headers = node.add(Text("headers", style=_DIM), expand=headers_only)
        for key, value in resolved.headers:
            # The DISPLAY sink masks $secret refs; the string-match redactor is the
            # backstop for a hardcoded-literal secret (and a secret used as a name).
            shown = redact(str(value))
            masked = "••••" in shown
            headers.add_leaf(
                Text.assemble((f"{redact(key)}: ", _DIM), (shown, _DRIFT if masked else _TEXT))
            )
        if resolved.body is not None and not headers_only:
            _value_into(node.add(Text("body", style=_DIM), expand=False), resolved.body, redact)

    if execution is not None and execution.response is not None and want_response:
        response = execution.response
        node = root.add(Text("RESPONSE", style=f"bold {_LABEL}"), expand=True)
        headers = node.add(Text("headers", style=_DIM), expand=headers_only)
        for key, value in response.headers[:24]:
            headers.add_leaf(Text.assemble((f"{redact(key)}: ", _DIM), (redact(str(value)), _TEXT)))
        if not headers_only:
            body = node.add(Text("body", style=_DIM), expand=len(response.body) < 800)
            _body_into(body, response.body, _content_type(response.headers), redact)
    elif execution is not None and execution.error is not None and focus in ("all", "response"):
        root.add_leaf(Text(redact(execution.error), style=_DRIFT))
    elif state == "pending" and focus in ("all", "request", "response"):
        root.add_leaf(Text("not run — press x to execute", style=_DIM))


def _raw_detail_into(
    root: TreeNode[object],
    resolved: ResolvedRequest | None,
    execution: Execution | None,
    redact: Callable[[str], str] = str,
) -> None:
    """Render the RAW detail view (RUN-27) — request line and response verbatim.

    The outbound request line and the response body are shown unparsed. The
    decoded body passes through ``redact`` so a secret a server echoes back is
    masked here too.
    """
    if resolved is not None:
        node = root.add(Text("RAW REQUEST", style=f"bold {_LABEL}"), expand=True)
        node.add_leaf(Text(f"{resolved.method} {redact(resolved.url)}", style=_TEXT_HI))
        for key, value in resolved.headers:
            node.add_leaf(Text(f"{redact(str(key))}: {redact(str(value))}", style=_DIM))
    if execution is not None and execution.response is not None:
        response = execution.response
        node = root.add(Text("RAW RESPONSE", style=f"bold {_LABEL}"), expand=True)
        node.add_leaf(Text(f"HTTP {response.status}", style=_TEXT_HI))
        for key, value in response.headers[:24]:
            node.add_leaf(Text(f"{redact(str(key))}: {redact(str(value))}", style=_DIM))
        raw = response.body.decode("utf-8", "replace") if response.body else ""
        body = node.add(Text("body", style=_DIM), expand=True)
        for line in redact(raw).splitlines()[:200] or [""]:
            body.add_leaf(Text(line, style=_TEXT))
    elif execution is not None and execution.error is not None:
        root.add_leaf(Text(redact(execution.error), style=_DRIFT))


def _value_into(node: TreeNode[object], value: object, redact: Callable[[str], str] = str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _value_child(node, str(key), item, redact)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _value_child(node, f"[{index}]", item, redact)
    else:
        node.add_leaf(Text.assemble(_scalar(value, redact)))


def _value_child(
    node: TreeNode[object], key: str, value: object, redact: Callable[[str], str] = str
) -> None:
    key = redact(key)  # a server can echo a secret as a JSON key, not just a value
    if isinstance(value, dict):
        label = Text.assemble((key, _AXIS), (f"  {{{len(value)}}}", _DIM))
        _value_into(node.add(label, expand=False), value, redact)
    elif isinstance(value, list):
        label = Text.assemble((key, _AXIS), (f"  [{len(value)}]", _DIM))
        _value_into(node.add(label, expand=False), value, redact)
    else:
        node.add_leaf(Text.assemble((key, _AXIS), (": ", _DIM), _scalar(value, redact)))


def _scalar(value: object, redact: Callable[[str], str] = str) -> tuple[str, str]:
    if value is None:
        return "null", _AXIS
    if isinstance(value, bool):
        return str(value).lower(), _WARN
    if isinstance(value, int | float):
        return str(value), _WARN
    return redact(f'"{value}"'), _SAME


def _content_type(headers: list[tuple[str, str]]) -> str:
    for key, value in headers:
        if key.lower() == "content-type":
            return value.lower()
    return ""


def _body_into(
    node: TreeNode[object], body: bytes, content_type: str, redact: Callable[[str], str] = str
) -> None:
    text = body.decode("utf-8", "replace")
    if "event-stream" in content_type or text.startswith(("data:", "event:", "id:", "retry:")):
        _sse_into(node, text, redact)
        return
    if "json" in content_type or text[:1] in "{[":
        try:
            _value_into(node, json.loads(body), redact)
            return
        except (ValueError, TypeError):
            pass
    if "html" in content_type or text.lstrip()[:1] == "<":
        # Redact the whole body BEFORE truncating, so a secret straddling the cut
        # can never leak its prefix (the same rule _sv follows).
        _HtmlOutline(node).feed(redact(text)[:20000])
        return
    for line in redact(text)[:4000].splitlines()[:200]:
        node.add_leaf(Text(line, style=_TEXT))


def _sse_into(node: TreeNode[object], text: str, redact: Callable[[str], str] = str) -> None:
    events = _parse_sse(text)
    if not events:
        node.add_leaf(Text("(no events)", style=_DIM))
        return
    for index, event in enumerate(events):
        label = Text.assemble((f"event {index}", _AXIS))
        if event.get("event"):
            label.append(f"  {redact(event['event'])}", style=_ACCENT)
        entry = node.add(label, expand=len(events) <= 8)
        if event.get("id"):
            entry.add_leaf(Text.assemble(("id: ", _DIM), (redact(event["id"]), _TEXT)))
        data = event.get("data", "")
        try:
            _value_into(entry.add(Text("data", style=_DIM), expand=True), json.loads(data), redact)
        except (ValueError, TypeError):
            # Redact before the 200-char clip so a straddling secret can't leak.
            entry.add_leaf(Text.assemble(("data: ", _DIM), (redact(data)[:200], _TEXT)))


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
        head.append(" to diff the selected requests against both.", style=_DIM)
        parts.append(head)
    parts.append(_diff_legend())
    return Group(*parts)


def _seg_toggle(options: tuple[str, ...], active: str) -> Text:
    """A mockup-style pill toggle: the active segment reversed, the rest dim."""
    text = Text()
    for index, option in enumerate(options):
        on = option == active
        if index:
            text.append(" ", style=_DIM)
        text.append(
            f" {option} ", style=f"bold {_INK} on {_ACCENT}" if on else f"{_DIM} on #1b2230"
        )
    return text


def _diff_legend() -> Text:
    text = Text("\n")
    text.append("▏", style=_SAME)
    text.append(" identical   ", style=_DIM)
    text.append("▌", style=_DRIFT)
    text.append(" ", style=_DIM)
    text.append("drift", style=_DRIFT)
    text.append("   ╎", style=_SKIP)
    text.append(" not compared", style=_DIM)
    return text


def _replay_drift_summary(record: ReportRecord) -> Text:
    """``one field · N cells · one bug, not N`` — the matrix-grouping takeaway."""
    fields = len(_replay_drift_groups(record))
    cells = sum(row.drift for row in record.requests)
    field_word = "one field" if fields == 1 else f"{fields} fields"
    text = Text(f"{field_word} · {cells} cell{'' if cells == 1 else 's'}", style=_DIM)
    if fields == 1 and cells > 1:
        text.append(f" · one bug, not {cells}", style=_DIM)
    return text


def _diff_field(
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]],
    pair: tuple[Environment, Environment] | None,
    redact: Callable[[str], str] = str,
) -> Group:
    path, entries = group
    baseline = pair[0].metadata.name if pair else "A"
    candidate = pair[1].metadata.name if pair else "B"
    parts: list[RenderableType] = []
    header = Text(redact(path), style=f"bold {_DRIFT}")
    header.append(f"   drifts on {len(entries)} cell{'' if len(entries) == 1 else 's'}", style=_DIM)
    parts.append(header)
    for cell, field in entries:
        block = Text("\n")
        block.append(f"{redact(cell.cell_key) or cell.request.metadata.name}", style=_AXIS)
        block.append(f"   {field.mode}\n", style=_MODE.get(field.mode, _DIM))
        detail = redact(field.detail)  # mask a secret echoed into the drifted value
        before, sep, after = detail.partition(" → ")
        if sep:
            block.append("  ▌ ", style=_DRIFT)
            block.append(f"{baseline:<10}", style=_DIM)
            block.append(_clip(before), style=_SAME)
            block.append("\n  ▌ ", style=_DRIFT)
            block.append(f"{candidate:<10}", style=_DIM)
            block.append(_clip(after), style=_DRIFT)
            block.append("\n")
        else:
            block.append("  ▌ ", style=_DRIFT)
            block.append(_clip(detail) or "differs", style=_TEXT)
            block.append("\n")
        parts.append(block)
    parts.append(_diff_legend())
    hint = Text("\npress ", style=_DIM)
    hint.append("i", style=f"bold {_ACCENT}")
    hint.append(" to silence this field — writes an ignore rule to the profile", style=_DIM)
    parts.append(hint)
    return Group(*parts)


def _sv(value: object, redact: Callable[[str], str] = str) -> str:
    # Redact BEFORE truncating, so a long secret's prefix can never survive the
    # 60-char clip on its way to the screen.
    rendered = redact(json.dumps(value, ensure_ascii=False))
    return rendered if len(rendered) <= 60 else f"{rendered[:57]}..."


def _clip(text: str, limit: int = 80) -> str:
    """Truncate an already-redacted string for compact display."""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _pad_cells(text: str, width: int) -> str:
    """Left-justify *text* to a fixed terminal-*cell* width, clipping if too wide.

    Uses ``rich.cells.cell_len`` (display width) rather than ``len`` so wide
    Unicode names still align a following column, and clips with an ellipsis so an
    over-long name never pushes the column out of alignment.
    """
    if cell_len(text) > width:
        clipped = text
        while clipped and cell_len(clipped) > width - 1:
            clipped = clipped[:-1]
        return f"{clipped}…"
    return text + " " * (width - cell_len(text))


def _app_redact(widget: Widget) -> Callable[[str], str]:
    """The project's secret-redactor for a widget, or identity if no project is loaded."""
    project = cast("ComparoApp", widget.app).project
    return Redactor.for_project(project).text if project is not None else str


def _body_diff_lines(
    base: object,
    cand: object,
    states: dict[str, FieldDiff],
    path: str = "$",
    depth: int = 0,
    key: str | None = None,
    trailing: str = "",
    redact: Callable[[str], str] = str,
) -> list[tuple[int, str, str, str, str]]:
    """Walk both response trees, yielding (depth, left, right, state, note) rows.

    ``state`` is ``same`` / ``drift`` / ``skip`` from the profile's FieldDiff at
    that path (``context`` for structural braces); ``note`` carries the skip mode.
    ``redact`` masks secret values echoed into the response before they render.
    """
    label = f'"{redact(key)}": ' if key is not None else ""
    decided = states.get(path)
    if (
        decided is not None
        and decided.state.value in ("skip", "drift")
        and isinstance(base, (dict, list))
    ):
        # The profile decided this whole node at once (e.g. an ignored $.headers,
        # or a type/length drift) — collapse it rather than recursing in.
        if isinstance(base, dict):
            placeholder = f"{{ … {len(base)} keys … }}"
        else:
            placeholder = f"[ … {len(base)} items … ]"
        note = f"{decided.mode}  {redact(path)}" if decided.state.value == "skip" else ""
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
                    base[name], cand[name], states, child, depth + 1, name, tail, redact
                )
            elif name in base:
                left = f'"{redact(name)}": {_sv(base[name], redact)}{tail}'
                rows.append((depth + 1, left, "", "drift", ""))
            else:
                right = f'"{redact(name)}": {_sv(cand[name], redact)}{tail}'
                rows.append((depth + 1, "", right, "drift", ""))
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
                    base[index], cand[index], states, child, depth + 1, None, tail, redact
                )
            elif index < len(base):
                rows.append((depth + 1, f"{_sv(base[index], redact)}{tail}", "", "drift", ""))
            else:
                rows.append((depth + 1, "", f"{_sv(cand[index], redact)}{tail}", "drift", ""))
        rows.append((depth, f"]{trailing}", f"]{trailing}", "context", ""))
        return rows
    field = states.get(path)
    state = field.state.value if field is not None else "same"
    note = f"{field.mode}  {redact(path)}" if field is not None and state == "skip" else ""
    left = f"{label}{_sv(base, redact)}{trailing}"
    right = f"{label}{_sv(cand, redact)}{trailing}"
    return [(depth, left, right, state, note)]


def _band(content: RenderableType, bg: str, *, expand: bool = True) -> Table:
    """A single full-width row whose background *bg* fills the whole cell.

    Rich fills a cell's padding with the *row* style, not the cell renderable's
    style, so a one-row ``expand`` table is the reliable primitive for a band that
    spans the full width at any panel size — in both the unified and the
    side-by-side view.
    """
    table = Table(expand=expand, box=None, show_header=False, padding=(0, 1))
    table.add_column(ratio=1)
    table.add_row(content, style=f"on {bg}")
    return table


def _hunk_band(hunk_text: str) -> Table:
    """The purple ``@@ … @@`` header row that opens the diff well."""
    return _band(Text(hunk_text, style=f"bold {_AXIS}", no_wrap=True), _HUNK_BG)


def _diff_unified(lines: list[tuple[int, str, str, str, str]]) -> Group:
    """A git-style unified diff: one full-width band per line.

    Deleted (baseline) lines carry a muted-red band, added (candidate) lines a
    muted-green band, unchanged/skip lines the recessed well — each filling the
    whole width so the well reads as one contiguous block.
    """
    ink = {_DEL_BG: _DRIFT, _ADD_BG: _SAME, _DIFF_BG: _DIM}
    rows: list[RenderableType] = []
    for sign, body, bg in _unified_rows(lines):
        fg = ink[bg]
        line = Text(no_wrap=True)
        line.append(f"{sign} ", style=f"bold {fg}")
        line.append(body, style=fg)
        rows.append(_band(line, bg))
    return Group(*rows)


def _unified_rows(lines: list[tuple[int, str, str, str, str]]) -> list[tuple[str, str, str]]:
    """(sign, body, band-bg) per rendered diff line."""
    rendered: list[tuple[str, str, str]] = []
    for depth, left, right, state, note in lines:
        pad = "  " * depth
        if state == "drift":
            if left:
                rendered.append(("-", f"{pad}{left}", _DEL_BG))
            if right:
                rendered.append(("+", f"{pad}{right}", _ADD_BG))
        elif state == "skip":
            rendered.append(("⋯", f"{pad}{left}   skipped · {note}", _DIFF_BG))
        else:
            rendered.append((" ", f"{pad}{left}", _DIFF_BG))
    return rendered


def _diff_side_by_side(
    lines: list[tuple[int, str, str, str, str]],
    pair: tuple[Environment, Environment] | None,
    names: tuple[str, str] | None = None,
) -> Table:
    """A two-pane diff with the SAME full-width bands as the unified view.

    Each pane is a stack of banded cells (red on the baseline side, green on the
    candidate side, well-dark for context) so both views share one visual style.
    """
    baseline = names[0] if names else (pair[0].metadata.name if pair else "baseline")
    candidate = names[1] if names else (pair[1].metadata.name if pair else "candidate")
    left_col: list[RenderableType] = [_band(Text(baseline, style=f"bold {_DIM}"), _DIFF_BG)]
    right_col: list[RenderableType] = [_band(Text(candidate, style=f"bold {_DIM}"), _DIFF_BG)]
    for depth, left, right, state, note in lines:
        pad = "  " * depth
        if state == "drift":
            left_col.append(_band(Text(f"{pad}{left}", style=_DRIFT, no_wrap=True), _DEL_BG))
            right_col.append(_band(Text(f"{pad}{right}", style=_SAME, no_wrap=True), _ADD_BG))
        elif state == "skip":
            left_col.append(
                _band(Text(f"{pad}{left}  ⋯ {note}", style=_SKIP, no_wrap=True), _DIFF_BG)
            )
            right_col.append(_band(Text(f"{pad}{right}", style=_SKIP, no_wrap=True), _DIFF_BG))
        else:
            left_col.append(_band(Text(f"{pad}{left}", style=_DIM, no_wrap=True), _DIFF_BG))
            right_col.append(_band(Text(f"{pad}{right}", style=_DIM, no_wrap=True), _DIFF_BG))
    table = Table(expand=True, box=None, show_header=False, padding=0)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_row(Group(*left_col), Group(*right_col))
    return table


def _diff_slug(name: str) -> str:
    """A git-path-friendly slug of a request name (``Price quote`` → ``price-quote``)."""
    lowered = "".join(char if char.isalnum() else "-" for char in name.lower())
    return "-".join(filter(None, lowered.split("-"))) or "response"


def _diff_body_view(
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]],
    pair: tuple[Environment, Environment] | None,
    *,
    unified: bool,
    names: tuple[str, str] | None = None,
    redact: Callable[[str], str] = str,
    chrome: bool = True,
) -> Group:
    path, entries = group
    if not entries:
        return _diff_ready([], pair)
    cell = entries[0][0]
    if cell.baseline_body is None or cell.candidate_body is None:
        return _diff_field(group, pair, redact)  # non-JSON / error cell — fall back
    baseline = names[0] if names else (pair[0].metadata.name if pair else "a")
    candidate = names[1] if names else (pair[1].metadata.name if pair else "b")
    slug = _diff_slug(cell.request.metadata.name)
    outbound = cell.request.spec.request
    parent = path.rsplit(".", 1)[0] if "." in path.lstrip("$.") else path
    states = {field.path: field for field in cell.fields}
    lines = _body_diff_lines(cell.baseline_body, cell.candidate_body, states, redact=redact)
    adds = sum(1 for _, _, right, state, _ in lines if state == "drift" and right)
    dels = sum(1 for _, left, _, state, _ in lines if state == "drift" and left)
    # .difftitle — git command on the left, hunk/stat count right-aligned.
    title = Table(box=None, expand=True, show_header=False, padding=0)
    title.add_column(justify="left")
    title.add_column(justify="right")
    cmd = Text("diff ", style=_DIM)
    for marker, value in (("a/", baseline.lower()), ("b/", candidate.lower())):
        cmd.append(marker, style=f"bold {_ACCENT}")
        cmd.append(value, style=_DIM)
        cmd.append("/", style=f"bold {_ACCENT}")
        cmd.append(f"{slug}.json ", style=_DIM)
    stat = Text("1 hunk · ", style=_DIM)
    stat.append(f"+{adds}", style=_SAME)
    stat.append(" ", style=_DIM)
    stat.append(f"−{dels}", style=_DRIFT)
    title.add_row(cmd, stat)
    # .hunk — the request, the containing path, the cell case, the HTTP call.
    # The hunk header sits on its own muted-purple band, matching the mockup .hunk;
    # it pads to the same width as the body bands so the well reads as one block.
    case = f"  {redact(cell.cell_key)}" if cell.cell_key else ""
    hunk_text = (
        f"@@ {cell.request.metadata.name} · {redact(parent)} @@{case}  "
        f"{outbound.method} {redact(outbound.endpoint)}"
    )
    body = _diff_unified(lines) if unified else _diff_side_by_side(lines, pair, names)
    # The whole well — the purple hunk band and the banded body — sits inside one
    # rounded outline that fills the panel width, so it reads as a single unit.
    well = Panel(
        Group(_hunk_band(hunk_text), body),
        box=ROUNDED,
        expand=True,
        padding=0,
        border_style=_WELL_BORDER,
    )
    if not chrome:
        # Stacked (in-flow) diff: one legend is rendered once at the bottom, and the
        # per-cell insight/silence hints are suppressed to keep the stack readable.
        return Group(title, Text(), well)
    insight = Text(
        "\nthe same request is replayed against both sides — press o to diff the "
        "outbound and confirm the drift is the service's",
        style=_SAME,
    )
    hint = Text("\npress ", style=_DIM)
    hint.append("v", style=f"bold {_ACCENT}")
    hint.append(f" for {'side-by-side' if unified else 'unified'}    ", style=_DIM)
    hint.append("o", style=f"bold {_ACCENT}")
    hint.append(" outbound    ", style=_DIM)
    hint.append("i", style=f"bold {_ACCENT}")
    hint.append(" to silence this field", style=_DIM)
    return Group(title, Text(), well, _git_legend(baseline, candidate), insight, hint)


def _git_legend(baseline: str, candidate: str) -> Text:
    text = Text("\n")
    text.append("− ", style=f"bold {_DRIFT}")
    text.append("baseline ", style=_DIM)
    text.append(baseline, style=_TEXT)
    text.append("    + ", style=f"bold {_SAME}")
    text.append("candidate ", style=_DIM)
    text.append(candidate, style=_TEXT)
    text.append("    ⋯ ", style=_SKIP)
    text.append("skipped by profile", style=_DIM)
    text.append("    dim ", style=_DIM)
    text.append("= unchanged", style=_DIM)
    return text


def _diff_skip_view(
    path: str,
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]] | None,
    redact: Callable[[str], str] = str,
) -> Group:
    """Explain a path the DiffProfile skips — the tri-state's third leg."""
    parts: list[RenderableType] = []
    head = Text()
    head.append("◐ ", style=_SKIP)
    head.append(redact(path), style=f"bold {_SKIP}")
    head.append("   skipped by the diff profile", style=_DIM)
    parts.append(head)
    if group is not None:
        _, entries = group
        mode = entries[0][1].mode
        requests = sorted({cell.request.metadata.name for cell, _ in entries})
        info = Text("\nmode ", style=_DIM)
        info.append(mode, style=_MODE.get(mode, _SKIP))
        info.append(
            f"   ·   {len(entries)} cell{'' if len(entries) == 1 else 's'}   ·   ", style=_DIM
        )
        info.append(", ".join(requests), style=_AXIS)
        parts.append(info)
    body = Text("\n\nThis path is deliberately not compared", style=_SKIP)
    body.append(
        " — a volatile field (a\ntimestamp, an echoed request, a generated id) whose value\n"
        "legitimately differs between environments. It is neither\nsame nor drift.",
        style=_DIM,
    )
    parts.append(body)
    note = Text("\n\nskip stays visible: ", style=_DIM)
    note.append("green never means full coverage.", style=_SKIP)
    parts.append(note)
    return Group(*parts)


def _diff_error_view(
    cell: CellDiff,
    pair: tuple[Environment, Environment] | None,
    *,
    names: tuple[str, str] | None = None,
    redact: Callable[[str], str] = str,
) -> Group:
    """Explain a cell that failed to execute — which request, which env, the message."""
    if names is not None:
        baseline, candidate = names
    else:
        baseline = pair[0].metadata.name if pair else "baseline"
        candidate = pair[1].metadata.name if pair else "candidate"
    parts: list[RenderableType] = []
    head = Text()
    head.append("! ", style=f"bold {_WARN}")
    head.append(cell.request.metadata.name, style=f"bold {_TEXT_HI}")
    identifier = cell.request.metadata.id
    if identifier:
        head.append(f"   {identifier}", style=_DIM)
    parts.append(head)
    sub = Text("\n")
    sub.append(f"{baseline} ⇄ {candidate}", style=_DIM)
    if cell.cell_key:
        sub.append(f"   {redact(cell.cell_key)}", style=_AXIS)
    parts.append(sub)
    which, _, message = redact(cell.error or "unknown error").partition(": ")
    box = Text("\n\n")
    if which in ("baseline", "candidate"):
        box.append(f"the {which} request failed\n", style=f"bold {_WARN}")
        box.append(message or which, style=_DRIFT)
    else:
        box.append("the request failed\n", style=f"bold {_WARN}")
        box.append(redact(cell.error or "unknown error"), style=_DRIFT)
    parts.append(box)
    hint = Text("\n\nNo response to compare against. ", style=_DIM)
    hint.append("Confirm the environment is reachable, then press ", style=_DIM)
    hint.append("x", style=f"bold {_ACCENT}")
    hint.append(" to replay.", style=_DIM)
    parts.append(hint)
    return Group(*parts)


def _outbound_diff_view(
    baseline: ResolvedRequest,
    candidate: ResolvedRequest,
    base_env: Environment,
    cand_env: Environment,
    *,
    redact: Callable[[str], str] = str,
) -> Group:
    """Diff the resolved outbound request across the pair (DIFF-27).

    The same request is replayed against both environments, so the outbound only
    differs where env config does — a different base URL, a per-env auth token,
    an env-specific header or variable. Every value is redacted, and masked
    secrets compare equal, so a hidden token can never surface as a false drift.
    """
    parts: list[RenderableType] = []
    head = Text()
    head.append("OUTBOUND REQUEST", style=f"bold {_LABEL}")
    parts.append(head)
    legend = Text("\n")
    legend.append("− ", style=f"bold {_DRIFT}")
    legend.append(base_env.metadata.name, style=_DIM)
    legend.append("    + ", style=f"bold {_SAME}")
    legend.append(cand_env.metadata.name, style=_DIM)
    parts.append(legend)

    diffs: list[tuple[str, str, str]] = []

    def scalar(label: str, a: object, b: object) -> None:
        sa, sb = redact(str(a)), redact(str(b))
        if sa != sb:
            diffs.append((label, sa, sb))

    def mapping(
        prefix: str,
        a: list[tuple[str, object]] | dict[str, object],
        b: list[tuple[str, object]] | dict[str, object],
    ) -> None:
        am = a if isinstance(a, dict) else dict(a)
        bm = b if isinstance(b, dict) else dict(b)
        ad = {redact(str(k)): redact(str(v)) for k, v in am.items()}
        bd = {redact(str(k)): redact(str(v)) for k, v in bm.items()}
        for key in sorted(set(ad) | set(bd)):
            av, bv = ad.get(key, "—"), bd.get(key, "—")
            if av != bv:
                diffs.append((f"{prefix} {key}", av, bv))

    scalar("method", baseline.method, candidate.method)
    scalar("url", baseline.url, candidate.url)
    mapping("header", baseline.headers, candidate.headers)
    mapping("query", baseline.query, candidate.query)
    if baseline.body != candidate.body:
        diffs.append(("body", "differs — an env value is injected into the body", ""))

    body = Text()
    if not diffs:
        body.append("\n\n✓ identical on both sides", style=f"bold {_SAME}")
        body.append(
            "\n\nThe request we send is the same for both environments, so any"
            "\nresponse drift is the service's — not something we sent differently.",
            style=_DIM,
        )
    else:
        for label, a, b in diffs:
            body.append(f"\n\n{label}", style=_LABEL)
            body.append("\n  − ", style=f"bold {_DRIFT}")
            body.append(a or "—", style=_TEXT)
            if b:
                body.append("\n  + ", style=f"bold {_SAME}")
                body.append(b, style=_TEXT)
        body.append("\n\n⚠ the outbound differs across environments", style=f"bold {_WARN}")
        body.append(
            " — some response drift\nmay be explained by what you send (host, auth, vars), "
            "not the service.",
            style=_DIM,
        )
    parts.append(body)
    return Group(*parts)


def _req_short(request_id: str) -> str:
    return request_id.split(".", 1)[-1]


def _run_label(run_id: str | None) -> str:
    """Display a run id with the ``run-`` prefix used across the UI (``run-7f3a``)."""
    if not run_id:
        return "run"
    return run_id if run_id.startswith("run-") else f"run-{run_id}"


def _assert_tally(results: list[AssertionResult]) -> tuple[int, int, int]:
    passed = failed = warned = 0
    for result in results:
        if result.ok:
            passed += 1
        elif result.severity == "warn":
            warned += 1
        else:
            failed += 1
    return passed, failed, warned


def _assert_count_text(tally: tuple[int, int, int]) -> Text:
    passed, failed, warned = tally
    text = Text()
    text.append(f"{passed} ✓", style=_SAME)
    text.append(" · ", style=_DIM)
    text.append(f"{failed} ✗", style=_DRIFT if failed else _DIM)
    text.append(" · ", style=_DIM)
    text.append(f"{warned} !", style=_WARN if warned else _DIM)
    return text


def _exec_assert_rows(
    outcomes: list[CellOutcome], side: str
) -> tuple[tuple[int, int, int], list[tuple[str, AssertionResult]]]:
    rows: list[tuple[str, AssertionResult]] = []
    flat: list[AssertionResult] = []
    for outcome in outcomes:
        results = (
            outcome.baseline_assertions if side == "baseline" else outcome.candidate_assertions
        )
        for result in results:
            rows.append((outcome.request_id, result))
            flat.append(result)
    return _assert_tally(flat), rows


def _exec_assert_body(
    rows: list[tuple[str, AssertionResult]], redact: Callable[[str], str] = str
) -> Text:
    if not rows:
        return Text("no assertions on this side", style=_DIM)
    text = Text()
    for index, (request_id, result) in enumerate(rows):
        if result.ok:
            glyph, style = "✓", _SAME
        elif result.severity == "warn":
            glyph, style = "!", _WARN
        else:
            glyph, style = "✗", _DRIFT
        if index:
            text.append("\n")
        text.append(f"{glyph} ", style=style)
        text.append(f"{_req_short(request_id):<11}", style=f"bold {_TEXT_HI}")
        text.append(redact(result.label), style=style if not result.ok else _TEXT)
        text.append(f"   {_clip(redact(result.detail))}", style=_DIM)
    return text


def _drift_change(outcome: CellOutcome, redact: Callable[[str], str] = str) -> Text:
    diff = outcome.diff
    if diff is None or not diff.drifts:
        return Text("drift", style=_DRIFT)
    field = diff.drifts[0]
    text = Text(redact(field.path), style=_DRIFT)
    text.append(f"  {field.mode}", style=_DIM)
    detail = _clip(redact(field.detail))
    if detail:
        text.append(f"  {detail}", style=_DIM)
    if len(diff.drifts) > 1:
        text.append(f"  +{len(diff.drifts) - 1}", style=_DIM)
    return text


def _exec_drift_fields(
    outcomes: list[CellOutcome], redact: Callable[[str], str] = str
) -> list[tuple[str, str, str, int, str]]:
    order: list[str] = []
    mode: dict[str, str] = {}
    detail: dict[str, str] = {}
    count: dict[str, int] = {}
    requests: dict[str, list[str]] = {}
    for outcome in outcomes:
        if outcome.diff is None:
            continue
        for field in outcome.diff.drifts:
            if field.path not in count:
                order.append(field.path)
                mode[field.path] = field.mode
                detail[field.path] = _clip(redact(field.detail)) or "differs"
                count[field.path] = 0
                requests[field.path] = []
            count[field.path] += 1
            name = _req_short(outcome.request_id)
            if name not in requests[field.path]:
                requests[field.path].append(name)
    return [
        (redact(path), mode[path], detail[path], count[path], ", ".join(requests[path]))
        for path in order
    ]


def _exec_skip_paths(outcomes: list[CellOutcome]) -> list[str]:
    seen: list[str] = []
    for outcome in outcomes:
        if outcome.diff is None:
            continue
        for field in outcome.diff.fields:
            if field.state is State.SKIP and field.path not in seen:
                seen.append(field.path)
    return seen


def _field_skip_count(diff: CellDiff | None) -> int:
    if diff is None:
        return 0
    return sum(1 for field in diff.fields if field.state is State.SKIP)


def _exec_selected_requests(project: LoadedProject, profile: ExecutionProfile) -> list[Request]:
    """The requests an ExecutionProfile selects — its ``select`` tags / ids, or all."""
    requests = sorted(
        (obj for obj in project.objects.values() if isinstance(obj, Request)),
        key=lambda request: request.metadata.id or "",
    )
    select = profile.spec.select
    ids = set(select.requests or []) if select is not None else set()
    tags = set(select.tags or []) if select is not None else set()
    if not ids and not tags:
        return requests
    return [
        request
        for request in requests
        if request.metadata.id in ids or (tags & set(request.metadata.tags or []))
    ]


def _exec_env_names(project: LoadedProject, profile: ExecutionProfile) -> tuple[str, str | None]:
    """Resolve a profile's baseline / candidate environment *names* for display.

    Falls back to the raw refs when an environment cannot be resolved, so the
    launch preview never crashes on a mis-referenced profile.
    """
    envs = profile.spec.environments
    base_ref = envs.baseline if envs is not None else None
    cand_ref = envs.candidate if envs is not None else None
    try:
        baseline = select_environment(project, base_ref).metadata.name
    except EnvironmentSelectionError:
        baseline = base_ref or "—"
    candidate: str | None = None
    if cand_ref is not None:
        try:
            candidate = select_environment(project, cand_ref).metadata.name
        except EnvironmentSelectionError:
            candidate = cand_ref
    return baseline, candidate


def _exec_mode(profile: ExecutionProfile) -> str:
    """The check mode a profile runs: ``both`` / ``assert`` / ``diff``."""
    check = profile.spec.check
    do_assert = check.assertions if check is not None else True
    do_diff = check.diff if check is not None else True
    envs = profile.spec.environments
    do_diff = do_diff and (envs is not None and envs.candidate is not None)
    if do_assert and do_diff:
        return "both"
    return "assert" if do_assert else "diff"


def _exec_profiles_hint() -> Text:
    """The dim ``run an ExecutionProfile`` header line atop the PROFILES panel."""
    hint = Text("run an ", style=_DIM)
    hint.append("ExecutionProfile", style=f"bold {_AXIS}")
    return hint


def _exec_profile_card(
    project: LoadedProject,
    profile: ExecutionProfile,
    redact: Callable[[str], str] = str,
    caret: bool = False,
) -> Text:
    """A three-line profile card for the launch picker — name, id, envs · mode.

    The highlighted card is prefixed with an accent ``▸`` caret; the others align
    under it with a blank gutter.
    """
    baseline, candidate = _exec_env_names(project, profile)
    card = Text()
    card.append("▸ " if caret else "  ", style=_ACCENT)
    card.append(redact(profile.metadata.name), style=f"bold {_TEXT_HI}")
    card.append(f"\n  {profile.metadata.id or ''}", style=_DIM)
    card.append("\n  ", style=_DIM)
    card.append(redact(baseline), style=_DIM)
    if candidate is not None:
        card.append(" ⇄ ", style=_DIM)
        card.append(redact(candidate), style=_DIM)
    card.append(f"  {_exec_mode(profile)}", style=_ACCENT)
    return card


def _exec_plan_line(
    project: LoadedProject,
    profile: ExecutionProfile,
    request: Request,
    redact: Callable[[str], str],
) -> tuple[Text, int]:
    """One plan-preview row: the request, its call, its matrix scope, and the cell count."""
    scopes = profile.spec.matrix or {}
    cells = expand(project, request, scopes)
    method = request.spec.request.method
    endpoint = redact(request.spec.request.endpoint)
    row = Text("  ")
    row.append("● ", style=_SAME)
    row.append(f"{_clip(request.metadata.name, 13):<14}", style=_TEXT)
    row.append(f"{method} {endpoint} ", style=_DIM)
    targets = {inj.target for cell in cells for inj in cell.injections}
    if targets:
        matrix_ids = []
        cases: list[str] = []
        for target in sorted(targets):
            matrix = next(
                (
                    obj
                    for obj in project.objects.values()
                    if isinstance(obj, Matrix) and obj.spec.target == target
                ),
                None,
            )
            if matrix is not None:
                matrix_ids.append(matrix.metadata.id or matrix.metadata.name)
        seen = [redact(cell.key) for cell in cells if cell.key]
        for key in seen:
            if key not in cases:
                cases.append(key)
        row.append(f"  {', '.join(matrix_ids)}", style=_AXIS)
        row.append(f" → {_clip(', '.join(cases), 24)}", style=_DIM)
    else:
        row.append("  no matrix", style=_DIM)
    row.append(f"   ×{len(cells)}", style=_DIM)
    return row, len(cells)


def _exec_setup(
    project: LoadedProject, profile: ExecutionProfile, redact: Callable[[str], str] = str
) -> Group:
    """The launch SETUP panel — pair, mode, selection, and the counted plan preview."""
    baseline, candidate = _exec_env_names(project, profile)
    mode = _exec_mode(profile)
    parts: list[RenderableType] = []
    head = Text()
    head.append(redact(profile.metadata.name), style=f"bold {_TEXT_HI}")
    if profile.metadata.description:
        head.append(f"   {_clip(redact(profile.metadata.description), 44)}", style=_DIM)
    parts.append(head)
    pair = Text("\nbaseline   ", style=_DIM)
    pair.append(f" {redact(baseline)} ", style=f"bold {_TEXT_HI} on {_SYNTAX_BG}")
    pair.append("  ⇄   ", style=_SAME)
    pair.append("candidate  ", style=_DIM)
    pair.append(
        f" {redact(candidate) if candidate else '—'} ", style=f"bold {_TEXT_HI} on {_SYNTAX_BG}"
    )
    parts.append(pair)
    mode_line = Text("\nmode       ", style=_DIM)
    for option in ("assert", "diff", "both"):
        on = option == mode
        mode_line.append(
            f" {option} ", style=f"bold {_INK} on {_ACCENT}" if on else f"{_DIM} on {_SYNTAX_BG}"
        )
        mode_line.append(" ", style=_DIM)
    mode_line.append("  both = assert ∧ diff", style=_DIM)
    parts.append(mode_line)
    select = profile.spec.select
    sel = Text("\nselect     ", style=_DIM)
    if select is not None and select.tags:
        sel.append("tags ", style=_DIM)
        sel.append(", ".join(redact(tag) for tag in select.tags), style=f"bold {_AXIS}")
        sel.append(" ✓", style=_SAME)
    if select is not None and select.requests:
        sel.append("   requests ", style=_DIM)
        sel.append(", ".join(redact(_req_short(r)) for r in select.requests), style=_TEXT)
    elif select is not None and select.tags:
        # A tag-based select runs every request carrying the tag.
        sel.append("   requests ", style=_DIM)
        sel.append("all in tag", style=_TEXT)
    if select is None or (not select.tags and not select.requests):
        sel.append("all requests", style=_TEXT)
    parts.append(sel)
    parts.append(Text("\nplan preview", style=f"bold {_DIM}"))
    total = 0
    for request in _exec_selected_requests(project, profile):
        line, count = _exec_plan_line(project, profile, request, redact)
        parts.append(line)
        total += count
    summary = Text("\n  will run ", style=_DIM)
    summary.append(f"{total} cell{'' if total == 1 else 's'}", style=f"bold {_TEXT_HI}")
    if mode == "both":
        summary.append(" · assertions on both sides · diff the pair", style=_DIM)
    elif mode == "assert":
        summary.append(" · assertions on both sides", style=_DIM)
    else:
        summary.append(" · diff the pair", style=_DIM)
    parts.append(summary)
    return Group(*parts)


class _RunningRow(NamedTuple):
    """One cell in the live running transition — the in-flight cell or a finished one.

    ``variant``/``method_path``/``drift`` are already redacted by the view before the
    row is built, so ``_running_body`` never handles a raw declared secret.
    """

    request: str
    variant: str = ""
    method_path: str = ""
    status: int | None = None
    baseline_ms: int | None = None
    candidate_ms: int | None = None
    drift: str = ""


def _running_cell_name(row: _RunningRow, hi: bool = True) -> Text:
    """``Price quote · free`` — request name, matrix variant axis-purple.

    ``hi`` is True for the in-flight cell (bold+bright) and False for the finished
    log (normal weight) so the eye separates the cell in flight from the log.
    """
    name = Text(row.request, style=f"bold {_TEXT_HI}" if hi else _TEXT)
    if row.variant:
        name.append(f" · {row.variant}", style=_AXIS)
    return name


def _running_row_from_progress(
    event: ExecutionProgress, redact: Callable[[str], str] = str
) -> _RunningRow:
    """Build a redacted live row from an engine tick — no raw secret is stored."""
    method_path = f"{event.method} {redact(event.path)}" if event.method else ""
    drift_leaf = redact(event.drift).rsplit(".", 1)[-1] if event.drift else ""
    return _RunningRow(
        request=_req_short(event.request_id),
        variant=redact(event.cell_key) if event.cell_key else "",
        method_path=method_path,
        status=event.status,
        baseline_ms=event.baseline_ms,
        candidate_ms=event.candidate_ms,
        drift=drift_leaf,
    )


def _running_body(
    label: str,
    done: int,
    total: int,
    current: _RunningRow | None,
    recent: list[_RunningRow],
    glyphs: list[str],
) -> Group:
    """The live running transition — progress bar, cell in flight, finished cells.

    Shared by the Execution running sub-view and the Diff RUNNING state so both
    speak the same visual language.
    """
    parts: list[RenderableType] = []
    head = Text()
    head.append(label or "run", style=f"bold {_TEXT_HI}")
    head.append("   executing the plan…", style=_DIM)
    parts.append(head)
    width = 24
    filled = round(width * done / total) if total else 0
    bar = Text("\n")
    bar.append("█" * filled, style=_ACCENT)
    bar.append("░" * (width - filled), style=_DIM)
    bar.append(f"   {done}/{total or '…'} cells", style=_TEXT)
    bar.append("   ", style=_DIM)
    bar.append(f"{done} ✓", style=_SAME)
    bar.append("  ", style=_DIM)
    bar.append("0 ✗", style=_DIM)
    bar.append("  ~410ms/cell", style=_DIM)
    parts.append(bar)
    cur = Text("\n▸ ", style=_ACCENT)
    if (done < total or not total) and current is not None:
        cur.append("running ", style=_DIM)
        cur.append_text(_running_cell_name(current))
        if current.method_path:
            cur.append(f"     {current.method_path}", style=_DIM)
        cur.append("     stable ", style=_DIM)
        cur.append("◐", style=_WARN)
        cur.append("  candidate ", style=_DIM)
        cur.append("◐", style=_WARN)
    elif done < total or not total:
        cur.append("running …", style=_DIM)
    else:
        cur.append("finishing…", style=_DIM)
    parts.append(cur)
    if recent:
        parts.append(Text("\nrecently finished", style=f"bold {_DIM}"))
        log = Text()
        for index, row in enumerate(recent[-6:]):
            if index:
                log.append("\n")
            log.append("✓ ", style=_SAME)
            log.append_text(_running_cell_name(row, hi=False))
            if row.method_path:
                log.append(f"    {row.method_path}", style=_DIM)
            if row.status is not None:
                log.append(f"    {row.status}", style=_SAME)
            if row.baseline_ms is not None:
                log.append(f"  {row.baseline_ms}ms base", style=_DIM)
            if row.candidate_ms is not None:
                log.append(f"  {row.candidate_ms}ms cand", style=_DIM)
            if row.drift:
                log.append(f"   ✗ {row.drift} drift", style=_DRIFT)
        parts.append(log)
    plan = Text("\nlive plan   ", style=f"bold {_DIM}")
    for glyph in glyphs:
        colour = _SAME if glyph == "●" else _WARN if glyph == "◐" else _DIM
        plan.append(glyph, style=colour)
    plan.append("   each glyph = one cell, updating as the engine ticks", style=_DIM)
    parts.append(plan)
    return Group(*parts)


def _exec_stacked_diff(
    drifted: list[CellOutcome],
    baseline: str,
    candidate: str | None,
    *,
    unified: bool,
    redact: Callable[[str], str] = str,
) -> Group:
    """The run's scoped body diff — every drifted cell stacked as a git-style well."""
    head = Text()
    head.append(baseline, style=_TEXT_HI)
    head.append(" ● ⇄ ", style=_SAME)
    head.append(candidate or "—", style=f"bold {_TEXT_HI}")
    head.append(
        f"    {len(drifted)} drifted cell(s) · one field, grouped across the matrix",
        style=_DIM,
    )
    parts: list[RenderableType] = [head, Text()]
    names = (baseline, candidate or "candidate")
    for outcome in drifted:
        crumb = Text("▸ ", style=_DRIFT)
        crumb.append(_req_short(outcome.request_id), style=f"bold {_TEXT_HI}")
        if outcome.cell_key:
            crumb.append(f" · {redact(outcome.cell_key)}", style=_AXIS)
        request = outcome.diff.request if outcome.diff is not None else None
        if request is not None:
            crumb.append(
                f"    {request.spec.request.method} {redact(request.spec.request.endpoint)}",
                style=_DIM,
            )
        parts.append(crumb)
        if outcome.error is not None and outcome.diff is not None:
            parts.append(_diff_error_view(outcome.diff, None, names=names, redact=redact))
        elif outcome.diff is not None:
            entries = [(outcome.diff, field) for field in outcome.diff.drifts]
            path = entries[0][1].path if entries else "$"
            parts.append(
                _diff_body_view(
                    (path, entries),
                    None,
                    unified=unified,
                    names=names,
                    redact=redact,
                    chrome=False,
                )
            )
        else:
            parts.append(Text("no diff computed for this cell", style=_DIM))
        parts.append(Text())
    # One shared git legend at the bottom of the stack — not repeated per cell.
    parts.append(_git_legend(baseline, candidate or "candidate"))
    return Group(*parts)


def _exec_header(
    profile: ExecutionProfile, result: ExecutionResult, redact: Callable[[str], str] = str
) -> Group:
    """The results EXECUTION panel — two rows so nothing clips at 104 cols.

    Row 1: the profile + its id + the baseline/candidate pair.
    Row 2: the mode, the select clause, and the counted plan (``req x2 · req x1``).
    """
    line1 = Text()
    line1.append("ExecutionProfile ", style=_DIM)
    line1.append(profile.metadata.name, style=f"bold {_TEXT_HI}")
    line1.append(f"  {profile.metadata.id or ''}", style=_DIM)
    line1.append("    baseline ", style=_DIM)
    line1.append(redact(result.baseline), style=_TEXT_HI)
    line1.append(" ●", style=_SAME)
    if result.candidate is not None:
        line1.append(" · candidate ", style=_DIM)
        line1.append(redact(result.candidate), style=_TEXT_HI)
        line1.append(" ●", style=_SAME)
    if result.checked_assertions and result.checked_diff:
        mode, detail = "both", " assertions + diff"
    elif result.checked_assertions:
        mode, detail = "assert", ""
    else:
        mode, detail = "diff", ""
    line2 = Text()
    line2.append("mode ", style=_DIM)
    line2.append(mode, style=f"bold {_ACCENT}")
    line2.append(detail, style=_DIM)
    select = profile.spec.select
    if select is not None and (select.tags or select.requests):
        clauses = []
        if select.tags:
            clauses.append("tags " + ", ".join(redact(tag) for tag in select.tags))
        if select.requests:
            clauses.append("requests " + ", ".join(redact(_req_short(r)) for r in select.requests))
        line2.append("    select ", style=_DIM)
        line2.append(" · ".join(clauses), style=_TEXT_HI)
    # plan — per-request cell counts, in first-seen order (Price quote x2 · Checkout x1)
    order: list[str] = []
    counts: dict[str, int] = {}
    for outcome in result.outcomes:
        name = _req_short(outcome.request_id)
        if name not in counts:
            order.append(name)
            counts[name] = 0
        counts[name] += 1
    if order:
        line2.append("    plan ", style=_DIM)
        line2.append(" · ".join(f"{redact(name)} ×{counts[name]}" for name in order), style=_TEXT)
    return Group(line1, line2)


def _exec_diff_summary(result: ExecutionResult, redact: Callable[[str], str] = str) -> Text:
    outcomes = result.outcomes
    calls = len(outcomes)
    drift, errors = result.drift, result.errors
    same = calls - drift - errors
    skipped = sum(_field_skip_count(outcome.diff) for outcome in outcomes)
    text = Text()
    text.append(f"{same} same", style=f"bold {_SAME}")
    text.append(" · ", style=_DIM)
    text.append(f"{drift} drift", style=f"bold {_DRIFT}" if drift else _DRIFT)
    text.append(" · ", style=_DIM)
    text.append(f"{errors} error", style=f"bold {_WARN}" if errors else _WARN)
    text.append(" · ", style=_DIM)
    text.append(f"{skipped} skipped", style=_SKIP)
    for path, mode, detail, count, requests in _exec_drift_fields(outcomes, redact):
        text.append("\n✗ ", style=_DRIFT)
        text.append(path, style=_DRIFT)
        text.append(f"  {mode} · ", style=_DIM)
        text.append(detail, style=_DRIFT)
        text.append(f" · {requests} ×{count}", style=_DIM)
    return text


def _exec_diff_legend(result: ExecutionResult, redact: Callable[[str], str] = str) -> Text:
    skips = _exec_skip_paths(result.outcomes)
    text = Text()
    if skips:
        for index, path in enumerate(skips):
            if index:
                text.append(" · ", style=_DIM)
            text.append(f"◐ {redact(path)}", style=_SKIP)
        text.append(" skipped by the diff profile (volatile) — ", style=_DIM)
        text.append("⏎", style=f"bold {_TEXT_HI}")
        text.append(" drills in.", style=_DIM)
    else:
        text.append("no paths skipped — ", style=_DIM)
        text.append("⏎", style=f"bold {_TEXT_HI}")
        text.append(" on a drifted cell drills in.", style=_DIM)
    return text


def _exec_gate_body(result: ExecutionResult) -> Group:
    parts: list[RenderableType] = []
    if result.passed:
        parts.append(Text("✓ gate: PASS", style=f"bold {_SAME}"))
    else:
        parts.append(Text("✗ gate: FAIL", style=f"bold {_DANGER}"))
    base_fail = sum(_assert_tally(o.baseline_assertions)[1] for o in result.outcomes)
    cand_fail = sum(_assert_tally(o.candidate_assertions)[1] for o in result.outcomes)
    warns = sum(
        _assert_tally(o.baseline_assertions)[2] + _assert_tally(o.candidate_assertions)[2]
        for o in result.outcomes
    )
    untriaged = result.drift + result.errors
    assert_failed = base_fail + cand_fail
    narrative = Text("\n")
    if result.passed:
        narrative.append("assertions hold on both sides and nothing drifted.", style=_DIM)
    elif assert_failed > 0:
        # Do not paint a red run green: name the failing assertions as a blocker.
        narrative.append("assertions ", style=_DIM)
        narrative.append("FAIL", style=_DRIFT)
        narrative.append(
            f" (baseline {base_fail} ✗ · candidate {cand_fail} ✗ · {warns} warn)", style=_DIM
        )
        if untriaged:
            noun = "drift" if untriaged == 1 else "drifts"
            narrative.append(" and ", style=_DIM)
            narrative.append(f"{untriaged} untriaged {noun}", style=_DRIFT)
        narrative.append(" block the run.", style=_DIM)
    else:
        narrative.append("assertions ", style=_DIM)
        narrative.append("pass", style=_SAME)
        narrative.append(f" on both sides ({warns} warn) — but ", style=_DIM)
        noun = "drift" if untriaged == 1 else "drifts"
        narrative.append(f"{untriaged} untriaged {noun}", style=_DRIFT)
        narrative.append(" block the run. Triage (", style=_DIM)
        narrative.append("i", style=f"bold {_ACCENT}")
        narrative.append("/", style=_DIM)
        narrative.append("x", style=f"bold {_ACCENT}")
        narrative.append(").", style=_DIM)
    parts.append(narrative)
    exit_code = 0 if result.passed else 1
    parity = Text(f"\nexit code {exit_code}", style=_SAME if result.passed else _DRIFT)
    parity.append(" — matches headless ", style=_DIM)
    parity.append(f"comparo exec {result.profile_id}", style=_ACCENT)
    parts.append(parity)
    return Group(*parts)


def _exec_foot(result: ExecutionResult) -> Table:
    table = Table(box=None, expand=True, show_header=False, padding=0)
    table.add_column(justify="left")
    table.add_column(justify="right")
    has_drift = any(
        outcome.error is not None or (outcome.diff is not None and outcome.diff.drifted)
        for outcome in result.outcomes
    )
    # ↑↓ section and ⏎ cell only do something when there are drifted cells to
    # navigate; on a clean pass the drift table is empty, so drop them.
    hints: tuple[tuple[str, str], ...] = (
        (("↑↓", "section"), ("⏎", "cell")) if has_drift else ()
    ) + (
        ("d", "diff"),
        ("e", "report"),
        ("r", "re-run"),
        ("?", "help"),
        ("esc/⌫/q", "close"),
    )
    keys = Text()
    for key, label in hints:
        keys.append(f"{key} ", style=f"bold {_TEXT_HI}")
        keys.append(f"{label}   ", style=_DIM)
    exit_code = 0 if result.passed else 1
    table.add_row(keys, Text(f"{result.profile_id} · exit {exit_code}", style=_DIM))
    return table


def _cell_verdict(outcome: CellOutcome, redact: Callable[[str], str] = str) -> Group:
    parts: list[RenderableType] = []
    _, base_fail, base_warn = _assert_tally(outcome.baseline_assertions)
    _, cand_fail, cand_warn = _assert_tally(outcome.candidate_assertions)
    line = Text()
    if base_fail == 0 and cand_fail == 0:
        line.append("assertions pass", style=_SAME)
        warns = base_warn + cand_warn
        line.append(
            f"   both sides · {warns} warn (non-blocking)" if warns else "   both sides",
            style=_DIM,
        )
    else:
        line.append("assertions fail", style=_DRIFT)
        line.append(f"   baseline {base_fail} ✗ · candidate {cand_fail} ✗", style=_DIM)
    parts.append(line)
    second = Text("\n")
    if outcome.error is not None:
        second.append("error", style=_WARN)
        second.append(f"   {redact(outcome.error)}", style=_DIM)
    elif outcome.diff is not None and outcome.diff.drifted:
        field = outcome.diff.drifts[0]
        second.append("diff drift", style=_DRIFT)
        second.append(f"   {redact(field.path)} · {field.mode}", style=_DIM)
    else:
        second.append("no drift", style=_SAME)
    parts.append(second)
    if outcome.error is None and outcome.diff is not None and outcome.diff.drifted:
        third = Text("\nuntriaged — press ", style=_DIM)
        third.append("i", style=f"bold {_ACCENT}")
        third.append(" to ignore", style=_DIM)
        parts.append(third)
    return Group(*parts)


def _relative_age(created: str) -> str:
    """A compact age like ``12m`` / ``2h`` / ``1d`` from an ISO timestamp."""
    try:
        when = datetime.fromisoformat(created)
    except ValueError:
        return ""
    seconds = int((datetime.now() - when).total_seconds())
    if seconds < 60:
        return "now" if seconds < 5 else f"{max(seconds, 0)}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def _envs_label(record: ReportRecord) -> str:
    if record.candidate is None:
        return record.baseline
    return f"{record.baseline}⇄{record.candidate}"


def _rel_dir(project: LoadedProject, path: Path) -> str:
    try:
        return str(path.relative_to(project.root))
    except ValueError:
        return str(path)


def _gate_banner(record: ReportRecord) -> Table:
    table = Table(box=None, expand=True, show_header=False, padding=0)
    table.add_column(justify="left")
    table.add_column(justify="right")
    glyph = "✓" if record.gate == "PASS" else ("!" if record.gate == "ERROR" else "✗")
    colour = _GATE_COLOR.get(record.gate, _DIM)
    left = Text(f"{glyph} gate: {record.gate}", style=f"bold {colour}")
    # Mockup: "<execution> · run-<id> · saved <age> ago" — profile name first, then
    # the run id, then the age; the envs live in the list's ENVS column, not here.
    meta = Text()
    if record.execution:
        meta.append(record.execution, style=_TEXT_HI)
        meta.append(" · ", style=_DIM)
    meta.append(_run_label(record.id), style=_TEXT_HI)
    age = _relative_age(record.created)
    meta.append(f" · saved {age} ago" if age else f" · {record.created}", style=_DIM)
    table.add_row(left, meta)
    return table


def _assert_counts(summary: AssertionSummary) -> Text:
    text = Text()
    text.append(f"{summary.passed} ✓", style=_SAME)
    text.append(" · ", style=_DIM)
    text.append(f"{summary.failed} ✗", style=_DRIFT if summary.failed else _DIM)
    text.append(" · ", style=_DIM)
    text.append(f"{summary.warned} !", style=_WARN if summary.warned else _DIM)
    return text


_ASSERT_GLYPH = {"pass": ("✓", _SAME), "warn": ("!", _WARN), "fail": ("✗", _DRIFT)}


def _assert_lines(summary: AssertionSummary) -> RenderableType:
    if not summary.lines:
        return Text("no assertions — ad-hoc diff run", style=_DIM)
    text = Text()
    for index, line in enumerate(summary.lines):
        glyph, colour = _ASSERT_GLYPH.get(line.state, ("·", _DIM))
        if index:
            text.append("\n")
        text.append(f"{glyph} ", style=colour)
        text.append(line.label, style=colour)
        text.append(f"   {line.detail}", style=_DIM)
    return text


def _breakdown_legend(record: ReportRecord) -> Text:
    drifted = [row for row in record.requests if row.drift]
    text = Text()
    if drifted:
        for row in drifted:
            text.append(f"{row.request}", style=_DRIFT)
            if row.drift_paths:
                text.append(f" drifted on {', '.join(row.drift_paths)}\n", style=_DIM)
            else:
                # A legacy/foreign archive can carry a count without the paths.
                text.append(
                    f" drifted on {row.drift} field(s) · paths not recorded, re-run to name them\n",
                    style=_DIM,
                )
        text.append("⏎", style=f"bold {_TEXT_HI}")
        text.append(" deep-dives here — every drifted field, in place. ", style=_DIM)
    else:
        text.append("no drift under the compared paths. ", style=_DIM)
    text.append("skip stays visible: ", style=_DIM)
    text.append("green never means full coverage.", style=_SKIP)
    return text


def _breakdown_table(record: ReportRecord) -> Table:
    table = _table()
    table.add_column("REQUEST", style=_TEXT_HI, no_wrap=True)
    table.add_column("SAME", justify="right", width=6)
    table.add_column("DRIFT", justify="right", width=6)
    table.add_column("SKIP", justify="right", width=6)
    table.add_column("VERDICT", justify="right", width=9)
    for row in record.requests:
        if row.verdict == "error":
            verdict = Text("! error", style=_WARN)
        elif row.verdict == "drift":
            verdict = Text("✗ drift", style=_DRIFT)
        elif row.verdict == "fail":
            verdict = Text("✗ fail", style=_DRIFT)
        else:
            verdict = Text("✓ pass", style=_SAME)
        table.add_row(
            Text(row.request, style=_TEXT_HI),
            Text(str(row.same), style=_SAME if row.same else _DIM),
            Text(str(row.drift), style=_DRIFT if row.drift else _DIM),
            Text(str(row.skip), style=_SKIP if row.skip else _DIM),
            verdict,
        )
    return table


def _record_kind(record: ReportRecord) -> str:
    """Classify a saved record: ``execution`` (named), ``run`` (no candidate), else ``diff``."""
    if record.execution is not None:
        return "execution"
    if record.candidate is None:
        return "run"
    return "diff"


def _report_reading_pane(record: ReportRecord) -> Group:
    """The step-1 reading pane: gate line, stat pills, assertion roll-ups, breakdown."""
    parts: list[RenderableType] = [_gate_banner(record), Text()]
    pills = Text()
    for label, value, colour in (
        ("calls", record.calls, _TEXT_HI),
        ("same", record.same, _SAME),
        ("drift", record.drift, _DRIFT if record.drift else _DIM),
        ("error", record.error, _WARN if record.error else _DIM),
        ("skipped", record.skipped, _SKIP),
    ):
        pills.append(f" {value} ", style=f"bold {colour} on {_SYNTAX_BG}")
        pills.append(f" {label} ", style=_DIM)
    parts += [pills, Text()]
    for title, env, summary in (
        ("Assertions · ", record.baseline, record.baseline_assertions),
        ("Assertions · ", record.candidate or "—", record.candidate_assertions),
    ):
        line = Text(title, style=_DIM)
        line.append(f"{env}   ", style=f"bold {_TEXT_HI}")
        line.append_text(_assert_counts(summary))
        parts.append(line)
    parts.append(Text())
    parts.append(Text.from_markup(f"[bold {_DIM}]DIFF BREAKDOWN[/] [{_DIM}]· per request[/]"))
    # Pad the request name to a fixed *cell* width (not len()) so the same/drift/skip
    # bars line up in a column regardless of name length or wide Unicode glyphs.
    for row in record.requests:
        line = Text("  ")
        line.append(_pad_cells(row.request, 14), style=_TEXT_HI)
        line.append("same ", style=_DIM)
        line.append(
            "██" if row.same and not row.drift else "░░",
            style=_SAME if row.same and not row.drift else _DIM,
        )
        line.append(" drift ", style=_DIM)
        line.append("██" if row.drift else "░░", style=_DRIFT if row.drift else _DIM)
        line.append(" skip ", style=_DIM)
        line.append("▓▓" if row.skip else "░░", style=_SKIP if row.skip else _DIM)
        cells = row.same + row.drift + row.skip
        line.append(f"   {cells} cell{'' if cells == 1 else 's'}", style=_DIM)
        parts.append(line)
    parts.append(Text())
    hint = Text("press ", style=_DIM)
    hint.append("⏎", style=f"bold {_TEXT_HI}")
    hint.append(" opens the full analysis\n", style=_ACCENT)
    hint.append("the Diff/Run panels, read-only, in this tab", style=_ACCENT)
    parts.append(hint)
    return Group(*parts)


def _replay_banner(record: ReportRecord, kind: str) -> Text:
    """The purple 'analyzing a saved diff/run' banner atop an in-tab replay."""
    noun = "diff" if kind == "diff" else "run"
    tail = (
        "read-only replay — no requests re-sent"
        if kind == "diff"
        else "read-only replay of the Run screen"
    )
    banner = Text()
    banner.append(f" ▸ analyzing a saved {noun} ", style=f"bold {_INK} on {_AXIS}")
    meta = f"  {record.id}"
    if record.execution:
        meta += f" · {record.execution}"
    age = _relative_age(record.created)
    meta += f" · saved {age + ' ago' if age else record.created}  "
    banner.append(meta, style=_DIM)
    banner.append(tail, style=_AXIS)
    return banner


def _cell_label(cell: "CellRecord") -> str:
    """``Price quote · free`` — the request and its matrix variant, if any."""
    return cell.request + (f" · {cell.variant}" if cell.variant else "")


def _replay_path_groups(
    record: ReportRecord, pick: "Callable[[CellRecord], list[str]]"
) -> list[tuple[str, list[str]]]:
    """Group saved paths across cells: one field → the ``request · variant`` cells it hit."""
    order: list[str] = []
    hits: dict[str, list[str]] = {}
    for cell in record.cells:
        label = _cell_label(cell)
        for path in pick(cell):
            if path not in hits:
                order.append(path)
                hits[path] = []
            if label not in hits[path]:
                hits[path].append(label)
    return [(path, hits[path]) for path in order]


def _replay_drift_groups(record: ReportRecord) -> list[tuple[str, list[str]]]:
    """Group the saved drift paths across cells: one field → the cells it hit."""
    if record.cells:
        return _replay_path_groups(record, lambda cell: cell.drift_paths)
    # Older records without cell detail fall back to the per-request breakdown.
    order: list[str] = []
    hits: dict[str, list[str]] = {}
    for row in record.requests:
        for path in row.drift_paths:
            if path not in hits:
                order.append(path)
                hits[path] = []
            if row.request not in hits[path]:
                hits[path].append(row.request)
    return [(path, hits[path]) for path in order]


def _replay_skip_groups(record: ReportRecord) -> list[tuple[str, list[str]]]:
    """Group the saved skipped paths across cells: one field → the cells it hit."""
    return _replay_path_groups(record, lambda cell: cell.skip_paths)


def _replay_diff_cell(record: ReportRecord) -> "CellRecord | None":
    """The first saved cell with a drift and both bodies — the well to replay."""
    for cell in record.cells:
        if cell.drift_paths and cell.baseline_body is not None and cell.candidate_body is not None:
            return cell
    return None


def _replay_compare_well(
    record: ReportRecord, unified: bool, redact: Callable[[str], str] = str
) -> Group:
    """The read-only COMPARE well for a saved diff, replayed from the archive.

    When the record stores per-cell bodies, the well reconstructs the REAL unified
    body diff (context lines plus git delete/add bands over the drifted fields), the
    same shape as the live Diff tab — sourced from disk, nothing re-sent.
    """
    cell = _replay_diff_cell(record)
    if cell is None:
        return _replay_compare_path_well(record, redact)
    baseline = record.baseline
    candidate = record.candidate or "b"
    slug = _diff_slug(cell.request)
    drift_path = cell.drift_paths[0]
    parent = drift_path.rsplit(".", 1)[0] if "." in drift_path.lstrip("$.") else drift_path
    title = Table(box=None, expand=True, show_header=False, padding=0)
    title.add_column(justify="left")
    cmd = Text("diff ", style=_DIM)
    for marker, value in (("a/", baseline.lower()), ("b/", candidate.lower())):
        cmd.append(marker, style=f"bold {_ACCENT}")
        cmd.append(value, style=_DIM)
        cmd.append("/", style=f"bold {_ACCENT}")
        cmd.append(f"{slug}.json ", style=_DIM)
    title.add_row(cmd)
    # Rebuild the profile decision (drift / skip) per path from the saved paths.
    states: dict[str, FieldDiff] = {p: FieldDiff(p, State.DRIFT, "exact") for p in cell.drift_paths}
    for path in cell.skip_paths:
        states.setdefault(path, FieldDiff(path, State.SKIP, "ignore"))
    lines = _body_diff_lines(cell.baseline_body, cell.candidate_body, states, redact=redact)
    body = (
        _diff_unified(lines) if unified else _diff_side_by_side(lines, None, (baseline, candidate))
    )
    case = f"  {cell.variant}" if cell.variant else ""
    call = f"   {cell.method} {cell.path}" if cell.method else ""
    hunk = f"@@ {cell.request} · {redact(parent)} @@{case}{call}"
    well = Panel(
        Group(_hunk_band(hunk), body),
        box=ROUNDED,
        expand=True,
        padding=0,
        border_style=_WELL_BORDER,
    )
    legend = Text("\n")
    legend.append("− ", style=f"bold {_DRIFT}")
    legend.append("baseline ", style=_DIM)
    legend.append(baseline, style=_DIM)
    legend.append("    + ", style=f"bold {_SAME}")
    legend.append("candidate ", style=_DIM)
    legend.append(record.candidate or "—", style=_DIM)
    note = Text(f"\nreplayed from reports/{record.id}.json", style=_AXIS)
    return Group(title, Text(), well, legend, note)


def _replay_compare_path_well(record: ReportRecord, redact: Callable[[str], str] = str) -> Group:
    """Fallback COMPARE well for older records with drift paths but no saved bodies."""
    groups = _replay_drift_groups(record)
    if not groups:
        return Group(Text("no drift under the compared paths — nothing to replay", style=_DIM))
    request = (
        groups[0][1][0]
        if groups[0][1]
        else (record.requests[0].request if record.requests else "response")
    )
    parent = groups[0][0].rsplit(".", 1)[0] if "." in groups[0][0].lstrip("$.") else groups[0][0]
    slug = _diff_slug(request)
    title = Table(box=None, expand=True, show_header=False, padding=0)
    title.add_column(justify="left")
    cmd = Text("diff ", style=_DIM)
    for marker, value in (
        ("a/", record.baseline.lower()),
        ("b/", (record.candidate or "b").lower()),
    ):
        cmd.append(marker, style=f"bold {_ACCENT}")
        cmd.append(value, style=_DIM)
        cmd.append("/", style=f"bold {_ACCENT}")
        cmd.append(f"{slug}.json ", style=_DIM)
    title.add_row(cmd)
    hunk = f"@@ {request} · {redact(parent)} @@  saved replay"
    rows: list[RenderableType] = [_hunk_band(hunk)]
    for path, requests in groups:
        drift_line = Text(no_wrap=True)
        drift_line.append("✗ ", style=f"bold {_DRIFT}")
        drift_line.append(f"{redact(path)}", style=_DRIFT)
        drift_line.append(f"   drifted · {', '.join(requests)}", style=_DRIFT)
        rows.append(_band(drift_line, _DEL_BG))
        add_line = Text(no_wrap=True)
        add_line.append("+ ", style=f"bold {_SAME}")
        add_line.append(f"{redact(path)}", style=_SAME)
        add_line.append("   candidate value differs (see live diff for the body)", style=_SAME)
        rows.append(_band(add_line, _ADD_BG))
    well = Panel(Group(*rows), box=ROUNDED, expand=True, padding=0, border_style=_WELL_BORDER)
    legend = Text("\n")
    legend.append("− ", style=f"bold {_DRIFT}")
    legend.append("baseline ", style=_DIM)
    legend.append(record.baseline, style=_TEXT)
    legend.append("    + ", style=f"bold {_SAME}")
    legend.append("candidate ", style=_DIM)
    legend.append(record.candidate or "—", style=_TEXT)
    note = Text(f"\nreplayed from reports/{record.id}.json", style=_AXIS)
    return Group(title, Text(), well, legend, note)


def _replay_run_progress(record: ReportRecord) -> Text:
    """The saved-run replay progress line — an archived, greyed-out Run bar."""
    text = Text("  run ", style=_DIM)
    text.append(record.id, style=f"bold {_ACCENT}")
    text.append(f"   {record.baseline}", style=_TEXT_HI)
    text.append("   ", style=_DIM)
    width = 24
    text.append("━" * width, style=_SAME)
    passed = record.baseline_assertions.passed
    failed = record.baseline_assertions.failed
    total = record.calls or (passed + failed)
    text.append(f"   {total}/{total}", style=f"bold {_TEXT_HI}")
    text.append("  ·  ", style=_DIM)
    text.append(f"{passed} ✓", style=_SAME)
    text.append("  ", style=_DIM)
    text.append(f"{failed} ✗", style=_DRIFT if failed else _DIM)
    text.append("      archived · not re-sent", style=_DIM)
    return text


def _request_latencies(record: ReportRecord) -> dict[str, list[int]]:
    """Per-request cell latencies from the saved cells, for the P50 column."""
    out: dict[str, list[int]] = {}
    for cell in record.cells:
        if cell.latency_ms is not None:
            out.setdefault(cell.request, []).append(cell.latency_ms)
    return out


def _p50(values: list[int]) -> int | None:
    """The median (lower) of a small latency sample, or ``None`` when empty."""
    if not values:
        return None
    return sorted(values)[(len(values) - 1) // 2]


def _cell_for_request(record: ReportRecord, request: str) -> "CellRecord | None":
    """The first saved cell belonging to *request*, for its detail tree."""
    return next((cell for cell in record.cells if cell.request == request), None)


def _fmt_bytes(size: int | None) -> str:
    """A compact byte count — ``1.2 kB`` / ``840 B`` / ``—``."""
    if size is None:
        return "—"
    return f"{size} B" if size < 1000 else f"{size / 1000:.1f} kB"


def _body_summary(body: object) -> str:
    """A one-line shape of a parsed body — ``{ args, headers, url }`` / ``[ 3 items ]``."""
    if isinstance(body, dict):
        keys = ", ".join(str(key) for key in list(body)[:6])
        return f"{{ {keys} }}" if keys else "{ }"
    if isinstance(body, list):
        return f"[ {len(body)} item{'' if len(body) == 1 else 's'} ]"
    return _sv(body)


def _replay_detail_tree(
    tree: Tree[object], record: ReportRecord, row: "RequestBreakdown | None"
) -> None:
    """Rebuild the Run detail tree for a saved request from the archived cell.

    When the record carries the cell's response (method/path/status/latency/bytes,
    headers and body), the tree rebuilds the real ▾ request / ▾ response subtrees
    and metrics — the same shape as the live Run tab, replayed from disk.
    """
    tree.clear()
    root = tree.root
    if row is None:
        root.add_leaf(Text("select a request", style=_DIM))
        return
    cell = _cell_for_request(record, row.request)
    head = Text()
    head.append(row.request, style=f"bold {_TEXT_HI}")
    head.append("  ·  base  ·  ", style=_DIM)
    if cell is not None and cell.method:
        head.append(f"{cell.method} {cell.path}", style=_SAME)
        if cell.status is not None:
            ok = 200 <= cell.status < 400
            head.append(f"  {cell.status}", style=_SAME if ok else _DRIFT)
    else:
        verdict = "✓ pass" if row.verdict == "pass" else "✗ " + row.verdict
        head.append(verdict, style=_SAME if row.verdict == "pass" else _DRIFT)
    root.add_leaf(head)
    metrics = root.add(Text("metrics", style=f"bold {_AXIS}"), expand=True)
    if cell is not None and cell.status is not None:
        latency = f"{cell.latency_ms}ms" if cell.latency_ms is not None else "—"
        metrics.add_leaf(
            Text.assemble(
                ("status ", _DIM),
                (str(cell.status), _SAME),
                ("   latency ", _DIM),
                (latency, _TEXT),
                ("   bytes ", _DIM),
                (_fmt_bytes(cell.size_bytes), _TEXT),
            )
        )
    else:
        metrics.add_leaf(
            Text.assemble(
                ("same ", _DIM),
                (str(row.same), _SAME),
                ("   drift ", _DIM),
                (str(row.drift), _DRIFT if row.drift else _DIM),
                ("   skip ", _DIM),
                (str(row.skip), _SKIP),
            )
        )
    if cell is not None and cell.method:
        request_node = root.add(Text("request", style=f"bold {_AXIS}"), expand=True)
        request_node.add_leaf(Text.assemble((f"{cell.method} ", _SAME), (cell.path, _TEXT)))
    if cell is not None and (cell.response_headers or cell.baseline_body is not None):
        response_node = root.add(Text("response", style=f"bold {_AXIS}"), expand=True)
        if cell.response_headers:
            headers_node = response_node.add(
                Text.assemble(("headers ", _DIM), (f"({len(cell.response_headers)})", _DIM)),
                expand=True,
            )
            for name, value in list(cell.response_headers.items())[:4]:
                headers_node.add_leaf(Text.assemble((f"{name}: ", _DIM), (value, _TEXT)))
        if cell.baseline_body is not None:
            response_node.add_leaf(
                Text.assemble(("body ", _DIM), (_body_summary(cell.baseline_body), _TEXT))
            )
    checks = root.add(Text("checks", style=f"bold {_AXIS}"), expand=True)
    lines = record.baseline_assertions.lines
    if lines:
        for line in lines:
            glyph, colour = _ASSERT_GLYPH.get(line.state, ("·", _DIM))
            checks.add_leaf(Text.assemble((f"{glyph} {line.label}  ", colour), (line.detail, _DIM)))
    else:
        checks.add_leaf(Text("no assertions recorded (ad-hoc diff)", style=_DIM))
    if row.drift_paths:
        drift = root.add(Text("drift", style=f"bold {_DRIFT}"), expand=True)
        for path in row.drift_paths:
            drift.add_leaf(Text(f"✗ {path}", style=_DRIFT))


def _record_detail(record: ReportRecord) -> Group:
    """The full in-place deep-dive for a saved run.

    Deeper than the reading pane: it names every drifted field per request (from
    the archive's ``drift_paths``) so a user can tell exactly what to investigate
    without leaving the Report screen.
    """
    parts: list[RenderableType] = [_gate_banner(record), Text()]
    stats = Text()
    for label, value, colour in (
        ("calls", record.calls, _TEXT_HI),
        ("same", record.same, _SAME),
        ("drift", record.drift, _DRIFT if record.drift else _DIM),
        ("error", record.error, _WARN if record.error else _DIM),
        ("skipped", record.skipped, _SKIP),
    ):
        stats.append(f"{value} ", style=f"bold {colour}")
        stats.append(f"{label}    ", style=_DIM)
    parts += [stats, Text()]
    for title, env, summary in (
        ("Assertions · baseline", record.baseline, record.baseline_assertions),
        ("Assertions · candidate", record.candidate or "—", record.candidate_assertions),
    ):
        header = Text(f"{title} ", style=f"bold {_TEXT_HI}")
        header.append(env, style=_DIM)
        header.append("   ", style=_DIM)
        header.append_text(_assert_counts(summary))
        parts += [header, _assert_lines(summary), Text()]
    parts.append(Text("Per-request drift", style=f"bold {_TEXT_HI}"))
    parts.append(_breakdown_table(record))
    # Show every drifting request — naming its fields, or an explicit notice when a
    # legacy/foreign archive recorded a count without the paths (never a bare number).
    drifted = [row for row in record.requests if row.drift]
    if drifted:
        parts.append(Text())
        for row in drifted:
            head = Text("▸ ", style=_DRIFT)
            head.append(row.request, style=f"bold {_TEXT_HI}")
            count = len(row.drift_paths) if row.drift_paths else row.drift
            head.append(f"  {count} drifted field(s)", style=_DIM)
            parts.append(head)
            if row.drift_paths:
                for path in row.drift_paths:
                    line = Text("    ↳ ", style=_DIM)
                    line.append(path, style=_DRIFT)
                    parts.append(line)
            else:
                parts.append(
                    Text("    ↳ field paths not recorded — re-run to name them", style=_DIM)
                )
    return Group(*parts)


def _record_markdown(record: ReportRecord) -> str:
    lines = [
        f"# comparo report {record.id}",
        "",
        f"- **gate**: {record.gate}",
        f"- **environments**: {_envs_label(record)}",
        f"- **when**: {record.created}",
    ]
    if record.execution:
        lines.append(f"- **execution**: {record.execution}")
    lines += [
        "",
        "| calls | same | drift | error | skipped |",
        "|------:|-----:|------:|------:|--------:|",
        f"| {record.calls} | {record.same} | {record.drift} | {record.error} | {record.skipped} |",
        "",
        "## Diff breakdown",
        "",
        "| request | same | drift | skip | verdict |",
        "|---------|-----:|------:|-----:|---------|",
    ]
    lines += [
        f"| {row.request} | {row.same} | {row.drift} | {row.skip} | {row.verdict} |"
        for row in record.requests
    ]
    return "\n".join(lines) + "\n"


_SETTINGS_SUBTITLE: dict[str, str] = {
    "about": "MIT · alpha",
    "project": "read-only",
    "security": "t · self-check",
    "appearance": "1 theme",
    "keybindings": "read-only",
    "updates": "opt-in",
    "plugins": "none",
    "engine": "comparo/v1",
    "behavior": "startup",
}
_REPO_URL = "github.com/wbenbihi/comparo"
_DOCS_URL = "github.com/wbenbihi/comparo/tree/main/docs"
#: The redaction sinks the self-check reports on, in display order (name, where).
_SELFCHECK_SINKS: tuple[tuple[str, str], ...] = (
    ("TUI display", "masked on render"),
    ("saved runs", ".runs/*.json"),
    ("saved reports", ".reports/*.json"),
    ("JUnit reporter", "reports/junit.xml"),
    ("SARIF reporter", "reports/comparo.sarif"),
    ("JSON reporter", "reports/comparo.json"),
    ("Markdown reporter", "GitHub step summary"),
    ("curl copy", "yanked command"),
    ("crash report", "traceback scrub"),
)


def _settings_body(
    project: LoadedProject,
    config: UserConfig,
    key: str,
    selfcheck: list[tuple[str, str, bool]] | None,
    checking: bool,
) -> RenderableType:
    """Render one settings section - the master/detail right pane."""
    if key == "about":
        return _settings_about()
    if key == "project":
        return _settings_project(project)
    if key == "security":
        return _settings_security(selfcheck, checking)
    if key == "appearance":
        return _settings_appearance(config)
    if key == "keybindings":
        return _settings_keybindings()
    if key == "updates":
        return _settings_updates(config)
    if key == "plugins":
        return _settings_plugins()
    if key == "engine":
        return _settings_engine()
    return _settings_behavior(config)


def _settings_about() -> Text:
    text = Text()
    text.append("comparo ", style=f"bold {_ACCENT}")
    text.append(f"{__version__}", style=f"bold {_TEXT_HI}")
    text.append("   alpha\n", style=_AXIS)
    text.append(
        "HTTP regression & diff testing across environments — TUI, CLI, and CI\n\n", style=_DIM
    )
    for label, value, style in (
        ("author", "Walid Benbihi", _TEXT_HI),
        ("license", "MIT", _TEXT),
        ("repo", _REPO_URL, _ACCENT),
        ("docs", _DOCS_URL, _ACCENT),
    ):
        text.append(f"{label:<9}", style=_LABEL)
        text.append(f"{value}\n", style=style)
    text.append("\nFree & open source. Built in the open.", style=_DIM)
    return text


def _settings_project(project: LoadedProject) -> Text:
    def count(kind: type | tuple[type, ...]) -> int:
        return sum(1 for obj in project.objects.values() if isinstance(obj, kind))

    # An env or project NAME can equal a declared secret value (the untainted
    # vector) — this is a display sink, so mask through the project's redactor.
    redact = Redactor.for_project(project).text
    manifest = project.project
    spec = manifest.spec if manifest else None
    default = _default_environment(project)
    text = Text()
    stats = (
        (count(Environment), "environments"),
        (count(Request), "requests"),
        (count(Schema), "schemas"),
        (count(Matrix), "matrices"),
        (count((DiffProfile, AssertionProfile, ExecutionProfile)), "profiles"),
    )
    for number, noun in stats:
        text.append(f"{number} ", style=f"bold {_TEXT_HI}")
        text.append(f"{noun}   ", style=_DIM)
    text.append("\n\n")
    report = getattr(spec, "report", None)
    report_dir = getattr(report, "output", None)
    if report_dir is None and isinstance(report, dict):
        report_dir = report.get("output")
    project_line = manifest.metadata.name if manifest else "—"
    if manifest and manifest.metadata.description:
        project_line = f"{manifest.metadata.name} · {manifest.metadata.description}"
    rows = [
        ("manifest", redact(f"{project.root.name}/comparo.yaml"), _TEXT_HI),
        ("project", redact(project_line), _TEXT_HI),
        ("default env", redact(default.metadata.name) if default else "—", _ACCENT),
        ("concurrency", str(getattr(spec, "concurrency", "—") or "—"), _TEXT),
        ("reporting dir", redact(str(report_dir or ".reports/")), _TEXT),
    ]
    for label, value, style in rows:
        text.append(f"{label:<14}", style=_LABEL)
        text.append(f"{value}\n", style=style)
    text.append(
        "\nEdit the YAML in your editor; the TUI reads. A summary, not an editor.", style=_DIM
    )
    return text


def _settings_security(selfcheck: list[tuple[str, str, bool]] | None, checking: bool) -> Text:
    text = Text()
    text.append("Never-leak guarantee.  ", style=f"bold {_SAME}")
    text.append(
        "A resolved secret is masked in every sink that\nleaves the process — the safe path is "
        "the only path.\n\n",
        style=_DIM,
    )
    if checking:
        text.append("running self-check…\n", style=_WARN)
    elif selfcheck is None:
        text.append("press ", style=_DIM)
        text.append("t", style=f"bold {_ACCENT}")
        text.append(" to run a canary secret through every sink\n", style=_DIM)
        rows: tuple[tuple[str, str, bool], ...] = tuple((n, d, True) for n, d in _SELFCHECK_SINKS)
        _selfcheck_rows(text, rows, muted=True)
    else:
        passed = sum(1 for _, _, ok in selfcheck if ok)
        total = len(selfcheck)
        good = passed == total
        text.append(
            f"{'✓' if good else '✗'} {passed}/{total} sinks masked the canary",
            style=f"bold {_SAME if good else _DRIFT}",
        )
        text.append("   canary ", style=_DIM)
        text.append("••••••", style=_SKIP)
        text.append(" (s3cr…-CANARY → masked everywhere)\n\n", style=_DIM)
        _selfcheck_rows(text, tuple(selfcheck), muted=False)
    text.append("\npress ", style=_DIM)
    text.append("t", style=f"bold {_ACCENT}")
    text.append(" to re-run   ·   also headless: ", style=_DIM)
    text.append("comparo doctor", style=f"bold {_ACCENT}")
    return text


def _selfcheck_rows(text: Text, rows: tuple[tuple[str, str, bool], ...], *, muted: bool) -> None:
    for name, detail, ok in rows:
        glyph, tint = ("✓", _SAME) if ok else ("✗", _DRIFT)
        text.append(f"{glyph} ", style=_DIM if muted else tint)
        text.append(f"{name:<18}", style=_DIM if muted else (_TEXT_HI if ok else _DRIFT))
        text.append(f"— {detail}\n", style=_DIM)


def _settings_appearance(config: UserConfig) -> Text:
    text = Text()
    text.append("THEME\n", style=_LABEL)
    text.append("● ", style=_SAME)
    text.append("comparo-ink   ", style=_TEXT_HI)
    text.append("deep-ink dark   accent #6d9eff\n", style=_DIM)
    text.append("○ more themes — swappable, post-alpha (row reserved)\n\n", style=_DIM)
    text.append("comparo-ink is 13 meaning-named tokens (theme.py):\n", style=_DIM)
    for token, style in (
        ("same", _SAME),
        ("drift", _DRIFT),
        ("skip", _SKIP),
        ("accent", _ACCENT),
        ("axis", _AXIS),
        ("warn", _WARN),
    ):
        text.append(token, style=style)
        text.append(" · ", style=_DIM)
    text.append("danger · border · cursor · footer…\n\n", style=_DIM)
    text.append("DEFAULT BODY-DIFF LAYOUT\n", style=_LABEL)
    text.append(_seg_toggle(("unified", "side-by-side"), config.diff_view))
    text.append("   how bodies render in Diff by default\n", style=_DIM)
    text.append("\npress ", style=_DIM)
    text.append("enter", style=f"bold {_ACCENT}")
    text.append(" to switch the default layout", style=_DIM)
    return text


def _settings_keybindings() -> RenderableType:
    globals_table = Table(box=None, show_header=False, padding=(0, 2, 0, 0))
    globals_table.add_column(style=_ACCENT, no_wrap=True)
    globals_table.add_column()
    for combo, action in (
        ("1–6", "switch tab — Explorer · Run · Diff · Execution · Report · Settings"),
        ("tab", "next panel"),
        ("q", "quit — everywhere"),
        ("esc / ⌫", "back"),
        ("/", "filter"),
        ("?", "help"),
    ):
        globals_table.add_row(combo, Text(action, style=_TEXT))
    per_tab = Table(box=None, show_header=False, padding=(0, 2, 0, 0))
    per_tab.add_column(style=f"bold {_ACCENT}", no_wrap=True)
    per_tab.add_column()
    for tab, verbs in (
        ("Explorer", "↑↓ select · enter default · h health · r raw · p curl · g graph"),
        ("Run", "↑↓ / h·l move · f fails · t views · z max · s save"),
        ("Diff", "↑↓ move · v unified · o outbound · i ignore · s save"),
        ("Execution", "↑↓ cells · enter open · v unified · s save"),
        ("Report", "↑↓ list · enter analyze · r reload · o export · d delete"),
        ("Settings", "↑↓ sections · enter/t activate"),
    ):
        per_tab.add_row(tab, Text(verbs, style=_DIM))
    group = Group(
        Text("GLOBAL", style=_LABEL),
        globals_table,
        Text("\nPER TAB", style=_LABEL),
        per_tab,
        Text("\nq always quits — it is never back/close. esc and ⌫ are back.", style=_DIM),
    )
    return group


def _settings_updates(config: UserConfig) -> Text:
    box = "[x]" if config.update_check else "[ ]"
    text = Text()
    text.append(f"{box} ", style=f"bold {_ACCENT}")
    text.append("check for updates on startup", style=_TEXT_HI)
    text.append("  — off (opt-in)" if not config.update_check else "  — on", style=_DIM)
    text.append("\n\nWhen on, comparo fetches PyPI's public version JSON\n", style=_DIM)
    text.append("(pypi.org/pypi/comparo/json) once at launch — a version\n", style=_DIM)
    text.append("string, nothing more.\n", style=_DIM)
    text.append("✓ no telemetry", style=_SAME)
    text.append(" · no account · nothing else leaves your machine\n\n", style=_DIM)
    text.append("status  ", style=_LABEL)
    seen = config.update_latest_seen
    if seen and updates_adapter.is_newer(seen, __version__):
        text.append(f"update available → {seen}", style=f"bold {_WARN}")
        text.append(f"  · you have {__version__}", style=_DIM)
    elif config.update_last_checked:
        text.append("✓ up to date", style=_SAME)
        text.append(f"  · {__version__} · last checked {config.update_last_checked}", style=_DIM)
    else:
        text.append("not checked yet", style=_DIM)
    text.append("\n\nWhen a newer version exists a one-time toast appears at launch.", style=_DIM)
    text.append("\npress ", style=_DIM)
    text.append("enter", style=f"bold {_ACCENT}")
    text.append(" to toggle the check", style=_DIM)
    return text


def _settings_plugins() -> Text:
    text = Text()
    text.append("○ ", style=_SKIP)
    text.append("no plugins installed\n\n", style=_DIM)
    text.append(
        "Plugins are a post-alpha extension point — reporters, auth\n"
        "providers, generators, comparators that plug into comparo.core\n"
        "without touching it. comparo is domain-agnostic by default.",
        style=_DIM,
    )
    return text


def _settings_engine() -> Text:
    text = Text()
    text.append("comparo.core", style=f"bold {_ACCENT}")
    text.append(" is the whole engine. The TUI, CLI, and GitHub\n", style=_TEXT)
    text.append(
        "Action are thin front-ends over it — and never leak back into it.\n\n", style=_TEXT
    )
    text.append("CONTRACTS", style=_LABEL)
    text.append("   enforced by import-linter in CI\n", style=_DIM)
    for contract in (
        "Interfaces and adapters may depend on core; core depends on neither",
        "Core must not import an HTTP library directly",
    ):
        text.append("✓ ", style=_SAME)
        text.append(f"{contract}\n", style=_TEXT)
    text.append("\nconfig API   ", style=_LABEL)
    text.append("comparo/v1\n", style=_ACCENT)
    text.append("docs         ", style=_LABEL)
    text.append(f"{_DOCS_URL}", style=_ACCENT)
    return text


def _settings_behavior(config: UserConfig) -> Text:
    text = Text()
    box = "[x]" if config.confirm_quit else "[ ]"
    text.append(f"{box} ", style=f"bold {_ACCENT}")
    text.append("confirm on quit", style=_TEXT_HI)
    text.append("  — ask before q closes the app\n\n", style=_DIM)
    text.append("default tab on launch   ", style=_LABEL)
    text.append(config.default_tab, style=_TEXT_HI)
    text.append("  · set in config.toml\n", style=_DIM)
    text.append("default diff layout     ", style=_LABEL)
    text.append(config.diff_view, style=_TEXT_HI)
    text.append("  · set in Appearance\n", style=_DIM)
    text.append("\npress ", style=_DIM)
    text.append("enter", style=f"bold {_ACCENT}")
    text.append(" to toggle confirm-on-quit", style=_DIM)
    return text
