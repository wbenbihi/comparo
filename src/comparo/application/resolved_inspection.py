"""Bounded, irreversibly masked inspection of run-scoped value expressions."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from decimal import Decimal
from typing import Protocol
from typing import cast
from typing import final
from uuid import uuid5

import msgspec

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import LocalFileLimits
from comparo.adapters.values import LocalFilesystemError
from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import ProjectFileValueProvider
from comparo.adapters.values import SafeYamlValueDecoder
from comparo.adapters.values import SecureGenerator
from comparo.adapters.values import SystemRunClock
from comparo.adapters.values import YamlValueLimits
from comparo.application.environment_binding import build_environment_snapshots
from comparo.application.environment_binding import validate_ambient_environment
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.inspection import inspect_diagnostic
from comparo.application.plan_inspection_identity import is_sha256
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.runtime_capabilities import preflight_runtime_capabilities
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import Unavailable
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import EnvironmentSnapshot
from comparo.core.resolution import MaskedPresent
from comparo.core.resolution import MaskedSensitiveValue
from comparo.core.resolution import MaskedValueResolver
from comparo.core.resolution import ResolutionTarget
from comparo.core.resolution import RunContext
from comparo.core.resolution import cell_inputs
from comparo.core.runtime import effective_request_expression
from comparo.core.runtime import execution_work_at
from comparo.core.runtime.models import ExecutionWork

_INSPECTION_SEED_ORIGIN = TaintOriginId(value="runtime/resolved-inspection-seed")


class ResolvedInspectionState(DomainStrEnum):
    """Closed result categories returned by masked expression inspection."""

    PRESENT = "present"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"
    ERROR = "error"
    BLOCKED = "blocked"


class ResolvedValueFormat(DomainStrEnum):
    """How a bounded node preview should be interpreted by a frontend."""

    NONE = "none"
    NULL = "null"
    TEXT = "text"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    HEX = "hex"
    NANOSECONDS = "nanoseconds"
    BYTES = "bytes"
    MASKED = "masked"


class ResolvedInputTiming(DomainStrEnum):
    """When one local value source is sampled relative to inspection."""

    FROZEN_REQUEST = "frozen-request"
    INSPECTION_TIME = "inspection-time"
    RESAMPLED_AT_EXECUTION_START = "resampled-at-execution-start"


@final
class ResolvedValueFreshness(msgspec.Struct, frozen=True, kw_only=True):
    """Machine-readable honesty contract for advisory resolved-value inspection."""

    plan_checkpoint_exact: bool = True
    ambient_environment: ResolvedInputTiming = ResolvedInputTiming.FROZEN_REQUEST
    dotenv_files: ResolvedInputTiming = ResolvedInputTiming.INSPECTION_TIME
    project_files: ResolvedInputTiming = ResolvedInputTiming.INSPECTION_TIME
    generated_values: ResolvedInputTiming = ResolvedInputTiming.INSPECTION_TIME
    execution_local_sources: ResolvedInputTiming = ResolvedInputTiming.RESAMPLED_AT_EXECUTION_START
    execution_generated_values: ResolvedInputTiming = (
        ResolvedInputTiming.RESAMPLED_AT_EXECUTION_START
    )

    def __post_init__(self) -> None:
        """Prevent callers from weakening the version-owned freshness statement."""
        if self.plan_checkpoint_exact is not True:
            raise ValueError("resolved inspection plan checkpoint must remain exact")
        expected = (
            (self.ambient_environment, ResolvedInputTiming.FROZEN_REQUEST),
            (self.dotenv_files, ResolvedInputTiming.INSPECTION_TIME),
            (self.project_files, ResolvedInputTiming.INSPECTION_TIME),
            (self.generated_values, ResolvedInputTiming.INSPECTION_TIME),
            (
                self.execution_local_sources,
                ResolvedInputTiming.RESAMPLED_AT_EXECUTION_START,
            ),
            (
                self.execution_generated_values,
                ResolvedInputTiming.RESAMPLED_AT_EXECUTION_START,
            ),
        )
        if any(actual is not required for actual, required in expected):
            raise ValueError("resolved inspection source timing is version-owned")


@final
class ResolvedValueInspectionSettings(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned local-source and frontend projection ceilings."""

    files: LocalFileLimits
    environment: EnvironmentSourceLimits
    yaml: YamlValueLimits
    maximum_page_nodes: PositiveCount
    maximum_scalar_bytes: ByteCount
    maximum_path_segments: PositiveCount
    maximum_path_segment_bytes: ByteCount

    def __post_init__(self) -> None:
        """Require explicit typed limits for every effect and returned value."""
        expected = (
            (self.files, LocalFileLimits),
            (self.environment, EnvironmentSourceLimits),
            (self.yaml, YamlValueLimits),
            (self.maximum_page_nodes, PositiveCount),
            (self.maximum_scalar_bytes, ByteCount),
            (self.maximum_path_segments, PositiveCount),
            (self.maximum_path_segment_bytes, ByteCount),
        )
        if any(type(value) is not kind for value, kind in expected):
            raise TypeError("resolved inspection settings contain an untyped limit")
        if self.maximum_scalar_bytes.value < 1 or self.maximum_path_segment_bytes.value < 1:
            raise ValueError("resolved inspection byte limits must be positive")


@final
class ResolvedValueProjectionLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Frontend-owned ceilings independent from local value-source adapters."""

    maximum_page_nodes: PositiveCount
    maximum_scalar_bytes: ByteCount
    maximum_path_segments: PositiveCount
    maximum_path_segment_bytes: ByteCount

    def __post_init__(self) -> None:
        """Require exact positive limits for every returned structure."""
        expected = (
            (self.maximum_page_nodes, PositiveCount),
            (self.maximum_scalar_bytes, ByteCount),
            (self.maximum_path_segments, PositiveCount),
            (self.maximum_path_segment_bytes, ByteCount),
        )
        if any(type(value) is not kind for value, kind in expected):
            raise TypeError("resolved projection limits contain an untyped limit")
        if self.maximum_scalar_bytes.value < 1 or self.maximum_path_segment_bytes.value < 1:
            raise ValueError("resolved projection byte limits must be positive")


@final
class ResolvedValueInspectionTarget(msgspec.Struct, frozen=True, kw_only=True):
    """Frontend-safe selection resolved later through an opaque Compare handle."""

    resource: ResourceCoordinate
    field_path: tuple[str, ...]
    execution_ordinal: int
    environment: ResourceCoordinate
    limits: ResolvedValueProjectionLimits
    start_node: int
    maximum_nodes: int
    expected_value_fingerprint: str | None = None

    def __post_init__(self) -> None:
        """Validate the whole selection without retaining a plan or local values."""
        if type(self.resource) is not ResourceCoordinate:
            raise TypeError("resolved inspection target resource must be ResourceCoordinate")
        if type(self.environment) is not ResourceCoordinate:
            raise TypeError("resolved inspection target Environment must be ResourceCoordinate")
        if self.environment.kind != ResourceKind.ENVIRONMENT.value:
            raise ValueError("resolved inspection target Environment must identify Environment")
        if type(self.limits) is not ResolvedValueProjectionLimits:
            raise TypeError("resolved inspection target limits must be typed")
        _validate_selection(
            field_path=self.field_path,
            execution_ordinal=self.execution_ordinal,
            start_node=self.start_node,
            maximum_nodes=self.maximum_nodes,
            maximum_page_nodes=self.limits.maximum_page_nodes.value,
            maximum_path_segments=self.limits.maximum_path_segments.value,
            maximum_path_segment_bytes=self.limits.maximum_path_segment_bytes.value,
            expected_value_fingerprint=self.expected_value_fingerprint,
        )


@final
class ResolvedValueInspectionRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One exact compiled expression and execution occurrence to inspect."""

    project: PreparedProjectPlan
    resource: ResourceCoordinate
    field_path: tuple[str, ...]
    execution_ordinal: int
    environment: ResourceCoordinate
    settings: ResolvedValueInspectionSettings
    start_node: int
    maximum_nodes: int
    ambient_environment: tuple[tuple[str, str], ...] | None = None
    expected_value_fingerprint: str | None = None

    def __post_init__(self) -> None:
        """Validate finite coordinates before any local source can be opened."""
        if type(self.project) is not PreparedProjectPlan:
            raise TypeError("resolved inspection requires PreparedProjectPlan")
        if type(self.resource) is not ResourceCoordinate:
            raise TypeError("resolved inspection resource must be ResourceCoordinate")
        if type(self.environment) is not ResourceCoordinate:
            raise TypeError("resolved inspection Environment must be ResourceCoordinate")
        if self.environment.kind != ResourceKind.ENVIRONMENT.value:
            raise ValueError("resolved inspection Environment coordinate must identify Environment")
        if type(self.settings) is not ResolvedValueInspectionSettings:
            raise TypeError("resolved inspection requires typed settings")
        _validate_selection(
            field_path=self.field_path,
            execution_ordinal=self.execution_ordinal,
            start_node=self.start_node,
            maximum_nodes=self.maximum_nodes,
            maximum_page_nodes=self.settings.maximum_page_nodes.value,
            maximum_path_segments=self.settings.maximum_path_segments.value,
            maximum_path_segment_bytes=self.settings.maximum_path_segment_bytes.value,
            expected_value_fingerprint=self.expected_value_fingerprint,
        )
        validate_ambient_environment(self.ambient_environment, self.settings.environment)


@final
class ResolvedExecutionCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """Safe exact occurrence identity used for one expression resolution."""

    ordinal: int
    execution_id: str
    attempt_id: str
    trial_id: str
    cell_id: str
    subject: ResourceCoordinate
    environment: ResourceCoordinate

    def __post_init__(self) -> None:
        """Require complete safe coordinates without retaining runtime scope objects."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("resolved execution ordinal must be non-negative")
        if any(
            type(value) is not str or not value
            for value in (self.execution_id, self.attempt_id, self.trial_id, self.cell_id)
        ):
            raise ValueError("resolved execution identities must be non-empty text")
        if (
            type(self.subject) is not ResourceCoordinate
            or type(self.environment) is not ResourceCoordinate
        ):
            raise TypeError("resolved execution resources must be safe coordinates")


@final
class ResolutionProvenanceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Bounded value-free summary of the core resolver's exact provenance."""

    provider_namespace: str
    provider_name: str
    provider_version: str
    namespace: str | None
    terminal_outcome: str
    source_attempts: int

    def __post_init__(self) -> None:
        """Retain only provider identity, outcome, namespace, and attempt count."""
        if any(
            type(value) is not str or not value
            for value in (
                self.provider_namespace,
                self.provider_name,
                self.provider_version,
                self.terminal_outcome,
            )
        ):
            raise ValueError("resolution provenance identity fields must be non-empty text")
        if self.namespace is not None and (type(self.namespace) is not str or not self.namespace):
            raise ValueError("resolution provenance namespace must be text or absent")
        if type(self.source_attempts) is not int or self.source_attempts < 1:
            raise ValueError("resolution provenance must contain at least one source attempt")


@final
class ResolvedValueNode(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded serializable node in a pre-order resolved-value window."""

    ordinal: int
    path: tuple[str, ...]
    kind: ValueKind
    format: ResolvedValueFormat
    preview: str | None
    byte_count: int | None
    child_count: int
    truncated: bool
    masked: bool

    def __post_init__(self) -> None:  # noqa: C901 - closed projection-shape validation
        """Reject ambiguous node shapes and any untyped frontend payload."""
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("resolved value node ordinal must be non-negative")
        if type(self.path) is not tuple or any(type(item) is not str for item in self.path):
            raise TypeError("resolved value node path must be immutable text")
        if type(self.kind) is not ValueKind or self.kind is ValueKind.UNKNOWN:
            raise ValueError("resolved value node kind must be exact")
        if type(self.format) is not ResolvedValueFormat:
            raise TypeError("resolved value node format must be typed")
        if self.preview is not None and type(self.preview) is not str:
            raise TypeError("resolved value node preview must be text or absent")
        if self.byte_count is not None and (
            type(self.byte_count) is not int or self.byte_count < 0
        ):
            raise ValueError("resolved value node byte count must be non-negative or absent")
        if type(self.child_count) is not int or self.child_count < 0:
            raise ValueError("resolved value node child count must be non-negative")
        if type(self.truncated) is not bool or type(self.masked) is not bool:
            raise TypeError("resolved value node disclosure flags must be bool")
        if self.masked != (self.format is ResolvedValueFormat.MASKED):
            raise ValueError("resolved value masked flag contradicts its format")
        if self.masked and self.preview != "<masked>":
            raise ValueError("masked resolved value nodes require the version-owned marker")
        container = self.kind in {ValueKind.ARRAY, ValueKind.OBJECT}
        if not self.masked and container != (self.format is ResolvedValueFormat.NONE):
            raise ValueError("resolved value node container metadata contradicts its kind")
        if (
            not self.masked
            and container
            and (self.preview is not None or self.byte_count is not None)
        ):
            raise ValueError("resolved value containers cannot retain scalar previews")
        if not container and self.child_count:
            raise ValueError("resolved value scalar cannot retain child metadata")


@final
class ResolvedValueInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One safe outcome for an exact expression and execution occurrence."""

    resource: ResourceCoordinate
    field_path: tuple[str, ...]
    execution: ResolvedExecutionCoordinate
    state: ResolvedInspectionState
    provenance: ResolutionProvenanceInspection
    value_fingerprint: str
    freshness: ResolvedValueFreshness = ResolvedValueFreshness()
    code: str | None = None
    kind: ValueKind | None = None
    masked: bool = False
    taint_origins: tuple[str, ...] = ()
    start_node: int = 0
    nodes: tuple[ResolvedValueNode, ...] = ()
    next_node: int | None = None

    def __post_init__(self) -> None:  # noqa: C901 - exact present/non-present union
        """Keep present windows and value-free non-present outcomes disjoint."""
        if type(self.resource) is not ResourceCoordinate:
            raise TypeError("resolved value inspection resource must be ResourceCoordinate")
        if type(self.field_path) is not tuple or any(
            type(item) is not str or not item for item in self.field_path
        ):
            raise TypeError("resolved value inspection path must contain non-empty text")
        if type(self.execution) is not ResolvedExecutionCoordinate:
            raise TypeError("resolved value inspection execution must be typed")
        if type(self.state) is not ResolvedInspectionState:
            raise TypeError("resolved value inspection state must be typed")
        if type(self.provenance) is not ResolutionProvenanceInspection:
            raise TypeError("resolved value inspection provenance must be typed")
        if not is_sha256(self.value_fingerprint):
            raise ValueError("resolved value inspection fingerprint must be SHA-256")
        if type(self.freshness) is not ResolvedValueFreshness:
            raise TypeError("resolved value inspection freshness must be typed")
        if self.code is not None and (type(self.code) is not str or not self.code):
            raise ValueError("resolved value inspection code must be text or absent")
        if self.kind is not None and (
            type(self.kind) is not ValueKind or self.kind is ValueKind.UNKNOWN
        ):
            raise ValueError("resolved value inspection kind must be exact or absent")
        if type(self.masked) is not bool:
            raise TypeError("resolved value inspection masked flag must be bool")
        if type(self.taint_origins) is not tuple or any(
            type(item) is not str or not item for item in self.taint_origins
        ):
            raise TypeError("resolved value taint origins must be immutable non-empty text")
        if len(self.taint_origins) != len(set(self.taint_origins)):
            raise ValueError("resolved value taint origins must be unique")
        if type(self.start_node) is not int or self.start_node < 0:
            raise ValueError("resolved value inspection cursor must be non-negative")
        if type(self.nodes) is not tuple or any(
            type(item) is not ResolvedValueNode for item in self.nodes
        ):
            raise TypeError("resolved value inspection nodes must be immutable and typed")
        ordinals = tuple(item.ordinal for item in self.nodes)
        if ordinals != tuple(range(self.start_node, self.start_node + len(self.nodes))):
            raise ValueError("resolved value inspection nodes must be a contiguous page")
        if self.next_node is not None and (
            type(self.next_node) is not int or self.next_node != self.start_node + len(self.nodes)
        ):
            raise ValueError("resolved value continuation must follow the returned page")
        present = self.state is ResolvedInspectionState.PRESENT
        if present:
            if self.code is not None or self.kind is None:
                raise ValueError("present resolved inspection requires kind and no failure code")
            if self.masked != bool(self.taint_origins):
                raise ValueError("masked resolved inspection requires safe taint origins")
        elif (
            self.kind is not None
            or self.masked
            or self.taint_origins
            or self.nodes
            or self.next_node is not None
        ):
            raise ValueError("non-present resolved inspection cannot retain value products")
        if not present:
            missing = self.state is ResolvedInspectionState.MISSING
            if missing != (self.code is None):
                raise ValueError("resolved inspection failure code contradicts its state")


@final
class ResolvedValueInspectionResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one masked inspection product or value-free diagnostics."""

    inspection: ResolvedValueInspection | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful and rejected application outcomes structurally disjoint."""
        if self.inspection is not None and type(self.inspection) is not ResolvedValueInspection:
            raise TypeError("resolved value result inspection must be typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("resolved value result diagnostics must be immutable and typed")
        if (self.inspection is None) == bool(self.diagnostics):
            return
        raise ValueError("resolved value result requires exactly inspection or diagnostics")


def bind_resolved_value_target(
    project: PreparedProjectPlan,
    target: ResolvedValueInspectionTarget,
    *,
    files: LocalFileLimits,
    environment: EnvironmentSourceLimits,
    yaml: YamlValueLimits,
    ambient_environment: tuple[tuple[str, str], ...],
) -> ResolvedValueInspectionRequest:
    """Bind a frontend-safe target to one internally retained prepared-plan lifetime."""
    if type(project) is not PreparedProjectPlan:
        raise TypeError("resolved target binding requires PreparedProjectPlan")
    if type(target) is not ResolvedValueInspectionTarget:
        raise TypeError("resolved target binding requires ResolvedValueInspectionTarget")
    return ResolvedValueInspectionRequest(
        project=project,
        resource=target.resource,
        field_path=target.field_path,
        execution_ordinal=target.execution_ordinal,
        environment=target.environment,
        settings=ResolvedValueInspectionSettings(
            files=files,
            environment=environment,
            yaml=yaml,
            maximum_page_nodes=target.limits.maximum_page_nodes,
            maximum_scalar_bytes=target.limits.maximum_scalar_bytes,
            maximum_path_segments=target.limits.maximum_path_segments,
            maximum_path_segment_bytes=target.limits.maximum_path_segment_bytes,
        ),
        start_node=target.start_node,
        maximum_nodes=target.maximum_nodes,
        ambient_environment=ambient_environment,
        expected_value_fingerprint=target.expected_value_fingerprint,
    )


def _validate_selection(
    *,
    field_path: tuple[str, ...],
    execution_ordinal: int,
    start_node: int,
    maximum_nodes: int,
    maximum_page_nodes: int,
    maximum_path_segments: int,
    maximum_path_segment_bytes: int,
    expected_value_fingerprint: str | None,
) -> None:
    if type(field_path) is not tuple or any(
        type(segment) is not str or not segment for segment in field_path
    ):
        raise TypeError("resolved inspection field path must contain non-empty text")
    if len(field_path) > maximum_path_segments or any(
        len(segment.encode("utf-8", errors="replace")) > maximum_path_segment_bytes
        for segment in field_path
    ):
        raise ValueError("resolved inspection field path exceeds its caller-owned limit")
    if type(execution_ordinal) is not int or execution_ordinal < 0:
        raise ValueError("resolved inspection execution ordinal must be non-negative")
    if type(start_node) is not int or start_node < 0:
        raise ValueError("resolved inspection node cursor must be non-negative")
    if type(maximum_nodes) is not int or not 1 <= maximum_nodes <= maximum_page_nodes:
        raise ValueError("resolved inspection page size exceeds its caller-owned limit")
    if expected_value_fingerprint is not None and not is_sha256(expected_value_fingerprint):
        raise ValueError("resolved inspection expected fingerprint must be SHA-256")


class _InspectionResources:
    __slots__ = ("context", "files")

    def __init__(self) -> None:
        self.files: PinnedProjectFiles | None = None
        self.context: RunContext | None = None

    async def close(self) -> bool:
        """Close every acquired live resource and retain only failure state."""
        failed = False
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:  # noqa: BLE001 - cleanup defects never cross the safe boundary
                failed = True
        if self.files is not None:
            try:
                self.files.close()
            except Exception:  # noqa: BLE001 - independent cleanup remains best effort
                failed = True
        return failed


class _ProjectionBudgetExceededError(Exception):
    """Private control flow for one caller-owned projection ceiling."""


class _Digest(Protocol):
    def update(self, value: bytes, /) -> None:
        """Consume one immutable digest fragment."""


@final
class _Selection(msgspec.Struct, frozen=True, kw_only=True):
    expression: ValueExpression
    work: ExecutionWork


async def inspect_resolved_value(
    request: ResolvedValueInspectionRequest,
) -> ResolvedValueInspectionResult:
    """Resolve one exact compiled expression through the shared masked resolver."""
    if type(request) is not ResolvedValueInspectionRequest:
        raise TypeError("resolved value inspection requires ResolvedValueInspectionRequest")
    capabilities = preflight_runtime_capabilities(request.project)
    if capabilities.diagnostics:
        return ResolvedValueInspectionResult(
            diagnostics=tuple(inspect_diagnostic(item) for item in capabilities.diagnostics)
        )
    selected = _select(request)
    if type(selected) is ApplicationDiagnostic:
        return ResolvedValueInspectionResult(diagnostics=(selected,))
    expression, work = selected.expression, selected.work
    resources = _InspectionResources()
    try:
        result = await _inspect_with_resources(request, expression, work, resources)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - live defects become one closed application diagnostic
        result = _failure("resolution.inspect.internal")
    finally:
        cleanup_failed = await resources.close()
    if cleanup_failed:
        return _failure("resolution.inspect.cleanup-failed")
    return result


async def _inspect_with_resources(
    request: ResolvedValueInspectionRequest,
    expression: ValueExpression,
    work: ExecutionWork,
    resources: _InspectionResources,
) -> ResolvedValueInspectionResult:
    try:
        resources.files = PinnedProjectFiles(
            request.project.project_root,
            limits=request.settings.files,
        )
    except LocalFilesystemError:
        return _failure("resolution.inspect.project-root-unavailable")
    snapshots = await build_environment_snapshots(
        request.project,
        resources.files,
        ambient=request.ambient_environment,
        limits=request.settings.environment,
    )
    if snapshots.diagnostics:
        return ResolvedValueInspectionResult(
            diagnostics=tuple(inspect_diagnostic(item) for item in snapshots.diagnostics)
        )
    resources.context = _run_context(request, snapshots.snapshots, resources.files)
    attempt = _attempt_scope(work)
    target = ResolutionTarget(
        symbol=_target_symbol(request),
        scope=attempt,
        cell_inputs=cell_inputs(request.project.prepared, work.trial.cell),
    )
    result = await MaskedValueResolver(resources.context).resolve(expression, target)
    fingerprint = _result_fingerprint(result)
    if (
        request.expected_value_fingerprint is not None
        and request.expected_value_fingerprint != fingerprint
    ):
        return _failure("resolution.inspect.value-changed")
    try:
        inspection = _project_result(request, work, attempt, result, fingerprint)
    except _ProjectionBudgetExceededError:
        return _failure("resolution.inspect.projection-budget-exceeded")
    return ResolvedValueInspectionResult(inspection=inspection)


def _select(
    request: ResolvedValueInspectionRequest,
) -> _Selection | ApplicationDiagnostic:
    resource = _find_resource(request.project, request.resource)
    if resource is None:
        return _request_diagnostic(request, "resolution.inspect.resource-not-found")
    work = execution_work_at(
        request.project.prepared,
        request.execution_ordinal,
    )
    if work is None:
        return _request_diagnostic(request, "resolution.inspect.execution-not-found")
    if _coordinate(work.environment) != request.environment:
        return _request_diagnostic(request, "resolution.inspect.environment-mismatch")
    node = resource.spec
    path = request.field_path
    if resource.definition.identity.kind is ResourceKind.REQUEST:
        if resource.definition != work.subject:
            return _request_diagnostic(request, "resolution.inspect.subject-mismatch")
        if path and path[0] == "request":
            authored = _child(node, "request")
            if type(authored) is not ObjectExpr:  # pragma: no cover - compiler invariant
                return _request_diagnostic(request, "resolution.inspect.expression-not-found")
            node = effective_request_expression(request.project.prepared, work, authored)
            path = path[1:]
    expression = _expression_at(node, path)
    if expression is None:
        return _request_diagnostic(request, "resolution.inspect.expression-not-found")
    return _Selection(expression=expression, work=work)


def _find_resource(
    project: PreparedProjectPlan,
    coordinate: ResourceCoordinate,
) -> CompiledResource | None:
    resources = (project.prepared.project.project, *project.prepared.project.resources)
    return next((item for item in resources if _coordinate(item.definition) == coordinate), None)


def _expression_at(node: object, path: tuple[str, ...]) -> ValueExpression | None:
    current = node
    for segment in path:
        selected = _child(current, segment)
        if selected is None:
            return None
        current = selected
    return current if is_value_expression(current) else None


def _child(node: object, segment: str) -> object | None:  # noqa: C901 - closed graph traversal
    if type(node) is CanonicalObject:
        return next((item.value for item in node.members if item.name == segment), None)
    if type(node) is ObjectExpr:
        return next((item.expression for item in node.members if item.name == segment), None)
    if type(node) is CanonicalArray:
        index = _index(segment)
        return node.items[index] if index is not None and index < len(node.items) else None
    if type(node) is ArrayExpr:
        index = _index(segment)
        return node.items[index] if index is not None and index < len(node.items) else None
    if type(node) is InlineResource:
        return node.spec if segment == "spec" else None
    if type(node) is ConvertExpr:
        return node.source if segment == "source" else None
    if type(node) is OptionalExpr:
        return node.source if segment == "source" else None
    if type(node) is FileExpr:
        return node.path if segment == "path" else None
    if type(node) is FallbackExpr:
        index = _index(segment)
        return node.sources[index] if index is not None and index < len(node.sources) else None
    if type(node) is InterpolationExpr:
        for index, item in enumerate(node.segments):
            if type(item) is ExpressionSegment and segment == f"expression-{index + 1}":
                return item.expression
    return None


def _index(segment: str) -> int | None:
    if not segment.isascii() or not segment.isdigit():
        return None
    value = int(segment)
    return value if str(value) == segment else None


def _run_context(
    request: ResolvedValueInspectionRequest,
    snapshots: tuple[EnvironmentSnapshot, ...],
    files: PinnedProjectFiles,
) -> RunContext:
    material = (
        "comparo.resolved-inspection/v1alpha1:"
        f"{request.project.prepared.plan.run_id.value}:"
        f"{request.project.prepared.plan.fingerprint.digest.hex_value}"
    ).encode("ascii")
    seed = SensitiveValue(
        BytesValue(value=hashlib.sha256(material).digest()),
        origins=(_INSPECTION_SEED_ORIGIN,),
    )
    return RunContext(
        prepared=request.project.prepared,
        environment_snapshots=snapshots,
        clock=SystemRunClock(),
        generator=DeterministicGenerator(seed),
        file_provider=ProjectFileValueProvider(files, request.project.policy),
        secure_generator=SecureGenerator(),
        yaml_decoder=SafeYamlValueDecoder(request.settings.yaml),
    )


def _attempt_scope(work: ExecutionWork) -> AttemptScope:
    return AttemptScope(
        execution=work.scope,
        attempt_id=AttemptId(value=uuid5(work.scope.execution_id.value, "attempt:0")),
    )


def _target_symbol(request: ResolvedValueInspectionRequest) -> SymbolPath:
    if (
        request.resource.kind == ResourceKind.REQUEST.value
        and request.field_path
        and request.field_path[0] == "request"
    ):
        segments = request.field_path[:2]
    else:
        segments = request.field_path or ("resolved-inspection",)
    try:
        return SymbolPath(segments=segments)
    except (TypeError, ValueError):
        return SymbolPath(segments=("resolved-inspection",))


def _result_fingerprint(
    result: MaskedPresent | Unavailable | Missing | Error | Blocked,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"comparo.resolved-inspection.value/v1alpha1\0")
    if type(result) is Unavailable:
        digest.update(b"unavailable\0")
        digest.update(result.reason.value.encode("ascii"))
    elif type(result) is Missing:
        digest.update(b"missing")
    elif type(result) is Error:
        digest.update(b"error\0")
        digest.update(result.code.value.encode("ascii"))
    elif type(result) is Blocked:
        digest.update(b"blocked\0")
        digest.update(result.code.value.encode("ascii"))
    elif type(result.value) is MaskedSensitiveValue:
        digest.update(b"masked\0")
        digest.update(result.value.kind.value.encode("ascii"))
        for origin in result.value.origins:
            digest.update(b"\0")
            digest.update(origin.value.encode("ascii"))
    else:
        digest.update(b"present\0")
        _hash_value(digest, result.value)
    return digest.hexdigest()


def _hash_value(  # noqa: C901 - exhaustive closed resolved-value hashing
    updater: _Digest,
    value: ResolvedValue,
) -> None:
    stack: list[Iterator[ResolvedValue | tuple[str, str]]] = [iter((value,))]
    while stack:
        try:
            current = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if type(current) is tuple:
            updater.update(b"key\0")
            _hash_text(updater, current[1])
        elif type(current) is JsonNull:
            updater.update(b"null")
        elif type(current) is JsonBoolean:
            updater.update(b"true" if current.value else b"false")
        elif type(current) is JsonInteger:
            updater.update(b"integer\0")
            _hash_integer(updater, current.value)
        elif type(current) is JsonDecimal:
            updater.update(b"number\0")
            sign, digits, exponent = current.value.as_tuple()
            _hash_integer(updater, sign)
            _hash_integer(updater, cast("int", exponent))
            for offset in range(0, len(digits), 4096):
                updater.update(bytes(digits[offset : offset + 4096]))
        elif type(current) is JsonString:
            updater.update(b"string\0")
            _hash_text(updater, current.value)
        elif type(current) is JsonArray:
            updater.update(b"array\0")
            _hash_integer(updater, len(current.items))
            stack.append(iter(current.items))
        elif type(current) is JsonObject:
            updater.update(b"object\0")
            _hash_integer(updater, len(current.members))
            stack.append(_object_hash_items(current))
        elif type(current) is BytesValue:
            updater.update(b"bytes\0")
            updater.update(current.value)
        elif type(current) is Duration:
            updater.update(b"duration\0")
            _hash_integer(updater, current.nanoseconds)
        elif type(current) is ByteCount:
            updater.update(b"size\0")
            _hash_integer(updater, current.value)
        else:  # pragma: no cover - the stack is populated from a closed ResolvedValue union
            raise TypeError("unsupported resolved fingerprint value")


def _object_hash_items(
    value: JsonObject,
) -> Iterator[ResolvedValue | tuple[str, str]]:
    for member in value.members:
        yield ("key", member.name)
        yield member.value


def _hash_text(updater: _Digest, value: str) -> None:
    for offset in range(0, len(value), 4096):
        updater.update(value[offset : offset + 4096].encode("utf-8", errors="replace"))


def _hash_integer(updater: _Digest, value: int) -> None:
    updater.update(b"-" if value < 0 else b"+")
    remaining = abs(value)
    updater.update(remaining.bit_length().to_bytes(8, "big"))
    mask = (1 << 4096) - 1
    while remaining:
        updater.update((remaining & mask).to_bytes(512, "big"))
        remaining >>= 4096


def _project_result(
    request: ResolvedValueInspectionRequest,
    work: ExecutionWork,
    attempt: AttemptScope,
    result: MaskedPresent | Unavailable | Missing | Error | Blocked,
    fingerprint: str,
) -> ResolvedValueInspection:
    execution = _execution_coordinate(work, attempt)
    provenance = _provenance(result.provenance)
    if type(result) is Unavailable:
        return ResolvedValueInspection(
            resource=request.resource,
            field_path=request.field_path,
            execution=execution,
            state=ResolvedInspectionState.UNAVAILABLE,
            provenance=provenance,
            value_fingerprint=fingerprint,
            code=result.reason.value,
            start_node=request.start_node,
        )
    if type(result) is Missing:
        return ResolvedValueInspection(
            resource=request.resource,
            field_path=request.field_path,
            execution=execution,
            state=ResolvedInspectionState.MISSING,
            provenance=provenance,
            value_fingerprint=fingerprint,
            start_node=request.start_node,
        )
    if type(result) is Error:
        return ResolvedValueInspection(
            resource=request.resource,
            field_path=request.field_path,
            execution=execution,
            state=ResolvedInspectionState.ERROR,
            provenance=provenance,
            value_fingerprint=fingerprint,
            code=result.code.value,
            start_node=request.start_node,
        )
    if type(result) is Blocked:
        return ResolvedValueInspection(
            resource=request.resource,
            field_path=request.field_path,
            execution=execution,
            state=ResolvedInspectionState.BLOCKED,
            provenance=provenance,
            value_fingerprint=fingerprint,
            code=result.code.value,
            start_node=request.start_node,
        )
    value = result.value
    if type(value) is MaskedSensitiveValue:
        nodes, next_node = _window(
            _masked_nodes(value),
            start=request.start_node,
            maximum=request.maximum_nodes,
        )
        return ResolvedValueInspection(
            resource=request.resource,
            field_path=request.field_path,
            execution=execution,
            state=ResolvedInspectionState.PRESENT,
            provenance=provenance,
            value_fingerprint=fingerprint,
            kind=value.kind,
            masked=True,
            taint_origins=tuple(item.value for item in value.origins),
            start_node=request.start_node,
            nodes=nodes,
            next_node=next_node,
        )
    nodes, next_node = _window(
        _value_nodes(value, request.settings),
        start=request.start_node,
        maximum=request.maximum_nodes,
    )
    return ResolvedValueInspection(
        resource=request.resource,
        field_path=request.field_path,
        execution=execution,
        state=ResolvedInspectionState.PRESENT,
        provenance=provenance,
        value_fingerprint=fingerprint,
        kind=_kind(value),
        start_node=request.start_node,
        nodes=nodes,
        next_node=next_node,
    )


def _window(
    nodes: Iterator[ResolvedValueNode],
    *,
    start: int,
    maximum: int,
) -> tuple[tuple[ResolvedValueNode, ...], int | None]:
    page: list[ResolvedValueNode] = []
    extra = False
    for node in nodes:
        if node.ordinal < start:
            continue
        if len(page) == maximum:
            extra = True
            break
        page.append(node)
    return tuple(page), start + len(page) if extra else None


def _masked_nodes(value: MaskedSensitiveValue) -> Iterator[ResolvedValueNode]:
    yield ResolvedValueNode(
        ordinal=0,
        path=(),
        kind=value.kind,
        format=ResolvedValueFormat.MASKED,
        preview=value.marker,
        byte_count=None,
        child_count=0,
        truncated=False,
        masked=True,
    )


def _value_nodes(
    value: ResolvedValue,
    settings: ResolvedValueInspectionSettings,
) -> Iterator[ResolvedValueNode]:
    stack: list[Iterator[tuple[tuple[str, ...], ResolvedValue]]] = [iter((((), value),))]
    ordinal = 0
    while stack:
        try:
            path, current = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        _validate_output_path(path, settings)
        yield _node(ordinal, path, current, settings.maximum_scalar_bytes.value)
        ordinal += 1
        if type(current) is JsonArray:
            stack.append((((*path, str(index)), item) for index, item in enumerate(current.items)))
        elif type(current) is JsonObject:
            stack.append(((*path, item.name), item.value) for item in current.members)


def _node(
    ordinal: int,
    path: tuple[str, ...],
    value: ResolvedValue,
    maximum_bytes: int,
) -> ResolvedValueNode:
    kind = _kind(value)
    if type(value) is JsonArray:
        return _container_node(ordinal, path, kind, len(value.items))
    if type(value) is JsonObject:
        return _container_node(ordinal, path, kind, len(value.members))
    if type(value) is JsonNull:
        return _scalar_node(ordinal, path, kind, ResolvedValueFormat.NULL, None, 0, False)
    if type(value) is JsonBoolean:
        return _text_node(
            ordinal,
            path,
            kind,
            ResolvedValueFormat.BOOLEAN,
            "true" if value.value else "false",
            maximum_bytes,
        )
    if type(value) is JsonInteger:
        return _integer_node(
            ordinal,
            path,
            kind,
            ResolvedValueFormat.DECIMAL,
            value.value,
            maximum_bytes,
        )
    if type(value) is JsonDecimal:
        text = _bounded_decimal(value.value, maximum_bytes)
        return _bounded_scalar_node(ordinal, path, kind, ResolvedValueFormat.DECIMAL, text)
    if type(value) is JsonString:
        return _text_node(
            ordinal,
            path,
            kind,
            ResolvedValueFormat.TEXT,
            value.value,
            maximum_bytes,
        )
    if type(value) is BytesValue:
        retained_bytes = (maximum_bytes + 1) // 2
        preview = value.value[:retained_bytes].hex()[:maximum_bytes]
        truncated = len(value.value) * 2 > maximum_bytes
        return _scalar_node(
            ordinal,
            path,
            kind,
            ResolvedValueFormat.HEX,
            preview,
            len(value.value),
            truncated,
        )
    if type(value) is Duration:
        return _integer_node(
            ordinal,
            path,
            kind,
            ResolvedValueFormat.NANOSECONDS,
            value.nanoseconds,
            maximum_bytes,
        )
    return _integer_node(
        ordinal,
        path,
        kind,
        ResolvedValueFormat.BYTES,
        value.value,
        maximum_bytes,
    )


def _container_node(
    ordinal: int,
    path: tuple[str, ...],
    kind: ValueKind,
    children: int,
) -> ResolvedValueNode:
    return ResolvedValueNode(
        ordinal=ordinal,
        path=path,
        kind=kind,
        format=ResolvedValueFormat.NONE,
        preview=None,
        byte_count=None,
        child_count=children,
        truncated=False,
        masked=False,
    )


def _text_node(
    ordinal: int,
    path: tuple[str, ...],
    kind: ValueKind,
    format: ResolvedValueFormat,
    text: str,
    maximum_bytes: int,
) -> ResolvedValueNode:
    preview, byte_count, truncated = _bounded_text(text, maximum_bytes)
    return _scalar_node(
        ordinal,
        path,
        kind,
        format,
        preview,
        byte_count,
        truncated,
    )


def _integer_node(
    ordinal: int,
    path: tuple[str, ...],
    kind: ValueKind,
    format: ResolvedValueFormat,
    value: int,
    maximum_bytes: int,
) -> ResolvedValueNode:
    estimate = _maximum_decimal_length(value)
    if estimate > maximum_bytes:
        return _scalar_node(
            ordinal,
            path,
            kind,
            format,
            None,
            None,
            True,
        )
    text = str(value)
    return _text_node(ordinal, path, kind, format, text, maximum_bytes)


def _bounded_scalar_node(
    ordinal: int,
    path: tuple[str, ...],
    kind: ValueKind,
    format: ResolvedValueFormat,
    bounded: tuple[str | None, int | None, bool],
) -> ResolvedValueNode:
    preview, byte_count, truncated = bounded
    return _scalar_node(
        ordinal,
        path,
        kind,
        format,
        preview,
        byte_count,
        truncated,
    )


def _scalar_node(
    ordinal: int,
    path: tuple[str, ...],
    kind: ValueKind,
    format: ResolvedValueFormat,
    preview: str | None,
    byte_count: int | None,
    truncated: bool,
) -> ResolvedValueNode:
    return ResolvedValueNode(
        ordinal=ordinal,
        path=path,
        kind=kind,
        format=format,
        preview=preview,
        byte_count=byte_count,
        child_count=0,
        truncated=truncated,
        masked=False,
    )


def _bounded_text(value: str, maximum_bytes: int) -> tuple[str, int | None, bool]:
    chunks: list[bytes] = []
    retained = 0
    for character in value:
        encoded = character.encode("utf-8", errors="replace")
        if retained + len(encoded) > maximum_bytes:
            return b"".join(chunks).decode("utf-8"), None, True
        chunks.append(encoded)
        retained += len(encoded)
    return b"".join(chunks).decode("utf-8"), retained, False


def _bounded_decimal(
    value: Decimal,
    maximum_bytes: int,
) -> tuple[str | None, int | None, bool]:
    if _decimal_length(value) > maximum_bytes:
        return None, None, True
    text = format(value, "f")
    return text, len(text), False


def _maximum_decimal_length(value: int) -> int:
    if value == 0:
        return 1
    bits = abs(value).bit_length()
    digits_upper_bound = (bits * 30103) // 100000 + 1
    return digits_upper_bound + (1 if value < 0 else 0)


def _decimal_length(value: Decimal) -> int:
    sign, digits, exponent = value.as_tuple()
    numeric_exponent = cast("int", exponent)
    if numeric_exponent >= 0:
        length = len(digits) + numeric_exponent
    else:
        fractional = -numeric_exponent
        length = len(digits) + 1 if fractional < len(digits) else fractional + 2
    return length + sign


def _validate_output_path(
    path: tuple[str, ...],
    settings: ResolvedValueInspectionSettings,
) -> None:
    if len(path) > settings.maximum_path_segments.value:
        raise _ProjectionBudgetExceededError
    if any(_utf8_exceeds(segment, settings.maximum_path_segment_bytes.value) for segment in path):
        raise _ProjectionBudgetExceededError


def _utf8_exceeds(value: str, maximum_bytes: int) -> bool:
    used = 0
    for character in value:
        used += len(character.encode("utf-8", errors="replace"))
        if used > maximum_bytes:
            return True
    return False


def _kind(value: ResolvedValue) -> ValueKind:
    kinds: dict[type[object], ValueKind] = {
        JsonNull: ValueKind.NULL,
        JsonBoolean: ValueKind.BOOLEAN,
        JsonInteger: ValueKind.INTEGER,
        JsonDecimal: ValueKind.NUMBER,
        JsonString: ValueKind.STRING,
        JsonArray: ValueKind.ARRAY,
        JsonObject: ValueKind.OBJECT,
        BytesValue: ValueKind.BYTES,
        Duration: ValueKind.DURATION,
        ByteCount: ValueKind.SIZE,
    }
    return kinds[type(value)]


def _provenance(value: ResolutionProvenance) -> ResolutionProvenanceInspection:
    provider = value.provider
    return ResolutionProvenanceInspection(
        provider_namespace=provider.namespace,
        provider_name=provider.name,
        provider_version=provider.version,
        namespace=None if value.namespace is None else value.namespace.value,
        terminal_outcome=value.terminal_outcome.value,
        source_attempts=len(value.steps),
    )


def _execution_coordinate(
    work: ExecutionWork,
    attempt: AttemptScope,
) -> ResolvedExecutionCoordinate:
    return ResolvedExecutionCoordinate(
        ordinal=work.ordinal.value,
        execution_id=str(work.scope.execution_id.value),
        attempt_id=str(attempt.attempt_id.value),
        trial_id=str(work.trial.trial_id.value),
        cell_id=str(work.trial.cell.cell_id.value),
        subject=_coordinate(work.subject),
        environment=_coordinate(work.environment),
    )


def _coordinate(value: DefinitionRef | CompiledResource) -> ResourceCoordinate:
    definition = value.definition if type(value) is CompiledResource else value
    identity = definition.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=definition.fingerprint.digest.hex_value,
    )


def _request_diagnostic(
    request: ResolvedValueInspectionRequest,
    code: str,
) -> ApplicationDiagnostic:
    coordinate = request.resource
    return ApplicationDiagnostic(
        code=code,
        severity="error",
        stage="resolution",
        resource=(coordinate.namespace, coordinate.kind, coordinate.resource_id),
        path=request.field_path,
    )


def _failure(code: str) -> ResolvedValueInspectionResult:
    return ResolvedValueInspectionResult(
        diagnostics=(
            ApplicationDiagnostic(
                code=code,
                severity="error",
                stage="resolution",
            ),
        )
    )


__all__ = (
    "ResolutionProvenanceInspection",
    "ResolvedExecutionCoordinate",
    "ResolvedInputTiming",
    "ResolvedInspectionState",
    "ResolvedValueFormat",
    "ResolvedValueFreshness",
    "ResolvedValueInspection",
    "ResolvedValueInspectionRequest",
    "ResolvedValueInspectionResult",
    "ResolvedValueInspectionSettings",
    "ResolvedValueInspectionTarget",
    "ResolvedValueNode",
    "ResolvedValueProjectionLimits",
    "bind_resolved_value_target",
    "inspect_resolved_value",
)
