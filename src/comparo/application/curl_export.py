"""Canonical POSIX cURL export from authenticated sanitized request captures."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.application.artifact_references import ArtifactBodyMessage
from comparo.application.artifact_references import ArtifactBodyReference
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.artifacts import ArtifactOperationErrorCode
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import read_artifact_body_window
from comparo.application.artifacts import verify_artifact
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.http import ArtifactRequest
from comparo.core.artifacts.model import RunArtifact


class CurlDialect(StrEnum):
    """Closed command dialect catalog for future serializer expansion."""

    POSIX_SH = "posix-sh"


class CurlExportWarning(StrEnum):
    """Safe reasons why an export differs from the original live request."""

    VALUES_REDACTED = "values-redacted"
    BODY_CAPTURE_INCOMPLETE = "body-capture-incomplete"
    BODY_FILE_REQUIRED = "body-file-required"
    BODY_NOT_TEXT = "body-not-text"


class CurlExportErrorCode(StrEnum):
    """Closed cURL export failure categories."""

    INVALID_REQUEST = "invalid-request"
    REQUEST_NOT_FOUND = "request-not-found"
    OUTPUT_LIMIT_EXCEEDED = "output-limit-exceeded"
    ARTIFACT_UNAVAILABLE = "artifact-unavailable"


class CurlExportError(RuntimeError):
    """Value-free export failure safe for frontend presentation."""

    __slots__ = ("code",)

    def __init__(self, code: CurlExportErrorCode) -> None:
        """Retain only the typed failure category."""
        if type(code) is not CurlExportErrorCode:
            raise TypeError("cURL export error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class CurlExportLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned body embedding and final command ceilings."""

    maximum_embedded_body_bytes: int
    maximum_command_characters: int

    def __post_init__(self) -> None:
        """Require explicit finite non-negative output budgets."""
        if (
            type(self.maximum_embedded_body_bytes) is not int
            or self.maximum_embedded_body_bytes < 0
            or type(self.maximum_command_characters) is not int
            or self.maximum_command_characters < 1
        ):
            raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)


@final
class CurlExportRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One authenticated artifact request selected by stable result coordinates."""

    root: Path
    bundle_id: str
    reference: ArtifactBodyReference
    artifact_limits: ArtifactReadLimits
    export_limits: CurlExportLimits
    dialect: CurlDialect = CurlDialect.POSIX_SH

    def __post_init__(self) -> None:
        """Require explicit paths, typed coordinates, limits, and dialect."""
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)
        if type(self.bundle_id) is not str or not self.bundle_id:
            raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)
        if (
            type(self.reference) is not ArtifactBodyReference
            or self.reference.message is not ArtifactBodyMessage.REQUEST
        ):
            raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)
        if type(self.artifact_limits) is not ArtifactReadLimits:
            raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)
        if type(self.export_limits) is not CurlExportLimits:
            raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)
        if type(self.dialect) is not CurlDialect:
            raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)


@final
class CurlExport(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded secret-safe command plus exact semantic limitations."""

    dialect: CurlDialect
    command: str
    executable: bool
    exact_for_sanitized_capture: bool
    warnings: tuple[CurlExportWarning, ...]
    redaction_count: int

    def __post_init__(self) -> None:
        """Require a typed immutable presentation product."""
        if type(self.dialect) is not CurlDialect or type(self.command) is not str:
            raise TypeError("cURL export dialect and command must be typed")
        if not self.command:
            raise ValueError("cURL export command must not be empty")
        if type(self.executable) is not bool or type(self.exact_for_sanitized_capture) is not bool:
            raise TypeError("cURL export state flags must be bool")
        if type(self.warnings) is not tuple or any(
            type(item) is not CurlExportWarning for item in self.warnings
        ):
            raise TypeError("cURL export warnings must be immutable and typed")
        if len(set(self.warnings)) != len(self.warnings):
            raise ValueError("cURL export warnings must be unique")
        if type(self.redaction_count) is not int or self.redaction_count < 0:
            raise ValueError("cURL export redaction count must be non-negative")


async def export_artifact_curl(request: CurlExportRequest) -> CurlExport:
    """Render one canonical command using only authenticated sanitized artifact values."""
    if type(request) is not CurlExportRequest:
        raise CurlExportError(CurlExportErrorCode.INVALID_REQUEST)
    captured: ArtifactRequest | None = None
    prefix = ""
    body_argument: str | None = None
    body_exact = False
    body_executable = False
    body_warnings: tuple[CurlExportWarning, ...] = ()
    failure: CurlExportErrorCode | None = None
    try:
        artifact = verify_artifact(request.root, request.bundle_id, request.artifact_limits)
        captured = _request(artifact, request.reference)
        prefix, body_argument, body_exact, body_executable, body_warnings = await _body(
            request,
            captured,
        )
    except CurlExportError as error:
        failure = error.code
    except ArtifactOperationError as error:
        failure = (
            CurlExportErrorCode.REQUEST_NOT_FOUND
            if error.code is ArtifactOperationErrorCode.BODY_NOT_FOUND
            else CurlExportErrorCode.ARTIFACT_UNAVAILABLE
        )
    if failure is not None or captured is None:
        raise CurlExportError(failure or CurlExportErrorCode.ARTIFACT_UNAVAILABLE)

    parts: list[str] = []
    used = len(prefix)
    used = _append(parts, "curl", used, request.export_limits.maximum_command_characters)
    used = _append(parts, "--disable", used, request.export_limits.maximum_command_characters)
    used = _append(
        parts,
        "--no-progress-meter",
        used,
        request.export_limits.maximum_command_characters,
    )
    used = _append(parts, "--request", used, request.export_limits.maximum_command_characters)
    used = _append_quoted(
        parts,
        captured.method,
        used,
        request.export_limits.maximum_command_characters,
    )
    used = _append(parts, "--url", used, request.export_limits.maximum_command_characters)
    used = _append_quoted(
        parts,
        captured.url,
        used,
        request.export_limits.maximum_command_characters,
    )
    for field in captured.headers:
        used = _append(parts, "--header", used, request.export_limits.maximum_command_characters)
        used = _append_quoted(
            parts,
            f"{field.name}: {field.value}",
            used,
            request.export_limits.maximum_command_characters,
        )
    for cookie in captured.cookies:
        used = _append(parts, "--cookie", used, request.export_limits.maximum_command_characters)
        used = _append_quoted(
            parts,
            f"{cookie.name}={cookie.value}",
            used,
            request.export_limits.maximum_command_characters,
        )
    if body_argument is not None:
        used = _append(
            parts,
            "--data-binary",
            used,
            request.export_limits.maximum_command_characters,
        )
        _append(
            parts,
            body_argument,
            used,
            request.export_limits.maximum_command_characters,
        )
    command = prefix + " ".join(parts)
    redactions = sum(field.redacted for field in captured.headers) + len(captured.cookies)
    if captured.body is not None:
        redactions += captured.body.redaction_count
    warnings = list(body_warnings)
    if redactions:
        warnings.append(CurlExportWarning.VALUES_REDACTED)
    return CurlExport(
        dialect=request.dialect,
        command=command,
        executable=body_executable,
        exact_for_sanitized_capture=body_exact,
        warnings=tuple(dict.fromkeys(warnings)),
        redaction_count=redactions,
    )


def _request(artifact: RunArtifact, reference: ArtifactBodyReference) -> ArtifactRequest:
    execution = next(
        (item for item in artifact.executions if item.execution_id == reference.execution_id),
        None,
    )
    attempt = (
        None
        if execution is None
        else next(
            (item for item in execution.attempts if item.attempt_id == reference.attempt_id),
            None,
        )
    )
    exchange = (
        None
        if attempt is None
        else next(
            (item for item in attempt.exchanges if item.exchange_id == reference.exchange_id),
            None,
        )
    )
    if exchange is None or exchange.request is None:
        raise CurlExportError(CurlExportErrorCode.REQUEST_NOT_FOUND)
    return exchange.request


async def _body(
    request: CurlExportRequest,
    captured: ArtifactRequest,
) -> tuple[str, str | None, bool, bool, tuple[CurlExportWarning, ...]]:
    body = captured.body
    if body is None:
        return "", None, True, True, ()
    if body.extent is not ArtifactCaptureExtent.COMPLETE or body.blob is None:
        return (
            "",
            _quote("@<body-not-captured>"),
            False,
            False,
            (CurlExportWarning.BODY_CAPTURE_INCOMPLETE,),
        )
    if body.blob.byte_count > request.export_limits.maximum_embedded_body_bytes:
        return (
            "",
            _quote("@<path-to-sanitized-body>"),
            False,
            False,
            (CurlExportWarning.BODY_FILE_REQUIRED,),
        )
    window = await read_artifact_body_window(
        request.root,
        request.bundle_id,
        request.reference,
        request.artifact_limits,
        offset=0,
        maximum_output_bytes=max(1, request.export_limits.maximum_embedded_body_bytes),
    )
    if window.next_offset is not None:
        raise CurlExportError(CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED)
    try:
        text = window.content.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if (not text and window.content) or "\0" in text:
        return (
            "",
            _quote("@<path-to-sanitized-body>"),
            False,
            False,
            (CurlExportWarning.BODY_NOT_TEXT, CurlExportWarning.BODY_FILE_REQUIRED),
        )
    prefix_size = len("printf '%s'  | ") + _quoted_length(text)
    if prefix_size > request.export_limits.maximum_command_characters:
        raise CurlExportError(CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED)
    return f"printf '%s' {_quote(text)} | ", _quote("@-"), True, True, ()


def _append(parts: list[str], value: str, used: int, maximum: int) -> int:
    additional = len(value) + int(bool(parts))
    if used + additional > maximum:
        raise CurlExportError(CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED)
    parts.append(value)
    return used + additional


def _append_quoted(parts: list[str], value: str, used: int, maximum: int) -> int:
    additional = _quoted_length(value) + int(bool(parts))
    if used + additional > maximum:
        raise CurlExportError(CurlExportErrorCode.OUTPUT_LIMIT_EXCEEDED)
    parts.append(_quote(value))
    return used + additional


def _quoted_length(value: str) -> int:
    return len(value) + 2 + 4 * value.count("'")


def _quote(value: str) -> str:
    """Return one exact POSIX shell single-quoted argument."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


__all__ = (
    "CurlDialect",
    "CurlExport",
    "CurlExportError",
    "CurlExportErrorCode",
    "CurlExportLimits",
    "CurlExportRequest",
    "CurlExportWarning",
    "export_artifact_curl",
)
