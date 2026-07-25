"""Reusable authenticated report-archive list components."""

from __future__ import annotations

from typing import final

from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Label
from textual.widgets import ListItem
from textual.widgets import ListView

from comparo.application.report_archive import ReportArchiveEntry
from comparo.tui.components.status import StatusBadge
from comparo.tui.components.status import status_for_outcome
from comparo.tui.presentation import safe_terminal_text


@final
class ReportListItem(ListItem):
    """One authenticated body-free archive row."""

    def __init__(self, entry: ReportArchiveEntry) -> None:
        """Initialize one row from an authenticated archive entry."""
        self.entry = entry
        environments = " ⇄ ".join(entry.environments) or "no environments"
        label = safe_terminal_text(
            (
                f"{entry.run_id} · {environments} · {entry.executions} executions · "
                f"{entry.comparisons} comparisons"
            ),
            maximum_characters=512,
        )
        super().__init__(
            Horizontal(
                StatusBadge(status_for_outcome(entry.gate), detail=entry.gate),
                Label(label, markup=False),
                classes="report-list-row",
            )
        )


@final
class ReportList(ListView):
    """Shared report archive browser list with typed selection messages."""

    @final
    class ReportSelected(Message):
        """A persisted report was selected for authenticated opening."""

        def __init__(self, bundle_id: str) -> None:
            """Retain only the selected safe bundle identifier."""
            super().__init__()
            self.bundle_id = bundle_id

    def __init__(
        self,
        entries: tuple[ReportArchiveEntry, ...],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize rows from immutable authenticated archive entries."""
        super().__init__(
            *(ReportListItem(entry) for entry in entries),
            id=id,
            classes=classes,
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Translate one list event into a bundle identifier only."""
        if type(event.item) is ReportListItem:
            self.post_message(self.ReportSelected(event.item.entry.bundle_id))


__all__ = ("ReportList", "ReportListItem")
