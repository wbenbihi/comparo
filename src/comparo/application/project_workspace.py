"""Safe project inspection facade for interactive frontends."""

from __future__ import annotations

from pathlib import Path
from typing import final

import msgspec

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import SourceCoordinate
from comparo.application.inspection import inspect_configuration_diagnostic
from comparo.configuration.loader import DEFAULT_PROJECT_LOAD_LIMITS
from comparo.configuration.loader import ProjectLoadLimits
from comparo.configuration.loader import SourceCatalogEntry
from comparo.configuration.loader import compile_project_path
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.provenance import SourceLocation


@final
class ProjectWorkspaceRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Complete bounded local-source input for one Explorer project inspection."""

    config: Path
    additional_sources: tuple[Path, ...] = ()
    load_limits: ProjectLoadLimits = DEFAULT_PROJECT_LOAD_LIMITS

    def __post_init__(self) -> None:
        """Reject untyped adapter inputs before opening any project source."""
        if not isinstance(self.config, Path):
            raise TypeError("project workspace config must be a Path")
        if type(self.additional_sources) is not tuple or any(
            not isinstance(source, Path) for source in self.additional_sources
        ):
            raise TypeError("project workspace sources must be an immutable Path tuple")
        if type(self.load_limits) is not ProjectLoadLimits:
            raise TypeError("project workspace load limits must be ProjectLoadLimits")


@final
class ProjectResourceSummary(msgspec.Struct, frozen=True, kw_only=True):
    """One resource's public identity, metadata, and source coordinate without its spec."""

    coordinate: ResourceCoordinate
    name: str | None
    description: str | None
    tags: tuple[str, ...]
    source: SourceCoordinate

    def __post_init__(self) -> None:
        """Keep the Explorer boundary immutable and free of untyped presentation values."""
        if type(self.coordinate) is not ResourceCoordinate:
            raise TypeError("project resource summary coordinate must be typed")
        for value in (self.name, self.description):
            if value is not None and type(value) is not str:
                raise TypeError("project resource summary metadata must be text or None")
        if type(self.tags) is not tuple or any(type(tag) is not str for tag in self.tags):
            raise TypeError("project resource summary tags must be an immutable text tuple")
        if type(self.source) is not SourceCoordinate:
            raise TypeError("project resource summary source must be typed")


@final
class ProjectSourceSummary(msgspec.Struct, frozen=True, kw_only=True):
    """One discovered local source and the safe resource coordinates compiled from it."""

    source_id: str
    path: str
    entry: bool
    resources: tuple[ResourceCoordinate, ...] = ()

    def __post_init__(self) -> None:
        """Require an absolute catalog path and unique immutable resource membership."""
        if type(self.source_id) is not str:
            raise TypeError("project source summary ID must be text")
        if not self.source_id:
            raise ValueError("project source summary ID must be non-empty text")
        if type(self.path) is not str:
            raise TypeError("project source summary path must be text")
        if not self.path or not Path(self.path).is_absolute():
            raise ValueError("project source summary path must be absolute non-empty text")
        if type(self.entry) is not bool:
            raise TypeError("project source summary entry marker must be bool")
        if type(self.resources) is not tuple or any(
            type(resource) is not ResourceCoordinate for resource in self.resources
        ):
            raise TypeError("project source summary resources must be an immutable typed tuple")
        if len(set(self.resources)) != len(self.resources):
            raise ValueError("project source summary resources must be unique")


@final
class ProjectWorkspaceSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    """One complete safe project/resource snapshot for Explorer presentation."""

    api_version: str
    namespace: str
    project: ProjectResourceSummary
    resources: tuple[ProjectResourceSummary, ...]

    def __post_init__(self) -> None:
        """Require one Project root and unique non-Project resources."""
        if type(self.api_version) is not str or not self.api_version:
            raise ValueError("project workspace API version must be non-empty text")
        if type(self.namespace) is not str or not self.namespace:
            raise ValueError("project workspace namespace must be non-empty text")
        if type(self.project) is not ProjectResourceSummary:
            raise TypeError("project workspace root must be a typed resource summary")
        if self.project.coordinate.kind != ResourceKind.PROJECT.value:
            raise ValueError("project workspace root must identify Project")
        if type(self.resources) is not tuple or any(
            type(resource) is not ProjectResourceSummary for resource in self.resources
        ):
            raise TypeError("project workspace resources must be an immutable typed tuple")
        coordinates = tuple(resource.coordinate for resource in self.resources)
        if any(item.kind == ResourceKind.PROJECT.value for item in coordinates):
            raise ValueError("project workspace resources cannot contain Project")
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("project workspace resource coordinates must be unique")


@final
class ProjectWorkspaceResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one safe snapshot or diagnostics, plus any discovered source catalog."""

    snapshot: ProjectWorkspaceSnapshot | None = None
    sources: tuple[ProjectSourceSummary, ...] = ()
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep success and failure disjoint and validate successful source closure."""
        if self.snapshot is not None and type(self.snapshot) is not ProjectWorkspaceSnapshot:
            raise TypeError("project workspace result snapshot must be typed")
        if type(self.sources) is not tuple or any(
            type(source) is not ProjectSourceSummary for source in self.sources
        ):
            raise TypeError("project workspace result sources must be an immutable typed tuple")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("project workspace diagnostics must be immutable and typed")
        if self.snapshot is None:
            if not self.diagnostics:
                raise ValueError("project workspace failure requires diagnostics")
            return
        if self.diagnostics:
            raise ValueError("project workspace success cannot contain diagnostics")
        self._validate_success_sources(self.snapshot)

    def _validate_success_sources(self, snapshot: ProjectWorkspaceSnapshot) -> None:
        """Require unique sources and exact project/resource membership on success."""
        if sum(source.entry for source in self.sources) != 1:
            raise ValueError("project workspace success requires one entry source")
        source_ids = tuple(source.source_id for source in self.sources)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("project workspace source IDs must be unique")
        listed = tuple(resource for source in self.sources for resource in source.resources)
        expected = (
            snapshot.project.coordinate,
            *(resource.coordinate for resource in snapshot.resources),
        )
        if len(set(listed)) != len(listed) or set(listed) != set(expected):
            raise ValueError("project workspace sources must cover every resource exactly once")


def inspect_project_workspace(request: ProjectWorkspaceRequest) -> ProjectWorkspaceResult:
    """Compile a local project and expose only safe Explorer inspection products."""
    if type(request) is not ProjectWorkspaceRequest:
        raise TypeError("project workspace inspection requires ProjectWorkspaceRequest")
    try:
        loaded = compile_project_path(
            request.config,
            additional_sources=request.additional_sources,
            limits=request.load_limits,
        )
    except Exception:  # noqa: BLE001 - never expose source-bearing adapter exception details
        return ProjectWorkspaceResult(
            diagnostics=(
                ApplicationDiagnostic(
                    code="project.workspace.internal",
                    severity="error",
                    stage="configuration",
                ),
            )
        )
    compilation = loaded.compilation
    if compilation.project is None:
        return ProjectWorkspaceResult(
            sources=_source_summaries(loaded.catalog, ()),
            diagnostics=tuple(
                inspect_configuration_diagnostic(item) for item in compilation.diagnostics
            ),
        )
    project = compilation.project
    project_summary = _resource_summary(project.project)
    resource_summaries = tuple(_resource_summary(resource) for resource in project.resources)
    all_resources = (project_summary, *resource_summaries)
    return ProjectWorkspaceResult(
        snapshot=ProjectWorkspaceSnapshot(
            api_version=project.api_version,
            namespace=project.namespace.value,
            project=project_summary,
            resources=resource_summaries,
        ),
        sources=_source_summaries(loaded.catalog, all_resources),
    )


def _resource_summary(resource: CompiledResource) -> ProjectResourceSummary:
    identity = resource.definition.identity
    metadata = resource.metadata
    return ProjectResourceSummary(
        coordinate=ResourceCoordinate(
            namespace=identity.namespace.value,
            kind=identity.kind.value,
            resource_id=identity.resource_id.value,
            fingerprint=resource.definition.fingerprint.digest.hex_value,
        ),
        name=metadata.name,
        description=metadata.description,
        tags=metadata.tags,
        source=_source_coordinate(resource.location),
    )


def _source_coordinate(location: SourceLocation) -> SourceCoordinate:
    return SourceCoordinate(
        source=location.source_id.value,
        document=location.document.value,
        line=location.line.value,
        column=location.column.value,
    )


def _source_summaries(
    catalog: tuple[SourceCatalogEntry, ...],
    resources: tuple[ProjectResourceSummary, ...],
) -> tuple[ProjectSourceSummary, ...]:
    return tuple(
        ProjectSourceSummary(
            source_id=item.source_id.value,
            path=str(item.path),
            entry=index == 0,
            resources=tuple(
                resource.coordinate
                for resource in resources
                if resource.source.source == item.source_id.value
            ),
        )
        for index, item in enumerate(catalog)
    )


__all__ = (
    "ProjectResourceSummary",
    "ProjectSourceSummary",
    "ProjectWorkspaceRequest",
    "ProjectWorkspaceResult",
    "ProjectWorkspaceSnapshot",
    "inspect_project_workspace",
)
