"""The Reports mockup's date-grouped archive, using the shared Menu primitive."""

from datetime import datetime
from typing import override

from rich.text import Text
from textual.events import Resize
from textual.message import Message

from comparo.application import ReportArchiveEntry
from comparo.tui.components.menu import Menu
from comparo.tui.components.menu import MenuItem
from comparo.tui.models.reports import archive_row
from comparo.tui.models.reports import date_group
from comparo.tui.theme import style


class ReportArchive(Menu):
    """Stable report identities, non-selectable date dividers, and responsive columns."""

    class LayoutChanged(Message):
        """Notify the archive owner after column space changes."""

    def __init__(self, *, id: str | None = None) -> None:
        """Start with no archive until its authenticated listing arrives."""
        super().__init__(id=id)
        self.entries: dict[str, ReportArchiveEntry] = {}

    def set_entries(self, entries: tuple[ReportArchiveEntry, ...], selected: str | None) -> None:
        """Keep date labels outside report navigation and restore the selected report."""
        self.entries = {entry.bundle_id: entry for entry in entries}
        rows: list[MenuItem] = []
        previous = ""
        now = datetime.now().astimezone()
        for index, entry in enumerate(entries):
            group = date_group(entry, now)
            if group != previous:
                rows.append(MenuItem(f"date:{index}", group, disabled=True))
                previous = group
            rows.append(MenuItem(entry.bundle_id, entry.run_id))
        self.items = tuple(rows)
        if selected in self.entries:
            self.highlighted = self.get_option_index(selected)

    @override
    def format_item(self, item: MenuItem) -> Text:
        entry = self.entries.get(item.id)
        if entry is None:
            return Text(item.label, style("muted"))
        return archive_row(entry, max(30, self.scrollable_content_region.width - 2))

    def on_resize(self, event: Resize) -> None:
        """Reflow columns without changing the report under the cursor."""
        if self.is_mounted:
            self._rebuild()
            self.post_message(self.LayoutChanged())
