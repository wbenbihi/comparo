"""Tests for canonical secret-safe cURL export from persisted artifacts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import msgspec
import pytest

from comparo.application import curl_export as curl_module
from comparo.application.artifacts import ArtifactBodyMessage
from comparo.application.artifacts import ArtifactBodyReference
from comparo.application.artifacts import ArtifactBodyWindow
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.curl_export import CurlDialect
from comparo.application.curl_export import CurlExport
from comparo.application.curl_export import CurlExportError
from comparo.application.curl_export import CurlExportErrorCode
from comparo.application.curl_export import CurlExportLimits
from comparo.application.curl_export import CurlExportRequest
from comparo.application.curl_export import CurlExportWarning
from comparo.application.curl_export import export_artifact_curl
from comparo.core.artifacts.common import ARTIFACT_REDACTION_MARKER
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import ArtifactSourceCaptureState
from comparo.core.artifacts.http import ArtifactCookie
from comparo.core.artifacts.http import ArtifactField
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
    assert exported.command.startswith("printf '%s' 'left' | curl --disable --no-progress-meter ")
    assert exported.command.split(" | ", 1)[1].split()[:3] == [
        "curl",
        "--disable",
        "--no-progress-meter",
    ]
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
    for append in (curl_module._append, curl_module._append_quoted):
        with pytest.raises(CurlExportError) as incremental:
            append([], "oversized", 0, 1)
        assert incremental.value.code is CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_curl_rejects_an_oversized_quoted_field_before_allocating_its_escaped_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_quote(_value: str) -> str:
        raise AssertionError("oversized values must be rejected before quoting")

    monkeypatch.setattr(curl_module, "_quote", unexpected_quote)

    with pytest.raises(CurlExportError) as caught:
        curl_module._append_quoted([], "'" * 1_000_000, 0, 32)

    assert caught.value.code is CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_curl_contracts_reject_untyped_unbounded_or_contradictory_values(
    tmp_path: Path,
) -> None:
    root, artifact = _bundle(tmp_path)
    request = _request(root, artifact)
    with pytest.raises(TypeError, match="error code"):
        CurlExportError(cast("CurlExportErrorCode", object()))
    limit_cases: tuple[dict[str, object], ...] = (
        {"maximum_embedded_body_bytes": -1},
        {"maximum_embedded_body_bytes": cast("int", True)},
        {"maximum_command_characters": 0},
        {"maximum_command_characters": cast("int", True)},
    )
    for changes in limit_cases:
        with pytest.raises(CurlExportError) as limits:
            msgspec.structs.replace(request.export_limits, **changes)
        assert limits.value.code is CurlExportErrorCode.INVALID_REQUEST

    response_reference = msgspec.structs.replace(
        request.reference,
        message=ArtifactBodyMessage.RESPONSE,
    )
    request_cases: tuple[dict[str, object], ...] = (
        {"root": Path("relative")},
        {"root": cast("Path", object())},
        {"bundle_id": ""},
        {"bundle_id": cast("str", object())},
        {"reference": response_reference},
        {"reference": cast("ArtifactBodyReference", object())},
        {"artifact_limits": cast("ArtifactReadLimits", object())},
        {"export_limits": cast("CurlExportLimits", object())},
        {"dialect": cast("CurlDialect", object())},
    )
    for changes in request_cases:
        with pytest.raises(CurlExportError) as invalid:
            msgspec.structs.replace(request, **changes)
        assert invalid.value.code is CurlExportErrorCode.INVALID_REQUEST

    valid = CurlExport(
        dialect=CurlDialect.POSIX_SH,
        command="curl 'https://example.test'",
        executable=True,
        exact_for_sanitized_capture=True,
        warnings=(),
        redaction_count=0,
    )
    export_cases: tuple[tuple[dict[str, object], type[Exception]], ...] = (
        ({"dialect": cast("CurlDialect", object())}, TypeError),
        ({"command": cast("str", object())}, TypeError),
        ({"command": ""}, ValueError),
        ({"executable": cast("bool", 1)}, TypeError),
        ({"exact_for_sanitized_capture": cast("bool", 1)}, TypeError),
        ({"warnings": cast("tuple[CurlExportWarning, ...]", [])}, TypeError),
        ({"warnings": (cast("CurlExportWarning", object()),)}, TypeError),
        ({"warnings": (CurlExportWarning.VALUES_REDACTED,) * 2}, ValueError),
        ({"redaction_count": -1}, ValueError),
        ({"redaction_count": cast("int", True)}, ValueError),
    )
    for changes, error in export_cases:
        with pytest.raises(error):
            msgspec.structs.replace(valid, **changes)

    with pytest.raises(CurlExportError) as invalid_call:
        asyncio.run(export_artifact_curl(cast("CurlExportRequest", object())))
    assert invalid_call.value.code is CurlExportErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    ("operation_code", "export_code"),
    [
        (ArtifactOperationErrorCode.BODY_NOT_FOUND, CurlExportErrorCode.REQUEST_NOT_FOUND),
        (
            ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE,
            CurlExportErrorCode.ARTIFACT_UNAVAILABLE,
        ),
    ],
)
def test_curl_collapses_artifact_failures_without_retaining_adapter_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_code: ArtifactOperationErrorCode,
    export_code: CurlExportErrorCode,
) -> None:
    root, artifact = _bundle(tmp_path)
    canary = "curl-source-secret-canary"

    def fail(*_args: object, **_kwargs: object) -> RunArtifact:
        try:
            raise OSError(2, canary, str(tmp_path / canary))
        except OSError as source:
            raise ArtifactOperationError(operation_code) from source

    monkeypatch.setattr(curl_module, "verify_artifact", fail)
    with pytest.raises(CurlExportError) as caught:
        asyncio.run(export_artifact_curl(_request(root, artifact)))

    assert caught.value.code is export_code
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert canary not in repr(caught.value)


def test_curl_request_selection_rejects_each_unknown_coordinate(tmp_path: Path) -> None:
    _root, artifact = _bundle(tmp_path)
    request = _request(_root, artifact)
    for changes in (
        {"execution_id": "unknown"},
        {"attempt_id": "unknown"},
        {"exchange_id": "unknown"},
    ):
        with pytest.raises(CurlExportError) as caught:
            curl_module._request(
                artifact,
                msgspec.structs.replace(request.reference, **changes),
            )
        assert caught.value.code is CurlExportErrorCode.REQUEST_NOT_FOUND

    with pytest.raises(CurlExportError) as exported_missing:
        asyncio.run(
            export_artifact_curl(
                msgspec.structs.replace(
                    request,
                    reference=msgspec.structs.replace(
                        request.reference,
                        execution_id="unknown",
                    ),
                )
            )
        )
    assert exported_missing.value.code is CurlExportErrorCode.REQUEST_NOT_FOUND

    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    assert exchange.request.body is not None
    assert exchange.request.body.blob is not None
    omitted_exchange = msgspec.structs.replace(
        exchange,
        request=None,
        request_omission=ArtifactOmissionReason.POLICY,
    )
    omitted_attempt = msgspec.structs.replace(attempt, exchanges=(omitted_exchange,))
    omitted_execution = msgspec.structs.replace(execution, attempts=(omitted_attempt,))
    omitted_artifact = cast(
        "RunArtifact",
        SimpleNamespace(executions=(omitted_execution,)),
    )
    with pytest.raises(CurlExportError) as omitted:
        curl_module._request(omitted_artifact, request.reference)
    assert omitted.value.code is CurlExportErrorCode.REQUEST_NOT_FOUND


def test_curl_body_variants_are_explicit_and_never_silently_truncated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, artifact = _bundle(tmp_path)
    request = _request(root, artifact)
    captured = curl_module._request(artifact, request.reference)
    assert captured.body is not None

    no_body = msgspec.structs.replace(captured, body=None)
    assert asyncio.run(curl_module._body(request, no_body)) == ("", None, True, True, ())
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    no_body_exchange = msgspec.structs.replace(exchange, request=no_body)
    no_body_attempt = msgspec.structs.replace(attempt, exchanges=(no_body_exchange,))
    no_body_execution = msgspec.structs.replace(execution, attempts=(no_body_attempt,))
    monkeypatch.setattr(
        curl_module,
        "verify_artifact",
        lambda *_args, **_kwargs: cast(
            "RunArtifact",
            SimpleNamespace(executions=(no_body_execution,)),
        ),
    )
    no_body_export = asyncio.run(export_artifact_curl(request))
    assert "--data-binary" not in no_body_export.command
    assert no_body_export.redaction_count == 0

    partial = msgspec.structs.replace(
        captured.body,
        source_state=ArtifactSourceCaptureState.REMOTE_TRUNCATED,
        extent=ArtifactCaptureExtent.PARTIAL,
        reason=ArtifactOmissionReason.SOURCE_INCOMPLETE,
    )
    incomplete = asyncio.run(
        curl_module._body(request, msgspec.structs.replace(captured, body=partial))
    )
    assert incomplete[2:4] == (False, False)
    assert incomplete[-1] == (CurlExportWarning.BODY_CAPTURE_INCOMPLETE,)

    async def binary_window(*_args: object, **_kwargs: object) -> ArtifactBodyWindow:
        return ArtifactBodyWindow(
            reference=request.reference,
            source_state="complete",
            extent="complete",
            reason=None,
            media_type="application/octet-stream",
            total_bytes=1,
            offset=0,
            content=b"\xff",
            next_offset=None,
            redaction_count=0,
        )

    monkeypatch.setattr(curl_module, "read_artifact_body_window", binary_window)
    binary = asyncio.run(curl_module._body(request, captured))
    assert binary[-1] == (
        CurlExportWarning.BODY_NOT_TEXT,
        CurlExportWarning.BODY_FILE_REQUIRED,
    )

    async def partial_window(*_args: object, **_kwargs: object) -> ArtifactBodyWindow:
        return ArtifactBodyWindow(
            reference=request.reference,
            source_state="complete",
            extent="complete",
            reason=None,
            media_type="text/plain",
            total_bytes=2,
            offset=0,
            content=b"a",
            next_offset=1,
            redaction_count=0,
        )

    monkeypatch.setattr(curl_module, "read_artifact_body_window", partial_window)
    with pytest.raises(CurlExportError) as truncated:
        asyncio.run(curl_module._body(request, captured))
    assert truncated.value.code is CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_curl_counts_sanitized_headers_cookies_and_body_redactions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, artifact = _bundle(tmp_path)
    request = _request(root, artifact)
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    assert exchange.request.body is not None
    sanitized = msgspec.structs.replace(
        exchange.request,
        headers=(
            *exchange.request.headers,
            ArtifactField(name="x-private", value=ARTIFACT_REDACTION_MARKER, redacted=True),
        ),
        cookies=(
            ArtifactCookie(
                name="session",
                value=ARTIFACT_REDACTION_MARKER,
                redacted=True,
            ),
        ),
        body=msgspec.structs.replace(exchange.request.body, redaction_count=2),
    )
    sanitized_exchange = msgspec.structs.replace(exchange, request=sanitized)
    sanitized_attempt = msgspec.structs.replace(attempt, exchanges=(sanitized_exchange,))
    sanitized_execution = msgspec.structs.replace(execution, attempts=(sanitized_attempt,))
    sanitized_artifact = msgspec.structs.replace(
        artifact,
        executions=(sanitized_execution, *artifact.executions[1:]),
    )
    monkeypatch.setattr(
        curl_module,
        "verify_artifact",
        lambda *_args, **_kwargs: sanitized_artifact,
    )

    exported = asyncio.run(export_artifact_curl(request))

    assert exported.redaction_count == 4
    assert exported.warnings.count(CurlExportWarning.VALUES_REDACTED) == 1
    assert "session=<redacted>" in exported.command
    assert curl_module._quote("left'right") == "'left'\"'\"'right'"
