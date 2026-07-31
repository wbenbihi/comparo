"""Table — semantic record table used across workspaces."""

from dataclasses import dataclass

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
            self.table = table
            self.row = row
            super().__init__()

        @property
        def control(self) -> "Table[R]":
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
        super().__init__(id=id, classes=classes)
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._columns = columns
        self._by_key: dict[str, Record[T]] = {}
        self.set_reactive(Table.records, records)

    def on_mount(self) -> None:
        for column in self._columns:
            self.add_column(column.label, width=column.width, key=column.id)
        self._rebuild()

    def watch_records(self) -> None:
        if self.is_mounted:
            self._rebuild()

    @property
    def cursor_row_id(self) -> T | None:
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
        event.stop()
        record = self._by_key.get(event.row_key.value or "")
        if record is not None:
            self.post_message(self.RowHighlighted(self, record.id))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        record = self._by_key.get(event.row_key.value or "")
        if record is not None:
            self.post_message(self.RowSelected(self, record.id))
