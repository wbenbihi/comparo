"""Tests for SSE and streaming-JSON items, storage indexes, and bounded windows."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.bodies import BodyText
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
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import location
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_timings
from tests.core.domain.http.stream_factories import view_handle


def test_sse_data_stays_text_until_nested_json_is_explicit() -> None:
    outer = SseEvent(
        data=BodyText(value='{"token": 1}'),
        event_type=BodyText(value="message"),
        last_event_id=BodyText(value="event-1"),
        fields=(
            SseFieldOccurrence(
                name=SseFieldName.DATA,
                byte_range=ByteRange(start=ByteCount(value=0), end_exclusive=ByteCount(value=12)),
                line=PositiveCount(value=1),
            ),
        ),
        location=location(end=12),
    )
    assert outer.nested_data is None

    nested = SseEvent(
        data=outer.data,
        event_type=outer.event_type,
        last_event_id=outer.last_event_id,
        fields=outer.fields,
        location=outer.location,
        nested_data=SseJsonReady(
            decoder=decoder(ViewKind.JSON),
            value=JsonInteger(value=1),
        ),
    )
    failed = SseEvent(
        data=BodyText(value="not-json"),
        event_type=BodyText(value="delta"),
        last_event_id=BodyText(value="event-2"),
        fields=(),
        location=location(item=1, start=13, end=21),
        nested_data=SseJsonDecodeError(
            decoder=decoder(ViewKind.JSON),
            code=DecodeErrorCode.INVALID_SYNTAX,
        ),
    )
    assert isinstance(nested.nested_data, SseJsonReady)
    assert isinstance(failed.nested_data, SseJsonDecodeError)


def test_sse_controls_comments_and_unknown_fields_are_not_dispatched_events() -> None:
    item_location = location()
    items = (
        SseComment(value=BodyText(value="heartbeat"), location=item_location),
        SseRetryControl(retry_after=Duration(nanoseconds=1_000), location=item_location),
        SseIdStateTransition(
            previous=BodyText(value="previous"),
            current=BodyText(value=""),
            location=item_location,
        ),
        SseUnknownField(
            name=BodyText(value="extension"),
            value=BodyText(value="value"),
            location=item_location,
        ),
    )
    assert not any(isinstance(item, SseEvent) for item in items)


def test_stream_item_envelope_binds_framing_and_parent_sensitivity() -> None:
    sensitive_view = view_handle(ViewKind.SSE)
    public_comment = SseComment(value=BodyText(value="heartbeat"), location=location())
    with pytest.raises(ValueError, match="retain sealed payloads"):
        StreamItemEnvelope(view=sensitive_view, item=public_comment)
    public_retry = SseRetryControl(
        retry_after=Duration(nanoseconds=1_000),
        location=location(),
    )
    with pytest.raises(ValueError, match="retain sealed payloads"):
        StreamItemEnvelope(view=sensitive_view, item=public_retry)

    sealed_comment = SseComment(
        value=SensitiveValue(
            BodyText(value="heartbeat"),
            origins=(TaintOriginId(value="stream-body"),),
        ),
        location=location(),
    )
    assert StreamItemEnvelope(view=sensitive_view, item=sealed_comment).item is sealed_comment
    public_view = msgspec.structs.replace(
        sensitive_view,
        source=msgspec.structs.replace(
            sensitive_view.source,
            digest=digest("e"),
            sensitivity=Sensitivity.PUBLIC,
        ),
        fingerprint=fingerprint("e"),
        sensitivity=Sensitivity.PUBLIC,
    )
    assert StreamItemEnvelope(view=public_view, item=public_comment).item is public_comment
    with pytest.raises(ValueError, match="public stream view"):
        StreamItemEnvelope(view=public_view, item=sealed_comment)
    sealed_retry = SseRetryControl(
        retry_after=SensitiveValue(
            Duration(nanoseconds=1_000),
            origins=(TaintOriginId(value="stream-body"),),
        ),
        location=location(),
    )
    assert StreamItemEnvelope(view=sensitive_view, item=sealed_retry).item is sealed_retry
    with pytest.raises(ValueError, match="framing"):
        StreamItemEnvelope(view=view_handle(ViewKind.NDJSON), item=sealed_comment)

    def sealed_text(value: str) -> SensitiveValue[BodyText]:
        return SensitiveValue(
            BodyText(value=value),
            origins=(TaintOriginId(value="stream-body"),),
        )

    event_with_public_nested_json = SseEvent(
        data=sealed_text("1"),
        event_type=sealed_text("message"),
        last_event_id=sealed_text("event-1"),
        fields=(),
        location=location(),
        nested_data=SseJsonReady(decoder=decoder(), value=JsonInteger(value=1)),
    )
    with pytest.raises(ValueError, match="retain sealed payloads"):
        StreamItemEnvelope(view=sensitive_view, item=event_with_public_nested_json)


def test_nested_sse_budget_evidence_binds_the_owning_response_body() -> None:
    view = view_handle(ViewKind.SSE)

    def sealed_text(value: str) -> SensitiveValue[BodyText]:
        return SensitiveValue(
            BodyText(value=value),
            origins=(TaintOriginId(value="stream-body"),),
        )

    def event(body_id: BodyId) -> SseEvent:
        return SseEvent(
            data=sealed_text("data"),
            event_type=sealed_text("message"),
            last_event_id=sealed_text("event-1"),
            fields=(),
            location=location(),
            nested_data=SseJsonBudgetExceeded(
                decoder=decoder(),
                exhaustion=view_exhaustion(ViewBudgetKind.JSON_NODES, body_id=body_id),
            ),
        )

    matching = event(view.body_id)
    assert StreamItemEnvelope(view=view, item=matching).item is matching

    foreign = event(BodyId(value=UUID(int=999_999)))
    with pytest.raises(ValueError, match="owning response body"):
        StreamItemEnvelope(view=view, item=foreign)


def test_stream_sensitive_values_reject_the_wrong_sealed_payload_type() -> None:
    wrong_text = SensitiveValue(
        JsonInteger(value=1),
        origins=(TaintOriginId(value="wrong-text"),),
    )
    with pytest.raises(TypeError, match="SSE comment"):
        SseComment(value=cast("BodyText", wrong_text), location=location())

    wrong_json = SensitiveValue(
        BodyText(value="not-json"),
        origins=(TaintOriginId(value="wrong-json"),),
    )
    with pytest.raises(TypeError, match="JSON stream record"):
        JsonStreamRecord(value=cast("JsonInteger", wrong_json), location=location())


def test_ndjson_and_json_sequence_records_keep_record_errors_and_locations() -> None:
    record = JsonStreamRecord(value=JsonInteger(value=1), location=location())
    error = JsonStreamRecordError(
        code=DecodeErrorCode.TRAILING_CONTENT,
        location=location(item=1, start=5, end=9),
    )
    assert record.location.line == PositiveCount(value=1)
    assert error.location.item_index == Count(value=1)
    assert records_handle(StreamKind.NDJSON, records=1, errors=1).error_count.value == 1
    assert records_handle(StreamKind.JSON_SEQUENCE, records=1).kind is StreamKind.JSON_SEQUENCE


def test_stream_record_handle_rejects_decoder_framing_drift() -> None:
    records = msgspec.structs.replace(
        records_handle(StreamKind.NDJSON, records=0),
        kind=StreamKind.SSE,
    )
    with pytest.raises(ValueError, match="agree"):
        StreamObservation(
            records=records,
            segments=(
                msgspec.structs.replace(
                    segment(index=0, first=0, count=0, kind=StreamKind.NDJSON),
                    view=records.owner.views[0],
                ),
            ),
            captured_bytes=ByteCount(value=4),
            extent=StreamExtent(
                completeness=StreamCompleteness.FULL,
                termination=StreamTerminationReason.REMOTE_EOF,
            ),
            trailing_incomplete=(),
            timings=stream_timings(),
        )


def test_stream_item_envelopes_cover_every_payload_shape_without_taint_loss() -> None:
    sensitive_sse = view_handle(ViewKind.SSE)
    public_sse = msgspec.structs.replace(
        sensitive_sse,
        source=msgspec.structs.replace(
            sensitive_sse.source,
            digest=digest("f"),
            sensitivity=Sensitivity.PUBLIC,
        ),
        fingerprint=fingerprint("f"),
        sensitivity=Sensitivity.PUBLIC,
    )
    sensitive_ndjson = view_handle(ViewKind.NDJSON)
    public_ndjson = msgspec.structs.replace(
        sensitive_ndjson,
        source=msgspec.structs.replace(
            sensitive_ndjson.source,
            digest=digest("a"),
            sensitivity=Sensitivity.PUBLIC,
        ),
        fingerprint=fingerprint("a"),
        sensitivity=Sensitivity.PUBLIC,
    )
    item_location = location()
    public_items = (
        SseEvent(
            data=BodyText(value="data"),
            event_type=BodyText(value="message"),
            last_event_id=BodyText(value="id"),
            fields=(),
            location=item_location,
        ),
        SseUnknownField(
            name=BodyText(value="extension"),
            value=BodyText(value="value"),
            location=item_location,
        ),
        SseIdStateTransition(
            previous=BodyText(value="before"),
            current=BodyText(value="after"),
            location=item_location,
        ),
    )
    assert all(StreamItemEnvelope(view=public_sse, item=item).item is item for item in public_items)

    record = JsonStreamRecord(value=JsonInteger(value=1), location=item_location)
    error = JsonStreamRecordError(code=DecodeErrorCode.INVALID_SYNTAX, location=item_location)
    assert StreamItemEnvelope(view=public_ndjson, item=record).item is record
    assert StreamItemEnvelope(view=public_ndjson, item=error).item is error
    with pytest.raises(ValueError, match="framing"):
        StreamItemEnvelope(view=public_sse, item=record)
    with pytest.raises(TypeError, match="envelope view"):
        StreamItemEnvelope(view=cast("DecodedViewHandle", "view"), item=record)
    with pytest.raises(TypeError, match="envelope item"):
        StreamItemEnvelope(view=public_sse, item=cast("SseEvent", "event"))
