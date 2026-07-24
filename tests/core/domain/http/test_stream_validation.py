"""Tests for SSE and streaming-JSON items, storage indexes, and bounded windows."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import JsonStreamRecord
from comparo.core.domain.http.stream_items import JsonStreamRecordError
from comparo.core.domain.http.stream_items import SseComment
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseFieldName
from comparo.core.domain.http.stream_items import SseFieldOccurrence
from comparo.core.domain.http.stream_items import SseIdStateTransition
from comparo.core.domain.http.stream_items import SseJsonBudgetExceeded
from comparo.core.domain.http.stream_items import SseJsonDecodeError
from comparo.core.domain.http.stream_items import SseJsonReady
from comparo.core.domain.http.stream_items import SseRetryControl
from comparo.core.domain.http.stream_items import SseUnknownField
from comparo.core.domain.http.stream_items import StreamItemLocation
from comparo.core.domain.http.streams import ConnectionSegment
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamRecordsHandle
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.streams import TrailingIncompleteItem
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import location
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment


def test_stream_item_coordinates_and_sse_provenance_validate_direct_construction() -> None:
    with pytest.raises(TypeError, match="bounds"):
        ByteRange(start=cast("ByteCount", 0), end_exclusive=ByteCount(value=1))
    with pytest.raises(ValueError, match="must not precede"):
        ByteRange(start=ByteCount(value=2), end_exclusive=ByteCount(value=1))
    with pytest.raises(TypeError, match="indexes"):
        StreamItemLocation(
            segment_index=cast("Count", 0),
            item_index=Count(value=0),
            category_index=Count(value=0),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            line=None,
            received_offset=Duration(nanoseconds=0),
        )
    with pytest.raises(TypeError, match="byte range"):
        StreamItemLocation(
            segment_index=Count(value=0),
            item_index=Count(value=0),
            category_index=Count(value=0),
            byte_range=cast("ByteRange", "0:1"),
            line=None,
            received_offset=Duration(nanoseconds=0),
        )
    with pytest.raises(TypeError, match="line"):
        StreamItemLocation(
            segment_index=Count(value=0),
            item_index=Count(value=0),
            category_index=Count(value=0),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            line=cast("PositiveCount", 1),
            received_offset=Duration(nanoseconds=0),
        )
    with pytest.raises(TypeError, match="received offset"):
        StreamItemLocation(
            segment_index=Count(value=0),
            item_index=Count(value=0),
            category_index=Count(value=0),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            line=None,
            received_offset=cast("Duration", 0),
        )
    with pytest.raises(TypeError, match="field name"):
        SseFieldOccurrence(
            name=cast("SseFieldName", "data"),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            line=PositiveCount(value=1),
        )
    with pytest.raises(TypeError, match="field byte range"):
        SseFieldOccurrence(
            name=SseFieldName.DATA,
            byte_range=cast("ByteRange", "0:1"),
            line=PositiveCount(value=1),
        )
    with pytest.raises(TypeError, match="field line"):
        SseFieldOccurrence(
            name=SseFieldName.DATA,
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            line=cast("PositiveCount", 1),
        )


def test_nested_sse_json_outcomes_require_json_decoders_and_typed_failures() -> None:
    text_decoder = decoder(ViewKind.TEXT)
    assert (
        SseJsonBudgetExceeded(
            decoder=decoder(),
            exhaustion=view_exhaustion(ViewBudgetKind.JSON_NODES),
        ).budget
        is ViewBudgetKind.JSON_NODES
    )
    with pytest.raises(ValueError, match="JSON DecoderSpec"):
        SseJsonReady(decoder=text_decoder, value=JsonInteger(value=1))
    with pytest.raises(ValueError, match="JSON DecoderSpec"):
        SseJsonDecodeError(decoder=text_decoder, code=DecodeErrorCode.INVALID_SYNTAX)
    with pytest.raises(ValueError, match="JSON DecoderSpec"):
        SseJsonBudgetExceeded(
            decoder=text_decoder,
            exhaustion=view_exhaustion(ViewBudgetKind.JSON_NODES),
        )
    with pytest.raises(TypeError, match="decode error"):
        SseJsonDecodeError(
            decoder=decoder(),
            code=cast("DecodeErrorCode", "syntax"),
        )
    with pytest.raises(TypeError, match="view budget evidence"):
        SseJsonBudgetExceeded(
            decoder=decoder(),
            exhaustion=cast("BudgetExceeded", "nodes"),
        )
    with pytest.raises(TypeError, match="nested SSE JSON"):
        SseJsonReady(decoder=decoder(), value=cast("JsonInteger", "one"))


def test_stream_items_reject_untyped_locations_controls_and_failures() -> None:
    item_location = location()
    with pytest.raises(TypeError, match="raw fields"):
        SseEvent(
            data=BodyText(value="data"),
            event_type=BodyText(value="message"),
            last_event_id=BodyText(value="id"),
            fields=cast("tuple[SseFieldOccurrence, ...]", []),
            location=item_location,
        )
    with pytest.raises(TypeError, match="SSE data"):
        SseEvent(
            data=cast("BodyText", "data"),
            event_type=BodyText(value="message"),
            last_event_id=BodyText(value="id"),
            fields=(),
            location=item_location,
        )
    with pytest.raises(TypeError, match="event location"):
        SseEvent(
            data=BodyText(value="data"),
            event_type=BodyText(value="message"),
            last_event_id=BodyText(value="id"),
            fields=(),
            location=cast("StreamItemLocation", "location"),
        )
    with pytest.raises(TypeError, match="nested data"):
        SseEvent(
            data=BodyText(value="data"),
            event_type=BodyText(value="message"),
            last_event_id=BodyText(value="id"),
            fields=(),
            location=item_location,
            nested_data=cast("SseJsonReady", "json"),
        )
    with pytest.raises(TypeError, match="retry hint"):
        SseRetryControl(retry_after=cast("Duration", 1), location=item_location)
    with pytest.raises(TypeError, match="sensitive SSE retry hint"):
        SseRetryControl(
            retry_after=cast(
                "SensitiveValue[Duration]",
                SensitiveValue(
                    BodyText(value="wrong"),
                    origins=(TaintOriginId(value="wrong-retry"),),
                ),
            ),
            location=item_location,
        )
    with pytest.raises(TypeError, match="retry location"):
        SseRetryControl(
            retry_after=Duration(nanoseconds=1),
            location=cast("StreamItemLocation", "bad"),
        )
    with pytest.raises(TypeError, match="comment location"):
        SseComment(value=BodyText(value="comment"), location=cast("StreamItemLocation", "bad"))
    with pytest.raises(TypeError, match="ID transition states"):
        SseIdStateTransition(
            previous=cast("BodyText", "previous"),
            current=BodyText(value="current"),
            location=item_location,
        )
    with pytest.raises(TypeError, match="ID transition location"):
        SseIdStateTransition(
            previous=BodyText(value="previous"),
            current=BodyText(value="current"),
            location=cast("StreamItemLocation", "bad"),
        )
    with pytest.raises(TypeError, match="unknown SSE field"):
        SseUnknownField(
            name=cast("BodyText", "name"),
            value=BodyText(value="value"),
            location=item_location,
        )
    with pytest.raises(TypeError, match="unknown SSE field location"):
        SseUnknownField(
            name=BodyText(value="name"),
            value=BodyText(value="value"),
            location=cast("StreamItemLocation", "bad"),
        )
    with pytest.raises(TypeError, match="record location"):
        JsonStreamRecord(
            value=JsonInteger(value=1),
            location=cast("StreamItemLocation", "bad"),
        )
    with pytest.raises(TypeError, match="record error"):
        JsonStreamRecordError(
            code=cast("DecodeErrorCode", "syntax"),
            location=item_location,
        )
    with pytest.raises(TypeError, match="stream error location"):
        JsonStreamRecordError(
            code=DecodeErrorCode.INVALID_SYNTAX,
            location=cast("StreamItemLocation", "bad"),
        )


def test_stream_handles_segments_and_observation_fields_validate_direct_construction() -> None:
    handle = records_handle(StreamKind.SSE, records=1)
    with pytest.raises(TypeError, match="records owner"):
        msgspec.structs.replace(
            handle,
            owner=cast("EnvironmentExecutionObservation", "owner"),
        )
    with pytest.raises(TypeError, match="records kind"):
        StreamRecordsHandle(
            owner=handle.owner,
            kind=cast("StreamKind", "sse"),
            record_count=Count(value=1),
            control_count=Count(value=0),
            error_count=Count(value=0),
        )
    with pytest.raises(TypeError, match="record, control, and error"):
        StreamRecordsHandle(
            owner=handle.owner,
            kind=StreamKind.SSE,
            record_count=cast("Count", 1),
            control_count=Count(value=0),
            error_count=Count(value=0),
        )
    with pytest.raises(TypeError, match="segment indexes"):
        ConnectionSegment(
            index=cast("Count", 0),
            attempt_id=AttemptId(value=UUID(int=1)),
            exchange_id=ExchangeId(value=UUID(int=2)),
            view=handle.owner.views[0],
            first_item_index=Count(value=0),
            item_count=Count(value=1),
            first_record_index=Count(value=0),
            record_count=Count(value=1),
            first_control_index=Count(value=0),
            control_count=Count(value=0),
            first_error_index=Count(value=0),
            error_count=Count(value=0),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            end_reason=SegmentEndReason.REMOTE_EOF,
        )
    with pytest.raises(TypeError, match="occurrence IDs"):
        ConnectionSegment(
            index=Count(value=0),
            attempt_id=cast("AttemptId", "attempt"),
            exchange_id=ExchangeId(value=UUID(int=2)),
            view=handle.owner.views[0],
            first_item_index=Count(value=0),
            item_count=Count(value=1),
            first_record_index=Count(value=0),
            record_count=Count(value=1),
            first_control_index=Count(value=0),
            control_count=Count(value=0),
            first_error_index=Count(value=0),
            error_count=Count(value=0),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            end_reason=SegmentEndReason.REMOTE_EOF,
        )
    with pytest.raises(TypeError, match="category counts"):
        ConnectionSegment(
            index=Count(value=0),
            attempt_id=AttemptId(value=UUID(int=1)),
            exchange_id=ExchangeId(value=UUID(int=2)),
            view=handle.owner.views[0],
            first_item_index=Count(value=0),
            item_count=Count(value=1),
            first_record_index=Count(value=0),
            record_count=cast("Count", 1),
            first_control_index=Count(value=0),
            control_count=Count(value=0),
            first_error_index=Count(value=0),
            error_count=Count(value=0),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            end_reason=SegmentEndReason.REMOTE_EOF,
        )
    with pytest.raises(TypeError, match="segment byte range"):
        ConnectionSegment(
            index=Count(value=0),
            attempt_id=AttemptId(value=UUID(int=1)),
            exchange_id=ExchangeId(value=UUID(int=2)),
            view=handle.owner.views[0],
            first_item_index=Count(value=0),
            item_count=Count(value=1),
            first_record_index=Count(value=0),
            record_count=Count(value=1),
            first_control_index=Count(value=0),
            control_count=Count(value=0),
            first_error_index=Count(value=0),
            error_count=Count(value=0),
            byte_range=cast("ByteRange", "0:1"),
            end_reason=SegmentEndReason.REMOTE_EOF,
        )
    with pytest.raises(TypeError, match="end reason"):
        ConnectionSegment(
            index=Count(value=0),
            attempt_id=AttemptId(value=UUID(int=1)),
            exchange_id=ExchangeId(value=UUID(int=2)),
            view=handle.owner.views[0],
            first_item_index=Count(value=0),
            item_count=Count(value=1),
            first_record_index=Count(value=0),
            record_count=Count(value=1),
            first_control_index=Count(value=0),
            control_count=Count(value=0),
            first_error_index=Count(value=0),
            error_count=Count(value=0),
            byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=1)),
            end_reason=cast("SegmentEndReason", "eof"),
        )
    with pytest.raises(TypeError, match="segment view"):
        msgspec.structs.replace(
            segment(index=0, first=0, count=1),
            view=cast("DecodedViewHandle", "view"),
        )
    with pytest.raises(ValueError, match="item count must equal"):
        msgspec.structs.replace(
            segment(index=0, first=0, count=1),
            item_count=Count(value=2),
        )
    with pytest.raises(TypeError, match="stream completeness"):
        StreamExtent(
            completeness=cast("StreamCompleteness", "full"),
            termination=StreamTerminationReason.REMOTE_EOF,
        )
    with pytest.raises(TypeError, match="stream termination"):
        StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=cast("StreamTerminationReason", "eof"),
        )
    with pytest.raises(TypeError, match="trailing stream item"):
        TrailingIncompleteItem(
            byte_range=cast("ByteRange", "0:1"),
            segment_index=Count(value=0),
        )
