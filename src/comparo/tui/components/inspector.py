"""Responsive resource sections and synchronized record/detail inspection."""

from collections.abc import Mapping
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Resize
from textual.widget import Widget

from comparo.tui.breakpoints import LayoutClass
from comparo.tui.breakpoints import layout_class
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.separator import Separator
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table


class InspectorColumns(Widget):
    """Adjacent sections on wide terminals, stacked through the shared breakpoint policy."""

    DEFAULT_CSS = """
    InspectorColumns { layout: horizontal; width: 100%; height: auto; }
    InspectorColumns > Widget { width: 1fr; padding: 0 1; }
    InspectorColumns > Vertical { height: auto; }
    InspectorColumns.stacked { layout: vertical; }
    InspectorColumns.stacked > Widget { width: 100%; }
    """

    def on_resize(self, event: Resize) -> None:
        """Use terminal width consistently with the rest of the application."""
        self.set_class(layout_class(self.app.size.width) is not LayoutClass.WIDE, "stacked")


class InspectorSection(Vertical):
    """A labeled semantic block that works alone or in responsive columns."""

    DEFAULT_CSS = "InspectorSection { width: 100%; height: auto; margin-bottom: 1; }"

    def __init__(self, title: str, *children: Widget, classes: str | None = None) -> None:
        """Reuse the standard separator and existing content widgets."""
        super().__init__(Separator(label=title, align="left"), *children, classes=classes)


class RecordInspector(Vertical):
    """Select a stable record to inspect its complete safe detail fields."""

    DEFAULT_CSS = """
    RecordInspector { width: 100%; height: auto; }
    RecordInspector Table { height: auto; min-height: 3; max-height: 12; }
    """

    def __init__(
        self,
        columns: tuple[Column, ...],
        records: tuple[Record[str], ...],
        details: Mapping[str, tuple[Field, ...]],
        *,
        title: str = "Selected item",
        classes: str | None = None,
    ) -> None:
        """Retain already projected values; selection never resolves data locally."""
        super().__init__(classes=classes)
        self._columns = columns
        self._records = records
        self._details = details
        self._title = title

    @override
    def compose(self) -> ComposeResult:
        """Mount one collection and one stable detail pane."""
        initial = self._details.get(self._records[0].id, ()) if self._records else ()
        with InspectorColumns():
            yield Table(columns=self._columns, records=self._records)
            yield InspectorSection(self._title, KeyValueList(*initial, classes="selected-record"))

    def on_table_row_highlighted(self, event: Table.RowHighlighted[str]) -> None:
        """Synchronize detail immediately on keyboard or pointer highlight."""
        event.stop()
        self.query_one(KeyValueList).entries = self._details.get(event.row, ())
