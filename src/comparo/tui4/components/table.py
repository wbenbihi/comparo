"""Table — semantic record table used across workspaces."""

from dataclasses import dataclass
from typing import override

from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import DataTable

type Cell = str | Content


@dataclass(frozen=True)
class Column:
    """One table column."""

    id: str
    label: str
    width: int | None = None


@dataclass(frozen=True)
class Record[T: str]:
    """One table row."""

    id: T
    cells: tuple[Cell, ...]


class Table[T: str](DataTable[Cell]):
    """Row-cursor table posting semantic Highlighted/Selected messages.

    ``records`` is the only write path; sort data before assigning.
    """

    records: reactive[tuple[Record[T], ...]] = reactive(())

    class RowHighlighted[R: str](Message):
        """Cursor moved onto a row."""

        def __init__(self, table: "Table[R]", row: R) -> None:
            """Initialize the message with its table and row identity."""
            self.table = table
            self.row = row
            super().__init__()

        @property
        @override
        def control(self) -> "Table[R]":
            """Return the table that emitted this message."""
            return self.table

    class RowSelected[R: str](RowHighlighted[R]):
        """Row committed (enter/click)."""

    def __init__(
        self,
        *,
        columns: tuple[Column, ...],
        records: tuple[Record[T], ...] = (),
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a table from immutable column and row models."""
        super().__init__(id=id, classes=classes)
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._columns = columns
        self._by_key: dict[str, Record[T]] = {}
        self.records = records

    @override
    def on_mount(self) -> None:
        """Create the fixed columns and populate the initial rows."""
        for column in self._columns:
            self.add_column(column.label, width=column.width, key=column.id)
        self._rebuild()

    def watch_records(self) -> None:
        """Rebuild mounted rows when the record projection changes."""
        if self.is_mounted:
            self._rebuild()

    @property
    def cursor_row_id(self) -> T | None:
        """Return the stable identity under the cursor, when available."""
        if 0 <= self.cursor_row < len(self.records):
            return self.records[self.cursor_row].id
        return None

    def _rebuild(self) -> None:
        current = self.cursor_row_id
        self.clear()  # rows only; columns survive
        self._by_key = {str(record.id): record for record in self.records}
        for record in self.records:
            self.add_row(*record.cells, key=str(record.id))
        ids = [record.id for record in self.records]
        if current is not None and current in ids:
            self.move_cursor(row=ids.index(current))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Translate Textual's row key into the semantic row identity."""
        event.stop()
        record = self._by_key.get(event.row_key.value or "")
        if record is not None:
            self.post_message(self.RowHighlighted(self, record.id))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Translate a committed Textual row into a semantic selection."""
        event.stop()
        record = self._by_key.get(event.row_key.value or "")
        if record is not None:
            self.post_message(self.RowSelected(self, record.id))
