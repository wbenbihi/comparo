"""Tests for the offline CLI-to-application request-rendering bridge."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application.environment_binding import CapturedAmbientEnvironment
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.planning import PlanningOptions
from comparo.application.render_preview import RenderedBodyOmission
from comparo.application.render_preview import RenderedRequestPreview
from comparo.application.render_preview import RenderPreviewRequest
from comparo.application.render_preview import RenderPreviewResult
from comparo.application.render_preview import RequestBodyOmissionReason
from comparo.cli import rendering as rendering_module
from comparo.cli.contracts import DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1
from comparo.cli.contracts import DEFAULT_CLI_RENDER_LIMITS_V1
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import RenderRequest
from comparo.cli.rendering import render_cli_request

_INVOCATION_ID = UUID("00000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000002")


def _write_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: .comparo\n"
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    environments: [{$use: staging}]\n",
        encoding="utf-8",
    )
    data = root / ".comparo"
    data.mkdir()
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: staging, name: Staging}\n"
        "spec: {baseUrl: https://staging.example.test}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: ping, name: Ping}\n"
        "spec: {request: {method: GET, endpoint: /ping}}\n",
        encoding="utf-8",
    )
    return config


def _request(config: Path) -> RenderRequest:
    return RenderRequest(
        plan=PlanRequest(
            config=config,
            additional_sources=(),
            policy=None,
            execution_profile=None,
            subjects=(),
            environments=(),
            baseline=None,
            candidates=(),
            matrices=(),
            trials=None,
            maximum_attempts=None,
            fail_fast=None,
            budgets=(),
            output=CliOutputMode.HUMAN,
        ),
        limits=DEFAULT_CLI_RENDER_LIMITS_V1,
    )


def _coordinate(kind: str, resource_id: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="demo",
        kind=kind,
        resource_id=resource_id,
        fingerprint="0" * 64,
    )


def _preview() -> RenderedRequestPreview:
    return RenderedRequestPreview(
        ordinal=0,
        execution_id="execution-0",
        trial_id="trial-0",
        cell_id="cell-0",
        subject=_coordinate("Request", "ping"),
        environment=_coordinate("Environment", "staging"),
        method="GET",
        url="https://staging.example.test/ping",
        headers=(),
        cookies=(),
        body=RenderedBodyOmission(
            reason=RequestBodyOmissionReason.NO_BODY,
            byte_count=None,
        ),
    )


def test_invalid_configuration_limits_precede_preflight_and_value_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("project preflight was reached")

    monkeypatch.setattr(rendering_module, "prepare_project_plan", unexpected)
    request = _request(Path("must-not-open.yaml"))
    request = msgspec.structs.replace(
        request,
        plan=msgspec.structs.replace(
            request.plan,
            load_limits=msgspec.structs.replace(
                DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1,
                maximum_yaml_bytes=0,
            ),
        ),
    )
    outcome = asyncio.run(
        render_cli_request(
            request,
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert outcome.previews == ()
    assert outcome.diagnostics[0].code == "cli.configuration-load.limits.invalid"


def test_bridge_applies_every_caller_limit_and_stays_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[RenderPreviewRequest] = []

    async def render(request: RenderPreviewRequest) -> RenderPreviewResult:
        captured.append(request)
        return RenderPreviewResult(previews=(_preview(),))

    monkeypatch.setattr(rendering_module, "render_prepared_requests", render)
    limits = msgspec.structs.replace(
        DEFAULT_CLI_RENDER_LIMITS_V1,
        maximum_file_bytes=2,
        maximum_file_path_bytes=3,
        maximum_file_segments=5,
        maximum_environment_files=7,
        maximum_environment_file_bytes=11,
        maximum_environment_total_bytes=13,
        maximum_environment_entries=17,
        maximum_environment_line_bytes=19,
        maximum_yaml_bytes=23,
        maximum_yaml_depth=29,
        maximum_yaml_nodes=31,
        maximum_redaction_patterns=37,
        maximum_redaction_pattern_bytes=41,
        maximum_redaction_value_bytes=43,
        maximum_redaction_value_nodes=47,
        maximum_output_bytes=53,
    )
    request = msgspec.structs.replace(_request(_write_project(tmp_path)), limits=limits)
    result = asyncio.run(
        render_cli_request(
            request,
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.previews == (_preview(),)
    application = captured[0]
    assert type(application.ambient_environment) is CapturedAmbientEnvironment
    settings = application.settings
    assert settings.files.maximum_bytes.value == 2
    assert settings.files.maximum_path_bytes.value == 3
    assert settings.files.maximum_segments.value == 5
    assert settings.environment.maximum_files.value == 7
    assert settings.environment.maximum_file_bytes.value == 11
    assert settings.environment.maximum_total_bytes.value == 13
    assert settings.environment.maximum_entries.value == 17
    assert settings.environment.maximum_line_bytes.value == 19
    assert settings.yaml.maximum_bytes == 23
    assert settings.yaml.maximum_depth == 29
    assert settings.yaml.maximum_nodes == 31
    assert settings.sanitization.maximum_patterns.value == 37
    assert settings.sanitization.maximum_pattern_bytes.value == 41
    assert settings.sanitization.maximum_value_bytes.value == 43
    assert settings.sanitization.maximum_value_nodes.value == 47


def test_ambient_capture_failure_is_a_value_free_render_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "ambient-render-canary"
    rendered = False

    def capture(*_arguments: object, **_keywords: object) -> object:
        raise ValueError(canary)

    async def render(_request_value: object) -> RenderPreviewResult:
        nonlocal rendered
        rendered = True
        return RenderPreviewResult(previews=(_preview(),))

    monkeypatch.setattr(rendering_module, "capture_process_environment", capture)
    monkeypatch.setattr(rendering_module, "render_prepared_requests", render)

    result = asyncio.run(
        render_cli_request(
            _request(_write_project(tmp_path)),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )

    assert result.previews == ()
    assert result.diagnostics == (
        ApplicationDiagnostic(
            code="cli.render.environment-capture-failed",
            severity="error",
            stage="invocation",
        ),
    )
    assert canary not in repr(result)
    assert not rendered


def test_bridge_renders_a_real_project_end_to_end_without_network(tmp_path: Path) -> None:
    result = asyncio.run(
        render_cli_request(
            _request(_write_project(tmp_path)),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert not result.diagnostics
    assert len(result.previews) == 1
    preview = result.previews[0]
    assert preview.method == "GET"
    assert preview.url == "https://staging.example.test/ping"
    assert preview.body.reason is RequestBodyOmissionReason.NO_BODY


@pytest.mark.parametrize("output_limit", [0, -1])
def test_invalid_limits_are_rejected_before_project_source_resolution(
    tmp_path: Path,
    output_limit: int,
) -> None:
    limits = msgspec.structs.replace(
        DEFAULT_CLI_RENDER_LIMITS_V1,
        maximum_output_bytes=output_limit,
    )
    request = msgspec.structs.replace(_request(tmp_path / "secret-missing.yaml"), limits=limits)
    result = asyncio.run(
        render_cli_request(
            request,
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert result.diagnostics[0].code == "cli.render.limits.invalid"
    assert result.diagnostics[0].stage == "invocation"


def test_project_and_application_diagnostics_cross_only_the_safe_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = asyncio.run(
        render_cli_request(
            _request(tmp_path / "sensitive-missing.yaml"),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert missing.diagnostics

    async def reject(_request: RenderPreviewRequest) -> RenderPreviewResult:
        return RenderPreviewResult(
            diagnostics=(
                ApplicationDiagnostic(
                    code="runtime.request.sensitive-target",
                    severity="error",
                    stage="request-preparation",
                ),
            )
        )

    monkeypatch.setattr(rendering_module, "render_prepared_requests", reject)
    rejected = asyncio.run(
        render_cli_request(
            _request(_write_project(tmp_path / "project")),
            PlanningOptions(),
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert rejected.diagnostics[0].code == "runtime.request.sensitive-target"


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("request", "RenderRequest"),
        ("options", "PlanningOptions"),
        ("invocation_id", "UUID"),
        ("run_id", "UUID"),
    ],
)
def test_render_bridge_rejects_untyped_boundary_inputs(field: str, message: str) -> None:
    values: dict[str, object] = {
        "request": _request(Path("unused.yaml")),
        "options": PlanningOptions(),
        "invocation_id": _INVOCATION_ID,
        "run_id": _RUN_ID,
    }
    values[field] = object()
    with pytest.raises(TypeError, match=message):
        asyncio.run(
            render_cli_request(
                cast("RenderRequest", values["request"]),
                cast("PlanningOptions", values["options"]),
                invocation_id=cast("UUID", values["invocation_id"]),
                run_id=cast("UUID", values["run_id"]),
            )
        )
