"""CursorList — the interactive list every screen navigates with.

Real keyboard behavior: ↑↓ move the cursor (skipping headers), space toggles
selection on selectable rows, enter activates, and a text filter hides
non-matching rows. Screens react through typed messages.
"""

from __future__ import annotations

from typing import ClassVar
from typing import override

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message

from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import StyledCell
from comparo.tuibis.viewmodels import StyledLine
from comparo.tuibis.widgets.lines import render_line
from comparo.tuibis.widgets.sections import TextRow


class CursorList(VerticalScroll, can_focus=True):
    """A navigable, selectable, filterable list of styled rows."""

    BINDINGS: ClassVar = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("space", "toggle_row", "Select", show=False),
        Binding("enter", "activate", "Open", show=False),
    ]

    class CursorMoved(Message):
        """The cursor highlighted a new row."""

        def __init__(self, key: str, source: CursorList) -> None:
            """Carry the highlighted row's key and its list."""
            super().__init__()
            self.key = key
            self.source = source

    class Toggled(Message):
        """A selectable row was toggled."""

        def __init__(self, key: str, selected: bool, source: CursorList) -> None:
            """Carry the toggled row's key, state, and list."""
            super().__init__()
            self.key = key
            self.selected = selected
            self.source = source

    class Activated(Message):
        """A row was activated with enter."""

        def __init__(self, key: str, source: CursorList) -> None:
            """Carry the activated row's key and its list."""
            super().__init__()
            self.key = key
            self.source = source

    def __init__(self, items: tuple[ListItemVM, ...], *, id: str | None = None) -> None:
        """Build rows and place the cursor on the first navigable one."""
        super().__init__(id=id)
        self._items: list[ListItemVM] = list(items)
        self._selected: set[str] = {item.key for item in items if item.selected}
        self._filter = ""
        self._cursor = self._first_navigable()
        self._rows: list[TextRow] = []

    @override
    def compose(self):  # type: ignore[no-untyped-def]
        """Yield one row per item."""
        self._rows = [TextRow(self._render_item(index)) for index in range(len(self._items))]
        self._apply_filter_display()
        yield from self._rows

    # ------------------------------------------------------------- rendering

    def _render_item(self, index: int):  # type: ignore[no-untyped-def]
        item = self._items[index]
        cells: list[StyledCell] = []
        if item.selectable:
            mark_on = item.key in self._selected
            cells.append(
                StyledCell("[x] " if mark_on else "[ ] ", "mark.on" if mark_on else "mark.off")
            )
        cells.extend(item.line.cells)
        line = StyledLine(
            tuple(cells),
            cursor=(index == self._cursor and not item.header),
            indent=item.line.indent,
        )
        return render_line(line)

    def _refresh_row(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            self._rows[index].update(self._render_item(index))
            self._rows[index].set_class(index == self._cursor, "row-cursor")

    def _refresh_all(self) -> None:
        for index in range(len(self._rows)):
            self._refresh_row(index)

    # ------------------------------------------------------------ navigation

    def _navigable(self, index: int) -> bool:
        item = self._items[index]
        if item.header or item.dimmed:
            return False
        return self._matches(item)

    def _first_navigable(self) -> int:
        for index in range(len(self._items)):
            if not self._items[index].header and not self._items[index].dimmed:
                return index
        return 0

    def _move(self, step: int) -> None:
        index = self._cursor
        while True:
            index += step
            if index < 0 or index >= len(self._items):
                return
            if self._navigable(index):
                break
        previous = self._cursor
        self._cursor = index
        self._refresh_row(previous)
        self._refresh_row(index)
        self._rows[index].scroll_visible()
        self.post_message(self.CursorMoved(self._items[index].key, self))

    def action_cursor_up(self) -> None:
        """Move the cursor to the previous navigable row."""
        self._move(-1)

    def action_cursor_down(self) -> None:
        """Move the cursor to the next navigable row."""
        self._move(1)

    def action_toggle_row(self) -> None:
        """Toggle selection on the cursor row."""
        item = self._items[self._cursor]
        if not item.selectable:
            return
        if item.key in self._selected:
            self._selected.discard(item.key)
        else:
            self._selected.add(item.key)
        self._refresh_row(self._cursor)
        self.post_message(self.Toggled(item.key, item.key in self._selected, self))

    def action_activate(self) -> None:
        """Activate the cursor row."""
        self.post_message(self.Activated(self._items[self._cursor].key, self))

    # --------------------------------------------------------------- filter

    def _matches(self, item: ListItemVM) -> bool:
        if not self._filter or item.header:
            return True
        haystack = item.search_text or " ".join(cell.text for cell in item.line.cells)
        return self._filter.lower() in haystack.lower()

    def _apply_filter_display(self) -> None:
        for index, item in enumerate(self._items):
            visible = item.header or self._matches(item)
            self._rows[index].display = visible

    def set_filter(self, query: str) -> None:
        """Filter rows by substring; headers stay visible."""
        self._filter = query
        self._apply_filter_display()
        if not self._navigable(self._cursor):
            self._cursor = self._first_matching()
            self._refresh_all()
            if self._items:
                self.post_message(self.CursorMoved(self._items[self._cursor].key, self))

    def _first_matching(self) -> int:
        for index in range(len(self._items)):
            if self._navigable(index):
                return index
        return self._first_navigable()

    # ---------------------------------------------------------------- state

    def select_all(self, selected: bool) -> None:
        """Select or clear every selectable row."""
        for item in self._items:
            if item.selectable:
                if selected:
                    self._selected.add(item.key)
                else:
                    self._selected.discard(item.key)
        self._refresh_all()
        self.post_message(self.Toggled("*", selected, self))

    @property
    def selected_keys(self) -> frozenset[str]:
        """Keys of all selected rows."""
        return frozenset(self._selected)

    @property
    def cursor_key(self) -> str:
        """Key of the cursor row."""
        return self._items[self._cursor].key
