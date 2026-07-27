"""Bounded application services over sanitized, authenticated artifact bundles."""

from __future__ import annotations

import os
import stat
from collections.abc import AsyncIterable
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import open_local_artifact_bundle
from comparo.adapters.artifacts import read_local_artifact_bundle
from comparo.application.artifact_references import ArtifactBodyMessage
from comparo.application.artifact_references import ArtifactBodyReference
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.http import ArtifactRequest
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.reporters import RenderedReport
from comparo.reporters import ReportError
from comparo.reporters import ReportFormat
from comparo.reporters import render_report


class ArtifactOperationErrorCode(StrEnum):
    """Closed failures from artifact loading, verification, and reporting."""

    INVALID_LIMIT = "invalid-limit"
    ARTIFACT_UNAVAILABLE = "artifact-unavailable"
    BLOB_NOT_FOUND = "blob-not-found"
    BODY_NOT_FOUND = "body-not-found"
    OUTPUT_LIMIT_EXCEEDED = "output-limit-exceeded"
    REPORT_FAILED = "report-failed"
    ROOT_UNAVAILABLE = "root-unavailable"
    ROOT_UNSAFE = "root-unsafe"


class ArtifactOperationError(RuntimeError):
    """Value-free artifact operation failure safe for frontend presentation."""

    __slots__ = ("code",)

    def __init__(self, code: ArtifactOperationErrorCode) -> None:
        """Retain only the closed failure code."""
        if type(code) is not ArtifactOperationErrorCode:
            raise TypeError("artifact operation error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class ArtifactReadLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned finite limits for opening and projecting one artifact."""

    maximum_blobs: int
    maximum_blob_bytes: int
    maximum_total_blob_bytes: int
    maximum_manifest_bytes: int
    maximum_report_bytes: int
    maximum_redaction_patterns: int
    maximum_redaction_pattern_bytes: int
    maximum_redaction_value_bytes: int
    maximum_redaction_value_nodes: int

    def __post_init__(self) -> None:
        """Require explicit finite non-negative byte limits and positive counts."""
        positive = (
            self.maximum_blobs,
            self.maximum_redaction_patterns,
            self.maximum_redaction_value_nodes,
        )
        byte_limits = (
            self.maximum_blob_bytes,
            self.maximum_total_blob_bytes,
            self.maximum_manifest_bytes,
            self.maximum_report_bytes,
            self.maximum_redaction_pattern_bytes,
            self.maximum_redaction_value_bytes,
        )
        if any(type(value) is not int or value < 1 for value in positive):
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
        if any(type(value) is not int or value < 0 for value in byte_limits):
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
        if self.maximum_blob_bytes > self.maximum_total_blob_bytes:
            raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)


@final
class ArtifactInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Small safe summary of one fully authenticated artifact bundle."""

    api_version: str
    artifact_id: str
    run_id: str
    invocation_id: str
    completion: str
    gate: str
    executions: int
    comparisons: int
    assertions: int
    diffs: int
    blobs: int
    blob_bytes: int
    coverage_issues: int


@final
class ArtifactBodyWindow(msgspec.Struct, frozen=True, kw_only=True):
    """One authenticated bounded byte window plus truthful capture metadata."""

    reference: ArtifactBodyReference
    source_state: str
    extent: str
    reason: str | None
    media_type: str | None
    total_bytes: int | None
    offset: int
    content: bytes
    next_offset: int | None
    redaction_count: int

    def __post_init__(self) -> None:
        """Require coherent availability, byte range, and continuation metadata."""
        if type(self.reference) is not ArtifactBodyReference:
            raise TypeError("artifact body window requires a typed reference")
        if any(type(value) is not str or not value for value in (self.source_state, self.extent)):
            raise ValueError("artifact body capture labels must be non-empty text")
        if self.reason is not None and (type(self.reason) is not str or not self.reason):
            raise ValueError("artifact body omission reason must be non-empty text or absent")
        if self.media_type is not None and type(self.media_type) is not str:
            raise TypeError("artifact body media type must be text or absent")
        if self.total_bytes is not None and (
            type(self.total_bytes) is not int or self.total_bytes < 0
        ):
            raise ValueError("artifact body total must be non-negative or absent")
        if type(self.offset) is not int or self.offset < 0 or type(self.content) is not bytes:
            raise ValueError("artifact body window requires a non-negative offset and bytes")
        if self.next_offset is not None and (
            type(self.next_offset) is not int
            or self.next_offset <= self.offset
            or self.total_bytes is None
            or self.next_offset >= self.total_bytes
        ):
            raise ValueError("artifact body continuation must name a remaining byte")
        if type(self.redaction_count) is not int or self.redaction_count < 0:
            raise ValueError("artifact body redaction count must be non-negative")


@final
class AuthenticatedArtifactRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One request and optional complete body pinned to the same authenticated bundle."""

    reference: ArtifactBodyReference
    request: ArtifactRequest
    body_content: bytes | None

    def __post_init__(self) -> None:
        """Keep the body product consistent with the selected sanitized request capture."""
        if (
            type(self.reference) is not ArtifactBodyReference
            or self.reference.message is not ArtifactBodyMessage.REQUEST
        ):
            raise TypeError("authenticated artifact request requires request coordinates")
        if type(self.request) is not ArtifactRequest:
            raise TypeError("authenticated artifact request must be typed")
        if self.body_content is not None and type(self.body_content) is not bytes:
            raise TypeError("authenticated artifact request body must be bytes or absent")
        body = self.request.body
        if self.body_content is not None and (
            body is None
            or body.blob is None
            or body.extent is not ArtifactCaptureExtent.COMPLETE
            or len(self.body_content) != body.blob.byte_count
        ):
            raise ValueError("authenticated artifact request body contradicts its capture")


def initialize_project_artifact_root(project_root: Path, selected: Path) -> Path:
    """Create and pin a private project-relative artifact root without following links."""
    if not isinstance(project_root, Path) or not isinstance(selected, Path):
        raise TypeError("artifact root initialization requires Path inputs")
    if not project_root.is_absolute() or selected.is_absolute() or not selected.parts:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNSAFE)
    if any(part in {"", ".", ".."} for part in selected.parts):
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNSAFE)
    _initialize_relative_directories(project_root, selected.parts)
    return _resolved_contained_root(project_root, selected)


def validate_project_artifact_root(project_root: Path, selected: Path) -> Path:
    """Validate an existing private relative artifact root without creating directories."""
    if not isinstance(project_root, Path) or not isinstance(selected, Path):
        raise TypeError("artifact root validation requires Path inputs")
    if not project_root.is_absolute() or selected.is_absolute() or not selected.parts:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNSAFE)
    if any(part in {"", ".", ".."} for part in selected.parts):
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNSAFE)
    _validate_relative_directories(project_root, selected.parts)
    return _resolved_contained_root(project_root, selected)


def validate_existing_artifact_root(root: Path) -> Path:
    """Validate and resolve one existing private artifact directory without mutation."""
    if not isinstance(root, Path):
        raise TypeError("artifact root validation requires a Path")
    if not root.is_absolute():
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNSAFE)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    with suppress(OSError):
        descriptor = os.open(root, flags)
    if descriptor < 0:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNAVAILABLE)
    validation_failure: ArtifactOperationErrorCode | None = None
    try:
        _validate_private_directory(descriptor)
    except ArtifactOperationError as error:
        validation_failure = error.code
    except OSError:
        validation_failure = ArtifactOperationErrorCode.ROOT_UNAVAILABLE
    finally:
        with suppress(OSError):
            os.close(descriptor)
    if validation_failure is not None:
        raise ArtifactOperationError(validation_failure)
    resolved: Path | None = None
    with suppress(OSError):
        resolved = root.resolve(strict=True)
    if resolved is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNAVAILABLE)
    return resolved


def _initialize_relative_directories(project_root: Path, parts: tuple[str, ...]) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    unavailable = False
    try:
        current = os.open(project_root, flags)
        descriptors.append(current)
        _validate_private_directory(current)
        for part in parts:
            try:
                child = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                os.mkdir(part, mode=0o700, dir_fd=current)
                child = os.open(part, flags, dir_fd=current)
            descriptors.append(child)
            current = child
            _validate_private_directory(current)
    except ArtifactOperationError:
        raise
    except OSError:
        unavailable = True
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)
    if unavailable:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNAVAILABLE)


def _validate_relative_directories(project_root: Path, parts: tuple[str, ...]) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    unavailable = False
    try:
        current = os.open(project_root, flags)
        descriptors.append(current)
        _validate_private_directory(current)
        for part in parts:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
            _validate_private_directory(current)
    except ArtifactOperationError:
        raise
    except OSError:
        unavailable = True
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)
    if unavailable:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNAVAILABLE)


def _resolved_contained_root(project_root: Path, selected: Path) -> Path:
    resolved_project: Path | None = None
    resolved: Path | None = None
    try:
        resolved_project = project_root.resolve(strict=True)
        resolved = (project_root / selected).resolve(strict=True)
    except OSError:  # pragma: no cover - pinned directories remain addressable
        pass
    if resolved_project is None or resolved is None:  # pragma: no cover - pinned invariant
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNAVAILABLE)
    if not resolved.is_relative_to(resolved_project):  # pragma: no cover - no-follow invariant
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNSAFE)
    return resolved


def _validate_private_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    unsafe_permissions = stat.S_IWGRP | stat.S_IWOTH
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & unsafe_permissions
    ):
        raise ArtifactOperationError(ArtifactOperationErrorCode.ROOT_UNSAFE)


def inspect_artifact(
    root: Path,
    bundle_id: str,
    limits: ArtifactReadLimits,
) -> ArtifactInspection:
    """Authenticate every declared blob and return a safe compact summary."""
    artifact = verify_artifact(root, bundle_id, limits)
    return ArtifactInspection(
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
        blobs=len(artifact.blobs),
        blob_bytes=sum(item.byte_count for item in artifact.blobs),
        coverage_issues=len(artifact.coverage_issues),
    )


def verify_artifact(
    root: Path,
    bundle_id: str,
    limits: ArtifactReadLimits,
) -> RunArtifact:
    """Authenticate the bundle marker, manifest, artifact, and every declared blob."""
    typed_limits = _limits(limits)
    artifact: RunArtifact | None = None
    with suppress(LocalArtifactStoreError):
        artifact = read_local_artifact_bundle(root, bundle_id, limits=typed_limits)
    if artifact is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)
    return artifact


def render_artifact_report(
    root: Path,
    bundle_id: str,
    limits: ArtifactReadLimits,
    format: ReportFormat,
) -> RenderedReport:
    """Validate the complete artifact before deriving one bounded report."""
    artifact = verify_artifact(root, bundle_id, limits)
    report: RenderedReport | None = None
    with suppress(ReportError):
        report = render_report(
            artifact,
            format,
            maximum_bytes=limits.maximum_report_bytes,
        )
    if report is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.REPORT_FAILED)
    return report


async def read_artifact_blob(
    root: Path,
    bundle_id: str,
    sha256: str,
    limits: ArtifactReadLimits,
    *,
    maximum_output_bytes: int,
) -> bytes:
    """Authenticate and read one explicitly requested sanitized blob within an output cap."""
    if type(maximum_output_bytes) is not int or maximum_output_bytes < 0:
        raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
    typed_limits = _limits(limits)
    unavailable = False
    blob: ArtifactBlob | None = None
    chunks: list[bytes] = []
    try:
        with open_local_artifact_bundle(root, bundle_id, limits=typed_limits) as bundle:
            blob = _find_blob(bundle.artifact, sha256)
            if blob.byte_count > maximum_output_bytes:
                raise ArtifactOperationError(ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED)
            chunks = [chunk async for chunk in bundle.read(blob)]
    except ArtifactOperationError:
        raise
    except LocalArtifactStoreError:
        unavailable = True
    if unavailable or blob is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)
    payload = b"".join(chunks)
    if len(payload) != blob.byte_count:  # pragma: no cover - adapter verifies exact size
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)
    return payload


async def read_artifact_body_window(
    root: Path,
    bundle_id: str,
    reference: ArtifactBodyReference,
    limits: ArtifactReadLimits,
    *,
    offset: int,
    maximum_output_bytes: int,
) -> ArtifactBodyWindow:
    """Authenticate a bundle and read one body range selected by result coordinates."""
    if type(reference) is not ArtifactBodyReference:
        raise TypeError("artifact body reading requires ArtifactBodyReference")
    if type(offset) is not int or offset < 0:
        raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
    if type(maximum_output_bytes) is not int or maximum_output_bytes < 1:
        raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
    try:
        with open_local_artifact_bundle(root, bundle_id, limits=_limits(limits)) as bundle:
            capture = _find_body_capture(bundle.artifact, reference)
            blob = capture.blob
            if blob is None:
                if offset:
                    raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
                return ArtifactBodyWindow(
                    reference=reference,
                    source_state=capture.source_state.value,
                    extent=capture.extent.value,
                    reason=None if capture.reason is None else capture.reason.value,
                    media_type=None,
                    total_bytes=None,
                    offset=0,
                    content=b"",
                    next_offset=None,
                    redaction_count=capture.redaction_count,
                )
            if offset > blob.byte_count:
                raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
            content = await _read_blob_window(
                bundle.read(blob),
                offset=offset,
                maximum_output_bytes=maximum_output_bytes,
            )
            consumed = offset + len(content)
            return ArtifactBodyWindow(
                reference=reference,
                source_state=capture.source_state.value,
                extent=capture.extent.value,
                reason=None if capture.reason is None else capture.reason.value,
                media_type=blob.media_type,
                total_bytes=blob.byte_count,
                offset=offset,
                content=content,
                next_offset=consumed if consumed < blob.byte_count else None,
                redaction_count=capture.redaction_count,
            )
    except ArtifactOperationError:
        raise
    except LocalArtifactStoreError:
        pass
    raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)


async def read_authenticated_artifact_request(
    root: Path,
    bundle_id: str,
    reference: ArtifactBodyReference,
    limits: ArtifactReadLimits,
    *,
    maximum_body_bytes: int,
) -> AuthenticatedArtifactRequest:
    """Read request metadata and an eligible complete body through one pinned bundle lease."""
    if (
        type(reference) is not ArtifactBodyReference
        or reference.message is not ArtifactBodyMessage.REQUEST
    ):
        raise TypeError("authenticated artifact request requires request coordinates")
    if type(maximum_body_bytes) is not int or maximum_body_bytes < 0:
        raise ArtifactOperationError(ArtifactOperationErrorCode.INVALID_LIMIT)
    unavailable = False
    try:
        with open_local_artifact_bundle(root, bundle_id, limits=_limits(limits)) as bundle:
            request = _find_request(bundle.artifact, reference)
            content: bytes | None = None
            body = request.body
            if (
                body is not None
                and body.blob is not None
                and body.extent is ArtifactCaptureExtent.COMPLETE
                and body.blob.byte_count <= maximum_body_bytes
            ):
                chunks = [chunk async for chunk in bundle.read(body.blob)]
                content = b"".join(chunks)
                if len(content) != body.blob.byte_count:  # pragma: no cover - adapter invariant
                    raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)
            return AuthenticatedArtifactRequest(
                reference=reference,
                request=request,
                body_content=content,
            )
    except ArtifactOperationError:
        raise
    except LocalArtifactStoreError:
        unavailable = True
    if unavailable:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)
    raise ArtifactOperationError(  # pragma: no cover - exhaustive adapter outcome
        ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE
    )


async def _read_blob_window(
    chunks: AsyncIterable[bytes],
    *,
    offset: int,
    maximum_output_bytes: int,
) -> bytes:
    output = bytearray()
    position = 0
    async for chunk in chunks:
        if type(chunk) is not bytes:
            raise TypeError("artifact body source chunks must be bytes")
        end = position + len(chunk)
        if end > offset and len(output) < maximum_output_bytes:
            start_in_chunk = max(0, offset - position)
            remaining = maximum_output_bytes - len(output)
            output.extend(chunk[start_in_chunk : start_in_chunk + remaining])
        position = end
    return bytes(output)


def _find_body_capture(
    artifact: RunArtifact,
    reference: ArtifactBodyReference,
) -> ArtifactCapture:
    execution = next(
        (item for item in artifact.executions if item.execution_id == reference.execution_id),
        None,
    )
    if execution is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_NOT_FOUND)
    attempt = next(
        (item for item in execution.attempts if item.attempt_id == reference.attempt_id),
        None,
    )
    if attempt is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_NOT_FOUND)
    exchange = next(
        (item for item in attempt.exchanges if item.exchange_id == reference.exchange_id),
        None,
    )
    if exchange is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_NOT_FOUND)
    message = (
        exchange.request if reference.message is ArtifactBodyMessage.REQUEST else exchange.response
    )
    if message is None or message.body is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_NOT_FOUND)
    return message.body


def _find_request(
    artifact: RunArtifact,
    reference: ArtifactBodyReference,
) -> ArtifactRequest:
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
        raise ArtifactOperationError(ArtifactOperationErrorCode.BODY_NOT_FOUND)
    return exchange.request


def _find_blob(artifact: RunArtifact, sha256: str) -> ArtifactBlob:
    if type(sha256) is not str:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BLOB_NOT_FOUND)
    result = next((item for item in artifact.blobs if item.sha256 == sha256), None)
    if result is None:
        raise ArtifactOperationError(ArtifactOperationErrorCode.BLOB_NOT_FOUND)
    return result


def _limits(value: ArtifactReadLimits) -> ArtifactLimits:
    if type(value) is not ArtifactReadLimits:
        raise TypeError("artifact operations require ArtifactReadLimits")
    return ArtifactLimits(
        maximum_blobs=PositiveCount(value=value.maximum_blobs),
        maximum_blob_bytes=ByteCount(value=value.maximum_blob_bytes),
        maximum_total_blob_bytes=ByteCount(value=value.maximum_total_blob_bytes),
        maximum_manifest_bytes=ByteCount(value=value.maximum_manifest_bytes),
        maximum_report_bytes=ByteCount(value=value.maximum_report_bytes),
        sanitization=SanitizationLimits(
            maximum_patterns=PositiveCount(value=value.maximum_redaction_patterns),
            maximum_pattern_bytes=ByteCount(value=value.maximum_redaction_pattern_bytes),
            maximum_value_bytes=ByteCount(value=value.maximum_redaction_value_bytes),
            maximum_value_nodes=PositiveCount(value=value.maximum_redaction_value_nodes),
        ),
    )


__all__ = (
    "ArtifactBodyMessage",
    "ArtifactBodyReference",
    "ArtifactBodyWindow",
    "ArtifactInspection",
    "ArtifactOperationError",
    "ArtifactOperationErrorCode",
    "ArtifactReadLimits",
    "AuthenticatedArtifactRequest",
    "initialize_project_artifact_root",
    "inspect_artifact",
    "read_artifact_blob",
    "read_artifact_body_window",
    "read_authenticated_artifact_request",
    "render_artifact_report",
    "validate_existing_artifact_root",
    "validate_project_artifact_root",
    "verify_artifact",
)
