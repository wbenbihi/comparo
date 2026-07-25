"""Concrete safe application-facade adapter for the local Textual frontend."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from pathlib import Path
from typing import final
from uuid import uuid4

from comparo.application import ArtifactReadLimits
from comparo.application import CompareExecutionControls
from comparo.application import CompareIntent
from comparo.application import ComparePreparationHandle
from comparo.application import ComparePreparationResult
from comparo.application import ComparePrepareRequest
from comparo.application import CompareService
from comparo.application import CompareServiceError
from comparo.application import CompareServiceErrorCode
from comparo.application import ExecutionSession
from comparo.application import PlanningOptions
from comparo.application import ProjectPlanRequest
from comparo.application import ProjectWorkspaceRequest
from comparo.application import ProjectWorkspaceResult
from comparo.application import ReportArchiveError
from comparo.application import ReportArchiveErrorCode
from comparo.application import ReportArchiveLimits
from comparo.application import ReportArchiveListing
from comparo.application import ResultProjection
from comparo.application import inspect_project_workspace
from comparo.application import list_report_archive
from comparo.application import persisted_production_execution_defaults_v1
from comparo.application import project_results
from comparo.application import verify_artifact
from comparo.tui.contracts.models import CompareDraft
from comparo.tui.contracts.models import CompareMode
from comparo.tui.contracts.models import TuiExecutionControls
from comparo.tui.contracts.models import TuiLaunchRequest
from comparo.tui.contracts.services import TuiBackendError
from comparo.tui.contracts.services import TuiBackendErrorCode

DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS = 8
DEFAULT_TUI_ARTIFACT_READ_LIMITS = ArtifactReadLimits(
    maximum_blobs=4096,
    maximum_blob_bytes=64 * 1024 * 1024,
    maximum_total_blob_bytes=512 * 1024 * 1024,
    maximum_manifest_bytes=64 * 1024 * 1024,
    maximum_report_bytes=64 * 1024 * 1024,
    maximum_redaction_patterns=4096,
    maximum_redaction_pattern_bytes=1024 * 1024,
    maximum_redaction_value_bytes=256 * 1024,
    maximum_redaction_value_nodes=4096,
)
DEFAULT_TUI_REPORT_ARCHIVE_LIMITS = ReportArchiveLimits(
    maximum_entries=4096,
    artifact=DEFAULT_TUI_ARTIFACT_READ_LIMITS,
)


def default_tui_execution_controls() -> TuiExecutionControls:
    """Return safe interactive defaults whose sanitized result is retained for Report."""
    try:
        tool_version = version("comparo")
    except PackageNotFoundError:
        tool_version = "unknown"
    return TuiExecutionControls(
        settings=persisted_production_execution_defaults_v1(),
        approve_mutations=False,
        tool_version=tool_version,
    )


@final
class ProductionTuiBackend:
    """Own one launch configuration and stateful Compare preparation service."""

    _artifact_root: Path
    _closed: bool
    _compare: CompareService
    _compare_lock: asyncio.Lock
    _launch: TuiLaunchRequest
    _report_limits: ReportArchiveLimits

    __slots__ = (
        "_artifact_root",
        "_closed",
        "_compare",
        "_compare_lock",
        "_launch",
        "_report_limits",
    )

    def __init__(
        self,
        launch: TuiLaunchRequest,
        *,
        report_limits: ReportArchiveLimits = DEFAULT_TUI_REPORT_ARCHIVE_LIMITS,
        maximum_outstanding_preparations: int = (DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS),
    ) -> None:
        """Create a backend with explicit bounded report and preparation limits."""
        if type(launch) is not TuiLaunchRequest or type(report_limits) is not ReportArchiveLimits:
            raise TuiBackendError(TuiBackendErrorCode.INVALID_REQUEST)
        compare: CompareService | None = None
        artifact_root: Path | None = None
        failure: TuiBackendErrorCode | None = None
        try:
            compare = CompareService(maximum_outstanding=maximum_outstanding_preparations)
            artifact_root = launch.artifact_root.resolve()
        except Exception:  # noqa: BLE001 - constructor errors may contain local paths
            failure = TuiBackendErrorCode.INVALID_REQUEST
        if failure is not None or compare is None or artifact_root is None:
            raise TuiBackendError(TuiBackendErrorCode.INVALID_REQUEST)
        self._launch = launch
        self._artifact_root = artifact_root
        self._report_limits = report_limits
        self._compare = compare
        self._compare_lock = asyncio.Lock()
        self._closed = False

    async def load_project(self) -> ProjectWorkspaceResult:
        """Inspect the launch-owned project through the safe Explorer facade."""
        self._require_open()
        request = ProjectWorkspaceRequest(
            config=self._launch.config,
            additional_sources=self._launch.additional_sources,
        )
        return await _safe_thread_call(
            partial(inspect_project_workspace, request),
            _fixed_failure(TuiBackendErrorCode.PROJECT_LOAD_FAILED),
        )

    async def prepare_compare(self, draft: CompareDraft) -> ComparePreparationResult:
        """Translate one draft and retain its admitted plan behind an opaque handle."""
        if type(draft) is not CompareDraft:
            raise TuiBackendError(TuiBackendErrorCode.INVALID_REQUEST)
        async with self._compare_lock:
            self._require_open()
            return await _safe_thread_call(
                partial(self._prepare_compare, draft),
                _fixed_failure(TuiBackendErrorCode.COMPARE_PREPARE_FAILED),
            )

    def _prepare_compare(self, draft: CompareDraft) -> ComparePreparationResult:
        """Build fresh occurrence identities and invoke the blocking preparation facade."""
        options: PlanningOptions = draft.planning_options()
        request = ComparePrepareRequest(
            intent=_compare_intent(draft.mode),
            project=ProjectPlanRequest(
                config=self._launch.config,
                additional_sources=self._launch.additional_sources,
                policy=self._launch.policy,
                options=options,
                invocation_id=uuid4(),
                run_id=uuid4(),
            ),
        )
        return self._compare.prepare(request)

    async def start_compare(
        self,
        handle: ComparePreparationHandle,
        controls: TuiExecutionControls,
    ) -> ExecutionSession:
        """Consume one retained plan and start it on the caller's event loop."""
        if (
            type(handle) is not ComparePreparationHandle
            or type(controls) is not TuiExecutionControls
        ):
            raise TuiBackendError(TuiBackendErrorCode.INVALID_REQUEST)
        async with self._compare_lock:
            self._require_open()
            return _safe_call(
                partial(
                    self._compare.start,
                    handle,
                    CompareExecutionControls(
                        settings=controls.settings,
                        artifact_root=self._artifact_root,
                        approve_mutations=controls.approve_mutations,
                        tool_version=controls.tool_version,
                    ),
                ),
                _compare_start_failure,
            )

    async def discard_compare(self, handle: ComparePreparationHandle) -> bool:
        """Discard one retained preparation without exposing its opaque value in errors."""
        if type(handle) is not ComparePreparationHandle:
            raise TuiBackendError(TuiBackendErrorCode.INVALID_REQUEST)
        async with self._compare_lock:
            self._require_open()
            return _safe_call(
                partial(self._compare.discard, handle),
                _fixed_failure(TuiBackendErrorCode.COMPARE_DISCARD_FAILED),
            )

    async def list_reports(self) -> ReportArchiveListing:
        """List authenticated report summaries under explicit archive read ceilings."""
        self._require_open()
        return await _safe_thread_call(
            partial(
                list_report_archive,
                self._artifact_root,
                self._report_limits,
            ),
            _report_list_failure,
        )

    async def open_report(self, bundle_id: str) -> ResultProjection:
        """Verify one complete artifact and project its body-free stored results."""
        if type(bundle_id) is not str or not bundle_id or len(bundle_id) > 63:
            raise TuiBackendError(TuiBackendErrorCode.INVALID_REQUEST)
        self._require_open()
        return await _safe_thread_call(
            partial(self._open_report, bundle_id),
            _fixed_failure(TuiBackendErrorCode.REPORT_OPEN_FAILED),
        )

    def _open_report(self, bundle_id: str) -> ResultProjection:
        artifact = verify_artifact(
            self._artifact_root,
            bundle_id,
            self._report_limits.artifact,
        )
        return project_results(artifact)

    async def close(self) -> None:
        """Idempotently close the retained-plan service and reject later operations."""
        async with self._compare_lock:
            if self._closed:
                return
            failure: TuiBackendErrorCode | None = None
            try:
                self._compare.close()
            except Exception:  # noqa: BLE001 - never expose backend implementation detail
                failure = TuiBackendErrorCode.CLOSE_FAILED
            self._closed = True
        if failure is not None:
            raise TuiBackendError(failure)

    def _require_open(self) -> None:
        if self._closed:
            raise TuiBackendError(TuiBackendErrorCode.CLOSED)


def _compare_intent(mode: CompareMode) -> CompareIntent:
    """Translate only UI vocabulary; topology semantics remain in the application facade."""
    if mode is CompareMode.ASSERTIONS:
        return CompareIntent.ASSERTION
    if mode is CompareMode.DIFFERENCES:
        return CompareIntent.COMPARISON
    return CompareIntent.EXECUTION_PROFILE


def _fixed_failure(
    code: TuiBackendErrorCode,
) -> Callable[[Exception], TuiBackendErrorCode]:
    """Return an exception-text-independent failure mapper."""

    def map_failure(_error: Exception) -> TuiBackendErrorCode:
        return code

    return map_failure


def _compare_start_failure(error: Exception) -> TuiBackendErrorCode:
    if isinstance(error, CompareServiceError):
        if error.code is CompareServiceErrorCode.CLOSED:
            return TuiBackendErrorCode.CLOSED
        if error.code is CompareServiceErrorCode.HANDLE_UNKNOWN:
            return TuiBackendErrorCode.COMPARE_HANDLE_UNKNOWN
    return TuiBackendErrorCode.COMPARE_START_FAILED


def _report_list_failure(error: Exception) -> TuiBackendErrorCode:
    if isinstance(error, ReportArchiveError):
        if error.code is ReportArchiveErrorCode.BUDGET_EXCEEDED:
            return TuiBackendErrorCode.REPORT_LIST_LIMIT_EXCEEDED
        return TuiBackendErrorCode.REPORT_LIST_UNAVAILABLE
    return TuiBackendErrorCode.REPORT_LIST_UNAVAILABLE


def _safe_call[ResultT](
    operation: Callable[[], ResultT],
    failure_code: Callable[[Exception], TuiBackendErrorCode],
) -> ResultT:
    """Run one synchronous facade call while severing unsafe failure provenance."""
    try:
        result = operation()
    except Exception as error:  # noqa: BLE001 - application exceptions are boundary-contained
        failure = failure_code(error)
    else:
        return result
    raise TuiBackendError(failure)


async def _safe_thread_call[ResultT](
    operation: Callable[[], ResultT],
    failure_code: Callable[[Exception], TuiBackendErrorCode],
) -> ResultT:
    """Run blocking facade work without racing cancellation or leaking its exceptions."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise
    except Exception as error:  # noqa: BLE001 - application exceptions are boundary-contained
        failure = failure_code(error)
    else:
        return result
    raise TuiBackendError(failure)


__all__ = (
    "DEFAULT_TUI_ARTIFACT_READ_LIMITS",
    "DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS",
    "DEFAULT_TUI_REPORT_ARCHIVE_LIMITS",
    "ProductionTuiBackend",
    "default_tui_execution_controls",
)
