"""Expandable semantic record groups using the shared table mechanics."""

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.binding import Binding
from textual.binding import BindingType
from textual.content import Content

from comparo.tui.components.table import Record
from comparo.tui.components.table import Table


@dataclass(frozen=True)
class RecordGroup:
    """A summary row and the records it owns, with stable identities at both levels."""

    summary: Record[str]
    children: tuple[Record[str], ...] = ()


class GroupedTable(Table[str]):
    """Keep expansion separate from selection, data refresh and keyboard focus."""

    _groups: tuple[RecordGroup, ...] = ()
    _expanded: set[str]

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("space", "toggle_group", "Expand/collapse"),
        Binding("right", "expand_group", "Expand", show=False),
        Binding("left", "collapse_group", "Collapse", show=False),
    ]

    def set_groups(self, groups: tuple[RecordGroup, ...], *, reset: bool = False) -> None:
        """Refresh records while preserving every user-controlled fold and selection."""
        self._groups = groups
        if reset or not hasattr(self, "_expanded"):
            self._expanded = set()
        self._expanded.intersection_update(group.summary.id for group in groups)
        self._render_groups()

    def _render_groups(self) -> None:
        records = []
        for group in self._groups:
            expanded = group.summary.id in self._expanded
            marker = "▾ " if expanded else "▸ " if group.children else "  "
            records.append(self._prefixed(group.summary, marker))
            if expanded:
                records.extend(self._prefixed(row, "  ↳ ") for row in group.children)
        self.records = tuple(records)

    @staticmethod
    def _prefixed(record: Record[str], prefix: str) -> Record[str]:
        first = record.cells[0]
        label = first if isinstance(first, Content) else Content(first)
        return Record(record.id, (Content(prefix) + label, *record.cells[1:]))

    def parent_id(self, identity: str | None) -> str | None:
        """Return the owning summary identity for a visible parent or child."""
        for group in self._groups:
            if group.summary.id == identity or any(row.id == identity for row in group.children):
                return group.summary.id
        return None

    def action_toggle_group(self) -> None:
        """Toggle the selected summary; a child collapses back to its parent."""
        parent = self.parent_id(self.cursor_row_id)
        if parent in getattr(self, "_expanded", set()):
            self.action_collapse_group()
        else:
            self.action_expand_group()

    def action_expand_group(self) -> None:
        """Expose the selected request's cases without moving its selection."""
        parent = self.parent_id(self.cursor_row_id)
        if parent is not None:
            self._expanded.add(parent)
            self._render_groups()

    def action_collapse_group(self) -> None:
        """Return a hidden child's selection to its visible summary."""
        parent = self.parent_id(self.cursor_row_id)
        if parent is not None:
            self._expanded.discard(parent)
            self._render_groups()
            self.move_cursor(row=next(i for i, row in enumerate(self.records) if row.id == parent))

    @on(Table.RowSelected)
    def _activate_group(self, event: Table.RowSelected[str]) -> None:
        if event.table is self and any(
            group.summary.id == event.row and group.children
            for group in getattr(self, "_groups", ())
        ):
            self.action_toggle_group()
