"""Framing-specific feasibility validation for stream aggregate counts."""

from __future__ import annotations

from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.primitives import Count


def validate_stream_framing_counts(
    view: ViewKind,
    control_count: Count,
    error_count: Count,
) -> None:
    """Reject counts for item variants absent from the current framing union."""
    if view is ViewKind.SSE:
        if error_count.value:
            raise ValueError("SSE streams cannot contain JSON record errors")
        return
    if view in {ViewKind.NDJSON, ViewKind.JSON_SEQUENCE}:
        if control_count.value:
            raise ValueError("JSON streams cannot contain SSE control items")
        return
    raise ValueError("stream counts require an SSE, NDJSON, or JSON Sequence view")
