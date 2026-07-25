"""Canonical machine-readable JSON projection of a complete RunArtifact."""

from __future__ import annotations

import json
from typing import Final
from typing import final

import msgspec

from comparo.core.artifacts.common import ARTIFACT_API_VERSION
from comparo.core.artifacts.model import RunArtifact
from comparo.reporters.contracts import RenderedReport
from comparo.reporters.contracts import ReportError
from comparo.reporters.contracts import ReportErrorCode
from comparo.reporters.contracts import ReportFormat
from comparo.reporters.contracts import complete_report

REPORT_API_VERSION: Final = "comparo.report/v1alpha1"
_REPORT_SCHEMA_ID: Final = "https://comparo.dev/schemas/comparo-report-v1alpha1.schema.json"
_ARTIFACT_SCHEMA_ID: Final = "https://comparo.dev/schemas/comparo-artifact-v1alpha1.schema.json"


@final
class _JsonReport(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Explicit report envelope independent from the artifact API version."""

    api_version: str
    format: str
    artifact_api_version: str
    artifact_id: str
    artifact: RunArtifact


_DECODER = msgspec.json.Decoder(_JsonReport)


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


def decode_json_report(payload: bytes, *, maximum_bytes: int) -> RunArtifact:
    """Decode one exact supported JSON report envelope within a publication limit."""
    if type(maximum_bytes) is not int or maximum_bytes < 0 or type(payload) is not bytes:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    if len(payload) > maximum_bytes:
        raise ReportError(ReportErrorCode.BUDGET_EXCEEDED)
    try:
        envelope = _DECODER.decode(payload)
    except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError):
        raise ReportError(ReportErrorCode.MALFORMED) from None
    if (
        envelope.api_version != REPORT_API_VERSION
        or envelope.format != ReportFormat.JSON.value
        or envelope.artifact_api_version != ARTIFACT_API_VERSION
    ):
        raise ReportError(ReportErrorCode.UNSUPPORTED_VERSION)
    if (
        envelope.artifact.api_version != envelope.artifact_api_version
        or envelope.artifact.metadata.artifact_id != envelope.artifact_id
    ):
        raise ReportError(ReportErrorCode.MALFORMED)
    return envelope.artifact


def generate_report_schema() -> dict[str, object]:
    """Return the independent JSON-report envelope schema with an artifact schema reference."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": _REPORT_SCHEMA_ID,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "api_version": {"const": REPORT_API_VERSION, "type": "string"},
            "format": {"const": ReportFormat.JSON.value, "type": "string"},
            "artifact_api_version": {
                "const": ARTIFACT_API_VERSION,
                "type": "string",
            },
            "artifact_id": {"type": "string", "minLength": 1, "maxLength": 63},
            "artifact": {"$ref": _ARTIFACT_SCHEMA_ID},
        },
        "required": (
            "api_version",
            "format",
            "artifact_api_version",
            "artifact_id",
            "artifact",
        ),
        "x-comparo-validation": "runtime-semantic-validation-required",
    }


def encode_report_schema() -> str:
    """Render the canonical checked-in JSON report schema."""
    return json.dumps(generate_report_schema(), indent=2, sort_keys=True) + "\n"
