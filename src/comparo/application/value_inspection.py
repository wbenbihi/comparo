"""Resolve reusable Values directly, without preparing executable consumers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import msgspec

from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import ProjectFileValueProvider
from comparo.adapters.values import RunEnvironmentSnapshotBuilder
from comparo.adapters.values import SafeYamlValueDecoder
from comparo.adapters.values import SecureGenerator
from comparo.adapters.values import SystemRunClock
from comparo.adapters.values.environment import capture_ambient_environment
from comparo.application.environment_binding import lower_environment_source
from comparo.application.inspection import ResourceCoordinate
from comparo.application.preflight import _load_policy
from comparo.application.preflight import resolve_project_root
from comparo.application.production_execution import ProductionExecutionSettings
from comparo.application.project_workspace import ProjectWorkspaceRequest
from comparo.application.resolved_inspection import ResolvedValueFormat
from comparo.application.resolved_inspection import ResolvedValueNode
from comparo.application.resolved_inspection import ResolvedValueProjectionLimits
from comparo.application.resolved_inspection import _coordinate
from comparo.application.resolved_inspection import _InspectionResources
from comparo.application.resolved_inspection import _masked_nodes
from comparo.application.resolved_inspection import _project_public_value
from comparo.application.resolved_inspection import _ResolvedValueInspectionSettings
from comparo.configuration.loader import compile_project_path
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import BytesValue
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import resource_spec
from comparo.core.planning.binding import _append_value_layer
from comparo.core.planning.matrices import compile_matrix
from comparo.core.planning.models import PlanningSource
from comparo.core.planning.models import ValueBindingLayer
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import MaskedPresent
from comparo.core.resolution import MaskedSensitiveValue
from comparo.core.resolution import MaskedValueResolver
from comparo.core.resolution import ResolutionTarget
from comparo.core.resolution import RunContext
from comparo.core.resolution.inputs import ResolutionInputs
from comparo.core.resolution.models import EnvironmentSnapshot
from comparo.policy import PolicyLoadLimits


class ValueInspectionField(msgspec.Struct, frozen=True, kw_only=True):
    """One projected node or a local unresolved expression, never a live value."""

    path: tuple[str, ...]
    node: ResolvedValueNode | None = None
    unavailable: bool = False


class MatrixCaseWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded collection of data cases, independent of consumers."""

    cases: tuple[str, ...]
    total: int
    start: int
    selected: str


class ValueInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Safe standalone Value result with no execution occurrence coordinates."""

    fields: tuple[ValueInspectionField, ...] = ()
    diagnostics: tuple[str, ...] = ()
    incomplete: bool = False
    matrix: MatrixCaseWindow | None = None


def _layers(
    project: CompiledProject,
    environment: CompiledResource | None,
) -> tuple[ValueBindingLayer, ...]:
    layers: list[ValueBindingLayer] = []
    spec = resource_spec(project.project)
    defaults = object_member(spec, "defaults")
    source = PlanningSource(kind=ProvenanceKind.PROJECT, definition=project.project.definition)
    _append_value_layer(layers, source=source, spec=spec)
    if type(defaults) is CanonicalObject:
        _append_value_layer(layers, source=source, spec=defaults)
    if environment is not None:
        _append_value_layer(
            layers,
            source=PlanningSource(kind=ProvenanceKind.PROJECT, definition=environment.definition),
            environment=environment.definition,
            spec=resource_spec(environment),
        )
    return tuple(layers)


async def inspect_value(
    workspace: ProjectWorkspaceRequest,
    resource: ResourceCoordinate,
    environment: ResourceCoordinate | None,
    *,
    policy_path: Path | None,
    policy_limits: PolicyLoadLimits,
    settings: ProductionExecutionSettings,
    limits: ResolvedValueProjectionLimits,
    case_id: str | None = None,
    start_case: int = 0,
) -> ValueInspection:
    """Resolve a Value in its Project and active Environment, masking at egress."""
    resources = _InspectionResources()
    try:
        loaded = await asyncio.to_thread(
            compile_project_path,
            workspace.config,
            additional_sources=workspace.additional_sources,
            limits=workspace.load_limits,
        )
        project = loaded.compilation.project
        if project is None:
            return ValueInspection(diagnostics=("value.configuration.invalid",))
        selected = next((item for item in project.resources if _coordinate(item) == resource), None)
        env = next((item for item in project.resources if _coordinate(item) == environment), None)
        if selected is None or resource.kind not in {"Value", "Matrix"}:
            return ValueInspection(diagnostics=("value.resource.changed",))
        if environment is not None and (env is None or environment.kind != "Environment"):
            return ValueInspection(diagnostics=("value.environment.changed",))
        policy = await asyncio.to_thread(_load_policy, policy_path, policy_limits)
        if not isinstance(policy, CompiledExecutionPolicy):
            return ValueInspection(diagnostics=("value.policy.invalid",))
        expression, matrix = _expression(selected, case_id, start_case)
        if expression is None:
            return ValueInspection(diagnostics=("value.expression.invalid",))
        resources.files = PinnedProjectFiles(
            resolve_project_root(workspace.config), limits=settings.files
        )
        snapshots = await _snapshots(env, policy, resources.files, settings)
        if snapshots is None:
            return ValueInspection(diagnostics=("value.environment.unavailable",))
        run = RunScope(run_id=RunId(value=uuid4()))
        resources.context = RunContext(
            prepared=ResolutionInputs(
                run=run,
                policy=policy.definition,
                environments=() if env is None else (env.definition,),
                value_layers=_layers(project, env),
                standalone=True,
            ),
            environment_snapshots=snapshots,
            clock=SystemRunClock(),
            generator=DeterministicGenerator(
                SensitiveValue(
                    BytesValue(value=run.run_id.value.bytes),
                    origins=(TaintOriginId(value="runtime/value-inspection-seed"),),
                )
            ),
            secure_generator=SecureGenerator(),
            file_provider=ProjectFileValueProvider(resources.files, policy),
            yaml_decoder=SafeYamlValueDecoder(settings.yaml),
        )
        projection = _ResolvedValueInspectionSettings(
            files=settings.files,
            environment=settings.environment,
            yaml=settings.yaml,
            **{name: getattr(limits, name) for name in limits.__struct_fields__},
        )
        target = ResolutionTarget(
            symbol=SymbolPath(segments=("value",)),
            scope=run
            if env is None
            else EnvironmentScope(run=run, environment=env.definition.identity),
        )
        result = await _inspect_expression(expression, resources.context, target, projection)
        return msgspec.structs.replace(result, matrix=matrix)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - never return source-bearing exception text
        return ValueInspection(diagnostics=("value.inspection.failed",))
    finally:
        await resources.close()


def _expression(
    resource: CompiledResource,
    case_id: str | None,
    start: int,
) -> tuple[ValueExpression | None, MatrixCaseWindow | None]:
    if resource.definition.identity.kind is ResourceKind.MATRIX:
        matrix = compile_matrix(
            resource,
            MatrixBindingId(value="explorer"),
            maximum_cases=10_000,
        )
        start = min(max(0, start), ((len(matrix.cases) - 1) // 50) * 50)
        page = matrix.cases[start : start + 50]
        selected = next((case for case in page if case.case_id.value == case_id), page[0])
        return selected.values, MatrixCaseWindow(
            cases=tuple(case.case_id.value for case in page),
            total=len(matrix.cases),
            start=start,
            selected=selected.case_id.value,
        )
    value = object_member(resource_spec(resource), "value")
    return (value if is_value_expression(value) else None), None


async def _snapshots(
    environment: CompiledResource | None,
    policy: CompiledExecutionPolicy,
    files: PinnedProjectFiles,
    settings: ProductionExecutionSettings,
) -> tuple[EnvironmentSnapshot, ...] | None:
    if environment is None:
        return ()
    source = lower_environment_source(environment, policy.definition)
    if source is None:
        return None
    ambient = (
        capture_ambient_environment(os.environ, policy, limits=settings.environment)
        if source.inherit_process
        else ()
    )
    result = await RunEnvironmentSnapshotBuilder(
        files,
        policy,
        ambient=ambient,
        limits=settings.environment,
    ).build(source)
    return None if result.snapshot is None else (result.snapshot,)


async def _inspect_expression(
    expression: ValueExpression,
    context: RunContext,
    target: ResolutionTarget,
    settings: _ResolvedValueInspectionSettings,
) -> ValueInspection:
    """Resolve siblings independently so a secret or missing input cannot hide the document."""
    fields: list[ValueInspectionField] = []
    pending: list[tuple[ValueExpression, tuple[str, ...]]] = [(expression, ("value",))]
    resolver = MaskedValueResolver(context)
    incomplete = False
    while pending and len(fields) < settings.maximum_page_nodes.value:
        current, path = pending.pop()
        if len(path) > settings.maximum_path_segments.value or any(
            len(segment.encode()) > settings.maximum_path_segment_bytes.value for segment in path
        ):
            incomplete = True
            continue
        children: tuple[tuple[ValueExpression, tuple[str, ...]], ...] = ()
        if type(current) is ObjectExpr:
            children = tuple(
                (member.expression, (*path, member.name)) for member in current.members
            )
        elif type(current) is ArrayExpr:
            children = tuple(
                (item, (*path, str(index))) for index, item in enumerate(current.items)
            )
        if type(current) in {ObjectExpr, ArrayExpr}:
            node = ResolvedValueNode(
                ordinal=len(fields),
                path=(),
                kind=ValueKind.OBJECT if type(current) is ObjectExpr else ValueKind.ARRAY,
                format=ResolvedValueFormat.NONE,
                preview=None,
                byte_count=None,
                child_count=len(children),
                truncated=False,
                masked=False,
            )
            fields.append(ValueInspectionField(path=path, node=node))
            # Bound work before materializing a large pending traversal.
            remaining = settings.maximum_page_nodes.value - len(fields)
            incomplete |= len(children) > remaining
            pending.extend(reversed(children[:remaining]))
            continue
        result = await resolver.resolve(
            current,
            msgspec.structs.replace(
                target,
                symbol=SymbolPath(segments=tuple("p" + part.encode().hex() for part in path)),
            ),
        )
        if type(result) is not MaskedPresent:
            fields.append(ValueInspectionField(path=path, unavailable=True))
            continue
        if type(result.value) is MaskedSensitiveValue:
            nodes = tuple(_masked_nodes(result.value))
        else:
            public = await _project_public_value(
                result.value,
                settings,
                start=0,
                maximum=settings.maximum_page_nodes.value - len(fields),
            )
            incomplete |= public.next_node is not None
            nodes = public.nodes
        fields.extend(ValueInspectionField(path=(*path, *node.path), node=node) for node in nodes)
        await asyncio.sleep(0)
    return ValueInspection(fields=tuple(fields), incomplete=incomplete or bool(pending))
