"""Framework-neutral execution bridge shared by every effectful CLI command."""

from __future__ import annotations

from typing import final
from uuid import UUID

import msgspec

from comparo.adapters.http import BodyMaterializationLimits
from comparo.adapters.http import ResponseCaptureLimits
from comparo.adapters.http import StreamDecodingLimits
from comparo.adapters.http import TransientBodyStoreConfig
from comparo.adapters.http.ordinary import OrdinaryResponseDecodeLimits
from comparo.application.environment_binding import capture_process_environment
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.planning import PlanningOptions
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.production_execution import ProductionExecutionFailureKind
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.production_execution import ProductionExecutionSettings
from comparo.application.production_execution import execute_production
from comparo.application.production_execution import persisted_production_execution_defaults_v1
from comparo.application.production_execution import production_execution_defaults_v1
from comparo.core.artifacts.common import ArtifactCompletionKind
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.ports import ArtifactPersistenceOutcome
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio

from .contracts import CliExecutionCommandKind
from .contracts import CliExecutionLimits
from .contracts import CliExitCode
from .contracts import ExecutionArtifactSummary
from .contracts import ExecutionRequest
from .loading import policy_load_limits
from .loading import project_load_limits


@final
class CliExecutionOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one safe artifact projection or value-free diagnostic failure."""

    exit_code: CliExitCode
    summary: ExecutionArtifactSummary | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep live execution state outside the CLI service boundary."""
        if type(self.exit_code) is not CliExitCode:
            raise TypeError("CLI execution outcome exit code must be typed")
        if self.summary is not None and type(self.summary) is not ExecutionArtifactSummary:
            raise TypeError("CLI execution outcome summary must be typed")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("CLI execution diagnostics must be immutable and typed")
        if (self.summary is None) == bool(self.diagnostics):
            return
        raise ValueError("CLI execution outcome requires exactly summary or diagnostics")


def production_execution_settings(
    limits: CliExecutionLimits,
    *,
    persist: bool,
) -> ProductionExecutionSettings:
    """Replace every exposed runtime limit over one explicit versioned default set."""
    defaults = production_execution_defaults_v1()
    capture = ResponseCaptureLimits(
        maximum_encoded_body_bytes=ByteCount(value=limits.maximum_encoded_body_bytes),
        maximum_entity_body_bytes=ByteCount(value=limits.maximum_entity_body_bytes),
        maximum_expansion_ratio=Ratio(
            numerator=limits.maximum_expansion_ratio,
            denominator=PositiveCount(value=1),
        ),
    )
    body_store = TransientBodyStoreConfig(
        memory_threshold=ByteCount(value=limits.body_memory_threshold_bytes),
        maximum_transient_bytes=ByteCount(value=limits.maximum_transient_body_bytes),
        io_chunk_size=defaults.body_store.io_chunk_size,
        root=defaults.body_store.root,
    )
    request_body = BodyMaterializationLimits(
        maximum_body_bytes=ByteCount(value=limits.maximum_request_body_bytes),
        maximum_json_depth=PositiveCount(value=limits.maximum_request_json_depth),
        maximum_json_nodes=PositiveCount(value=limits.maximum_request_json_nodes),
    )
    streams = StreamDecodingLimits(
        maximum_records=PositiveCount(value=limits.maximum_stream_records),
        maximum_controls=PositiveCount(value=limits.maximum_stream_controls),
        maximum_errors=PositiveCount(value=limits.maximum_stream_errors),
        maximum_item_bytes=ByteCount(value=limits.maximum_stream_item_bytes),
    )
    ordinary = OrdinaryResponseDecodeLimits(
        maximum_body_bytes=ByteCount(value=limits.maximum_decoded_body_bytes),
        maximum_text_characters=Count(value=limits.maximum_text_characters),
        maximum_json_depth=Count(value=limits.maximum_json_depth),
        maximum_json_nodes=Count(value=limits.maximum_json_nodes),
        maximum_json_tokens=Count(value=limits.maximum_json_tokens),
    )
    artifact_limits = ArtifactLimits(
        maximum_blobs=PositiveCount(value=limits.artifact.maximum_blobs),
        maximum_blob_bytes=ByteCount(value=limits.artifact.maximum_blob_bytes),
        maximum_total_blob_bytes=ByteCount(value=limits.artifact.maximum_total_blob_bytes),
        maximum_manifest_bytes=ByteCount(value=limits.artifact.maximum_manifest_bytes),
        maximum_report_bytes=ByteCount(value=limits.artifact.maximum_report_bytes),
        sanitization=SanitizationLimits(
            maximum_patterns=PositiveCount(value=limits.artifact.maximum_redaction_patterns),
            maximum_pattern_bytes=ByteCount(value=limits.artifact.maximum_redaction_pattern_bytes),
            maximum_value_bytes=ByteCount(value=limits.artifact.maximum_redaction_value_bytes),
            maximum_value_nodes=PositiveCount(value=limits.artifact.maximum_redaction_value_nodes),
        ),
    )
    persistence = ArtifactPersistMode.ALWAYS if persist else ArtifactPersistMode.NEVER
    artifact = msgspec.structs.replace(
        defaults.artifact,
        persist=persistence,
        limits=artifact_limits,
    )
    if type(artifact) is not ArtifactPolicy:  # pragma: no cover - msgspec typing invariant
        raise TypeError("production artifact policy replacement returned an invalid type")
    transport = msgspec.structs.replace(
        defaults.transport,
        capture=capture,
        classification=(
            persisted_production_execution_defaults_v1().transport.classification
            if persist
            else defaults.transport.classification
        ),
        maximum_authorization_occurrences=PositiveCount(
            value=limits.maximum_authorization_occurrences
        ),
    )
    return ProductionExecutionSettings(
        files=defaults.files,
        environment=defaults.environment,
        yaml=defaults.yaml,
        body_store=body_store,
        request_body=request_body,
        transport=transport,
        streams=streams,
        ordinary=ordinary,
        evaluation=defaults.evaluation,
        schemas=defaults.schemas,
        regexes=defaults.regexes,
        digests=defaults.digests,
        remote_schemas=defaults.remote_schemas,
        artifact=artifact,
        artifact_io_chunk_size=defaults.artifact_io_chunk_size,
    )


def _topology_diagnostic(
    command: CliExecutionCommandKind,
    *,
    compares: bool,
) -> ApplicationDiagnostic | None:
    """Reject a command/effective-plan mismatch before any runtime capability is acquired."""
    if command is CliExecutionCommandKind.RUN and compares:
        code = "cli.execution.topology.assertion-required"
    elif command is CliExecutionCommandKind.DIFF and not compares:
        code = "cli.execution.topology.comparison-required"
    else:
        return None
    return ApplicationDiagnostic(code=code, severity="error", stage="invocation")


def _exit_code(artifact: RunArtifact) -> CliExitCode:
    """Derive process status only from the sanitized artifact truth."""
    if artifact.gate.verdict is ArtifactGateVerdict.FAILED:
        return CliExitCode.GATE_FAILED
    if artifact.completion.kind is ArtifactCompletionKind.REJECTED:
        return CliExitCode.INVALID_INVOCATION
    if artifact.completion.kind is not ArtifactCompletionKind.COMPLETE:
        return CliExitCode.EXECUTION_INCOMPLETE
    return CliExitCode.SUCCESS


def _summary(
    artifact: RunArtifact,
    persistence: ArtifactPersistenceOutcome | None,
) -> ExecutionArtifactSummary:
    """Project only safe identifiers and aggregate artifact state."""
    return ExecutionArtifactSummary(
        api_version=artifact.api_version,
        artifact_id=artifact.metadata.artifact_id,
        run_id=artifact.metadata.run_id,
        invocation_id=artifact.metadata.invocation_id,
        completion=artifact.completion.kind.value,
        gate=artifact.gate.verdict.value,
        executions=len(artifact.executions),
        comparisons=len(artifact.comparisons),
        assertions=sum(len(item.assertions) for item in artifact.executions),
        diffs=sum(len(item.diffs) for item in artifact.comparisons),
        coverage_issues=len(artifact.coverage_issues),
        persistence="not-persisted" if persistence is None else persistence.status.value,
        bundle_id=None if persistence is None else persistence.bundle_id,
    )


def _outcome(result: ProductionExecutionResult) -> CliExecutionOutcome:
    """Translate one sanitized production result into the CLI's closed result contract."""
    if result.artifact is None:
        failure = result.failure
        if failure is None:  # pragma: no cover - production result invariant
            raise RuntimeError("failed production result omitted its typed failure")
        exit_code = {
            ProductionExecutionFailureKind.INVALID: CliExitCode.INVALID_INVOCATION,
            ProductionExecutionFailureKind.INCOMPLETE: CliExitCode.EXECUTION_INCOMPLETE,
            ProductionExecutionFailureKind.INTERNAL: CliExitCode.INTERNAL_ERROR,
        }[failure.kind]
        return CliExecutionOutcome(
            exit_code=exit_code,
            diagnostics=failure.diagnostics,
        )
    return CliExecutionOutcome(
        exit_code=_exit_code(result.artifact),
        summary=_summary(result.artifact, result.persistence),
    )


async def execute_cli_request(
    request: ExecutionRequest,
    options: PlanningOptions,
    *,
    invocation_id: UUID,
    run_id: UUID,
) -> CliExecutionOutcome:
    """Prepare, validate command topology, and execute through the production composition."""
    if type(request) is not ExecutionRequest:
        raise TypeError("CLI execution requires ExecutionRequest")
    if type(options) is not PlanningOptions:
        raise TypeError("CLI execution requires PlanningOptions")
    if type(invocation_id) is not UUID or type(run_id) is not UUID:
        raise TypeError("CLI execution identifiers must be exact UUID values")
    try:
        load_limits = project_load_limits(request.plan.load_limits)
        policy_limits = policy_load_limits(request.plan.load_limits)
    except (TypeError, ValueError):
        return CliExecutionOutcome(
            exit_code=CliExitCode.INVALID_INVOCATION,
            diagnostics=(
                ApplicationDiagnostic(
                    code="cli.configuration-load.limits.invalid",
                    severity="error",
                    stage="invocation",
                ),
            ),
        )
    try:
        settings = production_execution_settings(request.limits, persist=request.persist)
    except (TypeError, ValueError):
        return CliExecutionOutcome(
            exit_code=CliExitCode.INVALID_INVOCATION,
            diagnostics=(
                ApplicationDiagnostic(
                    code="cli.execution.limits.invalid",
                    severity="error",
                    stage="invocation",
                ),
            ),
        )
    prepared = prepare_project_plan(
        ProjectPlanRequest(
            config=request.plan.config,
            additional_sources=request.plan.additional_sources,
            policy=request.plan.policy,
            options=options,
            invocation_id=invocation_id,
            run_id=run_id,
            load_limits=load_limits,
            policy_load_limits=policy_limits,
        )
    )
    if prepared.project is None:
        return CliExecutionOutcome(
            exit_code=CliExitCode.INVALID_INVOCATION,
            diagnostics=prepared.diagnostics,
        )
    topology = _topology_diagnostic(
        request.command,
        compares=bool(prepared.project.prepared.effective.comparisons),
    )
    if topology is not None:
        return CliExecutionOutcome(
            exit_code=CliExitCode.INVALID_INVOCATION,
            diagnostics=(topology,),
        )
    try:
        ambient_environment = capture_process_environment(
            prepared.project,
            settings.environment,
        )
    except Exception:  # noqa: BLE001 - ambient values never cross the CLI boundary
        return CliExecutionOutcome(
            exit_code=CliExitCode.INVALID_INVOCATION,
            diagnostics=(
                ApplicationDiagnostic(
                    code="cli.execution.environment-capture-failed",
                    severity="error",
                    stage="invocation",
                ),
            ),
        )
    result = await execute_production(
        ProductionExecutionRequest(
            project=prepared.project,
            settings=settings,
            ambient_environment=ambient_environment,
            artifact_root=request.artifact_root,
            approve_mutations=request.approve_mutations,
            tool_version=request.tool_version,
        )
    )
    return _outcome(result)


__all__ = (
    "CliExecutionOutcome",
    "execute_cli_request",
    "production_execution_settings",
)
