"""Bounded Matrix data cases and their resolved values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import Static

from comparo.application.value_inspection import ValueInspection
from comparo.tui.components.actionbar import Action
from comparo.tui.components.actionbar import ActionBar
from comparo.tui.components.inspector import InspectorColumns
from comparo.tui.components.inspector import InspectorSection
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table
from comparo.tui.models.resource import ResourceDetail
from comparo.tui.theme import style
from comparo.tui.value_navigation import value_field


@dataclass(frozen=True)
class MatrixCursor:
    """Remember only the selected data case and page."""

    case_id: str | None = None
    start: int = 0


class MatrixBrowser(Vertical):
    """Browse generated data cases without choosing an executable consumer."""

    DEFAULT_CSS = """
    MatrixBrowser { height: auto; }
    MatrixBrowser Table { height: auto; min-height: 3; max-height: 10; }
    MatrixBrowser .matrix-status { height: auto; }
    """

    class Ready(Message):
        """Request inspection from the workspace owner."""

        def __init__(self, browser: MatrixBrowser) -> None:
            """Identify the mounted browser receiving the result."""
            super().__init__()
            self.browser = browser

    class Changed(Ready):
        """The selected data case or page changed."""

    class Gone(Ready):
        """The browser no longer owns an inspection."""

    def __init__(self, resource: ResourceDetail) -> None:
        """Keep the exact resource revision and safe selection coordinates."""
        super().__init__()
        self.resource = resource
        self.cursor = MatrixCursor()
        self.result: ValueInspection | None = None

    @override
    def compose(self) -> ComposeResult:
        yield Static("Loading cases…", classes="matrix-status")
        with InspectorColumns():
            with Vertical():
                yield Table(columns=(Column("case", "Case"),), id="matrix-cases")
                yield ActionBar(id="matrix-page-controls")
            yield InspectorSection("Selected case values", KeyValueList(id="matrix-preview"))

    def on_mount(self) -> None:
        """Start resource inspection."""
        self.post_message(self.Ready(self))

    def on_unmount(self) -> None:
        """Cancel work owned by this browser."""
        self.post_message(self.Gone(self))

    def restore(self, cursor: MatrixCursor) -> None:
        """Restore selection without navigation or synthetic events."""
        self.cursor = cursor

    def show_loading(self) -> None:
        """Clear the previous case while retaining the collection."""
        self.result = None
        self.query_one("#matrix-preview", KeyValueList).entries = ()
        self.query_one(ActionBar).actions = ()
        self.query_one(".matrix-status", Static).update("Loading cases…")

    def apply(self, result: ValueInspection) -> None:
        """Display a standalone case collection and its masked values."""
        self.result = result
        window = result.matrix
        status = self.query_one(".matrix-status", Static)
        if window is None:
            status.update("Cases unavailable: " + ", ".join(result.diagnostics))
            return
        self.cursor = MatrixCursor(window.selected, window.start)
        table = self.query_one("#matrix-cases", Table)
        records = tuple(Record(case, (case,)) for case in window.cases)
        with table.prevent(Table.RowHighlighted):
            if table.records != records:
                table.records = records
            table.move_cursor(row=window.cases.index(window.selected))
        status.update(
            Content(
                f"Cases {window.start + 1}-{window.start + len(window.cases)} of {window.total}"
                + (" · partial value preview" if result.incomplete else "")
            )
        )
        controls = self.query_one(ActionBar)
        controls.display = window.total > 50
        controls.actions = (
            Action("previous-page", "Previous page", enabled=window.start > 0),
            Action(
                "next-page", "Next page", enabled=window.start + len(window.cases) < window.total
            ),
        )
        self.query_one("#matrix-preview", KeyValueList).entries = tuple(
            Field(
                " / ".join(field.path[1:]) or "Values",
                Content.from_rich_text(Text(field.value, style(field.tone or "fg"))),
            )
            for item in result.fields
            if (field := value_field(item))
        )

    def _changed(self, cursor: MatrixCursor) -> None:
        if cursor == self.cursor:
            return
        self.cursor = cursor
        self.show_loading()
        self.post_message(self.Changed(self))

    def on_table_row_highlighted(self, event: Table.RowHighlighted[str]) -> None:
        """Inspect the highlighted data case."""
        event.stop()
        if event.table.id == "matrix-cases" and event.row == event.table.cursor_row_id:
            self._changed(MatrixCursor(event.row, self.cursor.start))

    def on_action_bar_requested(self, event: ActionBar.Requested) -> None:
        """Page through cases using shared compact actions."""
        event.stop()
        offset = -50 if event.action == "previous-page" else 50
        self._changed(MatrixCursor(start=max(0, self.cursor.start + offset)))
