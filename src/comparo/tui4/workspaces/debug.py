"""Developer-only component gallery and interaction workspace."""

from enum import StrEnum
from typing import ClassVar
from typing import override

from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import VerticalScroll
from textual.content import Content
from textual.reactive import reactive
from textual.widgets import ContentSwitcher
from textual.widgets import Static

from comparo.tui4.bindings import debug_bindings
from comparo.tui4.components.diffview import DiffView
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.list import Field
from comparo.tui4.components.list import KeyValueList
from comparo.tui4.components.menu import Menu
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.separator import Separator
from comparo.tui4.components.table import Column
from comparo.tui4.components.table import Record
from comparo.tui4.components.table import Table
from comparo.tui4.components.treemenu import MenuNode
from comparo.tui4.components.treemenu import TreeMenu
from comparo.tui4.components.typography import Caption
from comparo.tui4.components.typography import Hint
from comparo.tui4.components.typography import SectionTitle
from comparo.tui4.models.diff import build_diff_document
from comparo.tui4.models.diff import build_source_document
from comparo.tui4.models.diff import document_label
from comparo.tui4.types import ComparoScreen

from .base import Workspace

NODES = (
    MenuNode(
        "col-users",
        "Users API",
        children=(
            MenuNode("req-users-list", "list users"),
            MenuNode("req-users-create", "create user"),
        ),
    ),
    MenuNode("col-orders", "Orders API", children=(MenuNode("req-orders-create", "create order"),)),
)

METHOD_STYLES = {"GET": "$success", "POST": "$accent", "PUT": "$warning", "DELETE": "$error"}

FIXTURE_REQUESTS: dict[str, dict[str, str]] = {
    "req-users": {
        "method": "GET",
        "name": "list users",
        "url": "/api/users",
        "status": "200",
        "ms": "142",
    },
    "req-order": {
        "method": "POST",
        "name": "create order",
        "url": "/api/orders",
        "status": "201",
        "ms": "387",
    },
    "req-cache": {
        "method": "DELETE",
        "name": "purge cache",
        "url": "/internal/cache",
        "status": "500",
        "ms": "1204",
    },
    "req-prof": {
        "method": "PUT",
        "name": "update profile",
        "url": "/api/profile",
        "status": "404",
        "ms": "89",
    },
}

OLD_BODY = """{
  "user": {
    "id": 4021,
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "plan": "starter",
    "quota": {
      "requests": 1000,
      "burst": 50
    },
    "beta_features": false
  }
}"""

NEW_BODY = """{
  "user": {
    "id": 4021,
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "plan": "professional",
    "quota": {
      "requests": 5000,
      "burst": 50,
      "concurrency": 8
    }
  }
}"""

SAMPLE_HTML = """<!DOCTYPE html>
  <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>Order 4021 — receipt</title>
    </head>
    <body>
      <main class="receipt">
        <header>
          <h1>Order confirmed</h1>
          <p class="order-id">#4021</p>
        </header>
        <section class="items">
          <ul>
            <li data-sku="A-100">
              <span class="name">Widget</span>
              <span class="qty">2</span>
            </li>
            <li data-sku="B-250">
              <span class="name">Gadget</span>
              <span class="qty">1</span>
            </li>
          </ul>
        </section>
        <footer>
          <p>Total: <strong>49.90</strong></p>
        </footer>
      </main>
    </body>
  </html>
  """

SAMPLE_YAML = """requests:
  list-users:
    method: GET
    url: /api/users
    headers:
      accept: application/json
  create-order:
    method: POST
    url: /api/orders
    body:
      items:
        - sku: A-100
          quantity: 2
environments:
  staging:
    base_url: https://staging.example.com
"""

MINIFIED_BODY = (
    '{"user":{"id":4021,"name":"Ada Lovelace","plan":"professional",'
    '"quota":{"requests":5000,"burst":50,"concurrency":8}}}'
)

REQUEST_COLUMNS = (
    Column("method", "Method", width=8),
    Column("name", "Request"),
    Column("status", "Status", width=6),
    Column("ms", "Time", width=8),
)


def _method_cell(method: str) -> Content:
    return Content.from_markup(f"[bold {METHOD_STYLES[method]}]$m[/]", m=method)


def _status_cell(status: str) -> Content:
    style = "$success" if status < "400" else "$error"
    return Content.from_markup(f"[{style}]$s[/]", s=status)


def _request_records() -> tuple[Record[str], ...]:
    return tuple(
        Record(
            id=key,
            cells=(
                _method_cell(f["method"]),
                f["name"],
                _status_cell(f["status"]),
                f"{f['ms']} ms",
            ),
        )
        for key, f in FIXTURE_REQUESTS.items()
    )


class DebugSection(StrEnum):
    """Available sections in the developer workspace."""

    STATE = "state"
    MESSAGE = "messages"
    STYLES = "styles"
    COMPONENTS = "components"


class FilteredTextLine(Static):
    """Small reactive fixture used to exercise filter messages."""

    ELEMENTS: ClassVar[tuple[str, ...]] = ("foo", "bar", "hello", "world")

    filter_text: reactive[str] = reactive("")

    @override
    def render(self) -> str:
        """Render fixture elements matching the current filter."""
        selected = [el for el in self.ELEMENTS if self.filter_text in el]
        return " - ".join(selected) or "(no match)"


class ComponentsDebugPanel(Panel):
    """Debug Panel for Components."""

    filter_text: reactive[str] = reactive("", init=False)

    BINDINGS: ClassVar[list[BindingType]] = debug_bindings()

    @override
    def compose(self) -> ComposeResult:
        """Compose the reusable-component gallery."""
        with VerticalScroll():
            # Typography
            yield Panel(SectionTitle("Title"), Caption("Caption"), Hint("Hint"), title="Typography")

            # Key Value List
            with Horizontal(classes="height-auto"):
                yield Panel(
                    KeyValueList(
                        Field(key="key", value="value"),
                        Field(key="foo", value="bar"),
                        Field(key="hello", value="world"),
                        separators=False,
                    ),
                    title="KeyValue List",
                    classes="height-full",
                )
                yield Panel(
                    KeyValueList(
                        Field(key="key", value="value"),
                        Field(key="foo", value="bar"),
                        Field(key="hello", value="world"),
                        separators=True,
                    ),
                    title="KeyValue List (with separtors)",
                    classes="height-auto",
                )

            # FilterInput
            yield Panel(
                FilterInput(title="Sample Filter"),
                Caption("Filtered Elements"),
                FilteredTextLine(),
                Hint("Use `/` to get focus."),
                title="Filter Input",
            )

            # Separator
            yield Panel(
                Caption("above"),
                Separator(),
                Caption("between"),
                Separator("SECTION", align="left"),
                Caption("and an accent one"),
                Separator("ACCENT", classes="accent"),
                Separator("ERROR", classes="error"),
                Separator("SUCCESS", classes="success"),
                title="Separator",
            )

            # Data Table
            yield Panel(
                Table(columns=REQUEST_COLUMNS, records=_request_records(), id="debug-table"),
                title="Table",
            )

            # DiffView — diff mode
            yield Panel(
                DiffView(
                    document=build_diff_document(OLD_BODY, NEW_BODY, language="json"),
                    id="debug-diff",
                ),
                title="Diff (local)",
            )

            # DiffView — explorer mode
            yield Panel(
                DiffView(
                    document=build_source_document(SAMPLE_YAML, language="yaml"),
                    id="debug-source",
                ),
                title="Source explorer",
            )

            # DiffView — auto detect
            document = build_source_document(MINIFIED_BODY)  # no language argument
            yield Panel(
                DiffView(document=document, id="debug-minified"),
                title="Minified body",
                hint=document_label(document),  # "json · detected · pretty-printed"
            )

            yield Panel(
                DiffView(
                    document=build_source_document(SAMPLE_HTML, language="html"),
                    id="debug-source-html",
                ),
                title="Source explorer - HTML",
            )

            # Tree Menu
            yield Panel(TreeMenu(*NODES, initial="req-users-list"), title="Collections")

    def on_filter_input_changed(self, event: FilterInput.Changed) -> None:
        """Project filter edits into the fixture line."""
        self.filter_text = event.value

    def action_focus_filter(self) -> None:
        """Focus the filter box."""
        self.query_one(FilterInput).focus()

    def watch_filter_text(self) -> None:
        """Synchronize the filter fixture after reactive changes."""
        self.query_one(FilteredTextLine).filter_text = self.filter_text


_PANELS: dict[DebugSection, type[Panel]] = {DebugSection.COMPONENTS: ComponentsDebugPanel}


class DebugWorkspace(Workspace):
    """Debug Workspace."""

    SCREEN = ComparoScreen.DEBUG
    section: reactive[DebugSection] = reactive(DebugSection.STATE, init=False)

    @override
    def compose(self) -> ComposeResult:
        """Compose the debug navigation and selected panel."""
        with Horizontal(id="debug-layout"):
            yield Panel(
                Menu.from_enum(DebugSection, initial=self.section),
                title="Menu",
                id="debug-menu-panel",
            )
            with ContentSwitcher(initial=self.section, id="debug-right-panel"):
                for section in DebugSection:
                    yield (
                        _PANELS[section](
                            title=section.value.title(),
                            id=section,
                        )
                        if section in _PANELS
                        else Panel(
                            Static("Content"),
                            title=section.value.title(),
                            id=section,
                            classes="overflow",
                        )
                    )

    def watch_section(self) -> None:
        """On section changes."""
        self.query_one("#debug-right-panel", ContentSwitcher).current = self.section

    def on_menu_highlighted(self, event: Menu.Highlighted) -> None:
        """On menu item highlighted."""
        self.section = DebugSection(event.item)

    @override
    def focus_default(self) -> None:
        """Focus on the menu."""
        self.query_one(Menu).focus()
