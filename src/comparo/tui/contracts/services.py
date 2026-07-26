"""Narrow application-service port consumed by TUI controllers."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from comparo.application.compare import ComparePreparationHandle
from comparo.application.compare import ComparePreparationResult
from comparo.application.execution_session import ExecutionSession
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.tui.contracts.models import CompareDraft
from comparo.tui.contracts.models import TuiExecutionControls


class TuiBackendErrorCode(StrEnum):
    """Closed value-free failures from the concrete TUI backend boundary."""

    CLOSED = "closed"
    INVALID_REQUEST = "invalid-request"
    PROJECT_LOAD_FAILED = "project-load-failed"
    COMPARE_PREPARE_FAILED = "compare-prepare-failed"
    COMPARE_START_FAILED = "compare-start-failed"
    COMPARE_HANDLE_UNKNOWN = "compare-handle-unknown"
    COMPARE_DISCARD_FAILED = "compare-discard-failed"
    REPORT_LIST_UNAVAILABLE = "report-list-unavailable"
    REPORT_LIST_LIMIT_EXCEEDED = "report-list-limit-exceeded"
    REPORT_OPEN_FAILED = "report-open-failed"
    CLOSE_FAILED = "close-failed"


class TuiBackendError(RuntimeError):
    """Safe frontend failure retaining no paths, handles, or exception text."""

    __slots__ = ("code",)

    def __init__(self, code: TuiBackendErrorCode) -> None:
        """Retain only one closed error code."""
        if type(code) is not TuiBackendErrorCode:
            raise TypeError("TUI backend error code must be typed")
        self.code = code
        super().__init__(code.value)


class TuiBackend(Protocol):
    """All backend capabilities available to the Textual adapter."""

    async def load_project(self) -> ProjectWorkspaceResult:
        """Return one safe Explorer snapshot or diagnostics."""
        ...

    async def prepare_compare(
        self,
        draft: CompareDraft,
        controls: TuiExecutionControls,
    ) -> ComparePreparationResult:
        """Compile the draft and exact execution controls behind an opaque handle."""
        ...

    async def start_compare(
        self,
        handle: ComparePreparationHandle,
        controls: TuiExecutionControls,
    ) -> ExecutionSession:
        """Consume an admitted preparation and start one interactive execution."""
        ...

    async def discard_compare(self, handle: ComparePreparationHandle) -> bool:
        """Release one superseded prepared handle without effects."""
        ...

    async def list_reports(self) -> ReportArchiveListing:
        """Return the bounded authenticated report archive index."""
        ...

    async def open_report(self, bundle_id: str) -> ResultProjection:
        """Authenticate and project one stored result without resending requests."""
        ...

    async def close(self) -> None:
        """Release prepared plans and any backend-owned resources."""
        ...


__all__ = ("TuiBackend", "TuiBackendError", "TuiBackendErrorCode")
