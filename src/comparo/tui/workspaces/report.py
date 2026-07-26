"""Thin Report composition over authenticated archive and shared result widgets."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar
from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.widgets import Label
from textual.widgets import Static

from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.tui.bindings import textual_bindings
from comparo.tui.commands import CommandScope
from comparo.tui.components import ReportArchivePreview
from comparo.tui.components import ReportList
from comparo.tui.components import ResultsWorkspaceView
from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode
from comparo.tui.contracts import ResultAccess


class ReportWorkspaceState(StrEnum):
    """The archive browse and persisted-result surfaces."""

    BROWSE = "browse"
    RESULT = "result"


@final
class ReportWorkspace(Vertical):
    """Browse persisted artifacts and reuse the live result composition read-only."""

    BINDINGS: ClassVar[list[BindingType]] = textual_bindings(CommandScope.REPORT)

    def __init__(
        self,
        *,
        state: ReportWorkspaceState = ReportWorkspaceState.BROWSE,
        listing: ReportArchiveListing | None = None,
        result: ResultProjection | None = None,
        id: str | None = None,
        classes: str | None = "workspace",
    ) -> None:
        """Initialize the archive or one persisted result surface."""
        super().__init__(id=id, classes=classes)
        self.can_focus = True
        if type(state) is not ReportWorkspaceState:
            raise TypeError("Report workspace state must be typed")
        self.state = state
        self.listing = listing
        self.result = result
        self.error_message = "The selected report could not be opened safely."
        self.selected_entry = None if listing is None or not listing.entries else listing.entries[0]

    @override
    def compose(self) -> ComposeResult:
        """Compose archive or result state without reading storage."""
        with Vertical(classes="report-page"):
            with (
                Horizontal(classes="workspace-heading"),
                Vertical(classes="workspace-heading-copy"),
            ):
                yield Label("Report", classes="workspace-title")
                yield Static(
                    "Authenticated run history and shared read-only analysis.",
                    classes="workspace-subtitle",
                    markup=False,
                )
            yield self._content()

    def _content(self) -> StatePanel:
        if self.state is ReportWorkspaceState.RESULT:
            if self.result is None:
                return StatePanel(mode=StatePanelMode.ERROR, message=self.error_message)
            return StatePanel(
                ResultsWorkspaceView(self.result, access=ResultAccess.READ_ONLY),
                mode=StatePanelMode.CONTENT,
                classes="report-surface",
            )
        if self.listing is None:
            return StatePanel(
                mode=StatePanelMode.LOADING,
                message="Loading the authenticated report archive…",
            )
        if not self.listing.entries:
            message = "No persisted reports are available."
            if self.listing.failures:
                message = (
                    f"No reports could be opened · {len(self.listing.failures)} invalid entries."
                )
            return StatePanel(mode=StatePanelMode.EMPTY, message=message)
        selected = self.selected_entry or self.listing.entries[0]
        return StatePanel(
            Horizontal(
                ReportList(self.listing.entries, classes="report-archive-list"),
                ReportArchivePreview(selected, classes="report-reading-pane"),
                classes="report-browser-layout",
            ),
            mode=StatePanelMode.CONTENT,
            classes="report-surface",
        )

    @on(ReportList.ReportHighlighted)
    def preview_report(self, message: ReportList.ReportHighlighted) -> None:
        """Update report summary immediately on native cursor movement."""
        self.selected_entry = message.entry
        self.query_one(ReportArchivePreview).show_entry(message.entry)

    def show_loading(self) -> None:
        """Keep Report mounted while replacing its internal surface with loading."""
        self.state = ReportWorkspaceState.BROWSE
        self.listing = None
        self.result = None
        self.selected_entry = None
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_listing(self, listing: ReportArchiveListing) -> None:
        """Keep Report mounted while presenting a new authenticated archive listing."""
        if type(listing) is not ReportArchiveListing:
            raise TypeError("Report workspace listing must be typed")
        self.state = ReportWorkspaceState.BROWSE
        self.listing = listing
        self.result = None
        self.selected_entry = None if not listing.entries else listing.entries[0]
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_result(self, result: ResultProjection) -> None:
        """Keep Report mounted and reuse the shared result component read-only."""
        if type(result) is not ResultProjection:
            raise TypeError("Report workspace result must be typed")
        if self.state is ReportWorkspaceState.RESULT and self.result is result:
            return
        self.state = ReportWorkspaceState.RESULT
        self.result = result
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_error(self, message: str = "The report operation failed safely.") -> None:
        """Keep Report mounted while showing a bounded value-free error."""
        self.state = ReportWorkspaceState.RESULT
        self.result = None
        self.error_message = message
        if self.is_mounted:
            self.refresh(recompose=True)


__all__ = ("ReportWorkspace", "ReportWorkspaceState")
