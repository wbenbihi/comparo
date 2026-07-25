"""Framework-neutral bridge from CLI planning to safe offline request previews."""

from __future__ import annotations

from uuid import UUID

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import LocalFileLimits
from comparo.adapters.values import YamlValueLimits
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.planning import PlanningOptions
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.render_preview import RenderPreviewRequest
from comparo.application.render_preview import RenderPreviewResult
from comparo.application.render_preview import RenderPreviewSettings
from comparo.application.render_preview import render_prepared_requests
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount

from .contracts import CliRenderLimits
from .contracts import RenderRequest
from .loading import policy_load_limits
from .loading import project_load_limits


def _failure(code: str) -> RenderPreviewResult:
    """Create one value-free invocation diagnostic."""
    return RenderPreviewResult(
        diagnostics=(
            ApplicationDiagnostic(
                code=code,
                severity="error",
                stage="invocation",
            ),
        )
    )


def _settings(limits: CliRenderLimits) -> RenderPreviewSettings:
    """Translate every explicit CLI ceiling into application-owned adapter contracts."""
    if limits.maximum_output_bytes < 1:
        raise ValueError("render output limit must be positive")
    return RenderPreviewSettings(
        files=LocalFileLimits(
            maximum_bytes=ByteCount(value=limits.maximum_file_bytes),
            maximum_path_bytes=ByteCount(value=limits.maximum_file_path_bytes),
            maximum_segments=PositiveCount(value=limits.maximum_file_segments),
        ),
        environment=EnvironmentSourceLimits(
            maximum_files=PositiveCount(value=limits.maximum_environment_files),
            maximum_file_bytes=ByteCount(value=limits.maximum_environment_file_bytes),
            maximum_total_bytes=ByteCount(value=limits.maximum_environment_total_bytes),
            maximum_entries=PositiveCount(value=limits.maximum_environment_entries),
            maximum_line_bytes=ByteCount(value=limits.maximum_environment_line_bytes),
        ),
        yaml=YamlValueLimits(
            maximum_bytes=limits.maximum_yaml_bytes,
            maximum_depth=limits.maximum_yaml_depth,
            maximum_nodes=limits.maximum_yaml_nodes,
        ),
        sanitization=SanitizationLimits(
            maximum_patterns=PositiveCount(value=limits.maximum_redaction_patterns),
            maximum_pattern_bytes=ByteCount(value=limits.maximum_redaction_pattern_bytes),
            maximum_value_bytes=ByteCount(value=limits.maximum_redaction_value_bytes),
            maximum_value_nodes=PositiveCount(value=limits.maximum_redaction_value_nodes),
        ),
    )


async def render_cli_request(
    request: RenderRequest,
    options: PlanningOptions,
    *,
    invocation_id: UUID,
    run_id: UUID,
) -> RenderPreviewResult:
    """Prepare and render all requests without introducing a network-capable adapter."""
    if type(request) is not RenderRequest:
        raise TypeError("CLI rendering requires RenderRequest")
    if type(options) is not PlanningOptions:
        raise TypeError("CLI rendering requires PlanningOptions")
    if type(invocation_id) is not UUID or type(run_id) is not UUID:
        raise TypeError("CLI rendering identifiers must be exact UUID values")
    try:
        settings = _settings(request.limits)
    except (TypeError, ValueError):
        return _failure("cli.render.limits.invalid")
    try:
        load_limits = project_load_limits(request.plan.load_limits)
        policy_limits = policy_load_limits(request.plan.load_limits)
    except (TypeError, ValueError):
        return _failure("cli.configuration-load.limits.invalid")
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
        return RenderPreviewResult(diagnostics=prepared.diagnostics)
    return await render_prepared_requests(
        RenderPreviewRequest(
            project=prepared.project,
            settings=settings,
            ambient_environment=None,
        )
    )


__all__ = ("render_cli_request",)
