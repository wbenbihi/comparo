"""Read-only archive coordinator; saved runs reuse the existing ResultsView."""

import asyncio
from datetime import datetime
from typing import ClassVar
from typing import override

from rich.text import Text
from textual import on
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.events import Resize

from comparo.application import ReportArchiveEntry
from comparo.application import ReportArchiveListing
from comparo.application import ResultProjection
from comparo.tui.design.breakpoints import LayoutClass
from comparo.tui.design.breakpoints import layout_class
from comparo.tui4.components.actionbar import Action
from comparo.tui4.components.actionbar import ActionBar
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.menu import Menu
from comparo.tui4.components.menu import MenuItem
from comparo.tui4.components.reportarchive import ReportArchive
from comparo.tui4.components.reportexport import ExportDestination
from comparo.tui4.components.reportexport import ReportExport
from comparo.tui4.components.reportpreview import ReportPreview
from comparo.tui4.components.results import ResultsView
from comparo.tui4.components.selectfield import SelectField
from comparo.tui4.components.typography import Caption
from comparo.tui4.models.evidence import body_preview
from comparo.tui4.models.reports import ArchiveFilters
from comparo.tui4.models.reports import archive_columns
from comparo.tui4.models.reports import literal
from comparo.tui4.models.reports import profile_label
from comparo.tui4.models.reports import report_status
from comparo.tui4.models.reports import resource_label
from comparo.tui4.theme import style
from comparo.tui4.types import ComparoScreen
from comparo.tui4.workspaces.base import Workspace

PAGE_SIZE = 50


class ReportsWorkspace(Workspace):
    """Browse, preview, analyze, copy and explicitly export persisted results."""

    SCREEN = ComparoScreen.REPORTS
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("/", "filter", "Search"),
        Binding("enter", "open", "Open analysis"),
        Binding("p", "preview", "Preview"),
        Binding("e", "export", "Export"),
        Binding("c", "copy", "Copy ID"),
        Binding("r", "reload", "Reload"),
        Binding("escape", "archive", "Archive"),
        Binding("alt+left", "archive", "Back to archive", show=False),
    ]

    def __init__(self) -> None:
        """Keep archive selection independent of Launch and its current execution."""
        super().__init__()
        self.listing = ReportArchiveListing(entries=(), failures=())
        self.selected: ReportArchiveEntry | None = None
        self.projection: ResultProjection | None = None
        self.analysis_open = False
        self.archive_loading = False
        self.preview_loading = False
        self.page = 0
        self._generation = 0
        self._preview_generation = 0
        self._filtered: tuple[ReportArchiveEntry, ...] = ()

    @override
    def compose(self) -> ComposeResult:
        """Match the archive/filter/preview mockup without creating another analyzer."""
        yield Caption("Reports", id="reports-title")
        yield Caption("Saved execution results for this project · read-only", id="reports-subtitle")
        with Vertical(id="reports-browse"):
            with Horizontal(id="reports-filters"):
                yield FilterInput("Search reports…", id="reports-search")
                for name, label in (
                    ("status", "Status"),
                    ("profile", "Profile"),
                    ("environment", "Env"),
                    ("date", "Date"),
                    ("tag", "Tag"),
                ):
                    options: tuple[MenuItem, ...] = (
                        MenuItem("all", "Any time" if name == "date" else "All"),
                    )
                    if name == "date":
                        options += (
                            MenuItem("today", "Today"),
                            MenuItem("7", "Last 7 days"),
                            MenuItem("30", "Last 30 days"),
                        )
                    yield SelectField(label, options, id=f"reports-{name}")
            with Horizontal(id="reports-layout"):
                with Vertical(id="reports-index"):
                    yield Caption("", id="reports-columns", markup=False)
                    yield ReportArchive(id="reports-archive")
                    yield Caption("Loading saved reports…", id="reports-empty", markup=False)
                    yield Caption("", id="reports-page-info", markup=False)
                    yield ActionBar(id="reports-page-actions")
                yield ReportPreview(id="reports-preview")
            yield Caption("", id="reports-failures", markup=False)
        with Vertical(id="reports-analysis"):
            yield ActionBar((Action("archive", "Back to archive", key="Alt+←"),), id="reports-back")
            yield ResultsView(id="reports-results")
        yield Caption("", id="reports-notice", markup=False)

    def on_mount(self) -> None:
        """Keep the analyzer dormant until the user opens an authenticated saved run."""
        self.query_one("#reports-analysis").display = False

    @override
    def on_show(self) -> None:
        """Refresh saved runs on workspace entry without disturbing an open analysis."""
        if not self.analysis_open:
            self.action_reload()
        self.focus_default()

    @override
    def focus_default(self) -> None:
        """Restore focus to the active report surface."""
        if self.analysis_open:
            self.query_one(ResultsView).action_groups()
        elif self._filtered:
            self.query_one(ReportArchive).focus()
        else:
            actions = self.query_one("#reports-page-actions", ActionBar)
            if actions.actions:
                self.call_after_refresh(actions.focus_action)
            else:
                self.query_one("#reports-search", FilterInput).focus()

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Keep archive shortcuts from stealing the analyzer's evidence bindings."""
        if action == "archive":
            return self.analysis_open or self.has_class("previewing")
        if action in {"filter", "reload", "preview", "open", "export", "copy"}:
            if self.analysis_open:
                return False
            if action in {"open", "export"}:
                return not self.archive_loading and self.projection is not None
            if action in {"copy", "preview"}:
                return self.selected is not None
            return not self.archive_loading
        return super().check_action(action, parameters)

    def _status(self, message: str) -> None:
        self.query_one("#reports-notice", Caption).update(message)
        self.refresh_bindings()

    def action_reload(self) -> None:
        """Refresh the bounded archive and invalidate in-flight selected-run reads."""
        if self.analysis_open:
            return
        self._generation += 1
        self._preview_generation += 1
        self.archive_loading = True
        self.projection = None
        self.preview_loading = False
        self.query_one(ReportPreview).show_entry(self.selected)
        self._status("Refreshing saved reports…")
        self._load_archive(self._generation)

    @work(exclusive=True, group="reports-list")
    async def _load_archive(self, generation: int) -> None:
        try:
            listing = await asyncio.to_thread(self.backend.list_reports)
        except Exception:  # noqa: BLE001 - filesystem exception content is never displayed
            if generation == self._generation and self.is_mounted:
                self.archive_loading = False
                self.listing = ReportArchiveListing(entries=(), failures=())
                self._status("Archive unavailable or its read limit was reached. Press r to retry.")
                self._empty(
                    "Could not load saved reports. Check archive availability, then reload."
                )
                if self.display:
                    self.focus_default()
            return
        if generation != self._generation or not self.is_mounted:
            return
        self.listing = listing
        self.archive_loading = False
        self._refresh_choices()
        self.query_one("#reports-failures", Caption).update(
            (
                f"! {len(listing.failures)} reports could not be verified · "
                + " · ".join(
                    f"{item.bundle_id}: {item.code.value}" for item in listing.failures[:3]
                )
            )
            if listing.failures
            else ""
        )
        self.query_one("#reports-failures").display = bool(listing.failures)
        self._apply_filters(reset_page=False)
        self._status("Times shown in local timezone · captured bodies are verified when opened.")
        if self.display:
            self.focus_default()

    def _refresh_choices(self) -> None:
        entries = self.listing.entries
        choices = {
            "status": {report_status(row) for row in entries},
            "profile": {profile_label(row) for row in entries},
            "environment": {resource_label(item) for row in entries for item in row.environments},
            "tag": {tag for row in entries for tag in row.tags},
        }
        for name, values in choices.items():
            field = self.query_one(f"#reports-{name}", SelectField)
            if field.value != "all":
                values.add(field.value)
            field.set_items(
                (
                    MenuItem("all", "All"),
                    *(MenuItem(value, literal(value)) for value in sorted(values)),
                )
            )

    def _filters(self) -> ArchiveFilters:
        return ArchiveFilters(
            text=self.query_one("#reports-search", FilterInput).value,
            **{
                name: self.query_one(f"#reports-{name}", SelectField).value
                for name in ("status", "profile", "environment", "date", "tag")
            },
        )

    def _apply_filters(self, *, reset_page: bool = True) -> None:
        if reset_page:
            self.page = 0
        filters = self._filters()
        now = datetime.now().astimezone()
        self._filtered = tuple(row for row in self.listing.entries if filters.matches(row, now))
        self.page = min(self.page, max(0, (len(self._filtered) - 1) // PAGE_SIZE))
        visible = self._filtered[self.page * PAGE_SIZE : (self.page + 1) * PAGE_SIZE]
        selected_id = self.selected.bundle_id if self.selected else None
        self.query_one(ReportArchive).set_entries(visible, selected_id)
        self.query_one("#reports-empty").display = not visible
        self.query_one(ReportArchive).display = bool(visible)
        self.query_one("#reports-columns").display = bool(visible)
        self.set_class(not visible, "empty-archive")
        self.query_one("#reports-empty", Caption).update(
            "No matching reports. Change your filters or use Clear filters."
            if self.listing.entries
            else (
                "No readable reports. Saved reports failed verification; see the details below."
                if self.listing.failures
                else "No saved reports yet. Run a comparison in Launch to create one."
            )
        )
        self.query_one("#reports-page-info", Caption).update(
            f"{len(self._filtered)} matching · {len(self.listing.entries)} saved"
            + (
                f" · page {self.page + 1} of {(len(self._filtered) + PAGE_SIZE - 1) // PAGE_SIZE}"
                if visible
                else ""
            )
        )
        self.query_one("#reports-page-actions", ActionBar).actions = (
            Action("previous", "Previous", enabled=self.page > 0),
            Action("next", "Next", enabled=(self.page + 1) * PAGE_SIZE < len(self._filtered)),
            Action("clear", "Clear filters", enabled=filters != ArchiveFilters()),
            Action("reload", "Reload", key="r"),
        )
        selected = next(
            (row for row in visible if row.bundle_id == selected_id),
            visible[0] if visible else None,
        )
        self._select(selected, force=self.projection is None)
        self._column_header()

    def _empty(self, message: str) -> None:
        self._filtered = ()
        self.query_one(ReportArchive).set_entries((), None)
        self.query_one(ReportArchive).display = False
        self.query_one("#reports-columns").display = False
        self.query_one("#reports-empty").display = True
        self.query_one("#reports-empty", Caption).update(message)
        self.set_class(True, "empty-archive")
        self.query_one("#reports-page-info", Caption).update("Archive unavailable")
        self.query_one("#reports-page-actions", ActionBar).actions = (
            Action("reload", "Retry", key="r", primary=True),
        )
        self.query_one("#reports-failures").display = False
        self._select(None)

    def _select(self, entry: ReportArchiveEntry | None, *, force: bool = False) -> None:
        if not force and entry == self.selected:
            return
        self._preview_generation += 1
        self.selected = entry
        self.projection = None
        self.preview_loading = entry is not None
        self.query_one(ReportPreview).show_entry(entry)
        self.refresh_bindings()
        if entry is not None:
            self._load_preview(entry, self._preview_generation)

    @work(exclusive=True, group="reports-preview")
    async def _load_preview(self, entry: ReportArchiveEntry, generation: int) -> None:
        try:
            projection = await asyncio.to_thread(self.backend.read_report, entry.bundle_id)
        except Exception:  # noqa: BLE001 - fixed failure message at the UI boundary
            projection = None
        if generation != self._preview_generation or not self.is_mounted:
            return
        self.preview_loading = False
        self.projection = projection
        self.query_one(ReportPreview).show_entry(
            entry,
            projection,
            error="This report could not be verified. Reload the archive."
            if projection is None
            else "",
        )
        self.refresh_bindings()

    @on(Menu.Highlighted, "#reports-archive")
    def _highlight(self, event: Menu.Highlighted) -> None:
        event.stop()
        entry = self.query_one(ReportArchive).entries.get(event.item)
        if entry is not None:
            self._select(entry)

    @on(Menu.Selected, "#reports-archive")
    def _open_row(self, event: Menu.Selected) -> None:
        event.stop()
        if self.selected is not None and event.item == self.selected.bundle_id:
            self.action_open()

    @on(FilterInput.Changed, "#reports-search")
    @on(SelectField.Changed)
    def _filter_changed(self, event: FilterInput.Changed | SelectField.Changed) -> None:
        event.stop()
        self._apply_filters()

    @on(FilterInput.Submitted, "#reports-search")
    @on(FilterInput.Dismissed, "#reports-search")
    def _filter_done(self, event: FilterInput.Dismissed | FilterInput.Submitted) -> None:
        event.stop()
        self.focus_default()

    @on(ActionBar.Requested)
    def _action(self, event: ActionBar.Requested) -> None:
        event.stop()
        if event.action in {"previous", "next"}:
            self.page += 1 if event.action == "next" else -1
            self._apply_filters(reset_page=False)
            self.focus_default()
        elif event.action == "clear":
            self.query_one("#reports-search", FilterInput).value = ""
            for field in self.query(SelectField):
                field.set_value("all")
            self._apply_filters()
            self.focus_default()
        else:
            actions = {
                "open": self.action_open,
                "export": self.action_export,
                "copy": self.action_copy,
                "reload": self.action_reload,
                "archive": self.action_archive,
            }
            action = actions.get(event.action)
            if action:
                action()

    def action_filter(self) -> None:
        """Search the archive without stepping through preview controls."""
        self.remove_class("previewing")
        self.query_one("#reports-search", FilterInput).focus()

    def action_preview(self) -> None:
        """Reveal the preview on compact terminals and focus its scrolling content."""
        self.add_class("previewing")
        self.query_one(ReportPreview).query_one(VerticalScroll).focus()

    def action_open(self) -> None:
        """Mount the exact shared analyzer; no execution service is called."""
        if self.projection is None or self.archive_loading:
            return
        self.analysis_open = True
        self.add_class("analyzing")
        self.query_one("#reports-browse").display = False
        self.query_one("#reports-analysis").display = True
        self.query_one(ResultsView).load(self.projection)
        self._status("Saved report · read-only · Alt+← returns to the archive")
        self.focus_default()

    def action_archive(self) -> None:
        """Restore the same filtered archive, page, selection, and scroll position."""
        self.analysis_open = False
        self.remove_class("analyzing")
        self.remove_class("previewing")
        self.query_one("#reports-analysis").display = False
        self.query_one("#reports-browse").display = True
        self._status("Times shown in local timezone · captured bodies are verified when opened.")
        self.focus_default()

    def action_copy(self) -> None:
        """Copy only the selected safe report identity."""
        if self.selected:
            self.app.copy_to_clipboard(self.selected.bundle_id)
            self._status("Report ID copied.")

    def action_export(self) -> None:
        """Choose a destination before exporting the selected saved report."""
        if self.selected is None or self.projection is None:
            return
        entry = self.selected
        self.app.push_screen(
            ReportExport(self.backend.report_export_path(entry.bundle_id)),
            lambda destination: self._export_chosen(entry.bundle_id, destination),
        )

    def _export_chosen(self, bundle_id: str, destination: ExportDestination | None) -> None:
        if destination is not None:
            self._export(bundle_id, destination)

    @work(group="reports-export", exclusive=True)
    async def _export(self, bundle_id: str, destination: ExportDestination) -> None:
        self._status("Verifying and exporting saved report…")
        try:
            await asyncio.to_thread(
                self.backend.export_report, bundle_id, destination.path, destination.format
            )
        except FileExistsError:
            self._status("Export destination already exists. Choose a new filename.")
        except Exception:  # noqa: BLE001 - never expose raw paths or artifact failures
            self._status("Export failed. Check the destination and report integrity, then retry.")
        else:
            self._status(f"Exported report to {literal(str(destination.path))}")

    @on(ResultsView.BodyRequested)
    def _body_requested(self, event: ResultsView.BodyRequested) -> None:
        event.stop()
        if self.analysis_open:
            self._read_body(event)

    @work(exclusive=True, group="reports-body")
    async def _read_body(self, event: ResultsView.BodyRequested) -> None:
        try:
            windows = [
                await self.backend.read_result_body(reference) for reference in event.references
            ]
            document, message = body_preview(windows)
        except Exception:  # noqa: BLE001 - identical safe evidence failure across workspaces
            document, message = None, "Capture unavailable or report verification failed."
        if self.is_mounted:
            event.view.show_body(event.references, document, message)

    @on(ReportArchive.LayoutChanged)
    def _archive_layout_changed(self, event: ReportArchive.LayoutChanged) -> None:
        event.stop()
        self.call_after_refresh(self._column_header)

    def _column_header(self) -> None:
        width = max(30, self.query_one(ReportArchive).scrollable_content_region.width - 2)
        header = Text("", style("muted"))
        for name, size in archive_columns(width):
            header.append(f"{name:<{size}}")
        self.query_one("#reports-columns", Caption).update(header)

    def on_resize(self, event: Resize) -> None:
        """Keep the preview readable; compact terminals use an explicit preview replacement."""
        self.set_class(layout_class(event.size.width) is LayoutClass.COMPACT, "compact")
        self.set_class(layout_class(event.size.width) is LayoutClass.WIDE, "wide")
        self.call_after_refresh(self._column_header)
