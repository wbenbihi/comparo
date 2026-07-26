"""Bounded prepare/start facade for every interactive Compare topology."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import final
from uuid import uuid4

import msgspec

from comparo.application.evaluation_binding import preflight_configuration_evaluation
from comparo.application.execution_session import ExecutionSession
from comparo.application.execution_session import start_execution_session
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import inspect_diagnostic
from comparo.application.inspection import inspect_plan
from comparo.application.plan_review import PlanReviewInspection
from comparo.application.plan_review import inspect_execution_settings
from comparo.application.plan_review import inspect_runtime_limits
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionSettings
from comparo.application.runtime_capabilities import preflight_runtime_capabilities

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
class CompareService:
    """Retain a bounded set of admitted plans until start, discard, or service closure."""

    __slots__ = ("_closed", "_maximum_outstanding", "_prepared")

    def __init__(self, *, maximum_outstanding: int) -> None:
        """Create an open service with one explicit retained-plan ceiling."""
        if type(maximum_outstanding) is not int or maximum_outstanding < 1:
            raise ValueError("Compare outstanding preparation limit must be a positive integer")
        self._maximum_outstanding = maximum_outstanding
        self._prepared: dict[ComparePreparationHandle, _PreparedComparison] = {}
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
        self._prepared[handle] = _PreparedComparison(
            project=outcome.project,
            controls=request.controls,
            review=admitted,
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
                settings=controls.settings,
                ambient_environment=None,
                artifact_root=controls.artifact_root,
                approve_mutations=controls.approve_mutations,
                tool_version=controls.tool_version,
            )
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
        return self._prepared.pop(handle, None) is not None

    def close(self) -> None:
        """Idempotently discard every outstanding plan and reject future preparation."""
        self._prepared.clear()
        self._closed = True

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
        if self._closed:
            raise CompareServiceError(CompareServiceErrorCode.CLOSED)
        prepared = self._prepared.get(handle)
        if prepared is None:
            raise CompareServiceError(CompareServiceErrorCode.HANDLE_UNKNOWN)
        if controls != prepared.controls:
            raise CompareServiceError(CompareServiceErrorCode.PREPARATION_MISMATCH)
        if not prepared.review.execution_allowed:
            raise CompareServiceError(CompareServiceErrorCode.PREPARATION_BLOCKED)
        return prepared


@final
class _PreparedComparison(msgspec.Struct, frozen=True, kw_only=True):
    """One retained admitted project bound to every inspected execution control."""

    project: PreparedProjectPlan
    controls: CompareExecutionControls
    review: PlanReviewInspection

    def __post_init__(self) -> None:
        if type(self.project) is not PreparedProjectPlan:
            raise TypeError("prepared comparison project must be typed")
        if type(self.controls) is not CompareExecutionControls:
            raise TypeError("prepared comparison controls must be typed")
        if type(self.review) is not PlanReviewInspection:
            raise TypeError("prepared comparison review must be typed")


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
    evaluation = preflight_configuration_evaluation(project.prepared)
    if not evaluation.supported:
        return tuple(inspect_diagnostic(item) for item in evaluation.diagnostics)
    return PlanReviewInspection(
        plan=inspect_plan(
            project.prepared,
            project.policy,
            evaluation,
            request.controls.artifact_root,
        ),
        runtime=inspect_runtime_limits(request.controls.settings),
        execution=inspect_execution_settings(
            request.controls.settings,
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
    "CompareExecutionControls",
    "CompareIntent",
    "ComparePreparationHandle",
    "ComparePreparationResult",
    "ComparePrepareRequest",
    "CompareService",
    "CompareServiceError",
    "CompareServiceErrorCode",
)
