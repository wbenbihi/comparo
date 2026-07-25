"""Bounded application services over sanitized, authenticated artifact bundles."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import open_local_artifact_bundle
from comparo.adapters.artifacts import read_local_artifact_bundle
from comparo.core.artifacts.common import ArtifactBlob
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
    OUTPUT_LIMIT_EXCEEDED = "output-limit-exceeded"
    REPORT_FAILED = "report-failed"


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
    try:
        return read_local_artifact_bundle(root, bundle_id, limits=typed_limits)
    except LocalArtifactStoreError as error:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE) from error


def render_artifact_report(
    root: Path,
    bundle_id: str,
    limits: ArtifactReadLimits,
    format: ReportFormat,
) -> RenderedReport:
    """Validate the complete artifact before deriving one bounded report."""
    artifact = verify_artifact(root, bundle_id, limits)
    try:
        return render_report(
            artifact,
            format,
            maximum_bytes=limits.maximum_report_bytes,
        )
    except ReportError as error:
        raise ArtifactOperationError(ArtifactOperationErrorCode.REPORT_FAILED) from error


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
    try:
        with open_local_artifact_bundle(root, bundle_id, limits=typed_limits) as bundle:
            blob = _find_blob(bundle.artifact, sha256)
            if blob.byte_count > maximum_output_bytes:
                raise ArtifactOperationError(ArtifactOperationErrorCode.OUTPUT_LIMIT_EXCEEDED)
            chunks = [chunk async for chunk in bundle.read(blob)]
    except ArtifactOperationError:
        raise
    except LocalArtifactStoreError as error:
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE) from error
    payload = b"".join(chunks)
    if len(payload) != blob.byte_count:  # pragma: no cover - adapter verifies exact size
        raise ArtifactOperationError(ArtifactOperationErrorCode.ARTIFACT_UNAVAILABLE)
    return payload


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
    "ArtifactInspection",
    "ArtifactOperationError",
    "ArtifactOperationErrorCode",
    "ArtifactReadLimits",
    "inspect_artifact",
    "read_artifact_blob",
    "render_artifact_report",
    "verify_artifact",
)
