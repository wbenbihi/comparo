"""Table — semantic record table used across workspaces."""

from dataclasses import dataclass
from typing import override

from rich.text import Text
from textual.content import Content
from textual.events import Resize
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import DataTable
from textual.widgets.data_table import ColumnKey

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


class Table[T: str](DataTable[Text]):
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
        row_height: int = 1,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize a table from immutable column and row models."""
        super().__init__(id=id, classes=classes, cursor_foreground_priority="renderable")
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._columns = columns
        self._column_weights: tuple[float, ...] = ()
        self._minimum_table_width = 0
        self._row_height = row_height
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
        rendered = tuple(self._by_key.values())
        if 0 <= self.cursor_row < len(rendered):
            return rendered[self.cursor_row].id
        return None

    def configure(self, columns: tuple[Column, ...], *, row_height: int = 1) -> None:
        """Change a lens schema through the library without exposing native table state."""
        if columns == self._columns and row_height == self._row_height:
            return
        self._columns = columns
        self._column_weights = ()
        self._row_height = row_height
        self._by_key = {}
        self.records = ()
        if self.is_mounted:
            self.clear(columns=True)
            for column in columns:
                self.add_column(column.label, width=column.width, key=column.id)
            self._rebuild()

    def fit_proportions(self, weights: tuple[float, ...], *, minimum_width: int = 0) -> None:
        """Keep columns proportional after later visibility or container size changes."""
        self._column_weights = weights
        self._minimum_table_width = minimum_width
        self._fit_proportions()

    def _fit_proportions(self) -> None:
        if self._column_weights and self.is_mounted:
            available = max(self._minimum_table_width, self.size.width - 2 * len(self._columns) - 2)
            self.fit_columns(tuple(int(available * share) for share in self._column_weights))

    def on_resize(self, event: Resize) -> None:
        """Fit only when a caller has opted into proportional columns."""
        self._fit_proportions()

    def fit_columns(self, widths: tuple[int, ...]) -> None:
        """Resize presentation columns without replacing records or cursor identity."""
        for definition, width in zip(self._columns, widths, strict=True):
            column = self.columns.get(ColumnKey(definition.id))
            if column is not None:
                column.auto_width = False
                column.width = max(4, width)
        self.refresh(layout=True)

    def _rebuild(self) -> None:
        current = self.cursor_row_id
        old = self._by_key
        updated = {str(record.id): record for record in self.records}
        old_ids, new_ids = tuple(old), tuple(updated)
        if new_ids[: len(old_ids)] == old_ids:
            for key, record in updated.items():
                previous = old.get(key)
                if previous is None:
                    self.add_row(
                        *(self._cell(cell) for cell in record.cells),
                        key=key,
                        height=self._row_height,
                    )
                elif previous.cells != record.cells:
                    for column, cell, before in zip(
                        self._columns, record.cells, previous.cells, strict=True
                    ):
                        if cell != before:
                            self.update_cell(key, column.id, self._cell(cell), update_width=True)
        else:
            self.clear()
            for record in self.records:
                self.add_row(
                    *(self._cell(cell) for cell in record.cells),
                    key=str(record.id),
                    height=self._row_height,
                )
        self._by_key = updated
        if current is not None and str(current) in new_ids:
            self.move_cursor(row=new_ids.index(str(current)), scroll=False)

    @staticmethod
    def _cell(cell: Cell) -> Text:
        """Adapt literal Content to measurable Rich text at the native table boundary."""
        if isinstance(cell, str):
            return Text(cell)
        text = Text()
        for segment in cell.render_segments():
            text.append(segment.text, segment.style)
        return text

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
