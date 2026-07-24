"""Bind stream transforms to the semantic source selected at the pipeline root."""

from __future__ import annotations

from comparo.core.domain.diffs.operations import DiffTransform
from comparo.core.domain.diffs.operations import StreamProjection
from comparo.core.domain.diffs.operations import StreamSemanticSource
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource

_BODY_SOURCES = frozenset({SelectorSource.REQUEST_BODY, SelectorSource.RESPONSE_BODY})
_SSE_SEMANTICS = frozenset(
    {
        StreamSemanticSource.SSE_EVENT,
        StreamSemanticSource.SSE_DATA,
        StreamSemanticSource.SSE_JSON_DATA,
    }
)


def require_stream_projection_source(
    selector: CompiledSelector,
    transforms: tuple[DiffTransform, ...],
) -> None:
    """Require one root-bound stream projection before any source-erasing transform."""
    projections = tuple(
        (index, transform)
        for index, transform in enumerate(transforms)
        if type(transform) is StreamProjection
    )
    if not projections:
        return
    if len(projections) != 1 or projections[0][0] != 0:
        raise ValueError("stream projection must be the first and only stream projection")
    projection = projections[0][1]
    if not _source_supports_semantics(selector, projection.source):
        raise ValueError("stream projection semantics contradict the root selector source or view")


def _source_supports_semantics(
    selector: CompiledSelector,
    semantics: StreamSemanticSource,
) -> bool:
    if selector.source is SelectorSource.STREAM_EVENTS:
        return semantics in _SSE_SEMANTICS
    if selector.source is SelectorSource.STREAM_RECORDS:
        return semantics is StreamSemanticSource.JSON_RECORD
    if selector.source not in _BODY_SOURCES:
        return False
    if selector.body_view is BodyViewKind.SSE_EVENTS:
        return semantics in _SSE_SEMANTICS
    if selector.body_view in {
        BodyViewKind.NDJSON_RECORDS,
        BodyViewKind.JSON_SEQUENCE_RECORDS,
    }:
        return semantics is StreamSemanticSource.JSON_RECORD
    return False
