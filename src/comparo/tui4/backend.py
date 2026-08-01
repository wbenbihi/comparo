"""Process-scoped owner of effectful Comparo application services."""

from decimal import Decimal
from pathlib import Path

from comparo.application import AuthoredResourceInspectionRequest
from comparo.application import AuthoredResourceWindow
from comparo.application import CompareCloseResult
from comparo.application import ComparePreparationHandle
from comparo.application import CompareService
from comparo.application import EffectiveResourceInspectionRequest
from comparo.application import EffectiveResourceWindow
from comparo.application import ProjectWorkspaceRequest
from comparo.application import ProjectWorkspaceResult
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
from comparo.core.domain.primitives import Duration

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
