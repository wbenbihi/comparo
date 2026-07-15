"""The comparo terminal UI (Explorer screen).

A Textual front-end over the engine: browse the object model on the left, and
see the selected request resolved for the current environment on the right —
secrets masked, with a provenance trail. The core never depends on this module.
"""

from typing import ClassVar

from rich.text import Text
from textual.app import App
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import VerticalScroll
from textual.widgets import Footer
from textual.widgets import Static
from textual.widgets import Tree

from comparo.core.loader import LoadedProject
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import Instance
from comparo.core.models import Matrix
from comparo.core.models import Request
from comparo.core.models import Schema
from comparo.core.provenance import Trail
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import select_environment

_TEXT = "#c5d0de"
_TEXT_HI = "#eaf0f8"
_DIM = "#5c6878"
_ACCENT = "#6d9eff"
_AXIS = "#a98bf0"
_DRIFT = "#e0566b"

_KINDS: tuple[tuple[str, type], ...] = (
    ("Environments", Environment),
    ("Requests", Request),
    ("Matrices", Matrix),
    ("Schemas", Schema),
    ("Instances", Instance),
    ("Diff Profiles", DiffProfile),
)


class ComparoApp(App[None]):
    """The comparo Explorer application."""

    CSS_PATH = "comparo.tcss"
    TITLE = "comparo"
    BINDINGS: ClassVar[list[BindingType]] = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]

    def __init__(self, project: LoadedProject) -> None:
        """Build the app over a loaded project.

        Args:
            project: The project to explore.
        """
        super().__init__()
        self.project = project
        self.environment = _default_environment(project)

    def compose(self) -> ComposeResult:
        """Lay out the top bar, the object tree, the detail pane, and the footer."""
        yield Static(self._topbar(), id="topbar")
        with Horizontal(id="body"):
            yield self._object_tree()
            with VerticalScroll(id="detail"):
                yield Static(_hint(), id="detail-content")
        yield Footer()

    def on_mount(self) -> None:
        """Focus the tree and preselect the first request, if any."""
        tree = self.query_one("#tree", Tree)
        tree.focus()
        for request in self.project.objects.values():
            if isinstance(request, Request):
                self._show(request)
                break

    def on_tree_node_selected(self, event: Tree.NodeSelected[object]) -> None:
        """Render the selected object in the detail pane."""
        data = event.node.data
        if isinstance(data, Request):
            self._show(data)
        elif data is not None:
            self._detail(_summary(data))

    def action_refresh(self) -> None:
        """Re-render the current selection (a no-op placeholder for now)."""
        self.query_one("#tree", Tree).focus()

    def _object_tree(self) -> Tree[object]:
        tree: Tree[object] = Tree("project", id="tree")
        tree.show_root = False
        tree.guide_depth = 3
        for label, kind in _KINDS:
            objects = [obj for obj in self.project.objects.values() if isinstance(obj, kind)]
            branch = tree.root.add(f"{label}  ({len(objects)})", expand=True)
            for obj in objects:
                branch.add_leaf(obj.metadata.name, data=obj)
        return tree

    def _show(self, request: Request) -> None:
        if self.environment is None:
            self._detail(Text("no environment to resolve against", style=_DIM))
            return
        resolved = Resolver(self.project, self.environment).resolve_request(request)
        self._detail(_render_request(resolved, self.environment.metadata.name))

    def _detail(self, content: Text) -> None:
        self.query_one("#detail-content", Static).update(content)

    def _topbar(self) -> Text:
        name = self.project.project.metadata.name if self.project.project else "project"
        env = self.environment.metadata.name if self.environment else "—"
        bar = Text()
        bar.append("comparo", style=f"bold {_ACCENT}")
        bar.append(f"  ·  {name}  ·  env: ", style=_DIM)
        bar.append(env, style=_TEXT_HI)
        return bar


def _default_environment(project: LoadedProject) -> Environment | None:
    try:
        return select_environment(project, None)
    except EnvironmentSelectionError:
        for obj in project.objects.values():
            if isinstance(obj, Environment):
                return obj
        return None


def _hint() -> Text:
    return Text("select a request to see it resolved", style=_DIM)


def _summary(obj: object) -> Text:
    metadata = getattr(obj, "metadata", None)
    text = Text()
    text.append(f"{type(obj).__name__}\n", style=f"bold {_TEXT_HI}")
    text.append(getattr(metadata, "id", "") or "", style=_AXIS)
    description = getattr(metadata, "description", None)
    if description:
        text.append(f"\n\n{description}", style=_TEXT)
    return text


def _render_request(resolved: ResolvedRequest, environment_name: str) -> Text:
    text = Text()
    text.append(f"{resolved.method} {resolved.url}\n", style=f"bold {_TEXT_HI}")
    text.append(f"  env: {environment_name}\n", style=_DIM)
    if resolved.headers:
        text.append("\nheaders\n", style=_DIM)
        for key, value in resolved.headers:
            text.append(f"  {key}: ", style=_TEXT)
            masked = "••••" in str(value)
            text.append(f"{value}\n", style=_AXIS if masked else _TEXT)
    if resolved.query:
        text.append("\nquery\n", style=_DIM)
        for key, value in resolved.query.items():
            text.append(f"  {key}: {value}\n", style=_TEXT)
    if resolved.body is not None:
        text.append("\nbody\n", style=_DIM)
        text.append(f"  {resolved.body}\n", style=_TEXT)
    if resolved.trail:
        text.append("\nprovenance\n", style=_DIM)
        for entry in resolved.trail:
            _append_trail(text, entry)
    return text


def _append_trail(text: Text, entry: Trail) -> None:
    text.append(f"  {entry.path:<26} ", style=_TEXT)
    text.append(
        "secret" if entry.tainted else entry.origin.value, style=_DRIFT if entry.tainted else _AXIS
    )
    text.append(f" ← {entry.detail}\n", style=_DIM)
