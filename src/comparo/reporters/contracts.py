"""Pure bounded report contracts shared by every RunArtifact projection."""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec


class ReportFormat(StrEnum):
    """Closed built-in report projections."""

    JSON = "json"
    JUNIT = "junit"
    SARIF = "sarif"
    MARKDOWN = "markdown"


class ReportErrorCode(StrEnum):
    """Value-free report failure categories."""

    INVALID_INPUT = "invalid-input"
    BUDGET_EXCEEDED = "budget-exceeded"
    MALFORMED = "malformed"
    UNSUPPORTED_VERSION = "unsupported-version"
    RENDER_FAILED = "render-failed"


class ReportError(ValueError):
    """A reporter failure that never embeds artifact content."""

    __slots__ = ("code",)

    def __init__(self, code: ReportErrorCode) -> None:
        """Retain only a closed safe failure code."""
        if type(code) is not ReportErrorCode:
            raise TypeError("report error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class RenderedReport(msgspec.Struct, frozen=True, kw_only=True):
    """One complete immutable report ready for caller-owned output."""

    format: ReportFormat
    media_type: str
    extension: str
    content: bytes

    def __post_init__(self) -> None:
        """Require exact format metadata and immutable complete bytes."""
        if type(self.format) is not ReportFormat:
            raise TypeError("rendered report format must be typed")
        if type(self.media_type) is not str or not self.media_type:
            raise ValueError("rendered report media type must be non-empty text")
        if type(self.extension) is not str or not self.extension.startswith("."):
            raise ValueError("rendered report extension must begin with a dot")
        if type(self.content) is not bytes:
            raise TypeError("rendered report content must be bytes")


def complete_report(
    *,
    format: ReportFormat,
    media_type: str,
    extension: str,
    content: bytes,
    maximum_bytes: int,
) -> RenderedReport:
    """Apply the caller-owned total budget without publishing a partial report."""
    if type(maximum_bytes) is not int or maximum_bytes < 0:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    if type(content) is not bytes:
        raise ReportError(ReportErrorCode.RENDER_FAILED)
    if len(content) > maximum_bytes:
        raise ReportError(ReportErrorCode.BUDGET_EXCEEDED)
    return RenderedReport(
        format=format,
        media_type=media_type,
        extension=extension,
        content=content,
    )
