"""CursorList — keyboard- and mouse-driven list, the tui3 workhorse.

↑↓ move the cursor (headers skipped), space toggles ◉/○ on selectable rows,
enter activates, click moves the cursor and reports the click, and a text
filter hides non-matching rows. Screens react through typed messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import ClassVar
from typing import override

from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Static

from comparo.tui3.theme import style
from comparo.tui3.widgets import fragments as fr


@dataclass
class Row:
    """One list row."""

    key: str
    text: Text
    selectable: bool = False
    selected: bool = False
    header: bool = False
    dim: bool = False
    search: str = ""
    indent: int = 0
    meta: dict[str, str] = field(default_factory=dict)


class _RowStatic(Static):
    """Internal row widget that reports clicks with its index."""

    def __init__(self, index: int) -> None:
        """Remember the row index."""
        super().__init__("")
        self.index = index

    def on_click(self) -> None:
        """Report the click to the owning list."""
        self.post_message(CursorList.RowPressed(self.index))


class CursorList(VerticalScroll, can_focus=True):
    """The navigable list."""

    BINDINGS: ClassVar = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("space", "toggle_row", "Select", show=False),
        Binding("enter", "activate", "Open", show=False),
    ]

    class RowPressed(Message):
        """Internal: a row widget was clicked."""

        def __init__(self, index: int) -> None:
            """Carry the clicked index."""
            super().__init__()
            self.index = index

    class CursorMoved(Message):
        """The cursor highlighted a new row."""

        def __init__(self, key: str, source: CursorList) -> None:
            """Carry key and source list."""
            super().__init__()
            self.key = key
            self.source = source

    class Toggled(Message):
        """A selectable row changed state (or a click asked it to)."""

        def __init__(self, key: str, selected: bool, source: CursorList) -> None:
            """Carry key, new state, and source list."""
            super().__init__()
            self.key = key
            self.selected = selected
            self.source = source

    class Activated(Message):
        """A row was activated (enter or click on selected)."""

        def __init__(self, key: str, source: CursorList) -> None:
            """Carry key and source list."""
            super().__init__()
            self.key = key
            self.source = source

    def __init__(
        self, rows: tuple[Row, ...], *, click_toggles: bool = False, id: str | None = None
    ) -> None:
        """Build the list; ``click_toggles`` makes clicks toggle selection."""
        super().__init__(id=id, classes="cursor-list")
        self._rows = list(rows)
        self._widgets: list[_RowStatic] = []
        self._filter = ""
        self._click_toggles = click_toggles
        self._cursor = self._first_navigable()

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield one widget per row."""
        self._widgets = [_RowStatic(i) for i in range(len(self._rows))]
        for widget in self._widgets:
            widget.update(self._render_row(widget.index))
        self._apply_filter()
        yield from self._widgets

    # ------------------------------------------------------------ rendering

    def _render_row(self, index: int) -> Text:
        row = self._rows[index]
        out = Text(" " * (1 + row.indent))
        if row.selectable:
            out.append_text(fr.mark(row.selected))
            out.append(" ")
        out.append_text(row.text)
        if row.dim:
            out.stylize(style("dim"))
        return out

    def _paint(self, index: int) -> None:
        if 0 <= index < len(self._widgets):
            self._widgets[index].update(self._render_row(index))
            self._widgets[index].set_class(
                index == self._cursor and not self._rows[index].header, "row-cursor"
            )

    def _paint_all(self) -> None:
        for index in range(len(self._widgets)):
            self._paint(index)

    def refresh_rows(self) -> None:
        """Re-render every row (after external state changes)."""
        self._paint_all()

    # ----------------------------------------------------------- navigation

    def _matches(self, row: Row) -> bool:
        if not self._filter or row.header:
            return True
        hay = row.search or row.text.plain
        return self._filter.lower() in hay.lower()

    def _navigable(self, index: int) -> bool:
        row = self._rows[index]
        return not row.header and self._matches(row)

    def _first_navigable(self) -> int:
        for index, row in enumerate(self._rows):
            if not row.header:
                return index
        return 0

    def _move(self, step: int) -> None:
        index = self._cursor
        while True:
            index += step
            if index < 0 or index >= len(self._rows):
                return
            if self._navigable(index):
                break
        previous, self._cursor = self._cursor, index
        self._paint(previous)
        self._paint(index)
        self._widgets[index].scroll_visible()
        self.post_message(self.CursorMoved(self._rows[index].key, self))

    def action_cursor_up(self) -> None:
        """Move up."""
        self._move(-1)

    def action_cursor_down(self) -> None:
        """Move down."""
        self._move(1)

    def action_toggle_row(self) -> None:
        """Toggle the cursor row if selectable."""
        self._toggle(self._cursor)

    def action_activate(self) -> None:
        """Activate the cursor row."""
        self.post_message(self.Activated(self._rows[self._cursor].key, self))

    def _toggle(self, index: int) -> None:
        row = self._rows[index]
        if not row.selectable:
            return
        row.selected = not row.selected
        self._paint(index)
        self.post_message(self.Toggled(row.key, row.selected, self))

    def on_cursor_list_row_pressed(self, event: CursorList.RowPressed) -> None:
        """Handle a row click: move cursor, then toggle or activate."""
        event.stop()
        index = event.index
        if self._rows[index].header:
            self.post_message(self.Activated(self._rows[index].key, self))
            return
        previous, self._cursor = self._cursor, index
        self._paint(previous)
        self._paint(index)
        self.post_message(self.CursorMoved(self._rows[index].key, self))
        if self._click_toggles and self._rows[index].selectable:
            self._toggle(index)
        elif not self._rows[index].selectable:
            self.post_message(self.Activated(self._rows[index].key, self))

    # -------------------------------------------------------------- filter

    def _apply_filter(self) -> None:
        for index, row in enumerate(self._rows):
            self._widgets[index].display = row.header or self._matches(row)

    def set_filter(self, query: str) -> None:
        """Filter rows by substring."""
        self._filter = query
        self._apply_filter()
        if not self._navigable(self._cursor):
            for index in range(len(self._rows)):
                if self._navigable(index):
                    self._cursor = index
                    break
            self._paint_all()
            self.post_message(self.CursorMoved(self._rows[self._cursor].key, self))

    # --------------------------------------------------------------- state

    def set_selected(self, keys: set[str]) -> None:
        """Force the selected set (profile prefill)."""
        for row in self._rows:
            if row.selectable:
                row.selected = row.key in keys
        self._paint_all()

    @property
    def selected_keys(self) -> frozenset[str]:
        """Currently selected keys."""
        return frozenset(r.key for r in self._rows if r.selectable and r.selected)

    @property
    def cursor_key(self) -> str:
        """Key under the cursor."""
        return self._rows[self._cursor].key

    def move_cursor_to(self, key: str) -> None:
        """Place the cursor on a specific key."""
        for index, row in enumerate(self._rows):
            if row.key == key and not row.header:
                previous, self._cursor = self._cursor, index
                self._paint(previous)
                self._paint(index)
                return
