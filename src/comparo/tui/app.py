"""The comparo terminal UI.

Built to the comparo-ink design: a top nav bar of screen tabs, a full foldable
project tree on the Explorer, and rich per-object detail (the resolved outbound
request with a syntax-highlighted body, or the config of any other object). The
Diff screen carries the signature tri-state gutter. The core never depends on
this module.
"""

import json
from typing import ClassVar

import msgspec
from rich.console import Group
from rich.console import RenderableType
from rich.syntax import Syntax
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
from textual.widgets import Input
from textual.widgets import Label
from textual.widgets import Static
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from comparo.core.diagnostics import Diagnostic
from comparo.core.diagnostics import LoadError
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import Instance
from comparo.core.models import Matrix
from comparo.core.models import Request
from comparo.core.models import Schema
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import select_environment
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
    ("n/p", "drift"),
    ("u", "unified"),
    ("i/b/x", "triage"),
    ("1", "explorer"),
    ("q", "quit"),
)
_ERROR_KEYS = (
    ("r", "re-check"),
    ("q", "quit"),
)

_KIND_COLOR: dict[type, str] = {
    Environment: _SAME,
    Request: _TEXT_HI,
    Matrix: _AXIS,
    Schema: _ACCENT,
    Instance: _SAME,
    DiffProfile: _WARN,
}


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
            yield Label(f" {label} ", id=f"nav-{tab_id}", classes="nav-item")
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

    def _populate(self, query: str, *, prefer_request: bool = False) -> int:
        tree: Tree[object] = self.query_one("#tree", Tree)
        tree.clear()
        needle = query.strip().lower()
        first_leaf: TreeNode[object] | None = None
        first_request: TreeNode[object] | None = None
        total = 0
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

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[object]) -> None:
        """Show the highlighted object."""
        self._show(event.node.data)

    def _show(self, obj: object) -> None:
        if obj is None:
            return
        detail = self.query_one("#detail-panel")
        context = self.query_one("#context-panel")
        if isinstance(obj, Request) and self.environment is not None:
            resolved = Resolver(self.project, self.environment).resolve_request(obj)
            detail.border_title = _title(obj, resolved.method)
            detail.border_subtitle = f"resolved for {self.environment.metadata.name}"
            self._set_detail(_request_detail(self.project, obj, resolved))
            context.border_title = "PROVENANCE"
            self._set_context(_render_provenance(resolved.trail))
            return
        detail.border_title = _title(obj, type(obj).__name__.upper())
        detail.border_subtitle = ""
        self._set_detail(_object_detail(obj))
        context.border_title = "DESCRIPTION"
        self._set_context(_description(obj))

    def _set_detail(self, content: RenderableType) -> None:
        self.query_one("#detail-content", Static).update(content)

    def _set_context(self, content: RenderableType) -> None:
        self.query_one("#context-content", Static).update(content)


class DiffView(Horizontal):
    """The signature diff screen: drift grouped by field, side-by-side gutter."""

    def __init__(self) -> None:
        """Build the diff view."""
        super().__init__(id="diff-view", classes="view")

    def compose(self) -> ComposeResult:
        """Yield the drift list and the side-by-side diff panel."""
        with Vertical(id="drift-panel", classes="panel"):
            yield Static(_drift_list(), id="drift-content")
        with Vertical(id="diffpane-panel", classes="panel hero"):
            yield Static(_diff_detail(), id="diff-detail")

    def on_mount(self) -> None:
        """Title the panels."""
        self.query_one("#drift-panel").border_title = "DRIFT — grouped by field"
        pane = self.query_one("#diffpane-panel")
        pane.border_title = Text.from_markup(
            f"[{_DRIFT}]$.json.order.quantity[/]  ·  echo-anything · ja-JP"
        )
        pane.border_subtitle = "1 drift · 2 ignored · fails CI"


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


class Placeholder(Vertical):
    """A stand-in for a screen not yet built."""

    def __init__(self, view_id: str, name: str) -> None:
        """Build a placeholder.

        Args:
            view_id: The widget id (drives the content switcher).
            name: The screen name.
        """
        super().__init__(id=view_id, classes="view placeholder")
        self._name = name

    def compose(self) -> ComposeResult:
        """Yield a centered hint."""
        yield Static(Text(f"{self._name} — coming soon", style=_DIM))


class ComparoApp(App[None]):
    """The comparo application shell."""

    CSS_PATH = "comparo.tcss"
    TITLE = "comparo"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("1", "screen('explorer')", "Explorer"),
        Binding("3", "screen('diff')", "Diff"),
        Binding("slash", "filter", "Filter"),
        Binding("g", "graph", "Graph"),
        Binding("r", "reload", "Reload"),
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
            with ContentSwitcher(initial="explorer-view", id="content"):
                yield ExplorerView(self.project, self.environment)  # type: ignore[arg-type]
                yield Placeholder("run-view", "Run")
                yield DiffView()
                yield Placeholder("report-view", "Report")
                yield Placeholder("settings-view", "Settings")
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
        self._status(name)

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

    def action_reload(self) -> None:
        """Re-check a failed project in place; swap to a success note if it loads."""
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
        keys = _DIFF_KEYS if screen == "diff" else _EXPLORER_KEYS
        context = {
            "explorer": "project · read-only",
            "diff": ".runs/8c3e11 · local ⇄ prod",
        }.get(screen, "")
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


def _leaf(obj: object) -> Text:
    metadata = getattr(obj, "metadata", None)
    name = str(getattr(metadata, "name", "?"))
    row = Text()
    if isinstance(obj, Environment):
        row.append("● ", style=_SAME)
        row.append(name, style=_TEXT)
        if "local" not in name.lower():
            row.append("  live", style=f"bold {_DANGER}")
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


def _request_detail(project: LoadedProject, request: Request, resolved: ResolvedRequest) -> Group:
    parts: list[RenderableType] = []
    head = Text()
    head.append(
        f" {resolved.method} ", style=f"bold {_INK} on {_METHOD.get(resolved.method, _ACCENT)}"
    )
    head.append("  ")
    head.append(resolved.url, style=_TEXT_HI)
    parts.append(head)
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
    for key, value in resolved.headers:
        masked = "••••" in str(value)
        headers.append(f"\n  {key:<18}", style=_DIM)
        headers.append(str(value), style=_DRIFT if masked else _TEXT)
    parts.append(headers)
    if resolved.query:
        query = Text("\n\nQUERY", style=_LABEL)
        for key, value in resolved.query.items():
            query.append(f"\n  {key:<18}", style=_DIM)
            query.append(str(value), style=_AXIS)
        parts.append(query)
    if resolved.body is not None:
        parts.append(Text("\n\nBODY", style=_LABEL))
        parts.append(_json(resolved.body))
    response = request.spec.response
    if response is not None:
        line = [str(response.status)] if response.status else []
        line += [ref for ref in (_ref_id(response.schema), _ref_id(response.diff)) if ref]
        footer = Text("\nresponse   ", style=_LABEL)
        footer.append(" · ".join(line), style=_TEXT)
        parts.append(footer)
    return Group(*parts)


def _object_detail(obj: object) -> RenderableType:
    if isinstance(obj, Environment):
        return _environment_detail(obj)
    if isinstance(obj, Matrix):
        return Group(_matrix_head(obj), _json(obj.spec.values))
    if isinstance(obj, DiffProfile):
        return _diffprofile_detail(obj)
    if isinstance(obj, Schema):
        return _json(obj.spec)
    if isinstance(obj, Instance):
        return _json(obj.spec.value)
    return Text(str(obj), style=_TEXT)


def _environment_detail(env: Environment) -> Text:
    spec = env.spec
    text = Text()
    text.append("baseUrl    ", style=_LABEL)
    text.append(f"{spec.base_url}\n", style=_ACCENT)
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
        text.append("\nHEALTH\n", style=_LABEL)
        for check in spec.health:
            text.append(f"  {check.method} {check.endpoint}\n", style=_TEXT)
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


def _json(value: object) -> Syntax:
    rendered = json.dumps(value, indent=2, ensure_ascii=False)
    return Syntax(rendered, "json", theme="one-dark", background_color=_SYNTAX_BG, word_wrap=True)


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


def _drift_list() -> Text:
    text = Text()
    text.append(" ✗ $.json.order.quantity\n", style=f"bold {_DRIFT}")
    text.append("   echo-anything · all 3 ", style=_DIM)
    text.append("locales\n\n", style=_AXIS)
    for path in ("$.headers", "$.origin"):
        text.append(f" ◐ {path}", style=_SKIP)
        text.append("  ignored\n", style=_DIM)
        text.append("   all requests · by profile\n\n", style=_DIM)
    text.append("─" * 26 + "\n", style=_DIM)
    text.append("a field that drifts on 3 locales\nis one bug, not three.", style=_DIM)
    return text


def _diff_detail() -> Text:
    baseline = [
        ("skip", '"json": {'),
        ("skip", '  "order": {'),
        ("skip", '    "sku": "WIDGET-1",'),
        ("drift", '    "quantity": ', "3", _SAME),
        ("skip", "  } },"),
        ("gap", '"headers": { …not compared…'),
        ("gap", '"origin": "127.0.0.1"'),
    ]
    candidate = [
        ("skip", '"json": {'),
        ("skip", '  "order": {'),
        ("skip", '    "sku": "WIDGET-1",'),
        ("drift", '    "quantity": ', '"3"', _DRIFT),
        ("skip", "  } },"),
        ("gap", '"headers": { …not compared…'),
        ("gap", '"origin": "10.4.1.9"'),
    ]
    text = Text()
    text.append(f"{'A local · working tree':<40}", style=_DIM)
    text.append("B prod · candidate deploy\n", style=_DIM)
    text.append("─" * 40 + " ─" * 8 + "\n", style=_DIM)
    for left, right in zip(baseline, candidate, strict=True):
        _diff_row(text, left, pad=40)
        text.append("  ")
        _diff_row(text, right, pad=0)
        text.append("\n")
    text.append("\n")
    text.append("▏", style=_SAME)
    text.append(" compared · identical    ", style=_DIM)
    text.append("▌", style=_DRIFT)
    text.append(" compared · ", style=_DIM)
    text.append("drift", style=_DRIFT)
    text.append("    ╎", style=_SKIP)
    text.append(" not compared\n\n", style=_DIM)
    text.append("outbound request diff · identical\n", style=_SAME)
    text.append("both sides sent the same body — the drift is the service's", style=_DIM)
    return text


def _diff_row(text: Text, row: tuple[object, ...], *, pad: int) -> None:
    kind = str(row[0])
    glyph, colour = {"skip": ("▏", _SKIP), "drift": ("▌", _DRIFT), "gap": ("╎", _SKIP)}[kind]
    text.append(glyph, style=colour)
    text.append(" ")
    if kind == "drift":
        body, value = str(row[1]), str(row[2])
        line = f"{body}{value}"
        text.append(body, style=_TEXT)
        text.append(value, style=f"bold {row[3]}")
    else:
        line = str(row[1])
        text.append(line, style=_DIM if kind == "gap" else _TEXT)
    if pad:
        text.append(" " * max(0, pad - len(line) - 2))
