"""Tests for offline render service translation and terminal-safe output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.planning import PlanningOptions
from comparo.application.render_preview import RenderedBodyOmission
from comparo.application.render_preview import RenderedCookie
from comparo.application.render_preview import RenderedHeader
from comparo.application.render_preview import RenderedRequestPreview
from comparo.application.render_preview import RenderPreviewResult
from comparo.application.render_preview import RequestBodyOmissionReason
from comparo.cli import services as service_module
from comparo.cli.contracts import DEFAULT_CLI_RENDER_LIMITS_V1
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import RenderRequest
from comparo.cli.services import DefaultCliServices

_ESC = "\x1b"
_C1 = "\u009b"
_BIDI = "\u202e"
_SECRET = "malformed-super-secret-value"


def _request(
    *,
    output: CliOutputMode = CliOutputMode.HUMAN,
    matrices: tuple[str, ...] = (),
    maximum_output_bytes: int | None = None,
) -> RenderRequest:
    limits = DEFAULT_CLI_RENDER_LIMITS_V1
    if maximum_output_bytes is not None:
        limits = msgspec.structs.replace(limits, maximum_output_bytes=maximum_output_bytes)
    return RenderRequest(
        plan=PlanRequest(
            config=Path("comparo.yaml"),
            additional_sources=(),
            policy=None,
            execution_profile="release",
            subjects=("ping",),
            environments=("staging",),
            baseline=None,
            candidates=(),
            matrices=matrices,
            trials=2,
            maximum_attempts=3,
            fail_fast=True,
            budgets=("requests=4",),
            output=output,
        ),
        limits=limits,
    )


def _coordinate(kind: str, resource_id: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="demo",
        kind=kind,
        resource_id=resource_id,
        fingerprint="0" * 64,
    )


def _preview() -> RenderedRequestPreview:
    hostile = f"public{_ESC}]8;;https://evil.test\x07{_C1}{_BIDI}value"
    return RenderedRequestPreview(
        ordinal=0,
        execution_id="execution-0",
        trial_id="trial-0",
        cell_id="cell-0",
        subject=_coordinate("Request", "ping"),
        environment=_coordinate("Environment", "staging"),
        method="GET",
        url=f"https://example.test/{hostile}",
        headers=(RenderedHeader(name="x-public", value=hostile, redacted=False),),
        cookies=(RenderedCookie(name="session", value="<redacted>", redacted=True),),
        body=RenderedBodyOmission(
            reason=RequestBodyOmissionReason.NO_BODY,
            byte_count=None,
        ),
    )


def test_render_service_uses_shared_planning_translation_and_escapes_human_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[PlanningOptions] = []

    async def render(
        request: RenderRequest,
        options: PlanningOptions,
        **identifiers: object,
    ) -> RenderPreviewResult:
        del request
        assert set(identifiers) == {"invocation_id", "run_id"}
        captured.append(options)
        return RenderPreviewResult(previews=(_preview(),))

    monkeypatch.setattr(service_module, "render_cli_request", render)
    result = DefaultCliServices().render(_request())
    assert result.exit_code is CliExitCode.SUCCESS
    assert result.payload_kind is CliPayloadKind.TEXT
    text = result.payload.decode()
    assert text.startswith("Rendered 1 request(s).\n")
    assert "body: omitted (no-body)" in text
    assert "<redacted>" in text
    assert "\\u001b" in text
    assert "\\u009b" in text
    assert "\\u202e" in text
    assert _ESC not in text
    assert _C1 not in text
    assert _BIDI not in text
    options = captured[0]
    assert options.execution_profile == "release"
    assert options.environments == ("staging",)
    assert options.trials == 2
    assert options.maximum_attempts == 3
    assert options.fail_fast
    assert options.budgets[0].limit == 4


def test_render_json_contains_the_complete_sanitized_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def render(*args: object, **kwargs: object) -> RenderPreviewResult:
        del args, kwargs
        return RenderPreviewResult(previews=(_preview(),))

    monkeypatch.setattr(service_module, "render_cli_request", render)
    result = DefaultCliServices().render(_request(output=CliOutputMode.JSON))
    assert result.payload_kind is CliPayloadKind.JSON
    document = cast("dict[str, object]", json.loads(result.payload))
    assert document["status"] == "rendered"
    previews = cast("list[dict[str, object]]", document["requests"])
    assert previews[0]["method"] == "GET"
    assert cast("dict[str, object]", previews[0]["body"])["reason"] == "no-body"
    cookies = cast("list[dict[str, object]]", previews[0]["cookies"])
    assert cookies[0]["value"] == "<redacted>"


@pytest.mark.parametrize("output", [CliOutputMode.HUMAN, CliOutputMode.JSON])
def test_render_diagnostics_are_all_or_nothing_and_value_free(
    monkeypatch: pytest.MonkeyPatch,
    output: CliOutputMode,
) -> None:
    async def render(*args: object, **kwargs: object) -> RenderPreviewResult:
        del args, kwargs
        return RenderPreviewResult(
            diagnostics=(
                ApplicationDiagnostic(
                    code="runtime.request.sensitive-target",
                    severity="error",
                    stage="request-preparation",
                ),
            )
        )

    monkeypatch.setattr(service_module, "render_cli_request", render)
    result = DefaultCliServices().render(_request(output=output))
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    if output is CliOutputMode.HUMAN:
        assert result.messages[0].text == "Request rendering is invalid (1 issue(s))."
        assert result.messages[1].text == ("runtime.request.sensitive-target (request-preparation)")
    else:
        document = cast("dict[str, object]", json.loads(result.payload))
        assert document["status"] == "invalid"
        assert "requests" not in document


@pytest.mark.parametrize("output", [CliOutputMode.HUMAN, CliOutputMode.JSON])
def test_render_output_limit_fails_without_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    output: CliOutputMode,
) -> None:
    async def render(*args: object, **kwargs: object) -> RenderPreviewResult:
        del args, kwargs
        return RenderPreviewResult(previews=(_preview(),))

    monkeypatch.setattr(service_module, "render_cli_request", render)
    result = DefaultCliServices().render(_request(output=output, maximum_output_bytes=1))
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    if output is CliOutputMode.HUMAN:
        assert result.payload == b""
        assert result.messages[1].text == "cli.render.output-limit-exceeded (render)"
    else:
        assert b'"requests"' not in result.payload
        assert b'"cli.render.output-limit-exceeded"' in result.payload


def test_invalid_planning_syntax_never_reaches_render_or_echoes_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def render(*args: object, **kwargs: object) -> RenderPreviewResult:
        nonlocal called
        del args, kwargs
        called = True
        return RenderPreviewResult(previews=(_preview(),))

    monkeypatch.setattr(service_module, "render_cli_request", render)
    result = DefaultCliServices().render(_request(matrices=(_SECRET,)))
    assert result.exit_code is CliExitCode.INVALID_INVOCATION
    assert result.messages[1].text == "cli.plan.matrix.invalid (invocation)"
    assert _SECRET not in "\n".join(item.text for item in result.messages)
    assert not called
