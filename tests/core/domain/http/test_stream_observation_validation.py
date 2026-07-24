"""Tests for stream ownership, timing, and aggregate boundary validation."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import SseComment
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseJsonBudgetExceeded
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.stream_termination import EventCountWindowEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationEvidence
from comparo.core.domain.http.streams import ConnectionSegment
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamRecordsHandle
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.streams import TrailingIncompleteItem
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import location
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_timings


def test_stream_owner_lineage_and_empty_timing_are_exact() -> None:
    handle = records_handle(StreamKind.SSE, records=1)
    with pytest.raises(TypeError, match="EnvironmentExecutionObservation"):
        msgspec.structs.replace(
            handle,
            owner=cast("EnvironmentExecutionObservation", "execution"),
        )

    empty_records = msgspec.structs.replace(
        records_handle(StreamKind.SSE, records=1),
        record_count=Count(value=0),
    )
    empty_segment = segment(index=0, first=0, count=0)
    empty_timing = StreamTimings(
        time_to_first_record=NotPerformed(reason=NotPerformedReason.EMPTY_STREAM),
        observation_window_elapsed=stream_timings().observation_window_elapsed,
    )
    valid = StreamObservation(
        records=empty_records,
        segments=(empty_segment,),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=empty_timing,
    )
    assert valid.records.record_count.value == 0
    with pytest.raises(TypeError, match="exact typed variant"):
        msgspec.structs.replace(
            valid,
            termination_evidence=cast("StreamTerminationEvidence", "manual"),
        )
    with pytest.raises(ValueError, match="empty stream must explicitly omit"):
        msgspec.structs.replace(valid, timings=stream_timings())
    with pytest.raises(ValueError, match="non-empty stream"):
        msgspec.structs.replace(
            valid,
            records=msgspec.structs.replace(valid.records, record_count=Count(value=1)),
            segments=(segment(index=0, first=0, count=1),),
        )
    with pytest.raises(ValueError, match="exact execution owner"):
        msgspec.structs.replace(
            valid,
            segments=(
                msgspec.structs.replace(
                    empty_segment,
                    exchange_id=ExchangeId(value=UUID(int=99)),
                ),
            ),
        )

    with pytest.raises(ValueError, match="at least one connection segment"):
        StreamObservation(
            records=handle,
            segments=(),
            captured_bytes=ByteCount(value=1),
            extent=StreamExtent(
                completeness=StreamCompleteness.FULL,
                termination=StreamTerminationReason.REMOTE_EOF,
            ),
            trailing_incomplete=(),
            timings=stream_timings(),
        )
    with pytest.raises(ValueError, match="record counts"):
        StreamObservation(
            records=handle,
            segments=(
                msgspec.structs.replace(
                    segment(index=0, first=0, count=0),
                    item_count=Count(value=1),
                    control_count=Count(value=1),
                ),
            ),
            captured_bytes=ByteCount(value=1),
            extent=StreamExtent(
                completeness=StreamCompleteness.FULL,
                termination=StreamTerminationReason.REMOTE_EOF,
            ),
            trailing_incomplete=(),
            timings=stream_timings(),
        )


def test_stream_observation_rejects_every_structural_boundary_contradiction() -> None:
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
        byte_start=0,
        byte_end=4,
    )
    second = segment(index=1, first=1, count=1, byte_start=4, byte_end=8)
    full = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )
    valid = StreamObservation(
        records=records,
        segments=(first, second),
        captured_bytes=ByteCount(value=8),
        extent=full,
        trailing_incomplete=(),
        timings=stream_timings(),
    )

    with pytest.raises(TypeError, match="stream records"):
        msgspec.structs.replace(valid, records=cast("StreamRecordsHandle", "records"))
    with pytest.raises(TypeError, match="captured byte"):
        msgspec.structs.replace(valid, captured_bytes=cast("ByteCount", 10))
    with pytest.raises(TypeError, match="immutable typed tuple"):
        msgspec.structs.replace(
            valid,
            segments=cast("tuple[ConnectionSegment, ...]", ("segment",)),
        )
    with pytest.raises(ValueError, match="indexes must be contiguous"):
        msgspec.structs.replace(
            valid,
            segments=(first, msgspec.structs.replace(second, index=Count(value=2))),
        )
    with pytest.raises(ValueError, match="byte ranges must be contiguous"):
        msgspec.structs.replace(
            valid,
            segments=(
                first,
                msgspec.structs.replace(
                    second,
                    byte_range=ByteRange(
                        start=ByteCount(value=5),
                        end_exclusive=ByteCount(value=8),
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError, match="captured byte extent"):
        msgspec.structs.replace(valid, captured_bytes=ByteCount(value=9))
    with pytest.raises(TypeError, match="stream extent"):
        msgspec.structs.replace(valid, extent=cast("StreamExtent", "extent"))
    with pytest.raises(TypeError, match="trailing stream items"):
        msgspec.structs.replace(
            valid,
            trailing_incomplete=cast("tuple[TrailingIncompleteItem, ...]", []),
        )
    with pytest.raises(TypeError, match="stream timings"):
        msgspec.structs.replace(valid, timings=cast("StreamTimings", "timings"))
    with pytest.raises(TypeError, match="stream diagnostics"):
        msgspec.structs.replace(valid, diagnostics=cast("tuple[Diagnostic, ...]", []))
    with pytest.raises(TypeError, match="stream diagnostics"):
        msgspec.structs.replace(valid, diagnostics=cast("tuple[Diagnostic, ...]", ("bad",)))

    window = StreamExtent(
        completeness=StreamCompleteness.WINDOW,
        termination=StreamTerminationReason.EVENT_COUNT_WINDOW,
    )
    assert (
        msgspec.structs.replace(
            valid,
            records=records_handle(StreamKind.SSE, records=2),
            segments=(
                msgspec.structs.replace(
                    first,
                    item_count=Count(value=2),
                    record_count=Count(value=2),
                    end_reason=SegmentEndReason.WINDOW_CLOSED,
                    byte_range=ByteRange(
                        start=ByteCount(value=0), end_exclusive=ByteCount(value=4)
                    ),
                ),
            ),
            captured_bytes=ByteCount(value=4),
            extent=window,
            termination_evidence=EventCountWindowEvidence(limit=PositiveCount(value=2)),
        ).extent
        is window
    )
    cancelled = StreamExtent(
        completeness=StreamCompleteness.INCOMPLETE,
        termination=StreamTerminationReason.CANCELLED,
    )
    with pytest.raises(ValueError, match="exact logical extent"):
        msgspec.structs.replace(
            valid,
            records=records_handle(StreamKind.SSE, records=2),
            segments=(
                msgspec.structs.replace(
                    first,
                    item_count=Count(value=2),
                    record_count=Count(value=2),
                    end_reason=SegmentEndReason.CANCELLED,
                    byte_range=ByteRange(
                        start=ByteCount(value=0), end_exclusive=ByteCount(value=4)
                    ),
                ),
            ),
            captured_bytes=ByteCount(value=4),
            extent=cancelled,
        )


def test_reconnect_segments_require_distinct_exact_exchange_bodies_and_views() -> None:
    records = records_handle(StreamKind.SSE, records=2, connections=2)
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
    )
    second = segment(index=1, first=1, count=1)
    extent = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )

    with pytest.raises(ValueError, match="unique decoded view"):
        StreamObservation(
            records=records,
            segments=(first, msgspec.structs.replace(second, view=first.view)),
            captured_bytes=ByteCount(value=8),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    owner = records.owner
    duplicated_body_view = msgspec.structs.replace(
        owner.views[1],
        source=owner.views[0].source,
    )
    duplicated_body_owner = msgspec.structs.replace(
        owner,
        views=(owner.views[0], duplicated_body_view),
    )
    with pytest.raises(ValueError, match="unique response body"):
        StreamObservation(
            records=msgspec.structs.replace(records, owner=duplicated_body_owner),
            segments=(
                first,
                msgspec.structs.replace(second, view=duplicated_body_view),
            ),
            captured_bytes=ByteCount(value=8),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    swapped_views = (
        msgspec.structs.replace(owner.views[0], source=owner.views[1].source),
        msgspec.structs.replace(owner.views[1], source=owner.views[0].source),
    )
    with pytest.raises(ValueError, match="exact captured streaming view"):
        msgspec.structs.replace(owner, views=swapped_views)

    with pytest.raises(ValueError, match="byte extent must equal"):
        StreamObservation(
            records=records_handle(StreamKind.SSE, records=1),
            segments=(
                msgspec.structs.replace(
                    first,
                    end_reason=SegmentEndReason.REMOTE_EOF,
                    byte_range=ByteRange(
                        start=ByteCount(value=0),
                        end_exclusive=ByteCount(value=3),
                    ),
                ),
            ),
            captured_bytes=ByteCount(value=3),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    single_records = records_handle(StreamKind.SSE, records=1)
    single_owner = single_records.owner
    first_exchange = single_owner.attempts[0].exchanges[0]
    assert first_exchange.response is not None
    assert type(first_exchange.response.body) is ResponseEntityBody
    omitted_response = msgspec.structs.replace(
        first_exchange.response,
        body=ResponseEntityBody(
            capture=PolicyOmittedCapture(),
            handle=None,
            encoded_byte_count=None,
        ),
    )
    omitted_attempt = msgspec.structs.replace(
        owner.attempts[0],
        exchanges=(msgspec.structs.replace(first_exchange, response=omitted_response),),
    )
    omitted_owner = msgspec.structs.replace(
        single_owner,
        attempts=(omitted_attempt,),
        views=(),
    )
    with pytest.raises(ValueError, match="captured response body"):
        StreamObservation(
            records=msgspec.structs.replace(
                single_records,
                owner=omitted_owner,
            ),
            segments=(
                msgspec.structs.replace(
                    first,
                    end_reason=SegmentEndReason.REMOTE_EOF,
                ),
            ),
            captured_bytes=ByteCount(value=4),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    unowned_view = msgspec.structs.replace(
        first.view,
        view_id=ViewId(value=UUID(int=999)),
    )
    with pytest.raises(ValueError, match="exact view of its execution owner"):
        StreamObservation(
            records=records_handle(StreamKind.SSE, records=1),
            segments=(
                msgspec.structs.replace(
                    first,
                    view=unowned_view,
                    end_reason=SegmentEndReason.REMOTE_EOF,
                ),
            ),
            captured_bytes=ByteCount(value=4),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )


def test_stream_item_envelopes_are_bound_to_their_exact_segment_view() -> None:
    records = records_handle(StreamKind.SSE, records=0, controls=1)
    observed = StreamObservation(
        records=records,
        segments=(segment(index=0, first=0, count=0, controls=1),),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=StreamTimings(
            time_to_first_record=NotPerformed(reason=NotPerformedReason.EMPTY_STREAM),
            observation_window_elapsed=stream_timings().observation_window_elapsed,
        ),
    )
    sealed_comment = SseComment(
        value=SensitiveValue(
            BodyText(value="heartbeat"),
            origins=(TaintOriginId(value="stream-comment"),),
        ),
        location=location(),
    )
    valid = StreamItemEnvelope(view=observed.segments[0].view, item=sealed_comment)
    observed.validate_item_envelope(valid)

    with pytest.raises(TypeError, match="StreamItemEnvelope"):
        observed.validate_item_envelope(cast("StreamItemEnvelope", "item"))
    with pytest.raises(ValueError, match="declared connection segment"):
        observed.validate_item_envelope(
            msgspec.structs.replace(
                valid,
                item=msgspec.structs.replace(
                    sealed_comment,
                    location=location(segment=2),
                ),
            )
        )
    with pytest.raises(ValueError, match="exact segment view"):
        observed.validate_item_envelope(
            msgspec.structs.replace(
                valid,
                view=segment(index=1, first=0, count=0).view,
            )
        )
    with pytest.raises(ValueError, match="segment view extent"):
        observed.validate_item_envelope(
            msgspec.structs.replace(
                valid,
                item=msgspec.structs.replace(
                    sealed_comment,
                    location=location(start=3, end=5),
                ),
            )
        )
    with pytest.raises(ValueError, match="segment item interval"):
        observed.validate_item_envelope(
            msgspec.structs.replace(
                valid,
                item=msgspec.structs.replace(
                    sealed_comment,
                    location=location(item=1),
                ),
            )
        )


def test_nested_sse_budget_scope_binds_the_full_segment_attempt() -> None:
    """Reject body-matching nested budget evidence from another execution scope."""
    observed = StreamObservation(
        records=records_handle(StreamKind.SSE, records=1),
        segments=(segment(index=0, first=0, count=1),),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
    )
    view = observed.segments[0].view
    exhaustion = view_exhaustion(
        ViewBudgetKind.JSON_NODES,
        body_id=view.body_id,
    )
    scope = exhaustion.budget.scope
    assert type(scope) is BodyBudgetScope
    exchange = observed.records.owner.attempts[0].exchanges[0]
    matching_exhaustion = msgspec.structs.replace(
        exhaustion,
        budget=msgspec.structs.replace(
            exhaustion.budget,
            scope=msgspec.structs.replace(scope, attempt=exchange.request.occurrence.attempt),
        ),
    )

    def envelope(exhaustion_scope: BodyBudgetScope) -> StreamItemEnvelope:
        nested = msgspec.structs.replace(
            matching_exhaustion,
            budget=msgspec.structs.replace(
                matching_exhaustion.budget,
                scope=exhaustion_scope,
            ),
        )
        sealed = SensitiveValue(
            BodyText(value="data"),
            origins=(TaintOriginId(value="stream-data"),),
        )
        return StreamItemEnvelope(
            view=view,
            item=SseEvent(
                data=sealed,
                event_type=sealed,
                last_event_id=sealed,
                fields=(),
                location=location(),
                nested_data=SseJsonBudgetExceeded(
                    decoder=decoder(),
                    exhaustion=nested,
                ),
            ),
        )

    matching_scope = msgspec.structs.replace(scope, attempt=exchange.request.occurrence.attempt)
    observed.validate_item_envelope(envelope(matching_scope))
    foreign_scope = msgspec.structs.replace(
        matching_scope,
        attempt=msgspec.structs.replace(
            matching_scope.attempt,
            execution=msgspec.structs.replace(
                matching_scope.attempt.execution,
                execution_id=EnvironmentExecutionId(value=UUID(int=999)),
            ),
        ),
    )
    with pytest.raises(ValueError, match="nested SSE budget scope"):
        observed.validate_item_envelope(envelope(foreign_scope))
