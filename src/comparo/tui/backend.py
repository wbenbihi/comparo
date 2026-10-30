"""Process-scoped owner of effectful Comparo application services."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from comparo.application import ArtifactBodyReference
from comparo.application import ArtifactBodyWindow
from comparo.application import ArtifactOperationError
from comparo.application import AuthoredResourceInspectionRequest
from comparo.application import AuthoredResourceWindow
from comparo.application import CompareCloseResult
from comparo.application import CompareExecutionControls
from comparo.application import CompareIntent
from comparo.application import ComparePreparationHandle
from comparo.application import ComparePreparationResult
from comparo.application import ComparePrepareRequest
from comparo.application import CompareService
from comparo.application import EffectiveResourceInspectionRequest
from comparo.application import EffectiveResourceWindow
from comparo.application import PlanningOptions
from comparo.application import PlanOccurrenceInspection
from comparo.application import ProfileSelectionOverrides
from comparo.application import ProjectPlanRequest
from comparo.application import ProjectWorkspaceRequest
from comparo.application import ProjectWorkspaceResult
from comparo.application import RenderPreviewWindowResult
from comparo.application import ReportArchiveListing
from comparo.application import ResolvedValueInspectionBatch
from comparo.application import ResolvedValueInspectionBatchResult
from comparo.application import ResolvedValueInspectionTarget
from comparo.application import ResolvedValueProjectionLimits
from comparo.application import ResourceCoordinate
from comparo.application import ResultProjection
from comparo.application import RunHandle
from comparo.application import RunInspection
from comparo.application import RunService
from comparo.application import SourceInspectionLimits
from comparo.application import SourceInspectionRequest
from comparo.application import SourceViewMode
from comparo.application import SourceWindow
from comparo.application import initialize_project_artifact_root
from comparo.application import inspect_authored_resource
from comparo.application import inspect_effective_resource
from comparo.application import inspect_project_source
from comparo.application import inspect_project_workspace
from comparo.application import list_report_archive
from comparo.application import project_results
from comparo.application import read_artifact_body_window
from comparo.application import read_artifact_manifest
from comparo.application import render_artifact_report
from comparo.application import resolve_project_root
from comparo.application import validate_existing_artifact_root
from comparo.application import validate_project_artifact_root
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.reporters import ReportFormat
from comparo.tui.contracts import TuiLaunchRequest
from comparo.tui.models.resource import EnvironmentHealthCheckResult
from comparo.tui.models.resource import ResolvedMatrixPlan
from comparo.tui.models.resource import ResolvedRequestTarget

SOURCE_LIMITS = SourceInspectionLimits(
    maximum_source_bytes=4_000_000,
    maximum_output_bytes=64_000,
    maximum_lines=300,
)

SHUTDOWN_TIMEOUT = Duration.from_seconds(Decimal("5"))
MAXIMUM_INSPECTIONS_PER_PREPARATION = 2
MAXIMUM_REGISTERED_RUNS = 8
MAXIMUM_RESOLVED_FIELDS = 16
RESOLVED_PROJECTION_NODES = 64
RESOLVED_PROJECTION_LIMITS = ResolvedValueProjectionLimits(
    maximum_page_nodes=PositiveCount(value=RESOLVED_PROJECTION_NODES),
    maximum_scalar_bytes=ByteCount(value=4 * 1024),
    maximum_path_segments=PositiveCount(value=32),
    maximum_path_segment_bytes=ByteCount(value=512),
    maximum_traversal_nodes=PositiveCount(value=1024),
    maximum_traversal_bytes=ByteCount(value=256 * 1024),
    maximum_taint_origins=PositiveCount(value=16),
    maximum_taint_origin_bytes=ByteCount(value=256),
)


@dataclass(frozen=True, slots=True)
class ResolvedFieldBatch:
    """Safe result of one ephemeral, context-bound resolved inspection."""

    occurrence: PlanOccurrenceInspection | None = None
    values: ResolvedValueInspectionBatchResult | None = None
    diagnostics: tuple[str, ...] = ()
    selection_incomplete: bool = False
    total_occurrences: int | None = None
    request_target: ResolvedRequestTarget | None = None
    matrix_plan: ResolvedMatrixPlan | None = None

    def __post_init__(self) -> None:
        """Keep success and value-free failure structurally disjoint."""
        if (self.occurrence is None) != (self.values is None):
            raise ValueError("resolved field batch success must be complete")
        successful = self.occurrence is not None and self.values is not None
        if successful == bool(self.diagnostics):
            raise ValueError("resolved field batch requires success or diagnostics")
        if self.selection_incomplete and not successful:
            raise ValueError("only a successful resolved batch may be incomplete")
        if successful != (self.total_occurrences is not None):
            raise ValueError("resolved field batch must retain its total occurrence count")
        if self.total_occurrences is not None and self.total_occurrences < 1:
            raise ValueError("resolved field batch total occurrences must be positive")
        if self.request_target is not None and not successful:
            raise ValueError("only a successful Request batch may carry a rendered target")
        if self.matrix_plan is not None and not successful:
            raise ValueError("only a successful Matrix batch may carry a compiled plan")


@dataclass(frozen=True, slots=True)
class HealthCheckBatch:
    """Exactly one terminal health result or value-free rejection codes."""

    value: EnvironmentHealthCheckResult | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful and rejected results structurally disjoint."""
        successful = self.value is not None and not self.diagnostics
        rejected = self.value is None and bool(self.diagnostics)
        if successful or rejected:
            return
        raise ValueError("health-check batch requires exactly result or diagnostics")


def _resolved_field_selection(
    field_paths: tuple[tuple[str, ...], ...],
) -> tuple[tuple[tuple[str, ...], ...], bool] | None:
    """Validate one bounded field page without traversing an unbounded tail."""
    if not field_paths:
        return None
    selected = field_paths[:MAXIMUM_RESOLVED_FIELDS]
    if any(
        type(path) is not tuple
        or not path
        or any(type(segment) is not str or not segment for segment in path)
        for path in selected
    ):
        return None
    return selected, len(field_paths) > MAXIMUM_RESOLVED_FIELDS


def _resolved_context_diagnostic(
    resource: ResourceCoordinate,
    environment: ResourceCoordinate,
    subject_id: str | None,
    execution_ordinal: int,
) -> str | None:
    """Validate the complete caller-owned resolved inspection context."""
    if resource.kind not in {
        "Project",
        "Environment",
        "Request",
        "Scenario",
        "Matrix",
        "Value",
        "ExecutionProfile",
        "AssertionProfile",
        "DiffProfile",
    } or (environment.kind != "Environment"):
        return "resolution.context.invalid"
    if type(execution_ordinal) is not int or execution_ordinal < 0:
        return "resolution.execution.invalid"
    if (
        resource.kind
        in {
            "Matrix",
            "Value",
            "ExecutionProfile",
            "AssertionProfile",
            "DiffProfile",
        }
        and subject_id is None
    ):
        return "resolution.subject.required"
    if subject_id is not None and (
        type(subject_id) is not str
        or not subject_id
        or len(subject_id) > 255
        or not subject_id.isprintable()
    ):
        return "resolution.subject.invalid"
    return None


def _resolved_occurrence_diagnostic(
    occurrence: PlanOccurrenceInspection,
    resource: ResourceCoordinate,
    environment: ResourceCoordinate,
) -> str | None:
    """Verify that one prepared occurrence matches the requested exact context."""
    if occurrence.environment != environment:
        return "resolution.environment.mismatch"
    if resource.kind == "Request" and occurrence.subject != resource:
        return "resolution.subject.mismatch"
    return None


def _health_revisions_match(
    prepared: ComparePreparationResult,
    environment: ResourceCoordinate,
    requests: tuple[ResourceCoordinate, ...],
) -> bool:
    """Verify the recompiled executable topology against the selected exact revisions."""
    review = prepared.review
    if review is None:
        return False
    slices = review.plan.slices
    return (
        len(slices) == len(requests)
        and tuple(item.subject for item in slices) == requests
        and all(item.environments == (environment,) for item in slices)
    )


def _health_selection_is_valid(
    environment: ResourceCoordinate,
    requests: tuple[ResourceCoordinate, ...],
) -> bool:
    """Validate the strict typed health boundary before accessing selection fields."""
    return (
        type(environment) is ResourceCoordinate
        and environment.kind == "Environment"
        and type(requests) is tuple
        and bool(requests)
        and all(
            type(request) is ResourceCoordinate
            and request.kind == "Request"
            and request.namespace == environment.namespace
            for request in requests
        )
        and len(set(requests)) == len(requests)
    )


class TuiBackend:
    """Process-scoped owner of application services and launch choices.

    One instance belongs to each app. Workspaces access its narrow methods
    through their base class; immutable application DTOs are adapted by
    presentation models. Synchronous filesystem and compilation methods run
    in thread workers, while asynchronous lifecycle methods stay on the event
    loop.
    """

    def __init__(self, config: Path | TuiLaunchRequest) -> None:
        """Initialize services for the selected configuration or project directory."""
        self.launch = (
            config if isinstance(config, TuiLaunchRequest) else TuiLaunchRequest(config=config)
        )
        self._workspace_request = ProjectWorkspaceRequest(
            config=self.launch.config.resolve(),
            additional_sources=self.launch.additional_sources,
            load_limits=self.launch.limits.project_load,
        )
        self.compare = CompareService(
            maximum_outstanding=self.launch.limits.maximum_outstanding_preparations,
            maximum_inspections_per_preparation=MAXIMUM_INSPECTIONS_PER_PREPARATION,
        )
        self.runs = RunService(maximum_runs=MAXIMUM_REGISTERED_RUNS)
        self.execution_settings = self.launch.limits.execution_settings
        self._artifact_root: Path | None = None
        self._handle: ComparePreparationHandle | None = None
        self._launch_controls: CompareExecutionControls | None = None
        self._preparation_lock = asyncio.Lock()
        self._closed = False

    # ----------------------------------------------------------- project

    def load_workspace(self) -> ProjectWorkspaceResult:
        """Blocking filesystem/compile work — callers run it in a worker."""
        return inspect_project_workspace(self._workspace_request)

    def inspect_source(
        self, source_id: str, expected_sha256: str, around_line: int
    ) -> SourceWindow:
        """Masked authored window near one resource. Blocking; raises SourceInspectionError."""
        return inspect_project_source(
            SourceInspectionRequest(
                workspace=self._workspace_request,
                source_id=source_id,
                expected_sha256=expected_sha256,
                start_line=max(1, around_line - 10),
                mode=SourceViewMode.MASKED,
                limits=SOURCE_LIMITS,
            )
        )

    def inspect_authored(
        self, coordinate: ResourceCoordinate, expected_sha256: str | None
    ) -> AuthoredResourceWindow:
        """Inspect exactly one masked authored resource document. Blocking."""
        return inspect_authored_resource(
            AuthoredResourceInspectionRequest(
                workspace=self._workspace_request,
                coordinate=coordinate,
                limits=SOURCE_LIMITS,
                expected_sha256=expected_sha256,
            )
        )

    def inspect_effective(self, coordinate: ResourceCoordinate) -> EffectiveResourceWindow:
        """First effective window for one exact resource revision. Blocking."""
        return inspect_effective_resource(
            EffectiveResourceInspectionRequest(
                workspace=self._workspace_request,
                coordinate=coordinate,
                start_field=0,
                maximum_fields=100,
                start_relationship=0,
                maximum_relationships=100,
            )
        )

    async def inspect_resolved_fields(
        self,
        resource: ResourceCoordinate,
        environment: ResourceCoordinate,
        field_paths: tuple[tuple[str, ...], ...],
        *,
        subject_id: str | None = None,
        execution_ordinal: int = 0,
    ) -> ResolvedFieldBatch:
        """Resolve the bounded first field page through an ephemeral masked Compare plan."""
        context_diagnostic = _resolved_context_diagnostic(
            resource,
            environment,
            subject_id,
            execution_ordinal,
        )
        if context_diagnostic is not None:
            return ResolvedFieldBatch(diagnostics=(context_diagnostic,))
        selection = _resolved_field_selection(field_paths)
        if selection is None:
            return ResolvedFieldBatch(diagnostics=("resolution.selection.invalid",))
        selected_paths, selection_incomplete = selection
        async with self._preparation_lock:
            if self._closed:
                return ResolvedFieldBatch(diagnostics=("resolution.service.closed",))
            prepared, controls = await self._join_preparation(
                asyncio.create_task(
                    asyncio.to_thread(
                        self._prepare_resolved_context,
                        environment.resource_id,
                        resource.kind,
                        resource.resource_id,
                        subject_id,
                    )
                )
            )
            handle = prepared.handle
            if handle is None:
                return ResolvedFieldBatch(
                    diagnostics=tuple(item.code for item in prepared.diagnostics)
                )
            try:
                occurrences = self.compare.list_occurrences(
                    handle,
                    controls,
                    start_ordinal=execution_ordinal,
                    maximum_occurrences=1,
                )
                if not occurrences.occurrences:
                    return ResolvedFieldBatch(diagnostics=("resolution.execution.unavailable",))
                occurrence = occurrences.occurrences[0]
                occurrence_diagnostic = _resolved_occurrence_diagnostic(
                    occurrence,
                    resource,
                    environment,
                )
                if occurrence_diagnostic is not None:
                    return ResolvedFieldBatch(diagnostics=(occurrence_diagnostic,))
                request_target = (
                    await self._preview_request_target(
                        handle,
                        controls,
                        occurrence,
                        resource,
                        environment,
                    )
                    if resource.kind == "Request"
                    else None
                )
                matrix_plan = self._resolved_matrix_plan(prepared, resource)
                if resource.kind == "Matrix" and matrix_plan is None:
                    return ResolvedFieldBatch(
                        diagnostics=("resolution.context.matrix-binding-ambiguous",)
                    )
                targets = tuple(
                    ResolvedValueInspectionTarget(
                        resource=resource,
                        field_path=path,
                        execution_ordinal=occurrence.ordinal,
                        environment=environment,
                        limits=RESOLVED_PROJECTION_LIMITS,
                        start_node=0,
                        maximum_nodes=RESOLVED_PROJECTION_NODES,
                    )
                    for path in selected_paths
                )
                values = await self.compare.inspect_resolved_values(
                    handle,
                    controls,
                    ResolvedValueInspectionBatch(
                        targets=targets,
                        maximum_targets=MAXIMUM_RESOLVED_FIELDS,
                    ),
                )
                return ResolvedFieldBatch(
                    occurrence=occurrence,
                    values=values,
                    selection_incomplete=selection_incomplete,
                    total_occurrences=occurrences.total_occurrences,
                    request_target=request_target,
                    matrix_plan=matrix_plan,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - adapter exposes closed codes only
                return ResolvedFieldBatch(diagnostics=("resolution.inspect.internal",))
            finally:
                with suppress(Exception):
                    self.compare.discard(handle)

    @staticmethod
    def _resolved_matrix_plan(
        prepared: ComparePreparationResult,
        resource: ResourceCoordinate,
    ) -> ResolvedMatrixPlan | None:
        """Project one exact Matrix axis from the prepared Compare topology."""
        if resource.kind != "Matrix" or prepared.review is None:
            return None
        matching = tuple(
            (plan_slice.subject, axis)
            for plan_slice in prepared.review.plan.slices
            for axis in plan_slice.matrices
            if axis.matrix == resource
        )
        if len(matching) != 1:
            return None
        subject, axis = matching[0]
        return ResolvedMatrixPlan(
            subject=subject,
            binding=axis.binding,
            cases=axis.cases,
        )

    async def _preview_request_target(
        self,
        handle: ComparePreparationHandle,
        controls: CompareExecutionControls,
        occurrence: PlanOccurrenceInspection,
        resource: ResourceCoordinate,
        environment: ResourceCoordinate,
    ) -> ResolvedRequestTarget | None:
        """Return one exact sanitized rendered target or fail closed to no target."""
        preview = await self.compare.preview_requests(
            handle,
            controls,
            start_ordinal=occurrence.ordinal,
            maximum_previews=1,
        )
        if len(preview.previews) != 1:
            return None
        rendered = preview.previews[0]
        if (
            rendered.ordinal != occurrence.ordinal
            or rendered.subject != resource
            or rendered.environment != environment
        ):
            return None
        return ResolvedRequestTarget(method=rendered.method, url=rendered.url)

    async def run_environment_health_checks(
        self,
        environment: ResourceCoordinate,
        requests: tuple[ResourceCoordinate, ...],
    ) -> HealthCheckBatch:
        """Run declared Request probes through Compare and retain the run for Reports."""
        if not _health_selection_is_valid(environment, requests):
            return HealthCheckBatch(diagnostics=("health-check.selection.invalid",))
        request_ids = tuple(request.resource_id for request in requests)
        async with self._preparation_lock:
            if self._closed:
                return HealthCheckBatch(diagnostics=("health-check.service.closed",))
            try:
                prepared, controls = await self._join_preparation(
                    asyncio.create_task(
                        asyncio.to_thread(
                            self._prepare_health_context,
                            environment.resource_id,
                            request_ids,
                        )
                    )
                )
            except Exception:  # noqa: BLE001 - adapter exposes closed codes only
                return HealthCheckBatch(diagnostics=("health-check.prepare.internal",))
            handle = prepared.handle
            if handle is None:
                return HealthCheckBatch(
                    diagnostics=tuple(item.code for item in prepared.diagnostics)
                )
            if not _health_revisions_match(prepared, environment, requests):
                with suppress(Exception):
                    self.compare.discard(handle)
                return HealthCheckBatch(diagnostics=("health-check.revision.changed",))
            try:
                run = self.runs.start(self.compare, handle, controls)
            except Exception:  # noqa: BLE001 - handle cleanup remains value-free
                with suppress(Exception):
                    self.compare.discard(handle)
                return HealthCheckBatch(diagnostics=("health-check.execute.internal",))
        return await self._collect_health_result(run, len(request_ids))

    async def _collect_health_result(
        self,
        run: RunHandle,
        request_count: int,
    ) -> HealthCheckBatch:
        """Project a retained run into one bounded Environment health summary."""
        try:
            access = await self.runs.result(run)
            if access.result is None:
                diagnostics = tuple(item.code for item in access.diagnostics)
                return HealthCheckBatch(
                    diagnostics=diagnostics or ("health-check.execute.unavailable",)
                )
            result = access.result
            if result.artifact is None:
                diagnostics = tuple(item.code for item in result.diagnostics)
                return HealthCheckBatch(
                    diagnostics=diagnostics or ("health-check.execute.unavailable",)
                )
            artifact = result.artifact
            facts = (
                *(fact for execution in artifact.executions for fact in execution.assertions),
                *(fact for comparison in artifact.comparisons for fact in comparison.diffs),
            )
            adverse = sum(
                fact.outcome.value not in {"satisfied", "equivalent", "skipped"} for fact in facts
            )
            completion = artifact.completion.kind.value
            gate = artifact.gate.verdict.value
            return HealthCheckBatch(
                value=EnvironmentHealthCheckResult(
                    run_id=run.run_id,
                    request_count=request_count,
                    completion=completion,
                    gate=gate,
                    adverse_facts=adverse,
                    healthy=(completion == "complete" and gate != "failed" and adverse == 0),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - adapter exposes closed codes only
            return HealthCheckBatch(diagnostics=("health-check.execute.internal",))

    def _prepare_resolved_context(
        self,
        environment_id: str,
        resource_kind: str,
        resource_id: str,
        subject_id: str | None,
    ) -> tuple[ComparePreparationResult, CompareExecutionControls]:
        """Prepare the exact profile/subject precedence needed by masked inspection."""
        selected_subject = resource_id if resource_kind in {"Request", "Scenario"} else subject_id
        profiles = ProfileSelectionOverrides(
            assertions=(resource_id,) if resource_kind == "AssertionProfile" else None,
            diffs=(resource_id,) if resource_kind == "DiffProfile" else None,
        )
        controls = CompareExecutionControls(
            settings=self.execution_settings,
            artifact_root=None,
            approve_mutations=False,
            tool_version="tui",
        )
        prepared = self.compare.prepare(
            ComparePrepareRequest(
                intent=(
                    CompareIntent.EXECUTION_PROFILE
                    if resource_kind == "ExecutionProfile"
                    else CompareIntent.ASSERTION
                ),
                project=ProjectPlanRequest(
                    config=self._workspace_request.config,
                    additional_sources=self._workspace_request.additional_sources,
                    policy=self.launch.policy,
                    options=PlanningOptions(
                        execution_profile=(
                            resource_id if resource_kind == "ExecutionProfile" else None
                        ),
                        subjects=() if selected_subject is None else (selected_subject,),
                        environments=(environment_id,),
                        profiles=profiles,
                    ),
                    invocation_id=uuid4(),
                    run_id=uuid4(),
                    load_limits=self._workspace_request.load_limits,
                    policy_load_limits=self.launch.limits.policy_load,
                ),
                controls=controls,
            )
        )
        return prepared, controls

    async def _join_preparation(
        self,
        task: asyncio.Task[tuple[ComparePreparationResult, CompareExecutionControls]],
    ) -> tuple[ComparePreparationResult, CompareExecutionControls]:
        """Keep preparation ownership serialized until its thread has actually stopped."""
        cancellation_requested = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_requested = True
            except Exception:  # noqa: BLE001 - retrieve terminal exception once below
                break
        if task.cancelled():
            raise asyncio.CancelledError
        try:
            completed = task.result()
        except Exception:
            if cancellation_requested:
                raise asyncio.CancelledError from None
            raise
        if not cancellation_requested:
            return completed
        if completed[0].handle is not None:
            with suppress(Exception):
                self.compare.discard(completed[0].handle)
        raise asyncio.CancelledError

    def _prepare_health_context(
        self,
        environment_id: str,
        request_ids: tuple[str, ...],
    ) -> tuple[ComparePreparationResult, CompareExecutionControls]:
        """Prepare declared probes as one normal assertion run."""
        artifact_root = self.ensure_artifact_root()
        controls = CompareExecutionControls(
            settings=self.execution_settings,
            artifact_root=artifact_root,
            approve_mutations=False,
            tool_version="tui",
        )
        prepared = self.compare.prepare(
            ComparePrepareRequest(
                intent=CompareIntent.ASSERTION,
                project=ProjectPlanRequest(
                    config=self._workspace_request.config,
                    additional_sources=self._workspace_request.additional_sources,
                    policy=self.launch.policy,
                    options=PlanningOptions(
                        subjects=request_ids,
                        environments=(environment_id,),
                    ),
                    invocation_id=uuid4(),
                    run_id=uuid4(),
                    load_limits=self._workspace_request.load_limits,
                    policy_load_limits=self.launch.limits.policy_load,
                ),
                controls=controls,
            )
        )
        return prepared, controls

    # ------------------------------------------------------ artifact root

    @property
    def artifact_root(self) -> Path | None:
        """The validated artifact root, once ensured."""
        return self._artifact_root

    def ensure_artifact_root(self) -> Path | None:
        """Initialize the project-local artifact root.

        Blocking; call after a successful workspace load, from the same thread worker.
        """
        if self.execution_settings.artifact.persist.value == "never":
            return None
        if self._artifact_root is None:
            selected = self.launch.artifact_root
            self._artifact_root = (
                validate_existing_artifact_root(selected)
                if selected.is_absolute()
                else initialize_project_artifact_root(
                    resolve_project_root(self._workspace_request.config), selected
                )
            )
        return self._artifact_root

    # ---------------------------------------------------- draft handle slot

    async def preview_launch(
        self, intent: CompareIntent, options: PlanningOptions, *, approve_mutations: bool
    ) -> ComparePreparationResult:
        """Inspect a temporary plan without replacing the explicit admission checkpoint."""
        async with self._preparation_lock:
            if self._closed:
                raise RuntimeError("launch service closed")
            prepared, _ = await self._join_preparation(
                asyncio.create_task(
                    asyncio.to_thread(self._prepare_launch, intent, options, approve_mutations)
                )
            )
            if prepared.handle is not None:
                self.compare.discard(prepared.handle)
            return prepared

    async def launch_destinations(self) -> RenderPreviewWindowResult:
        """Read a bounded sanitized destination sample from the retained checkpoint."""
        if self._handle is None or self._launch_controls is None:
            raise RuntimeError("launch requires preparation")
        return await self.compare.preview_requests(
            self._handle,
            self._launch_controls,
            start_ordinal=0,
            maximum_previews=64,
        )

    def release_launch(self, run: RunHandle) -> None:
        """Release terminal result memory when returning to configuration."""
        self.runs.discard(run)

    async def prepare_launch(
        self, intent: CompareIntent, options: PlanningOptions, *, approve_mutations: bool
    ) -> ComparePreparationResult:
        """Serialize preparation with Explorer and retain the exact reviewed controls."""
        async with self._preparation_lock:
            if self._closed:
                raise RuntimeError("launch service closed")
            self.discard_handle()
            prepared, controls = await self._join_preparation(
                asyncio.create_task(
                    asyncio.to_thread(self._prepare_launch, intent, options, approve_mutations)
                )
            )
            if prepared.handle is not None:
                self.adopt_handle(prepared.handle)
                self._launch_controls = controls
            return prepared

    def _prepare_launch(
        self, intent: CompareIntent, options: PlanningOptions, approve_mutations: bool
    ) -> tuple[ComparePreparationResult, CompareExecutionControls]:
        """Compile and admit a draft off the UI thread, without executing requests."""
        controls = CompareExecutionControls(
            settings=self.execution_settings,
            artifact_root=self.ensure_artifact_root(),
            approve_mutations=approve_mutations,
            tool_version="tui",
        )
        result = self.compare.prepare(
            ComparePrepareRequest(
                intent=intent,
                project=ProjectPlanRequest(
                    config=self._workspace_request.config,
                    additional_sources=self._workspace_request.additional_sources,
                    policy=self.launch.policy,
                    options=options,
                    invocation_id=uuid4(),
                    run_id=uuid4(),
                    load_limits=self._workspace_request.load_limits,
                    policy_load_limits=self.launch.limits.policy_load,
                ),
                controls=controls,
            )
        )
        return result, controls

    def start_launch(self) -> RunHandle:
        """Consume the one-shot reviewed plan with its unchanged execution controls."""
        if self._handle is None or self._launch_controls is None:
            raise RuntimeError("launch requires preparation")
        run = self.runs.start(self.compare, self._handle, self._launch_controls)
        self._handle = None
        self._launch_controls = None
        return run

    def inspect_launch(self, run: RunHandle) -> RunInspection:
        """Read coalesced, value-safe progress without exposing the running task."""
        return self.runs.inspect(run)

    def cancel_launch(self, run: RunHandle) -> bool:
        """Request cancellation while the backend continues draining the run."""
        return self.runs.cancel(run)

    async def launch_outcome(self, run: RunHandle) -> tuple[str, ...]:
        """Expose terminal persistence and failure codes without artifact internals."""
        access = await self.runs.result(run)
        if access.result is None:
            return tuple(item.code for item in access.diagnostics)
        result = access.result
        if result.diagnostics:
            return tuple(item.code for item in result.diagnostics)
        if result.persistence is None:
            return ("Report not persisted.",)
        outcome = result.persistence
        status = f"Report: {outcome.status.value}."
        return (status,) if outcome.failure is None else (status, outcome.failure.code.value)

    async def launch_results(self, run: RunHandle) -> ResultProjection | None:
        """Project only the sanitized terminal artifact into the shared results surface."""
        access = await self.runs.result(run)
        if access.result is None or access.result.artifact is None:
            return None
        return await asyncio.to_thread(project_results, access.result.artifact)

    async def read_result_body(self, reference: ArtifactBodyReference) -> ArtifactBodyWindow:
        """Authenticate one bounded capture window using its exact manifest coordinate."""
        if self._artifact_root is None:
            raise RuntimeError("report storage is unavailable")
        return await read_artifact_body_window(
            self._artifact_root,
            reference.artifact_id,
            reference,
            self.launch.limits.report_archive.artifact,
            offset=0,
            maximum_output_bytes=64 * 1024,
        )

    # --------------------------------------------------------- saved reports

    def _existing_report_root(self) -> Path | None:
        project = resolve_project_root(self._workspace_request.config)
        selected = self.launch.artifact_root
        if selected.is_absolute():
            root = validate_existing_artifact_root(selected)
        else:
            candidate = project / selected
            if not candidate.exists() and not candidate.is_symlink():
                return None
            root = validate_project_artifact_root(project, selected)
        self._artifact_root = root
        return root

    def list_reports(self) -> ReportArchiveListing:
        """Read a bounded authenticated archive without creating storage or sending HTTP."""
        root = self._existing_report_root()
        if root is None:
            return ReportArchiveListing(entries=(), failures=())
        return list_report_archive(
            root,
            self.launch.limits.report_archive,
        )

    def read_report(self, bundle_id: str) -> ResultProjection:
        """Reauthenticate the selected manifest and reuse the live result projection."""
        root = self._existing_report_root()
        if root is None:
            raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNAVAILABLE)
        return project_results(
            read_artifact_manifest(root, bundle_id, self.launch.limits.report_archive.artifact)
        )

    def report_export_path(self, bundle_id: str) -> Path:
        """Suggest an output beside the project; no file is created here."""
        return resolve_project_root(self._workspace_request.config) / f"report-{bundle_id}.json"

    def export_report(self, bundle_id: str, destination: Path, format_name: str) -> None:
        """Verify and export to an explicitly selected new file, never overwrite existing data."""
        root = self._existing_report_root()
        if root is None:
            raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNAVAILABLE)
        report = render_artifact_report(
            root, bundle_id, self.launch.limits.report_archive.artifact, ReportFormat(format_name)
        )
        with destination.expanduser().open("xb") as output:
            output.write(report.content)

    @property
    def handle(self) -> ComparePreparationHandle | None:
        """The current draft preparation handle, if any."""
        return self._handle

    def adopt_handle(self, handle: ComparePreparationHandle) -> None:
        """Retain a new draft handle, discarding any previous one."""
        self.discard_handle()
        self._handle = handle

    def discard_handle(self) -> None:
        """Release the current draft handle, if any."""
        self._launch_controls = None
        if self._handle is not None:
            self.compare.discard(self._handle)
            self._handle = None

    # ----------------------------------------------------------- shutdown

    async def shutdown(self) -> CompareCloseResult | None:
        """Contract shutdown order: runs, then draft handle, then service."""
        async with self._preparation_lock:
            if self._closed:
                return None
            self._closed = True
        await self.runs.close()
        self.discard_handle()
        self.compare.close()
        return await self.compare.aclose(timeout=SHUTDOWN_TIMEOUT)
