"""Process-scoped owner of effectful Comparo application services."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

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
from comparo.application import ProjectPlanRequest
from comparo.application import ProjectWorkspaceRequest
from comparo.application import ProjectWorkspaceResult
from comparo.application import ResolvedValueInspectionBatch
from comparo.application import ResolvedValueInspectionBatchResult
from comparo.application import ResolvedValueInspectionTarget
from comparo.application import ResolvedValueProjectionLimits
from comparo.application import ResourceCoordinate
from comparo.application import RunHandle
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
from comparo.application import persisted_production_execution_defaults_v1
from comparo.application import resolve_project_root
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.tui4.models.resource import EnvironmentHealthCheckResult
from comparo.tui4.models.resource import ResolvedRequestTarget

SOURCE_LIMITS = SourceInspectionLimits(
    maximum_source_bytes=4_000_000,
    maximum_output_bytes=64_000,
    maximum_lines=300,
)

ARTIFACT_RELATIVE_ROOT = Path(".comparo/artifacts")
SHUTDOWN_TIMEOUT = Duration.from_seconds(Decimal("5"))
MAXIMUM_OUTSTANDING_PREPARATIONS = 2
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
    request_target: ResolvedRequestTarget | None = None

    def __post_init__(self) -> None:
        """Keep success and value-free failure structurally disjoint."""
        if (self.occurrence is None) != (self.values is None):
            raise ValueError("resolved field batch success must be complete")
        successful = self.occurrence is not None and self.values is not None
        if successful == bool(self.diagnostics):
            raise ValueError("resolved field batch requires success or diagnostics")
        if self.selection_incomplete and not successful:
            raise ValueError("only a successful resolved batch may be incomplete")
        if self.request_target is not None and not successful:
            raise ValueError("only a successful Request batch may carry a rendered target")


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

    def __init__(self, config: Path) -> None:
        """Initialize services for the selected configuration or project directory."""
        self._workspace_request = ProjectWorkspaceRequest(config=config.resolve())
        self.compare = CompareService(
            maximum_outstanding=MAXIMUM_OUTSTANDING_PREPARATIONS,
            maximum_inspections_per_preparation=MAXIMUM_INSPECTIONS_PER_PREPARATION,
        )
        self.runs = RunService(maximum_runs=MAXIMUM_REGISTERED_RUNS)
        self.execution_settings = persisted_production_execution_defaults_v1()
        self._artifact_root: Path | None = None
        self._handle: ComparePreparationHandle | None = None
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
    ) -> ResolvedFieldBatch:
        """Resolve the bounded first field page through an ephemeral masked Compare plan."""
        if resource.kind not in {"Project", "Environment", "Request"} or (
            environment.kind != "Environment"
        ):
            return ResolvedFieldBatch(diagnostics=("resolution.context.invalid",))
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
                        resource.resource_id if resource.kind == "Request" else None,
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
                    start_ordinal=0,
                    maximum_occurrences=1,
                )
                if not occurrences.occurrences:
                    return ResolvedFieldBatch(diagnostics=("resolution.execution.unavailable",))
                occurrence = occurrences.occurrences[0]
                if occurrence.environment != environment:
                    return ResolvedFieldBatch(diagnostics=("resolution.environment.mismatch",))
                if resource.kind == "Request" and occurrence.subject != resource:
                    return ResolvedFieldBatch(diagnostics=("resolution.subject.mismatch",))
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
                    request_target=request_target,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - adapter exposes closed codes only
                return ResolvedFieldBatch(diagnostics=("resolution.inspect.internal",))
            finally:
                with suppress(Exception):
                    self.compare.discard(handle)

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
        request_id: str | None,
    ) -> tuple[ComparePreparationResult, CompareExecutionControls]:
        """Prepare a single-Environment assertion plan without runtime effects."""
        controls = CompareExecutionControls(
            settings=self.execution_settings,
            artifact_root=None,
            approve_mutations=False,
            tool_version="tui4",
        )
        prepared = self.compare.prepare(
            ComparePrepareRequest(
                intent=CompareIntent.ASSERTION,
                project=ProjectPlanRequest(
                    config=self._workspace_request.config,
                    additional_sources=self._workspace_request.additional_sources,
                    policy=None,
                    options=PlanningOptions(
                        subjects=() if request_id is None else (request_id,),
                        environments=(environment_id,),
                    ),
                    invocation_id=uuid4(),
                    run_id=uuid4(),
                    load_limits=self._workspace_request.load_limits,
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
            tool_version="tui4",
        )
        prepared = self.compare.prepare(
            ComparePrepareRequest(
                intent=CompareIntent.ASSERTION,
                project=ProjectPlanRequest(
                    config=self._workspace_request.config,
                    additional_sources=self._workspace_request.additional_sources,
                    policy=None,
                    options=PlanningOptions(
                        subjects=request_ids,
                        environments=(environment_id,),
                    ),
                    invocation_id=uuid4(),
                    run_id=uuid4(),
                    load_limits=self._workspace_request.load_limits,
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

    def ensure_artifact_root(self) -> Path:
        """Initialize the project-local artifact root.

        Blocking; call after a successful workspace load, from the same thread worker.
        """
        if self._artifact_root is None:
            project_root = resolve_project_root(self._workspace_request.config)
            self._artifact_root = initialize_project_artifact_root(
                project_root, ARTIFACT_RELATIVE_ROOT
            )
        return self._artifact_root

    # ---------------------------------------------------- draft handle slot

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
