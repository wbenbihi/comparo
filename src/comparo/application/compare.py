"""Bounded prepare/start facade for every interactive Compare topology."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from sys import float_info
from typing import Any
from typing import cast
from typing import final
from uuid import uuid4

import msgspec

from comparo.application.environment_binding import CapturedAmbientEnvironment
from comparo.application.environment_binding import capture_process_environment
from comparo.application.evaluation_binding import preflight_configuration_evaluation
from comparo.application.execution_session import ExecutionSession
from comparo.application.execution_session import start_execution_session
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import inspect_diagnostic
from comparo.application.inspection import inspect_plan
from comparo.application.plan_occurrences import PlanOccurrenceWindow
from comparo.application.plan_occurrences import inspect_plan_occurrence_window
from comparo.application.plan_review import PlanReviewInspection
from comparo.application.plan_review import inspect_execution_settings
from comparo.application.plan_review import inspect_runtime_limits
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionSettings
from comparo.application.production_execution import ProductionRunAnnotations
from comparo.application.profile_binding import bind_execution_profiles
from comparo.application.render_preview import RenderPreviewRequest
from comparo.application.render_preview import RenderPreviewSettings
from comparo.application.render_preview import RenderPreviewWindowRequest
from comparo.application.render_preview import RenderPreviewWindowResult
from comparo.application.render_preview import render_prepared_request_window
from comparo.application.resolved_inspection import ResolvedValueInspectionResult
from comparo.application.resolved_inspection import ResolvedValueInspectionTarget
from comparo.application.resolved_inspection import _bind_resolved_value_target
from comparo.application.resolved_inspection import (
    _inspect_resolved_value as _inspect_prepared_resolved_value,
)
from comparo.application.runtime_capabilities import preflight_runtime_capabilities
from comparo.core.domain.primitives import Duration

_HANDLE_ALLOCATION_ATTEMPTS = 8


class CompareIntent(StrEnum):
    """The three user intentions unified by the Compare workspace."""

    ASSERTION = "assertion"
    COMPARISON = "comparison"
    EXECUTION_PROFILE = "execution-profile"


class CompareServiceErrorCode(StrEnum):
    """Closed value-free failures from stateful Compare operations."""

    CLOSED = "closed"
    HANDLE_UNKNOWN = "handle-unknown"
    PREPARATION_MISMATCH = "preparation-mismatch"
    PREPARATION_BLOCKED = "preparation-blocked"
    PREPARATION_BUSY = "preparation-busy"
    INSPECTION_CANCELLED = "inspection-cancelled"


class CompareServiceError(RuntimeError):
    """One safe stateful-service failure that never retains a supplied handle."""

    __slots__ = ("code",)

    def __init__(self, code: CompareServiceErrorCode) -> None:
        """Retain only one closed failure category."""
        if type(code) is not CompareServiceErrorCode:
            raise TypeError("Compare service error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class ComparePreparationHandle(msgspec.Struct, frozen=True, kw_only=True):
    """Opaque one-shot capability identifying an internally retained admitted plan."""

    value: str

    def __post_init__(self) -> None:
        """Reject empty, oversized, or non-text handle representations."""
        if type(self.value) is not str or not self.value or len(self.value) > 64:
            raise ValueError("Compare preparation handle must be bounded non-empty text")


@final
class CompareExecutionControls(msgspec.Struct, frozen=True, kw_only=True):
    """Frontend-owned effect and persistence choices bound to one preparation."""

    settings: ProductionExecutionSettings
    artifact_root: Path | None
    approve_mutations: bool
    tool_version: str
    annotations: ProductionRunAnnotations | None = None

    def __post_init__(self) -> None:
        """Validate effectful choices before preparing or consuming a handle."""
        if type(self.settings) is not ProductionExecutionSettings:
            raise TypeError("Compare execution settings must be typed")
        if self.artifact_root is not None and not isinstance(self.artifact_root, Path):
            raise TypeError("Compare artifact root must be a Path or absent")
        if self.artifact_root is not None and not self.artifact_root.is_absolute():
            raise ValueError("Compare artifact root must be resolved and absolute")
        if type(self.approve_mutations) is not bool:
            raise TypeError("Compare mutation approval must be bool")
        if (
            type(self.tool_version) is not str
            or not self.tool_version
            or len(self.tool_version) > 64
        ):
            raise ValueError("Compare tool version must be bounded non-empty text")
        if self.annotations is not None and type(self.annotations) is not ProductionRunAnnotations:
            raise TypeError("Compare run annotations must be typed or absent")


@final
class ComparePrepareRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One typed Compare intention, project request, and exact start controls."""

    intent: CompareIntent
    project: ProjectPlanRequest
    controls: CompareExecutionControls

    def __post_init__(self) -> None:
        """Require a closed intention and one exact shared planning request."""
        if type(self.intent) is not CompareIntent:
            raise TypeError("Compare preparation intent must be typed")
        if type(self.project) is not ProjectPlanRequest:
            raise TypeError("Compare preparation project request must be typed")
        if type(self.controls) is not CompareExecutionControls:
            raise TypeError("Compare preparation controls must be typed")


@final
class ComparePreparationResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one safe handle/review pair or value-free diagnostics."""

    handle: ComparePreparationHandle | None = None
    review: PlanReviewInspection | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep retained-plan success and diagnostic rejection structurally disjoint."""
        if self.handle is not None and type(self.handle) is not ComparePreparationHandle:
            raise TypeError("Compare preparation result handle must be typed")
        if self.review is not None and type(self.review) is not PlanReviewInspection:
            raise TypeError("Compare preparation result review must be typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("Compare preparation diagnostics must be immutable and typed")
        successful = self.handle is not None and self.review is not None
        if successful and not self.diagnostics:
            return
        if self.handle is None and self.review is None and self.diagnostics:
            return
        raise ValueError("Compare preparation requires exactly success or diagnostics")


@final
class CompareCloseResult(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free result of waiting for service-owned inspection cleanup."""

    drained: bool
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful drain and value-free timeout diagnostics disjoint."""
        if type(self.drained) is not bool:
            raise TypeError("Compare close drained state must be bool")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("Compare close diagnostics must be immutable and typed")
        if self.drained == bool(self.diagnostics):
            raise ValueError("Compare close requires either drained state or diagnostics")


@final
class CompareService:
    """Retain a bounded set of admitted plans until start, discard, or service closure."""

    __slots__ = (
        "_closed",
        "_inspections",
        "_maximum_inspections_per_preparation",
        "_maximum_outstanding",
        "_prepared",
        "_revoked",
    )

    def __init__(
        self,
        *,
        maximum_outstanding: int,
        maximum_inspections_per_preparation: int = 1,
    ) -> None:
        """Create an open service with explicit retained-plan and inspection ceilings."""
        if type(maximum_outstanding) is not int or maximum_outstanding < 1:
            raise ValueError("Compare outstanding preparation limit must be a positive integer")
        if (
            type(maximum_inspections_per_preparation) is not int
            or maximum_inspections_per_preparation < 1
        ):
            raise ValueError("Compare per-preparation inspection limit must be a positive integer")
        self._maximum_outstanding = maximum_outstanding
        self._maximum_inspections_per_preparation = maximum_inspections_per_preparation
        self._prepared: dict[ComparePreparationHandle, _PreparedComparison] = {}
        self._inspections: dict[
            ComparePreparationHandle,
            set[_InspectionLease],
        ] = {}
        self._revoked: set[ComparePreparationHandle] = set()
        self._closed = False

    @property
    def outstanding(self) -> int:
        """Return the number of prepared plans retaining application state."""
        return len(self._prepared)

    @property
    def closed(self) -> bool:
        """Return whether this service has discarded its retained plans permanently."""
        return self._closed

    def prepare(self, request: ComparePrepareRequest) -> ComparePreparationResult:
        """Prepare and retain one exact plan while returning only a safe opaque preview."""
        if type(request) is not ComparePrepareRequest:
            raise TypeError("Compare preparation requires ComparePrepareRequest")
        if self._closed:
            return _rejected("compare.service.closed")
        if len(self._prepared) >= self._maximum_outstanding:
            return _rejected("compare.preparation.limit-exceeded")
        intent = _intent_request_diagnostic(request)
        if intent is not None:
            return ComparePreparationResult(diagnostics=(intent,))
        try:
            outcome = prepare_project_plan(request.project)
        except Exception:  # noqa: BLE001 - never expose source-bearing failure details
            return _rejected("compare.preparation.internal")
        if outcome.project is None:
            return ComparePreparationResult(diagnostics=outcome.diagnostics)
        admitted = _admit_review(request, outcome.project)
        if isinstance(admitted, tuple):
            return ComparePreparationResult(diagnostics=admitted)
        handle = self._allocate_handle()
        if handle is None:
            return _rejected("compare.preparation.handle-unavailable")
        try:
            ambient_environment = capture_process_environment(
                outcome.project,
                request.controls.settings.environment,
            )
        except Exception:  # noqa: BLE001 - ambient values never cross the safe boundary
            return _rejected("compare.preparation.environment-capture-failed")
        self._prepared[handle] = _PreparedComparison(
            project=outcome.project,
            controls=request.controls,
            review=admitted,
            ambient_environment=ambient_environment,
        )
        return ComparePreparationResult(
            handle=handle,
            review=admitted,
        )

    def start(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
    ) -> ExecutionSession:
        """Consume one exact prepared plan and start its interactive execution session."""
        if type(handle) is not ComparePreparationHandle:
            raise TypeError("Compare start handle must be typed")
        if type(controls) is not CompareExecutionControls:
            raise TypeError("Compare start controls must be typed")
        prepared = self._validate_start(handle, controls)
        del self._prepared[handle]
        return start_execution_session(
            ProductionExecutionRequest(
                project=prepared.project,
                settings=prepared.controls.settings,
                ambient_environment=prepared.ambient_environment,
                artifact_root=prepared.controls.artifact_root,
                approve_mutations=prepared.controls.approve_mutations,
                tool_version=prepared.controls.tool_version,
                annotations=prepared.controls.annotations,
            )
        )

    async def preview_requests(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
        *,
        start_ordinal: int,
        maximum_previews: int,
    ) -> RenderPreviewWindowResult:
        """Render one safe page from the exact retained plan without consuming its handle."""
        if type(handle) is not ComparePreparationHandle:
            raise TypeError("Compare preview handle must be typed")
        if type(controls) is not CompareExecutionControls:
            raise TypeError("Compare preview controls must be typed")
        prepared = self._validate_inspection(handle, controls)
        settings = prepared.controls.settings
        operation = render_prepared_request_window(
            RenderPreviewWindowRequest(
                preview=RenderPreviewRequest(
                    project=prepared.project,
                    settings=RenderPreviewSettings(
                        files=settings.files,
                        environment=settings.environment,
                        yaml=settings.yaml,
                        sanitization=settings.artifact.limits.sanitization,
                        body_store=settings.body_store,
                        request_body=settings.request_body,
                    ),
                    ambient_environment=prepared.ambient_environment,
                ),
                start_ordinal=start_ordinal,
                maximum_previews=maximum_previews,
            )
        )
        lease = self._begin_inspection(handle, operation)
        return cast(
            "RenderPreviewWindowResult",
            await self._run_inspection(handle, lease),
        )

    async def inspect_resolved_value(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
        target: ResolvedValueInspectionTarget,
    ) -> ResolvedValueInspectionResult:
        """Inspect one masked value from the retained plan without consuming its handle."""
        if type(handle) is not ComparePreparationHandle:
            raise TypeError("Compare resolved inspection handle must be typed")
        if type(controls) is not CompareExecutionControls:
            raise TypeError("Compare resolved inspection controls must be typed")
        if type(target) is not ResolvedValueInspectionTarget:
            raise TypeError("Compare resolved inspection target must be typed")
        prepared = self._validate_inspection(handle, controls)
        settings = prepared.controls.settings
        operation = _inspect_prepared_resolved_value(
            _bind_resolved_value_target(
                prepared.project,
                target,
                files=settings.files,
                environment=settings.environment,
                yaml=settings.yaml,
                ambient_environment=prepared.ambient_environment,
            )
        )
        lease = self._begin_inspection(handle, operation)
        return cast(
            "ResolvedValueInspectionResult",
            await self._run_inspection(handle, lease),
        )

    def list_occurrences(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
        *,
        start_ordinal: int,
        maximum_occurrences: int,
    ) -> PlanOccurrenceWindow:
        """List a value-free canonical occurrence page without consuming the preparation."""
        if type(handle) is not ComparePreparationHandle:
            raise TypeError("Compare occurrence handle must be typed")
        if type(controls) is not CompareExecutionControls:
            raise TypeError("Compare occurrence controls must be typed")
        prepared = self._validate_inspection(handle, controls)
        return inspect_plan_occurrence_window(
            prepared.project,
            start_ordinal=start_ordinal,
            maximum_occurrences=maximum_occurrences,
        )

    def validate_start(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
    ) -> None:
        """Validate exact start authority without consuming the preparation or causing effects."""
        if type(handle) is not ComparePreparationHandle:
            raise TypeError("Compare start handle must be typed")
        if type(controls) is not CompareExecutionControls:
            raise TypeError("Compare start controls must be typed")
        self._validate_start(handle, controls)

    def discard(self, handle: ComparePreparationHandle) -> bool:
        """Release one unused prepared plan and report whether it was retained."""
        if type(handle) is not ComparePreparationHandle:
            raise TypeError("Compare discard handle must be typed")
        if handle in self._revoked or handle not in self._prepared:
            return False
        if self._inspections.get(handle):
            self._revoked.add(handle)
            self._cancel_inspections(handle)
        else:
            del self._prepared[handle]
        return True

    def close(self) -> None:
        """Reject new work and request cancellation while active plans drain cleanup."""
        self._closed = True
        for handle in tuple(self._prepared):
            if self._inspections.get(handle):
                self._revoked.add(handle)
                self._cancel_inspections(handle)
            else:
                del self._prepared[handle]

    async def aclose(self, *, timeout: Duration) -> CompareCloseResult:
        """Request closure and wait up to one caller-owned duration for cleanup to drain."""
        if type(timeout) is not Duration:
            raise TypeError("Compare asynchronous close timeout must be Duration")
        self.close()
        leases = tuple(
            (handle, lease) for handle, active in self._inspections.items() for lease in active
        )
        if not leases:
            return CompareCloseResult(drained=True)
        _done, pending = await asyncio.wait(
            tuple(lease.task for _handle, lease in leases),
            timeout=_finite_timeout_seconds(timeout),
        )
        for handle, lease in leases:
            if lease.task.done():
                self._end_inspection(handle, lease)
        if pending:
            return CompareCloseResult(
                drained=False,
                diagnostics=(_diagnostic("compare.service.close-timeout"),),
            )
        return CompareCloseResult(drained=True)

    def _allocate_handle(self) -> ComparePreparationHandle | None:
        for _attempt in range(_HANDLE_ALLOCATION_ATTEMPTS):
            handle = ComparePreparationHandle(value=str(uuid4()))
            if handle not in self._prepared:
                return handle
        return None

    def _validate_start(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
    ) -> _PreparedComparison:
        prepared = self._validate_retained(handle, controls)
        if self._inspections.get(handle):
            raise CompareServiceError(CompareServiceErrorCode.PREPARATION_BUSY)
        if not prepared.review.execution_allowed:
            raise CompareServiceError(CompareServiceErrorCode.PREPARATION_BLOCKED)
        return prepared

    def _validate_inspection(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
    ) -> _PreparedComparison:
        prepared = self._validate_retained(handle, controls)
        tasks = self._inspections.get(handle)
        if tasks is not None and len(tasks) >= self._maximum_inspections_per_preparation:
            raise CompareServiceError(CompareServiceErrorCode.PREPARATION_BUSY)
        return prepared

    def _begin_inspection(
        self,
        handle: ComparePreparationHandle,
        operation: Coroutine[Any, Any, object],
    ) -> _InspectionLease:
        """Retain a service-owned child task, never the arbitrary calling task."""
        lease = _InspectionLease(asyncio.create_task(operation))
        tasks = self._inspections.get(handle)
        if tasks is None:
            tasks = self._inspections.setdefault(handle, set())
        tasks.add(lease)
        return lease

    def _end_inspection(
        self,
        handle: ComparePreparationHandle,
        lease: _InspectionLease,
    ) -> None:
        tasks = self._inspections.get(handle)
        if tasks is None:
            return
        tasks.discard(lease)
        if tasks:
            return
        del self._inspections[handle]
        if handle in self._revoked:
            self._revoked.remove(handle)
            self._prepared.pop(handle, None)

    async def _await_inspection(self, lease: _InspectionLease) -> object:
        """Keep service cancellation from cancelling an arbitrary caller orchestration task."""
        caller = asyncio.current_task()
        cancellation_count = 0 if caller is None else caller.cancelling()
        try:
            outcome = await asyncio.shield(lease.task)
        except asyncio.CancelledError:
            if caller is not None and caller.cancelling() > cancellation_count:
                lease.cancel()
                raise
            raise CompareServiceError(CompareServiceErrorCode.INSPECTION_CANCELLED) from None
        if lease.cancel_requested:
            raise CompareServiceError(CompareServiceErrorCode.INSPECTION_CANCELLED)
        return outcome

    async def _run_inspection(
        self,
        handle: ComparePreparationHandle,
        lease: _InspectionLease,
    ) -> object:
        deferred = False
        try:
            return await self._await_inspection(lease)
        except asyncio.CancelledError:
            self._retain_inspection_until_done(handle, lease)
            deferred = True
            raise
        finally:
            if not deferred:
                self._end_inspection(handle, lease)

    def _retain_inspection_until_done(
        self,
        handle: ComparePreparationHandle,
        lease: _InspectionLease,
    ) -> None:
        """Keep cancelled child authority alive until its terminal outcome is observed."""
        lease.task.add_done_callback(
            lambda task: self._finish_retained_inspection(handle, lease, task)
        )

    def _finish_retained_inspection(
        self,
        handle: ComparePreparationHandle,
        lease: _InspectionLease,
        task: asyncio.Task[object],
    ) -> None:
        """Observe a detached child exactly once before releasing its prepared plan lease."""
        with suppress(BaseException):
            task.result()
        self._end_inspection(handle, lease)

    def _cancel_inspections(self, handle: ComparePreparationHandle) -> None:
        for lease in self._inspections.get(handle, set()):
            lease.cancel()

    def _validate_retained(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
    ) -> _PreparedComparison:
        if self._closed:
            raise CompareServiceError(CompareServiceErrorCode.CLOSED)
        if handle in self._revoked:
            raise CompareServiceError(CompareServiceErrorCode.HANDLE_UNKNOWN)
        prepared = self._prepared.get(handle)
        if prepared is None:
            raise CompareServiceError(CompareServiceErrorCode.HANDLE_UNKNOWN)
        if controls != prepared.controls:
            raise CompareServiceError(CompareServiceErrorCode.PREPARATION_MISMATCH)
        return prepared


@final
class _PreparedComparison(msgspec.Struct, frozen=True, kw_only=True):
    """One retained admitted project bound to every inspected execution control."""

    project: PreparedProjectPlan
    controls: CompareExecutionControls
    review: PlanReviewInspection
    ambient_environment: CapturedAmbientEnvironment

    def __post_init__(self) -> None:
        if type(self.project) is not PreparedProjectPlan:
            raise TypeError("prepared comparison project must be typed")
        if type(self.controls) is not CompareExecutionControls:
            raise TypeError("prepared comparison controls must be typed")
        if type(self.review) is not PlanReviewInspection:
            raise TypeError("prepared comparison review must be typed")
        if type(self.ambient_environment) is not CapturedAmbientEnvironment:
            raise TypeError("prepared comparison ambient environment must be sealed")


@final
class _InspectionLease:
    """One identity-distinct service-owned child task with idempotent cancellation."""

    __slots__ = ("_cancel_requested", "task")

    def __init__(self, task: asyncio.Task[object]) -> None:
        self.task = task
        self._cancel_requested = False

    def cancel(self) -> bool:
        if self._cancel_requested or self.task.done():
            return False
        self._cancel_requested = True
        self.task.cancel()
        return True

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested


def _finite_timeout_seconds(timeout: Duration) -> float:
    """Convert an exact duration to a finite timeout accepted by asyncio."""
    try:
        return timeout.nanoseconds / 1_000_000_000
    except OverflowError:
        return float_info.max


def _intent_request_diagnostic(request: ComparePrepareRequest) -> ApplicationDiagnostic | None:
    selected_profile = request.project.options.execution_profile is not None
    if request.intent is CompareIntent.EXECUTION_PROFILE and not selected_profile:
        return _diagnostic("compare.intent.execution-profile-required")
    if request.intent is not CompareIntent.EXECUTION_PROFILE and selected_profile:
        return _diagnostic("compare.intent.ad-hoc-profile-forbidden")
    return None


def _admit_review(
    request: ComparePrepareRequest,
    project: PreparedProjectPlan,
) -> PlanReviewInspection | tuple[ApplicationDiagnostic, ...]:
    topology = _effective_topology_diagnostic(request.intent, project)
    if topology is not None:
        return (topology,)
    capabilities = preflight_runtime_capabilities(project)
    if not capabilities.supported:
        return tuple(inspect_diagnostic(item) for item in capabilities.diagnostics)
    profile_binding = bind_execution_profiles(
        project.prepared,
        artifact=request.controls.settings.artifact,
        response=request.controls.settings.transport.classification,
    )
    if profile_binding.profiles is None:
        return tuple(inspect_diagnostic(item) for item in profile_binding.diagnostics)
    effective_settings = msgspec.structs.replace(
        request.controls.settings,
        artifact=profile_binding.profiles.artifact,
    )
    evaluation = preflight_configuration_evaluation(
        project.prepared,
        policy=project.policy,
    )
    if not evaluation.supported:
        return tuple(inspect_diagnostic(item) for item in evaluation.diagnostics)
    return PlanReviewInspection(
        plan=inspect_plan(
            project.prepared,
            project.policy,
            evaluation,
            request.controls.artifact_root,
        ),
        runtime=inspect_runtime_limits(effective_settings),
        execution=inspect_execution_settings(
            effective_settings,
            request.controls.artifact_root,
        ),
        mutation_approved=request.controls.approve_mutations,
        tool_version=request.controls.tool_version,
    )


def _effective_topology_diagnostic(
    intent: CompareIntent,
    project: PreparedProjectPlan,
) -> ApplicationDiagnostic | None:
    compares = bool(project.prepared.effective.comparisons)
    if intent is CompareIntent.ASSERTION and compares:
        return _diagnostic("compare.intent.assertion-topology-required")
    if intent is CompareIntent.COMPARISON and not compares:
        return _diagnostic("compare.intent.comparison-topology-required")
    return None


def _rejected(code: str) -> ComparePreparationResult:
    return ComparePreparationResult(diagnostics=(_diagnostic(code),))


def _diagnostic(code: str) -> ApplicationDiagnostic:
    return ApplicationDiagnostic(code=code, severity="error", stage="invocation")


__all__ = (
    "CompareCloseResult",
    "CompareExecutionControls",
    "CompareIntent",
    "ComparePreparationHandle",
    "ComparePreparationResult",
    "ComparePrepareRequest",
    "CompareService",
    "CompareServiceError",
    "CompareServiceErrorCode",
)
