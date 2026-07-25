"""Framework-neutral effect and execution coordinator for the Textual shell."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Coroutine
from contextlib import suppress
from enum import StrEnum
from typing import Any
from typing import final

from comparo.application.compare import ComparePreparationHandle
from comparo.application.execution_session import ExecutionProgressSnapshot
from comparo.application.execution_session import ExecutionSession
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.application.report_archive import ReportArchiveListing
from comparo.application.results import ResultProjection
from comparo.application.results import project_results
from comparo.tui.contracts import CompareDraft
from comparo.tui.contracts import CompareMode
from comparo.tui.contracts import TuiBackend
from comparo.tui.contracts import TuiBackendError
from comparo.tui.contracts import TuiExecutionControls
from comparo.tui.navigation import WorkspaceId

_GENERIC_PROJECT_FAILURE = "tui.project.load-failed"
_GENERIC_COMPARE_PREPARATION_FAILURE = "tui.compare.prepare-failed"
_GENERIC_COMPARE_EXECUTION_FAILURE = "tui.compare.execution-failed"
_GENERIC_RESULT_PROJECTION_FAILURE = "tui.compare.result-projection-failed"
_GENERIC_REPORT_LIST_FAILURE = "tui.report.list-failed"
_GENERIC_REPORT_OPEN_FAILURE = "tui.report.open-failed"


class CompareSurface(StrEnum):
    """The three renderable Compare lifecycle surfaces."""

    PREPARE = "prepare"
    RUNNING = "running"
    RESULTS = "results"


class ReportSurface(StrEnum):
    """The report archive and selected-result surfaces."""

    BROWSE = "browse"
    RESULT = "result"


ChangeCallback = Callable[[WorkspaceId], Awaitable[None]]


@final
class TuiCoordinator:
    """Own backend effects, bounded tasks, and one active execution independently of Textual."""

    def __init__(
        self,
        backend: TuiBackend,
        controls: TuiExecutionControls,
        changed: ChangeCallback,
    ) -> None:
        """Create an idle coordinator around injected effects and a render callback."""
        if type(controls) is not TuiExecutionControls:
            raise TypeError("TUI coordinator execution controls must be typed")
        self._backend = backend
        self._controls = controls
        self._changed = changed
        self.project: ProjectWorkspaceResult | None = None
        self.report_listing: ReportArchiveListing | None = None
        self.report_result: ResultProjection | None = None
        self.report_surface = ReportSurface.BROWSE
        self.report_error_code: str | None = None
        self.compare_draft = CompareDraft(mode=CompareMode.DIFFERENCES)
        self.compare_surface = CompareSurface.PREPARE
        self.compare_handle: ComparePreparationHandle | None = None
        self.compare_plan: PlanInspection | None = None
        self.compare_progress: ExecutionProgressSnapshot | None = None
        self.compare_result: ResultProjection | None = None
        self.compare_diagnostics: tuple[ApplicationDiagnostic, ...] = ()
        self.session: ExecutionSession | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._project_revision = 0
        self._report_revision = 0
        self._compare_revision = 0
        self._closing = False
        self._closed = False

    @property
    def project_resources(self) -> tuple[ProjectResourceSummary, ...]:
        """Return safe resource summaries for Compare field hints."""
        if self.project is None or self.project.snapshot is None:
            return ()
        return self.project.snapshot.resources

    @property
    def can_execute(self) -> bool:
        """Return whether an exact admitted comparison handle can be consumed."""
        return self.compare_handle is not None and self.compare_plan is not None

    def begin(self) -> None:
        """Start independent initial project and report loads."""
        self.spawn(self.reload_project())
        self.spawn(self.reload_reports())

    def spawn(self, operation: Coroutine[Any, Any, None]) -> None:
        """Own one background operation and consume its safe terminal state."""
        if self._closing:
            operation.close()
            return
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def update_draft(self, draft: CompareDraft) -> None:
        """Retain a meaningful preparation change and invalidate an old preview."""
        if type(draft) is not CompareDraft:
            raise TypeError("TUI coordinator comparison draft must be typed")
        if self.session is not None or self.compare_surface is not CompareSurface.PREPARE:
            return
        equivalent = same_compare_intent(self.compare_draft, draft)
        self.compare_draft = draft
        if equivalent:
            return
        self._compare_revision += 1
        invalidates = (
            self.compare_handle is not None
            or self.compare_plan is not None
            or bool(self.compare_diagnostics)
        )
        if not invalidates:
            return
        handle = self.compare_handle
        self.compare_handle = None
        self._reset_preparation()
        self.spawn(self._finish_invalidation(handle))

    async def reload_project(self) -> None:
        """Load one safe project snapshot and reject stale completion."""
        self._project_revision += 1
        revision = self._project_revision
        self.project = None
        await self._changed(WorkspaceId.EXPLORER)
        try:
            result = await self._backend.load_project()
        except Exception as error:  # noqa: BLE001 - sever backend exception provenance
            result = ProjectWorkspaceResult(
                diagnostics=(failure_diagnostic(error, _GENERIC_PROJECT_FAILURE, "configuration"),)
            )
        if self._closing or revision != self._project_revision:
            return
        self.project = result
        await self._changed(WorkspaceId.EXPLORER)
        if self.session is None and self.compare_surface is CompareSurface.PREPARE:
            await self._changed(WorkspaceId.COMPARE)

    async def reload_reports(self) -> None:
        """Load the authenticated archive index and reject stale completion."""
        self._report_revision += 1
        revision = self._report_revision
        self.report_listing = None
        self.report_result = None
        self.report_surface = ReportSurface.BROWSE
        self.report_error_code = None
        await self._changed(WorkspaceId.REPORT)
        try:
            listing = await self._backend.list_reports()
        except Exception as error:  # noqa: BLE001 - sever backend exception provenance
            if self._closing or revision != self._report_revision:
                return
            self.report_error_code = failure_code(error, _GENERIC_REPORT_LIST_FAILURE)
            self.report_surface = ReportSurface.RESULT
            await self._changed(WorkspaceId.REPORT)
            return
        if self._closing or revision != self._report_revision:
            return
        self.report_listing = listing
        await self._changed(WorkspaceId.REPORT)

    async def open_report(self, bundle_id: str) -> bool:
        """Open one stored result and report whether the current request completed."""
        self._report_revision += 1
        revision = self._report_revision
        try:
            projection = await self._backend.open_report(bundle_id)
        except Exception as error:  # noqa: BLE001 - sever backend exception provenance
            if self._closing or revision != self._report_revision:
                return False
            self.report_result = None
            self.report_surface = ReportSurface.RESULT
            self.report_error_code = failure_code(error, _GENERIC_REPORT_OPEN_FAILURE)
        else:
            if self._closing or revision != self._report_revision:
                return False
            self.report_result = projection
            self.report_surface = ReportSurface.RESULT
            self.report_error_code = None
        await self._changed(WorkspaceId.REPORT)
        return True

    def show_report_archive(self) -> None:
        """Return the Report workspace to its retained archive listing."""
        self.report_surface = ReportSurface.BROWSE
        self.report_result = None
        self.report_error_code = None

    def show_compare_prepare(self) -> None:
        """Return terminal Compare results to the retained preparation draft."""
        if self.compare_surface is not CompareSurface.RESULTS:
            return
        self.compare_surface = CompareSurface.PREPARE
        self.compare_handle = None
        self.compare_plan = None
        self.compare_progress = None
        self.compare_result = None
        self.compare_diagnostics = ()

    async def prepare_compare(self, draft: CompareDraft | None = None) -> None:
        """Prepare one topology and retain only its opaque handle and safe inspection."""
        if draft is not None:
            if type(draft) is not CompareDraft:
                raise TypeError("TUI coordinator comparison draft must be typed")
            self.compare_draft = draft
        if self.session is not None:
            return
        self._compare_revision += 1
        revision = self._compare_revision
        await self._discard_prepared()
        self._reset_preparation()
        await self._changed(WorkspaceId.COMPARE)
        try:
            prepared = await self._backend.prepare_compare(self.compare_draft)
        except Exception as error:  # noqa: BLE001 - sever backend exception provenance
            if self._closing or revision != self._compare_revision:
                return
            self.compare_diagnostics = (
                failure_diagnostic(error, _GENERIC_COMPARE_PREPARATION_FAILURE, "invocation"),
            )
            await self._changed(WorkspaceId.COMPARE)
            return
        if self._closing or revision != self._compare_revision:
            if prepared.handle is not None:
                await self._discard_handle(prepared.handle)
            return
        if prepared.handle is None or prepared.inspection is None:
            self.compare_diagnostics = prepared.diagnostics
        else:
            self.compare_handle = prepared.handle
            self.compare_plan = prepared.inspection
        await self._changed(WorkspaceId.COMPARE)

    async def start_compare(self) -> None:
        """Consume the exact admitted handle and supervise one execution session."""
        handle = self.compare_handle
        plan = self.compare_plan
        if handle is None or plan is None or self.session is not None:
            return
        self.compare_handle = None
        self.compare_surface = CompareSurface.RUNNING
        self.compare_progress = None
        self.compare_diagnostics = ()
        await self._changed(WorkspaceId.COMPARE)
        try:
            session = await self._backend.start_compare(handle, self._controls)
        except Exception as error:  # noqa: BLE001 - sever backend exception provenance
            self.compare_surface = CompareSurface.PREPARE
            self.compare_diagnostics = (
                failure_diagnostic(error, _GENERIC_COMPARE_EXECUTION_FAILURE, "execution"),
            )
            await self._changed(WorkspaceId.COMPARE)
            return
        self.session = session
        self.spawn(self._supervise_execution(session, plan))

    def cancel_compare(self) -> bool:
        """Request cancellation without allowing session failures to escape."""
        session = self.session
        if session is None:
            return False
        try:
            return session.cancel()
        except Exception:  # noqa: BLE001 - live session detail is not renderable
            return False

    async def close(self) -> None:
        """Idempotently close the session, pending operations, and backend."""
        if self._closed:
            return
        self._closing = True
        self._project_revision += 1
        self._report_revision += 1
        self._compare_revision += 1
        if self.session is not None:
            with suppress(Exception):
                await self.session.close()
        current = asyncio.current_task()
        pending = tuple(task for task in self._tasks if task is not current and not task.done())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        with suppress(Exception):
            await self._backend.close()
        self.session = None
        self._closed = True

    async def _supervise_execution(
        self,
        session: ExecutionSession,
        plan: PlanInspection,
    ) -> None:
        try:
            async for snapshot in session.updates():
                if self._closing or self.session is not session:
                    return
                self.compare_progress = snapshot
                await self._changed(WorkspaceId.COMPARE)
            result = await session.result()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - sever session exception provenance
            if not self._closing and self.session is session:
                with suppress(Exception):
                    await session.close()
                if self._closing or self.session is not session:
                    return
                self._show_failure(
                    (failure_diagnostic(error, _GENERIC_COMPARE_EXECUTION_FAILURE, "execution"),)
                )
                await self._changed(WorkspaceId.COMPARE)
            return
        if self._closing or self.session is not session:
            return
        await self._show_terminal_result(result, plan)

    async def _show_terminal_result(
        self,
        result: ProductionExecutionResult,
        plan: PlanInspection,
    ) -> None:
        self.session = None
        if result.artifact is None:
            self._show_failure(result.diagnostics)
            await self._changed(WorkspaceId.COMPARE)
            return
        try:
            projection = project_results(result.artifact)
        except Exception as error:  # noqa: BLE001 - projection failures remain value-free
            self._show_failure(
                (failure_diagnostic(error, _GENERIC_RESULT_PROJECTION_FAILURE, "reporting"),)
            )
            await self._changed(WorkspaceId.COMPARE)
            return
        self.compare_plan = plan
        self.compare_progress = None
        self.compare_result = projection
        self.compare_diagnostics = ()
        self.compare_surface = CompareSurface.RESULTS
        await self._changed(WorkspaceId.COMPARE)

    async def _finish_invalidation(
        self,
        handle: ComparePreparationHandle | None,
    ) -> None:
        if handle is not None:
            await self._discard_handle(handle)
        await self._changed(WorkspaceId.COMPARE)

    def _show_failure(self, diagnostics: tuple[ApplicationDiagnostic, ...]) -> None:
        self.session = None
        self.compare_progress = None
        self.compare_result = None
        self.compare_diagnostics = diagnostics or (
            diagnostic(_GENERIC_COMPARE_EXECUTION_FAILURE, "execution"),
        )
        self.compare_surface = CompareSurface.PREPARE

    async def _discard_prepared(self) -> None:
        handle = self.compare_handle
        self.compare_handle = None
        if handle is not None:
            await self._discard_handle(handle)

    async def _discard_handle(self, handle: ComparePreparationHandle) -> None:
        with suppress(Exception):
            await self._backend.discard_compare(handle)

    def _reset_preparation(self) -> None:
        self.compare_surface = CompareSurface.PREPARE
        self.compare_plan = None
        self.compare_progress = None
        self.compare_result = None
        self.compare_diagnostics = ()

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            with suppress(Exception):
                task.exception()


def failure_code(error: Exception, generic: str) -> str:
    """Collapse an exception to a safe closed backend code or generic code."""
    if isinstance(error, TuiBackendError):
        return f"tui.backend.{error.code.value}"
    return generic


def failure_diagnostic(error: Exception, generic: str, stage: str) -> ApplicationDiagnostic:
    """Create one value-free diagnostic without retaining exception text."""
    return diagnostic(failure_code(error, generic), stage)


def diagnostic(code: str, stage: str) -> ApplicationDiagnostic:
    """Create one closed error diagnostic."""
    return ApplicationDiagnostic(code=code, severity="error", stage=stage)


def same_compare_intent(left: CompareDraft, right: CompareDraft) -> bool:
    """Compare only fields consumed by the selected topology."""
    if left.mode is not right.mode:
        return False
    if left.mode is CompareMode.ASSERTIONS:
        return (left.subject, left.environment) == (right.subject, right.environment)
    if left.mode is CompareMode.DIFFERENCES:
        return (left.subject, left.baseline, left.candidate) == (
            right.subject,
            right.baseline,
            right.candidate,
        )
    return left.execution_profile == right.execution_profile


__all__ = (
    "CompareSurface",
    "ReportSurface",
    "TuiCoordinator",
    "diagnostic",
    "failure_code",
    "failure_diagnostic",
    "same_compare_intent",
)
