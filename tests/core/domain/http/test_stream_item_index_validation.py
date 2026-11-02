"""Tests for per-segment global and per-category stream item indexes."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.stream_items import JsonStreamRecordError
from comparo.core.domain.http.stream_items import SseComment
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.stream_factories import location
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_timings


def _empty_timings() -> StreamTimings:
    return StreamTimings(
        time_to_first_record=NotPerformed(reason=NotPerformedReason.EMPTY_STREAM),
        observation_window_elapsed=stream_timings().observation_window_elapsed,
    )


def _control_observation() -> StreamObservation:
    return StreamObservation(
        records=records_handle(StreamKind.SSE, records=0, controls=2),
        segments=(segment(index=0, first=0, count=0, controls=2),),
        captured_bytes=segment(index=0, first=0, count=0).view.source.byte_count,
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=_empty_timings(),
    )


def _comment_envelope(
    observation: StreamObservation,
    *,
    item: int,
    category: int,
) -> StreamItemEnvelope:
    comment = SseComment(
        value=SensitiveValue(
            BodyText(value="heartbeat"),
            origins=(TaintOriginId(value="stream-index"),),
        ),
        location=location(item=item, category=category),
    )
    return StreamItemEnvelope(view=observation.segments[0].view, item=comment)


def test_store_window_requires_unique_global_and_category_indexes() -> None:
    observation = _control_observation()
    first = _comment_envelope(observation, item=0, category=0)
    same_global = _comment_envelope(observation, item=0, category=1)
    with pytest.raises(ValueError, match="item indexes must be unique"):
        observation.validate_item_envelopes((first, same_global))

    same_category = _comment_envelope(observation, item=1, category=0)
    with pytest.raises(ValueError, match="category indexes must be unique"):
        observation.validate_item_envelopes((first, same_category))
    with pytest.raises(TypeError, match="immutable StreamItemEnvelope tuple"):
        observation.validate_item_envelopes(cast("tuple[StreamItemEnvelope, ...]", [first]))


def test_error_items_use_the_error_interval_not_record_or_control_capacity() -> None:
    observation = StreamObservation(
        records=records_handle(StreamKind.NDJSON, records=0, errors=1),
        segments=(
            segment(
                index=0,
                first=0,
                count=0,
                errors=1,
                kind=StreamKind.NDJSON,
            ),
        ),
        captured_bytes=segment(
            index=0,
            first=0,
            count=0,
            kind=StreamKind.NDJSON,
        ).view.source.byte_count,
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=_empty_timings(),
    )
    error = JsonStreamRecordError(
        code=DecodeErrorCode.INVALID_SYNTAX,
        location=location(),
    )
    observation.validate_item_envelope(
        StreamItemEnvelope(view=observation.segments[0].view, item=error)
    )
