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
from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount

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
MAXIMUM_RESOLVED_FIELDS = 8
RESOLVED_PROJECTION_NODES = 128

RESOLVED_PROJECTION_LIMITS = ResolvedValueProjectionLimits(
    maximum_page_nodes=PositiveCount(value=RESOLVED_PROJECTION_NODES),
    maximum_scalar_bytes=ByteCount(value=16 * 1024),
    maximum_path_segments=PositiveCount(value=32),
    maximum_path_segment_bytes=ByteCount(value=512),
    maximum_traversal_nodes=PositiveCount(value=4096),
    maximum_traversal_bytes=ByteCount(value=4 * 1024 * 1024),
    maximum_taint_origins=PositiveCount(value=32),
    maximum_taint_origin_bytes=ByteCount(value=512),
)


@dataclass(frozen=True, slots=True)
class ResolvedFieldBatch:
    """Safe result of one ephemeral, context-bound resolved inspection."""

    occurrence: PlanOccurrenceInspection | None = None
    values: ResolvedValueInspectionBatchResult | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep success and value-free failure structurally disjoint."""
        if (self.occurrence is None) != (self.values is None):
            raise ValueError("resolved field batch success must be complete")
        successful = self.occurrence is not None and self.values is not None
        if successful == bool(self.diagnostics):
            raise ValueError("resolved field batch requires success or diagnostics")


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
        self._resolution_lock = asyncio.Lock()
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
        """Resolve a bounded field batch through an ephemeral masked Compare plan."""
        if resource.kind != "Project" or environment.kind != "Environment":
            return ResolvedFieldBatch(diagnostics=("resolution.context.invalid",))
        if (
            not field_paths
            or len(field_paths) > MAXIMUM_RESOLVED_FIELDS
            or any(not path or any(not segment for segment in path) for path in field_paths)
        ):
            return ResolvedFieldBatch(diagnostics=("resolution.selection.invalid",))
        async with self._resolution_lock:
            if self._closed:
                return ResolvedFieldBatch(diagnostics=("resolution.service.closed",))
            prepared, controls = await asyncio.to_thread(
                self._prepare_resolved_context,
                environment.resource_id,
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
                    for path in field_paths
                )
                values = await self.compare.inspect_resolved_values(
                    handle,
                    controls,
                    ResolvedValueInspectionBatch(
                        targets=targets,
                        maximum_targets=MAXIMUM_RESOLVED_FIELDS,
                    ),
                )
                return ResolvedFieldBatch(occurrence=occurrence, values=values)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - adapter exposes closed codes only
                return ResolvedFieldBatch(diagnostics=("resolution.inspect.internal",))
            finally:
                with suppress(Exception):
                    self.compare.discard(handle)

    def _prepare_resolved_context(
        self,
        environment_id: str,
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
                    options=PlanningOptions(environments=(environment_id,)),
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
        if self._closed:
            return None
        self._closed = True
        await self.runs.close()
        self.discard_handle()
        self.compare.close()
        return await self.compare.aclose(timeout=SHUTDOWN_TIMEOUT)
