"""One production composition root from an admitted plan to a safe RunArtifact.

This module owns adapter wiring only. Configuration lowering, planning, value resolution, HTTP
execution, evaluation, sanitization, and persistence remain implemented by their narrow services.
No live runtime value crosses the returned application boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import PosixSanitizedBlobSpool
from comparo.adapters.artifacts import SanitizedSpoolError
from comparo.adapters.artifacts import TransientArtifactBodyReader
from comparo.adapters.http import BodyMaterializationLimits
from comparo.adapters.http import BodyStoreError
from comparo.adapters.http import DefaultCaptureBudgetProvider
from comparo.adapters.http import ExplicitTlsContextProvider
from comparo.adapters.http import HttpcoreTransport
from comparo.adapters.http import HttpcoreTransportConfig
from comparo.adapters.http import HttpRequestFinalizer
from comparo.adapters.http import PolicyHttpExchangePreparer
from comparo.adapters.http import ProductionRetryWaiter
from comparo.adapters.http import RequestBodyMaterializer
from comparo.adapters.http import ResponseCaptureLimits
from comparo.adapters.http import ResponseClassification
from comparo.adapters.http import SseDataJsonPolicy
from comparo.adapters.http import StandardRedirectResolver
from comparo.adapters.http import StoredStreamSnapshotProvider
from comparo.adapters.http import StreamDecodingLimits
from comparo.adapters.http import SystemHostResolver
from comparo.adapters.http import TransientBodyStore
from comparo.adapters.http import TransientBodyStoreConfig
from comparo.adapters.http.ordinary import OrdinaryResponseDecodeLimits
from comparo.adapters.http.ordinary import StoredOrdinaryResponseSnapshotProvider
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
from comparo.application.evaluation_binding import EvaluationRuleLimits
from comparo.application.evaluation_binding import bind_configuration_evaluation
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import inspect_diagnostic
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.runtime_capabilities import preflight_runtime_capabilities
from comparo.application.schema_validation import DEFAULT_SCHEMA_VALIDATION_LIMITS_V1
from comparo.application.schema_validation import SchemaValidationLimits
from comparo.core.artifacts.capture import ArtifactCapturingDriver
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.compiler import ArtifactCompilationStatus
from comparo.core.artifacts.compiler import compile_run_artifact
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactCapturePolicy
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.artifacts.ports import ArtifactPersistenceOutcome
from comparo.core.artifacts.ports import ArtifactPersistenceStatus
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budget_origins import VersionedDefaultSetRef
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import EnvironmentSnapshot
from comparo.core.resolution import RunCancellation
from comparo.core.resolution import RunContext
from comparo.core.runtime import ConfigurationRequestProvider
from comparo.core.runtime import HttpExecutionDriver
from comparo.core.runtime import NullEventSink
from comparo.core.runtime import run_prepared
from comparo.core.runtime.ports import EvaluationPreparation
from comparo.core.runtime.ports import RuntimeEventSink
from comparo.core.selection import SelectionSnapshotRequirements

_GENERATION_SEED_ORIGIN = TaintOriginId(value="runtime/generation-seed")
_RUNTIME_DEFAULT_SET = "comparo.runtime-defaults"
_RUNTIME_DEFAULT_VERSION = "1"


def production_execution_defaults_v1() -> ProductionExecutionSettings:
    """Return the explicit replaceable local defaults for the first production composition."""
    response_capture = ResponseCaptureLimits(
        maximum_encoded_body_bytes=ByteCount(value=64 * 1024 * 1024),
        maximum_entity_body_bytes=ByteCount(value=64 * 1024 * 1024),
        maximum_expansion_ratio=Ratio(
            numerator=100,
            denominator=PositiveCount(value=1),
        ),
    )
    artifact_limits = ArtifactLimits(
        maximum_blobs=PositiveCount(value=4096),
        maximum_blob_bytes=ByteCount(value=64 * 1024 * 1024),
        maximum_total_blob_bytes=ByteCount(value=512 * 1024 * 1024),
        maximum_manifest_bytes=ByteCount(value=64 * 1024 * 1024),
        maximum_report_bytes=ByteCount(value=64 * 1024 * 1024),
        sanitization=SanitizationLimits(
            maximum_patterns=PositiveCount(value=4096),
            maximum_pattern_bytes=ByteCount(value=1024 * 1024),
            maximum_value_bytes=ByteCount(value=256 * 1024),
            maximum_value_nodes=PositiveCount(value=4096),
        ),
    )
    return ProductionExecutionSettings(
        files=LocalFileLimits(),
        environment=EnvironmentSourceLimits(),
        yaml=YamlValueLimits(),
        body_store=TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=1024 * 1024),
            maximum_transient_bytes=ByteCount(value=512 * 1024 * 1024),
            io_chunk_size=PositiveCount(value=64 * 1024),
        ),
        request_body=BodyMaterializationLimits(
            maximum_body_bytes=ByteCount(value=64 * 1024 * 1024),
            maximum_json_depth=PositiveCount(value=256),
            maximum_json_nodes=PositiveCount(value=1_000_000),
        ),
        transport=HttpcoreTransportConfig(
            maximum_connections=PositiveCount(value=20),
            maximum_keepalive_connections=Count(value=10),
            maximum_authorization_occurrences=PositiveCount(value=1_000_000),
            keepalive_expiry=Duration(nanoseconds=30_000_000_000),
            capture=response_capture,
            classification=ResponseClassification(body=Sensitivity.PUBLIC),
        ),
        streams=StreamDecodingLimits(
            maximum_records=PositiveCount(value=100_000),
            maximum_controls=PositiveCount(value=10_000),
            maximum_errors=PositiveCount(value=10_000),
            maximum_item_bytes=ByteCount(value=1024 * 1024),
        ),
        ordinary=OrdinaryResponseDecodeLimits(
            maximum_body_bytes=ByteCount(value=8 * 1024 * 1024),
            maximum_text_characters=Count(value=8 * 1024 * 1024),
            maximum_json_depth=Count(value=256),
            maximum_json_nodes=Count(value=1_000_000),
            maximum_json_tokens=Count(value=2_000_000),
        ),
        evaluation=EvaluationRuleLimits(
            selection_work=1_000_000,
            selection_matches=100_000,
            selected_result_bytes=16 * 1024 * 1024,
            transform_work=1_000_000,
            facts=100_000,
            evidence_examples=1_000,
            evidence_bytes=16 * 1024 * 1024,
            derived_values=100_000,
            derived_bytes=16 * 1024 * 1024,
            pairs=100_000,
        ),
        schemas=DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
        artifact=ArtifactPolicy(
            persist=ArtifactPersistMode.NEVER,
            unsafe_source=UnsafeSourceBehavior.OMIT,
            capture=ArtifactCapturePolicy(),
            limits=artifact_limits,
        ),
        artifact_io_chunk_size=PositiveCount(value=64 * 1024),
    )


def persisted_production_execution_defaults_v1() -> ProductionExecutionSettings:
    """Return persistence defaults that conservatively omit unclassified response bodies."""
    defaults = production_execution_defaults_v1()
    artifact = msgspec.structs.replace(
        defaults.artifact,
        persist=ArtifactPersistMode.ALWAYS,
    )
    transport = msgspec.structs.replace(
        defaults.transport,
        classification=ResponseClassification(body=Sensitivity.SENSITIVE),
    )
    return msgspec.structs.replace(defaults, artifact=artifact, transport=transport)


@final
class ProductionExecutionSettings(msgspec.Struct, frozen=True, kw_only=True):
    """Every caller-owned finite resource and decoding limit for one production run."""

    files: LocalFileLimits
    environment: EnvironmentSourceLimits
    yaml: YamlValueLimits
    body_store: TransientBodyStoreConfig
    request_body: BodyMaterializationLimits
    transport: HttpcoreTransportConfig
    streams: StreamDecodingLimits
    ordinary: OrdinaryResponseDecodeLimits
    evaluation: EvaluationRuleLimits
    schemas: SchemaValidationLimits
    artifact: ArtifactPolicy
    artifact_io_chunk_size: PositiveCount

    def __post_init__(self) -> None:
        """Reject untyped composition before any adapter acquires a resource."""
        expected = (
            (self.files, LocalFileLimits),
            (self.environment, EnvironmentSourceLimits),
            (self.yaml, YamlValueLimits),
            (self.body_store, TransientBodyStoreConfig),
            (self.request_body, BodyMaterializationLimits),
            (self.transport, HttpcoreTransportConfig),
            (self.streams, StreamDecodingLimits),
            (self.ordinary, OrdinaryResponseDecodeLimits),
            (self.evaluation, EvaluationRuleLimits),
            (self.schemas, SchemaValidationLimits),
            (self.artifact, ArtifactPolicy),
            (self.artifact_io_chunk_size, PositiveCount),
        )
        if any(type(value) is not kind for value, kind in expected):
            raise TypeError("production execution settings contain an untyped section")
        if self.transport.capture.maximum_entity_body_bytes.value > (
            self.body_store.maximum_transient_bytes.value
        ):
            raise ValueError("response capture limit exceeds the run transient-store limit")
        if self.request_body.maximum_body_bytes.value > (
            self.body_store.maximum_transient_bytes.value
        ):
            raise ValueError("request body limit exceeds the run transient-store limit")
        if self.ordinary.maximum_body_bytes.value > (
            self.transport.capture.maximum_entity_body_bytes.value
        ):
            raise ValueError("ordinary decode limit exceeds captured entity bytes")


@final
class ProductionExecutionRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Complete effectful request assembled only after project planning succeeds."""

    project: PreparedProjectPlan
    settings: ProductionExecutionSettings
    ambient_environment: Mapping[str, str] | None
    artifact_root: Path | None
    approve_mutations: bool
    tool_version: str
    events: RuntimeEventSink | None = None
    cancellation: RunCancellation | None = None

    def __post_init__(self) -> None:
        """Validate primitive frontend choices without retaining any output text."""
        if type(self.project) is not PreparedProjectPlan:
            raise TypeError("production execution requires PreparedProjectPlan")
        if type(self.settings) is not ProductionExecutionSettings:
            raise TypeError("production execution requires typed settings")
        if self.ambient_environment is not None and not isinstance(
            self.ambient_environment, Mapping
        ):
            raise TypeError("production ambient environment must be a Mapping or None")
        if self.ambient_environment is not None and any(
            type(name) is not str or type(value) is not str
            for name, value in self.ambient_environment.items()
        ):
            raise TypeError("production ambient environment entries must be exact strings")
        if self.artifact_root is not None and not isinstance(self.artifact_root, Path):
            raise TypeError("production artifact root must be a Path or None")
        if type(self.approve_mutations) is not bool:
            raise TypeError("production mutation approval must be bool")
        if (
            type(self.tool_version) is not str
            or not self.tool_version
            or len(self.tool_version) > 64
        ):
            raise ValueError("production tool version must be bounded non-empty text")
        if self.events is not None and not callable(getattr(self.events, "emit", None)):
            raise TypeError("production execution events must implement RuntimeEventSink")
        if self.cancellation is not None and type(self.cancellation) is not RunCancellation:
            raise TypeError("production execution cancellation must be RunCancellation or None")


@final
class ProductionExecutionFailureKind(StrEnum):
    """Stable application-level failure classes independent from diagnostic wording."""

    INVALID = "invalid"
    INCOMPLETE = "incomplete"
    INTERNAL = "internal"


@final
class ProductionExecutionFailure(msgspec.Struct, frozen=True, kw_only=True):
    """One typed failure class plus value-free diagnostic evidence."""

    kind: ProductionExecutionFailureKind
    diagnostics: tuple[ApplicationDiagnostic, ...]

    def __post_init__(self) -> None:
        """Require a closed class and at least one immutable safe diagnostic."""
        if type(self.kind) is not ProductionExecutionFailureKind:
            raise TypeError("production failure kind must be typed")
        if (
            type(self.diagnostics) is not tuple
            or not self.diagnostics
            or any(type(item) is not ApplicationDiagnostic for item in self.diagnostics)
        ):
            raise TypeError("production failure diagnostics must be non-empty and typed")


@final
class ProductionExecutionResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one safe artifact outcome or typed value-free failure."""

    artifact: RunArtifact | None = None
    persistence: ArtifactPersistenceOutcome | None = None
    failure: ProductionExecutionFailure | None = None

    def __post_init__(self) -> None:
        """Keep safe success and diagnostic failure structurally disjoint."""
        if self.artifact is not None and type(self.artifact) is not RunArtifact:
            raise TypeError("production result artifact must be RunArtifact")
        if (
            self.persistence is not None
            and type(self.persistence) is not ArtifactPersistenceOutcome
        ):
            raise TypeError("production persistence outcome must be typed")
        if self.failure is not None and type(self.failure) is not ProductionExecutionFailure:
            raise TypeError("production result failure must be typed")
        if (self.artifact is None) == (self.failure is None):
            raise ValueError("production execution requires exactly artifact or failure")
        if self.artifact is None and self.persistence is not None:
            raise ValueError("failed production execution cannot retain persistence")

    @property
    def diagnostics(self) -> tuple[ApplicationDiagnostic, ...]:
        """Expose only the safe diagnostics retained by a typed failure."""
        return () if self.failure is None else self.failure.diagnostics


async def execute_production(request: ProductionExecutionRequest) -> ProductionExecutionResult:
    """Execute once through the shared planner product and return only sanitized state."""
    if type(request) is not ProductionExecutionRequest:
        raise TypeError("production execution requires ProductionExecutionRequest")
    capabilities = preflight_runtime_capabilities(request.project)
    if capabilities.diagnostics:
        return _failure(
            ProductionExecutionFailureKind.INVALID,
            tuple(inspect_diagnostic(item) for item in capabilities.diagnostics),
        )
    resources = _ExecutionResources()
    try:
        result = await _execute_with_resources(request, resources)
    except asyncio.CancelledError:
        await resources.close()
        raise
    except Exception:  # noqa: BLE001 - never expose live adapter exception details
        result = _failure_code(
            ProductionExecutionFailureKind.INTERNAL,
            "execution.internal-failed",
        )
    cleanup_failed = await resources.close()
    return (
        _failure_code(
            ProductionExecutionFailureKind.INTERNAL,
            "execution.cleanup-failed",
        )
        if cleanup_failed
        else result
    )


class _ExecutionResources:
    __slots__ = ("body_store", "capture", "context", "files", "sink", "spool")

    def __init__(self) -> None:
        self.sink: LocalArtifactBundleSink | None = None
        self.files: PinnedProjectFiles | None = None
        self.spool: PosixSanitizedBlobSpool | None = None
        self.context: RunContext | None = None
        self.body_store: TransientBodyStore | None = None
        self.capture: ArtifactCapturingDriver | None = None

    async def close(self) -> bool:
        """Attempt every cleanup and return only a value-free failure signal."""
        failed = _release_capture(self.capture)
        cancelled: asyncio.CancelledError | None = None
        for async_resource in (self.spool, self.body_store, self.context):
            if async_resource is None:
                continue
            try:
                await async_resource.close()
            except asyncio.CancelledError as error:
                cancelled = error
            except Exception:  # noqa: BLE001 - cleanup must continue across adapters
                failed = True
        for sync_resource in (self.files, self.sink):
            if sync_resource is None:
                continue
            try:
                sync_resource.close()
            except Exception:  # noqa: BLE001 - cleanup must continue across adapters
                failed = True
        if cancelled is not None:
            raise cancelled
        return failed


def _release_capture(capture: ArtifactCapturingDriver | None) -> bool:
    if capture is None:
        return False
    try:
        capture.release()
    except Exception:  # noqa: BLE001 - cleanup must continue across adapters
        return True
    return False


async def _execute_with_resources(
    request: ProductionExecutionRequest,
    resources: _ExecutionResources,
) -> ProductionExecutionResult:
    context, diagnostics = await _prepare_context(request, resources)
    if diagnostics:
        return _failure(ProductionExecutionFailureKind.INVALID, diagnostics)
    if context is None:  # pragma: no cover - preparation result invariant
        raise RuntimeError("successful context preparation omitted its context")
    evaluation = bind_configuration_evaluation(
        request.project.prepared,
        context,
        request.settings.evaluation,
        request.settings.schemas,
    )
    if evaluation.diagnostics:
        return _failure(
            ProductionExecutionFailureKind.INVALID,
            tuple(inspect_diagnostic(item) for item in evaluation.diagnostics),
        )
    preparation = evaluation.preparation
    if preparation is None:  # pragma: no cover - binding result invariant
        raise RuntimeError("successful evaluation binding omitted its preparation")
    sink, sink_failure = _artifact_sink(
        request.project,
        request.artifact_root,
        request.settings.artifact,
    )
    if sink_failure is not None:
        return _failure_code(ProductionExecutionFailureKind.INVALID, sink_failure)
    resources.sink = sink
    try:
        resources.spool = PosixSanitizedBlobSpool(
            request.settings.artifact.limits,
            io_chunk_size=request.settings.artifact_io_chunk_size,
        )
        capture = _capture_driver(
            request,
            context,
            resources.spool,
            preparation.snapshot_requirements,
            resources,
        )
        resources.capture = capture
    except (BodyStoreError, OSError, SanitizedSpoolError, ValueError):
        return _failure_code(
            ProductionExecutionFailureKind.INCOMPLETE,
            "execution.runtime-setup-failed",
        )
    return await _run_compile_persist(request, resources, context, capture, preparation)


async def _prepare_context(
    request: ProductionExecutionRequest,
    resources: _ExecutionResources,
) -> tuple[RunContext | None, tuple[ApplicationDiagnostic, ...]]:
    try:
        resources.files = PinnedProjectFiles(
            request.project.project_root,
            limits=request.settings.files,
        )
    except LocalFilesystemError:
        return None, _diagnostics("execution.project-root-unavailable")
    snapshots = await build_environment_snapshots(
        request.project,
        resources.files,
        ambient=request.ambient_environment,
        limits=request.settings.environment,
    )
    if snapshots.diagnostics:
        return None, tuple(inspect_diagnostic(item) for item in snapshots.diagnostics)
    context = _run_context(
        request.project,
        snapshots.snapshots,
        resources.files,
        request.settings,
        request.cancellation,
    )
    resources.context = context
    return context, ()


async def _run_compile_persist(
    request: ProductionExecutionRequest,
    resources: _ExecutionResources,
    context: RunContext,
    capture: ArtifactCapturingDriver,
    evaluation: EvaluationPreparation,
) -> ProductionExecutionResult:
    created = SystemRunClock().now().unix_nanoseconds
    result = await run_prepared(
        request.project.prepared,
        driver=capture,
        evaluation=evaluation,
        cancellation=context.cancellation,
        clock=SystemRunClock(),
        events=NullEventSink() if request.events is None else request.events,
    )
    compilation = compile_run_artifact(
        request.project.prepared,
        result,
        capture,
        policy=request.settings.artifact,
        artifact_id=_artifact_id(request.project.prepared),
        created_unix_nanoseconds=created,
        tool_version=request.tool_version,
    )
    if compilation.status is not ArtifactCompilationStatus.CREATED:
        code = (
            "artifact.compilation-rejected"
            if compilation.failure is None
            else f"artifact.{compilation.failure.code}"
        )
        kind = (
            ProductionExecutionFailureKind.INTERNAL
            if compilation.failure is None
            else ProductionExecutionFailureKind.INCOMPLETE
        )
        return _failure_code(kind, code)
    artifact = compilation.artifact
    if artifact is None:  # pragma: no cover - compilation result invariant
        raise RuntimeError("created artifact compilation omitted its product")
    spool = resources.spool
    if spool is None:  # pragma: no cover - resource lifecycle invariant
        raise RuntimeError("artifact compilation omitted its sanitized spool")
    persistence = await _persist(
        resources.sink,
        artifact,
        spool,
        request.settings.artifact,
    )
    if persistence is not None and persistence.status is not ArtifactPersistenceStatus.PERSISTED:
        return _failure_code(
            ProductionExecutionFailureKind.INCOMPLETE,
            _persistence_code(persistence),
        )
    return ProductionExecutionResult(artifact=artifact, persistence=persistence)


def _run_context(
    project: PreparedProjectPlan,
    snapshots: tuple[EnvironmentSnapshot, ...],
    files: PinnedProjectFiles,
    settings: ProductionExecutionSettings,
    cancellation: RunCancellation | None,
) -> RunContext:
    """Compose isolated value providers without consulting ambient process state."""
    seed = SensitiveValue(
        BytesValue(value=secrets.token_bytes(32)),
        origins=(_GENERATION_SEED_ORIGIN,),
    )
    return RunContext(
        prepared=project.prepared,
        environment_snapshots=snapshots,
        clock=SystemRunClock(),
        generator=DeterministicGenerator(seed),
        file_provider=ProjectFileValueProvider(files, project.policy),
        secure_generator=SecureGenerator(),
        yaml_decoder=SafeYamlValueDecoder(settings.yaml),
        cancellation=cancellation,
    )


def _capture_driver(
    request: ProductionExecutionRequest,
    context: RunContext,
    spool: PosixSanitizedBlobSpool,
    requirements: SelectionSnapshotRequirements,
    resources: _ExecutionResources,
) -> ArtifactCapturingDriver:
    """Compose the one real HTTP driver beneath the live-to-safe capture decorator."""
    settings = request.settings
    store = TransientBodyStore(settings.body_store)
    resources.body_store = store
    streams = StoredStreamSnapshotProvider(
        store=store,
        limits=settings.streams,
        sse_json=SseDataJsonPolicy(),
    )
    effective_requirements = (
        requirements if requirements.stream else msgspec.structs.replace(requirements, stream=True)
    )
    snapshots = StoredOrdinaryResponseSnapshotProvider(
        store=store,
        limits=settings.ordinary,
        streams=streams,
        requirements=effective_requirements,
    )
    transport = HttpcoreTransport(
        body_store=store,
        config=settings.transport,
        tls=ExplicitTlsContextProvider(),
        budgets=DefaultCaptureBudgetProvider(
            settings.transport.capture,
            _capture_budget_origin(settings.transport.capture),
        ),
    )
    request_materializer = RequestBodyMaterializer(store, settings.request_body)
    driver = HttpExecutionDriver(
        request_provider=ConfigurationRequestProvider(
            request.project.prepared,
            context,
            HttpRequestFinalizer(request_materializer),
        ),
        exchange_preparer=PolicyHttpExchangePreparer(
            policy=request.project.policy,
            plan_fingerprint=request.project.prepared.plan.fingerprint,
            resolver=SystemHostResolver(),
            approve_mutations=request.approve_mutations,
        ),
        transport=transport,
        snapshot_provider=snapshots,
        retry_waiter=ProductionRetryWaiter(),
        redirect_resolver=StandardRedirectResolver(),
        cancellation=context.cancellation,
        maximum_attempts=request.project.prepared.effective.maximum_attempts,
    )
    return ArtifactCapturingDriver(
        driver=driver,
        snapshots=context,
        bodies=TransientArtifactBodyReader(store),
        blobs=spool,
        policy=settings.artifact,
        redaction=RedactionPolicy(),
    )


def _capture_budget_origin(limits: ResponseCaptureLimits) -> DefaultBudgetOrigin:
    payload = msgspec.json.encode(limits, order="deterministic")
    fingerprint = Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(payload).hexdigest()),
    )
    return DefaultBudgetOrigin(
        default_set=VersionedDefaultSetRef(
            identifier=_RUNTIME_DEFAULT_SET,
            version=_RUNTIME_DEFAULT_VERSION,
            fingerprint=fingerprint,
        )
    )


def _artifact_sink(
    project: PreparedProjectPlan,
    selected: Path | None,
    policy: ArtifactPolicy,
) -> tuple[LocalArtifactBundleSink | None, str | None]:
    if policy.persist is ArtifactPersistMode.NEVER:
        return None, None
    if selected is None:
        return None, "artifact.root-required"
    root = selected if selected.is_absolute() else project.project_root / selected
    try:
        prospective = root.resolve(strict=False)
    except OSError:
        return None, "artifact.root-unavailable"
    if not _artifact_root_allowed(project, prospective):
        return None, "artifact.root-denied"
    try:
        root = root.resolve(strict=True)
    except OSError:
        return None, "artifact.root-unavailable"
    if not _artifact_root_allowed(project, root):
        return None, "artifact.root-denied"
    try:
        return LocalArtifactBundleSink(root), None
    except LocalArtifactStoreError:
        return None, "artifact.root-unsafe"


def _artifact_root_allowed(project: PreparedProjectPlan, root: Path) -> bool:
    authority = project.policy.artifacts
    if not authority.allow_sanitized_persistence:
        return False
    return any(root.is_relative_to(Path(item)) for item in authority.roots)


async def _persist(
    sink: LocalArtifactBundleSink | None,
    artifact: RunArtifact,
    spool: PosixSanitizedBlobSpool,
    policy: ArtifactPolicy,
) -> ArtifactPersistenceOutcome | None:
    if sink is None or policy.persist is ArtifactPersistMode.NEVER:
        return None
    if (
        policy.persist is ArtifactPersistMode.ON_GATE_FAILURE
        and artifact.gate.verdict is not ArtifactGateVerdict.FAILED
    ):
        return None
    return await sink.persist(artifact, spool, policy.limits)


def _persistence_code(outcome: ArtifactPersistenceOutcome) -> str:
    failure = outcome.failure
    code = ArtifactStoreErrorCode.IO_FAILED if failure is None else failure.code
    return f"artifact.persistence.{code.value}"


def _artifact_id(prepared: PreparedRun) -> str:
    return f"run-{prepared.plan.run_id.value}"


def _failure(
    kind: ProductionExecutionFailureKind,
    diagnostics: tuple[ApplicationDiagnostic, ...],
) -> ProductionExecutionResult:
    return ProductionExecutionResult(
        failure=ProductionExecutionFailure(kind=kind, diagnostics=diagnostics)
    )


def _failure_code(
    kind: ProductionExecutionFailureKind,
    code: str,
) -> ProductionExecutionResult:
    return _failure(kind, _diagnostics(code))


def _diagnostics(code: str) -> tuple[ApplicationDiagnostic, ...]:
    return (
        ApplicationDiagnostic(
            code=code,
            severity="error",
            stage="execution",
        ),
    )


__all__ = (
    "ProductionExecutionFailure",
    "ProductionExecutionFailureKind",
    "ProductionExecutionRequest",
    "ProductionExecutionResult",
    "ProductionExecutionSettings",
    "execute_production",
    "persisted_production_execution_defaults_v1",
    "production_execution_defaults_v1",
)
