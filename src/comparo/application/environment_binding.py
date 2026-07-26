"""Lower canonical Environment sources and build isolated run-start snapshots."""

from __future__ import annotations

import os
from typing import cast
from typing import final

import msgspec

from comparo.adapters.values import EnvFileConflict
from comparo.adapters.values import EnvironmentFileSource
from comparo.adapters.values import EnvironmentInlineValue
from comparo.adapters.values import EnvironmentSnapshotErrorCode
from comparo.adapters.values import EnvironmentSnapshotRequest
from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import RunEnvironmentSnapshotBuilder
from comparo.adapters.values.environment import capture_ambient_environment
from comparo.application.preflight import PreparedProjectPlan
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticContext
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import resource_spec
from comparo.core.resolution.models import EnvironmentSnapshot

_ENV_SHAPE = DiagnosticCode(value="environment.source.shape-invalid")


@final
class EnvironmentBindingResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly all selected Environment snapshots or value-free preflight diagnostics."""

    snapshots: tuple[EnvironmentSnapshot, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful and failed snapshot preparation structurally disjoint."""
        if type(self.snapshots) is not tuple or any(
            type(item) is not EnvironmentSnapshot for item in self.snapshots
        ):
            raise TypeError("Environment binding snapshots must be immutable and typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not Diagnostic for item in self.diagnostics
        ):
            raise TypeError("Environment binding diagnostics must be immutable and typed")
        if bool(self.snapshots) == bool(self.diagnostics):
            raise ValueError("Environment binding requires exactly snapshots or diagnostics")


async def build_environment_snapshots(
    project: PreparedProjectPlan,
    files: PinnedProjectFiles,
    *,
    ambient: tuple[tuple[str, str], ...] | None,
    limits: EnvironmentSourceLimits,
) -> EnvironmentBindingResult:
    """Validate every selected source before reading files, then build all snapshots."""
    if type(project) is not PreparedProjectPlan:
        raise TypeError("Environment binding requires PreparedProjectPlan")
    if type(files) is not PinnedProjectFiles:
        raise TypeError("Environment binding requires PinnedProjectFiles")
    if type(limits) is not EnvironmentSourceLimits:
        raise TypeError("Environment binding requires EnvironmentSourceLimits")
    lowered, diagnostics = _lower_requests(project)
    if diagnostics:
        return EnvironmentBindingResult(diagnostics=diagnostics)
    builder = RunEnvironmentSnapshotBuilder(
        files,
        project.policy,
        ambient=(ambient or ()) if any(item.inherit_process for item in lowered) else (),
        limits=limits,
    )
    snapshots, failures = await _build_requests(project, builder, lowered)
    if failures:
        return EnvironmentBindingResult(diagnostics=failures)
    return EnvironmentBindingResult(snapshots=snapshots)


def capture_process_environment(
    project: PreparedProjectPlan,
    limits: EnvironmentSourceLimits,
) -> tuple[tuple[str, str], ...]:
    """Capture selected process values once, before asynchronous execution can begin."""
    if type(project) is not PreparedProjectPlan:
        raise TypeError("process environment capture requires PreparedProjectPlan")
    if type(limits) is not EnvironmentSourceLimits:
        raise TypeError("process environment capture requires EnvironmentSourceLimits")
    lowered, _diagnostics = _lower_requests(project)
    if not any(item.inherit_process for item in lowered):
        return ()
    return capture_ambient_environment(os.environ, project.policy, limits=limits)


def validate_ambient_environment(
    entries: tuple[tuple[str, str], ...] | None,
    limits: EnvironmentSourceLimits,
) -> None:
    """Validate one already-frozen ambient snapshot before asynchronous execution starts."""
    if entries is None:
        return
    if type(entries) is not tuple or any(
        type(item) is not tuple
        or len(item) != 2
        or type(item[0]) is not str
        or type(item[1]) is not str
        for item in entries
    ):
        raise TypeError("ambient environment must be immutable text pairs")
    names = tuple(item[0] for item in entries)
    if names != tuple(sorted(set(names))):
        raise ValueError("ambient environment names must be unique and sorted")
    if len(entries) > limits.maximum_entries.value:
        raise ValueError("ambient environment exceeds its entry limit")
    total = 0
    for name, value in entries:
        encoded_value = value.encode("utf-8")
        if len(encoded_value) > limits.maximum_value_bytes.value:
            raise ValueError("ambient environment value exceeds its byte limit")
        total += len(name.encode("utf-8")) + len(encoded_value)
        if total > limits.maximum_total_bytes.value:
            raise ValueError("ambient environment exceeds its aggregate byte limit")


def _lower_requests(
    project: PreparedProjectPlan,
) -> tuple[tuple[EnvironmentSnapshotRequest, ...], tuple[Diagnostic, ...]]:
    lowered: list[EnvironmentSnapshotRequest] = []
    diagnostics: list[Diagnostic] = []
    for definition in project.prepared.effective.environments:
        resource = project.prepared.project.find(
            ResourceKind.ENVIRONMENT,
            definition.identity.resource_id,
        )
        if resource is None:  # pragma: no cover - planner/compiler coherence invariant
            raise RuntimeError("selected Environment is absent from the compiled project")
        request = _lower_environment(resource, project)
        if request is None:
            diagnostics.append(_diagnostic(_ENV_SHAPE, resource))
        else:
            lowered.append(request)
    return tuple(lowered), tuple(diagnostics)


async def _build_requests(
    project: PreparedProjectPlan,
    builder: RunEnvironmentSnapshotBuilder,
    lowered: tuple[EnvironmentSnapshotRequest, ...],
) -> tuple[tuple[EnvironmentSnapshot, ...], tuple[Diagnostic, ...]]:
    snapshots: list[EnvironmentSnapshot] = []
    diagnostics: list[Diagnostic] = []
    for request in lowered:
        result = await builder.build(request)
        if result.snapshot is None:
            resource = project.prepared.project.find(
                ResourceKind.ENVIRONMENT,
                request.environment.identity.resource_id,
            )
            if resource is None:  # pragma: no cover - lowering coherence invariant
                raise RuntimeError("lowered Environment is absent from the compiled project")
            diagnostics.append(_diagnostic(_snapshot_code(result.error), resource))
        else:
            snapshots.append(result.snapshot)
    return tuple(snapshots), tuple(diagnostics)


def _lower_environment(
    resource: CompiledResource,
    project: PreparedProjectPlan,
) -> EnvironmentSnapshotRequest | None:
    source = object_member(resource_spec(resource), "env")
    provenance = _provenance(resource)
    if source is None:
        return _with_invocation_values(
            EnvironmentSnapshotRequest(
                environment=resource.definition,
                configuration=provenance,
                policy=project.policy.definition,
            ),
            project,
        )
    if type(source) is not JsonObject:
        return None
    fields = {item.name: item.value for item in source.members}
    if not set(fields) <= {"inheritProcess", "files", "values"}:
        return None
    inherit = fields.get("inheritProcess", JsonBoolean(value=False))
    if type(inherit) is not JsonBoolean:
        return None
    file_sources = _files(fields.get("files"), provenance)
    inline_values = _values(fields.get("values"), provenance)
    if file_sources is None or inline_values is None:
        return None
    return _with_invocation_values(
        EnvironmentSnapshotRequest(
            environment=resource.definition,
            configuration=provenance,
            policy=project.policy.definition,
            inherit_process=inherit.value,
            files=file_sources,
            values=inline_values,
        ),
        project,
    )


def _files(
    value: object,
    provenance: ValueProvenance,
) -> tuple[EnvironmentFileSource, ...] | None:
    if value is None:
        return ()
    if type(value) is not JsonArray:
        return None
    sources: list[EnvironmentFileSource] = []
    for item in value.items:
        if type(item) is not JsonObject:
            return None
        fields = {member.name: member.value for member in item.members}
        path = fields.get("path")
        if not set(fields) <= {"path", "onConflict"} or type(path) is not JsonString:
            return None
        conflict = fields.get("onConflict", JsonString(value=EnvFileConflict.ERROR.value))
        if type(conflict) is not JsonString:
            return None
        try:
            mode = EnvFileConflict(conflict.value)
        except ValueError:
            return None
        sources.append(
            EnvironmentFileSource(
                path=path,
                configuration=provenance,
                on_conflict=mode,
            )
        )
    return tuple(sources)


def _values(
    value: object,
    provenance: ValueProvenance,
) -> tuple[EnvironmentInlineValue, ...] | None:
    if value is None:
        return ()
    if type(value) is not JsonObject:
        return None
    if any(type(member.value) is not JsonString for member in value.members):
        return None
    try:
        return tuple(
            EnvironmentInlineValue(
                symbol=SymbolPath(segments=(member.name,)),
                value=cast("JsonString", member.value),
                configuration=provenance,
            )
            for member in value.members
        )
    except ValueError:
        return None


def _with_invocation_values(
    request: EnvironmentSnapshotRequest,
    project: PreparedProjectPlan,
) -> EnvironmentSnapshotRequest:
    """Apply sealed caller values after declared sources without retaining duplicates."""
    environment = request.environment.identity.resource_id
    replacements = {
        item.symbol: item
        for item in project.prepared.invocation.environment_values
        if item.environment == environment
    }
    if not replacements:
        return request
    retained = tuple(item for item in request.values if item.symbol not in replacements)
    provenance = ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.INVOCATION),))
    invocation_values = tuple(
        EnvironmentInlineValue(
            symbol=item.symbol,
            value=item.value,
            configuration=provenance,
        )
        for item in sorted(replacements.values(), key=lambda item: item.symbol.segments)
    )
    return msgspec.structs.replace(request, values=(*retained, *invocation_values))


def _provenance(resource: CompiledResource) -> ValueProvenance:
    return ValueProvenance(
        entries=(
            ProvenanceEntry(
                kind=ProvenanceKind.PROJECT,
                resource=resource.definition.identity,
                location=resource.location,
            ),
        )
    )


def _snapshot_code(error: EnvironmentSnapshotErrorCode | None) -> DiagnosticCode:
    value = "internal" if error is None else error.value
    return DiagnosticCode(value=f"environment.snapshot.{value}")


def _diagnostic(code: DiagnosticCode, resource: CompiledResource) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.RESOLUTION,
        context=DiagnosticContext(
            resource=resource.definition.identity,
            location=resource.location,
        ),
    )


__all__ = (
    "EnvironmentBindingResult",
    "build_environment_snapshots",
    "capture_process_environment",
    "validate_ambient_environment",
)
