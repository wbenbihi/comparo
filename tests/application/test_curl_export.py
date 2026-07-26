"""Tests for canonical secret-safe cURL export from persisted artifacts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from comparo.application.artifacts import ArtifactBodyMessage
from comparo.application.artifacts import ArtifactBodyReference
from comparo.application.curl_export import CurlExportError
from comparo.application.curl_export import CurlExportErrorCode
from comparo.application.curl_export import CurlExportLimits
from comparo.application.curl_export import CurlExportRequest
from comparo.application.curl_export import CurlExportWarning
from comparo.application.curl_export import export_artifact_curl
from comparo.core.artifacts.model import RunArtifact
from tests.application.test_artifacts import _bundle
from tests.application.test_artifacts import _limits


def _request(
    root: Path,
    artifact: RunArtifact,
    *,
    body: int = 1024,
    command: int = 8192,
) -> CurlExportRequest:
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    return CurlExportRequest(
        root=root,
        bundle_id=artifact.metadata.artifact_id,
        reference=ArtifactBodyReference(
            execution_id=execution.execution_id,
            attempt_id=attempt.attempt_id,
            exchange_id=exchange.exchange_id,
            message=ArtifactBodyMessage.REQUEST,
        ),
        artifact_limits=_limits(),
        export_limits=CurlExportLimits(
            maximum_embedded_body_bytes=body,
            maximum_command_characters=command,
        ),
    )


def test_curl_embeds_complete_utf8_sanitized_body_without_live_values(tmp_path: Path) -> None:
    root, artifact = _bundle(tmp_path)

    exported = asyncio.run(export_artifact_curl(_request(root, artifact)))

    assert exported.executable is True
    assert exported.exact_for_sanitized_capture is True
    assert exported.command.startswith("printf '%s' 'left' | curl ")
    assert "--data-binary '@-'" in exported.command
    assert "right" not in exported.command
    assert "accept: application/json" in exported.command
    assert exported.redaction_count == 0


def test_curl_uses_non_executable_placeholder_when_body_exceeds_export_budget(
    tmp_path: Path,
) -> None:
    root, artifact = _bundle(tmp_path)

    exported = asyncio.run(export_artifact_curl(_request(root, artifact, body=3)))

    assert exported.executable is False
    assert exported.exact_for_sanitized_capture is False
    assert "@<path-to-sanitized-body>" in exported.command
    assert CurlExportWarning.BODY_FILE_REQUIRED in exported.warnings
    assert "left" not in exported.command


def test_curl_refuses_to_truncate_the_final_command(tmp_path: Path) -> None:
    root, artifact = _bundle(tmp_path)

    with pytest.raises(CurlExportError) as caught:
        asyncio.run(export_artifact_curl(_request(root, artifact, command=10)))

    assert caught.value.code is CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED
