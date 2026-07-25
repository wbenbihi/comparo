"""Pure bounded JSON, JUnit, SARIF, and Markdown RunArtifact projections."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Final

from comparo.core.artifacts.model import RunArtifact
from comparo.reporters.contracts import RenderedReport as RenderedReport
from comparo.reporters.contracts import ReportError as ReportError
from comparo.reporters.contracts import ReportErrorCode as ReportErrorCode
from comparo.reporters.contracts import ReportFormat as ReportFormat
from comparo.reporters.json import REPORT_API_VERSION as REPORT_API_VERSION
from comparo.reporters.json import decode_json_report as decode_json_report
from comparo.reporters.json import encode_report_schema as encode_report_schema
from comparo.reporters.json import generate_report_schema as generate_report_schema
from comparo.reporters.json import render_json_report as render_json_report
from comparo.reporters.junit import render_junit_report as render_junit_report
from comparo.reporters.markdown import render_markdown_report as render_markdown_report
from comparo.reporters.sarif import render_sarif_report as render_sarif_report

type _Renderer = Callable[[RunArtifact], RenderedReport]


def _json(artifact: RunArtifact, maximum_bytes: int) -> RenderedReport:
    return render_json_report(artifact, maximum_bytes=maximum_bytes)


def _junit(artifact: RunArtifact, maximum_bytes: int) -> RenderedReport:
    return render_junit_report(artifact, maximum_bytes=maximum_bytes)


def _sarif(artifact: RunArtifact, maximum_bytes: int) -> RenderedReport:
    return render_sarif_report(artifact, maximum_bytes=maximum_bytes)


def _markdown(artifact: RunArtifact, maximum_bytes: int) -> RenderedReport:
    return render_markdown_report(artifact, maximum_bytes=maximum_bytes)


type _BoundedRenderer = Callable[[RunArtifact, int], RenderedReport]

_RENDERERS: Final = MappingProxyType[ReportFormat, _BoundedRenderer](
    {
        ReportFormat.JSON: _json,
        ReportFormat.JUNIT: _junit,
        ReportFormat.SARIF: _sarif,
        ReportFormat.MARKDOWN: _markdown,
    }
)


def render_report(
    artifact: RunArtifact,
    format: ReportFormat,
    *,
    maximum_bytes: int,
) -> RenderedReport:
    """Dispatch one exact built-in projection without ambient state or I/O."""
    if type(format) is not ReportFormat:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    return _RENDERERS[format](artifact, maximum_bytes)


__all__ = (
    "REPORT_API_VERSION",
    "RenderedReport",
    "ReportError",
    "ReportErrorCode",
    "ReportFormat",
    "decode_json_report",
    "encode_report_schema",
    "generate_report_schema",
    "render_json_report",
    "render_junit_report",
    "render_markdown_report",
    "render_report",
    "render_sarif_report",
)
