"""Reports workspace — archive + preview; replay mounts the Analyse view."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import Vertical

from comparo.tuibis import fixtures_analyse
from comparo.tuibis import fixtures_misc
from comparo.tuibis.viewmodels import KeyHint
from comparo.tuibis.widgets.analyse_view import AnalyseView
from comparo.tuibis.widgets.cursor_list import CursorList
from comparo.tuibis.widgets.filter_bar import FilterBar
from comparo.tuibis.widgets.host import InspectorHost
from comparo.tuibis.widgets.lines import LineBlock
from comparo.tuibis.widgets.panel import Panel

REPLAY_HINTS: tuple[KeyHint, ...] = (
    KeyHint("i", "next index"),
    KeyHint("z", "deep dive"),
    KeyHint("esc", "back to archive"),
)


class ReportsWorkspace(Vertical):
    """Composition only: browse the archive, replay a report read-only."""

    BINDINGS: ClassVar = [
        Binding("slash", "focus_filter", "Filter", show=False),
    ]

    def __init__(self) -> None:
        """Build the interactive archive, the live preview, and the replay."""
        self._browsing = True
        self._replay = AnalyseView(fixtures_analyse.analyse(), replay=True)
        self._previews = fixtures_misc.report_previews()
        self._archive = CursorList(fixtures_misc.archive_items(), id="reports-archive-list")
        self._filter = FilterBar("search runs…", fixtures_misc.REPORTS_CHIPS, "all")
        initial = self._previews[self._archive.cursor_key]
        self._preview_block = LineBlock(initial.lines)
        self._preview_panel = Panel(
            self._preview_block,
            title=initial.title,
            id="reports-preview",
        )
        archive_panel = Panel(
            self._archive,
            self._filter,
            title="ARCHIVE — read-only · opening never sends HTTP",
            hint="/ filter · enter open",
            id="reports-archive",
        )
        browse = Horizontal(archive_panel, self._preview_panel, classes="stack")
        self._host = InspectorHost({"browse": browse, "replay": self._replay}, initial="browse")
        super().__init__(self._host, id="reports-workspace")

    @property
    def hints(self) -> tuple[KeyHint, ...]:
        """Contextual footer hints."""
        return fixtures_misc.REPORTS_BROWSE_HINTS if self._browsing else REPLAY_HINTS

    def _show_preview(self, key: str) -> None:
        """Swap the preview panel content for the run under the cursor."""
        preview = self._previews.get(key)
        if preview is None:
            return
        self._preview_block.update_lines(preview.lines)
        self._preview_panel.border_title = preview.title

    def on_cursor_list_cursor_moved(self, event: CursorList.CursorMoved) -> None:
        """Refresh the preview when the archive cursor moves."""
        if event.source is self._archive:
            self._show_preview(event.key)

    def on_cursor_list_activated(self, event: CursorList.Activated) -> None:
        """Enter on an archive run opens the replay, like `o`."""
        if event.source is not self._archive:
            return
        opener = getattr(self.app, "action_command", None)
        if opener is not None:
            opener("open")

    def on_filter_bar_changed(self, event: FilterBar.Changed) -> None:
        """Narrow the archive to runs matching the query."""
        self._archive.set_filter(event.query)

    def action_focus_filter(self) -> None:
        """Jump to the archive search input."""
        if self._browsing:
            self._filter.focus_query()

    def handle_command(self, command: str) -> bool:
        """Open replays the cursor run; esc returns to the archive."""
        if command == "open" and self._browsing:
            self._browsing = False
            self._host.show("replay")
            return True
        if command == "back" and not self._browsing:
            if self._replay.close_dive():
                return True
            self._browsing = True
            self._host.show("browse")
            return True
        if not self._browsing:
            if command == "index":
                self._replay.cycle_index()
                return True
            if command == "dive-response":
                return self._replay.dive("response")
        return False
