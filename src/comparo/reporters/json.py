"""Canonical machine-readable JSON projection of a complete RunArtifact."""

from __future__ import annotations

from typing import Final
from typing import final

import msgspec

from comparo.core.artifacts.model import RunArtifact
from comparo.reporters.contracts import RenderedReport
from comparo.reporters.contracts import ReportError
from comparo.reporters.contracts import ReportErrorCode
from comparo.reporters.contracts import ReportFormat
from comparo.reporters.contracts import complete_report

REPORT_API_VERSION: Final = "comparo.report/v1alpha1"


@final
class _JsonReport(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Explicit report envelope independent from the artifact API version."""

    api_version: str
    format: str
    artifact_api_version: str
    artifact_id: str
    artifact: RunArtifact


def render_json_report(artifact: RunArtifact, *, maximum_bytes: int) -> RenderedReport:
    """Render the complete artifact in one independently versioned report envelope."""
    if type(artifact) is not RunArtifact:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    try:
        content = msgspec.json.encode(
            _JsonReport(
                api_version=REPORT_API_VERSION,
                format=ReportFormat.JSON.value,
                artifact_api_version=artifact.api_version,
                artifact_id=artifact.metadata.artifact_id,
                artifact=artifact,
            ),
            order="deterministic",
        )
    except Exception as error:
        raise ReportError(ReportErrorCode.RENDER_FAILED) from error
    return complete_report(
        format=ReportFormat.JSON,
        media_type="application/vnd.comparo.report+json",
        extension=".json",
        content=content,
        maximum_bytes=maximum_bytes,
    )
