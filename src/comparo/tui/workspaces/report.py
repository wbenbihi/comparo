"""Thin Report composition over authenticated archive and shared result widgets."""

from __future__ import annotations

from enum import StrEnum
from typing import final
from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical

from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
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
        if type(state) is not ReportWorkspaceState:
            raise TypeError("Report workspace state must be typed")
        self.state = state
        self.listing = listing
        self.result = result
        self.error_message = "The selected report could not be opened safely."

    @override
    def compose(self) -> ComposeResult:
        """Compose archive or result state without reading storage."""
        if self.state is ReportWorkspaceState.RESULT:
            if self.result is None:
                yield StatePanel(
                    mode=StatePanelMode.ERROR,
                    message=self.error_message,
                )
            else:
                yield StatePanel(
                    ResultsWorkspaceView(self.result, access=ResultAccess.READ_ONLY),
                    mode=StatePanelMode.CONTENT,
                )
            return
        if self.listing is None:
            yield StatePanel(
                mode=StatePanelMode.LOADING,
                message="Loading the authenticated report archive…",
            )
            return
        if not self.listing.entries:
            message = "No persisted reports are available."
            if self.listing.failures:
                message = (
                    f"No reports could be opened · {len(self.listing.failures)} invalid entries."
                )
            yield StatePanel(mode=StatePanelMode.EMPTY, message=message)
            return
        yield StatePanel(
            ReportList(self.listing.entries, classes="report-archive-list"),
            mode=StatePanelMode.CONTENT,
        )

    def show_loading(self) -> None:
        """Keep Report mounted while replacing its internal surface with loading."""
        self.state = ReportWorkspaceState.BROWSE
        self.listing = None
        self.result = None
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_listing(self, listing: ReportArchiveListing) -> None:
        """Keep Report mounted while presenting a new authenticated archive listing."""
        if type(listing) is not ReportArchiveListing:
            raise TypeError("Report workspace listing must be typed")
        self.state = ReportWorkspaceState.BROWSE
        self.listing = listing
        self.result = None
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_result(self, result: ResultProjection) -> None:
        """Keep Report mounted and reuse the shared result component read-only."""
        if type(result) is not ResultProjection:
            raise TypeError("Report workspace result must be typed")
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
