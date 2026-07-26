"""Reusable authenticated report-archive list components."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import final

from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import Static

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
        subjects = ", ".join(entry.subjects) or "no subjects"
        created = datetime.fromtimestamp(
            entry.created_unix_nanoseconds / 1_000_000_000,
            tz=UTC,
        ).strftime("%Y-%m-%d %H:%M UTC")
        title = safe_terminal_text(
            f"{created}  ·  {entry.run_id[:18]}…",
            maximum_characters=512,
        )
        detail = safe_terminal_text(
            f"{subjects}  ·  {environments}  ·  {entry.executions} exec  ·  "
            f"{entry.comparisons} comparisons  ·  {entry.diffs} diffs",
            maximum_characters=512,
        )
        super().__init__(
            Vertical(
                Horizontal(
                    StatusBadge(status_for_outcome(entry.gate), detail=entry.gate),
                    Label(title, classes="report-list-title", markup=False),
                    classes="report-list-heading",
                ),
                Static(detail, classes="report-list-meta", markup=False),
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

    @final
    class ReportHighlighted(Message):
        """The archive cursor moved to one authenticated entry."""

        def __init__(self, entry: ReportArchiveEntry) -> None:
            """Retain the safe report summary."""
            super().__init__()
            self.entry = entry

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

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Publish native cursor movement for immediate report preview."""
        if type(event.item) is ReportListItem:
            self.post_message(self.ReportHighlighted(event.item.entry))


@final
class ReportArchivePreview(Static):
    """Outcome-first preview for one authenticated archive entry."""

    def __init__(
        self,
        entry: ReportArchiveEntry,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize from one safe manifest summary."""
        super().__init__("", id=id, classes=classes, markup=False)
        self.show_entry(entry)

    def show_entry(self, entry: ReportArchiveEntry) -> None:
        """Update the preview without opening report bodies or sending HTTP."""
        if type(entry) is not ReportArchiveEntry:
            raise TypeError("report preview requires a typed archive entry")
        topology = " → ".join(entry.environments) or "No environments"
        subjects = ", ".join(entry.subjects) or "No subjects"
        self.update(
            safe_terminal_text(
                "\n".join(
                    (
                        f"{entry.gate.upper()}  {entry.run_id}",
                        "",
                        f"Subjects           {subjects}",
                        f"Topology           {topology}",
                        f"Executions         {entry.executions}",
                        f"Comparisons        {entry.comparisons}",
                        f"Assertions         {entry.assertions}",
                        f"Diffs              {entry.diffs}",
                        f"Coverage issues    {entry.coverage_issues}",
                        f"Stored blobs       {entry.blobs} · {entry.blob_bytes} bytes",
                        "",
                        "Press Enter to open the shared read-only analysis.",
                    )
                ),
                maximum_characters=4096,
                multiline=True,
            )
        )


__all__ = ("ReportArchivePreview", "ReportList", "ReportListItem")
