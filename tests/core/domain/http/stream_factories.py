"""Deterministic storage-backed view and stream factories."""

from __future__ import annotations

from functools import cache
from uuid import UUID

import msgspec

from comparo.core.domain.http.attempt_transitions import StreamReconnectTransition
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_items import ByteRange
from comparo.core.domain.http.stream_items import StreamItemLocation
from comparo.core.domain.http.streams import ConnectionSegment
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamRecordsHandle
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import DecoderIdentity
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewStorageToken
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import followed_redirect
from tests.core.domain.http.observation_factories import measured
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response


def decoder(view: ViewKind = ViewKind.JSON) -> DecoderSpec:
    """Build a versioned decoder spec."""
    return DecoderSpec(identity=DecoderIdentity(value=f"comparo/{view.value}@1"), view=view)


def view_handle(
    view: ViewKind = ViewKind.JSON,
    *,
    completeness: ViewCompleteness = ViewCompleteness.FULL,
    source: BodyHandle | RepeatableByteSource | None = None,
    view_number: int = 1,
) -> DecodedViewHandle:
    """Build one sealed storage-backed decoded-view handle."""
    locator = SensitiveValue(
        ViewStorageToken(value="private/view/path"),
        origins=(TaintOriginId(value="view-store"),),
    )
    sealed_fingerprint = SensitiveValue(
        fingerprint("b"),
        origins=(TaintOriginId(value="view-content"),),
    )
    return DecodedViewHandle(
        view_id=ViewId(value=UUID(int=view_number)),
        source=source
        or body_handle(
            size=4,
            extent=(
                BodyCaptureExtent.PARTIAL
                if completeness is ViewCompleteness.PREFIX
                else BodyCaptureExtent.COMPLETE
            ),
        ),
        locator=locator,
        decoder=decoder(view),
        fingerprint=sealed_fingerprint,
        sensitivity=Sensitivity.SENSITIVE,
        completeness=completeness,
    )


def location(
    *,
    segment: int = 0,
    item: int = 0,
    category: int | None = None,
    start: int = 0,
    end: int = 4,
) -> StreamItemLocation:
    """Build stable record coordinates independent of transport chunks."""
    return StreamItemLocation(
        segment_index=Count(value=segment),
        item_index=Count(value=item),
        category_index=Count(value=item if category is None else category),
        byte_range=ByteRange(start=ByteCount(value=start), end_exclusive=ByteCount(value=end)),
        line=PositiveCount(value=item + 1),
        received_offset=Duration(nanoseconds=item * 10),
    )


def records_handle(
    kind: StreamKind,
    *,
    records: int,
    controls: int = 0,
    errors: int = 0,
    connections: int = 1,
) -> StreamRecordsHandle:
    """Build a storage-backed record index for one stream framing."""
    view_kind = {
        StreamKind.SSE: ViewKind.SSE,
        StreamKind.NDJSON: ViewKind.NDJSON,
        StreamKind.JSON_SEQUENCE: ViewKind.JSON_SEQUENCE,
    }[kind]
    if connections not in {1, 2}:
        raise ValueError("test stream factories support one or two physical connections")
    return StreamRecordsHandle(
        owner=(
            single_stream_execution(view_kind) if connections == 1 else stream_execution(view_kind)
        ),
        kind=kind,
        record_count=Count(value=records),
        control_count=Count(value=controls),
        error_count=Count(value=errors),
    )


@cache
def single_stream_execution(view: ViewKind) -> EnvironmentExecutionObservation:
    """Build one complete stream connection with no omitted outgoing reconnect."""
    execution = stream_execution(view)
    return msgspec.structs.replace(
        execution,
        attempts=(execution.attempts[0],),
        views=(execution.views[0],),
    )


def segment(
    *,
    index: int,
    first: int,
    count: int,
    first_item: int | None = None,
    item_count: int | None = None,
    first_control: int = 0,
    controls: int = 0,
    first_error: int = 0,
    errors: int = 0,
    kind: StreamKind = StreamKind.SSE,
    end_reason: SegmentEndReason = SegmentEndReason.REMOTE_EOF,
    byte_start: int | None = None,
    byte_end: int | None = None,
) -> ConnectionSegment:
    """Build one deterministic physical connection segment."""
    start = index * 4 if byte_start is None else byte_start
    end = start + 4 if byte_end is None else byte_end
    total_items = count + controls + errors if item_count is None else item_count
    return ConnectionSegment(
        index=Count(value=index),
        attempt_id=AttemptId(value=UUID(int=index + 1)),
        exchange_id=ExchangeId(value=UUID(int=index + 11)),
        view=stream_execution(
            {
                StreamKind.SSE: ViewKind.SSE,
                StreamKind.NDJSON: ViewKind.NDJSON,
                StreamKind.JSON_SEQUENCE: ViewKind.JSON_SEQUENCE,
            }[kind]
        ).views[index],
        first_item_index=Count(value=first if first_item is None else first_item),
        item_count=Count(value=total_items),
        first_record_index=Count(value=first),
        record_count=Count(value=count),
        first_control_index=Count(value=first_control),
        control_count=Count(value=controls),
        first_error_index=Count(value=first_error),
        error_count=Count(value=errors),
        byte_range=ByteRange(
            start=ByteCount(value=start),
            end_exclusive=ByteCount(value=end),
        ),
        end_reason=end_reason,
    )


@cache
def stream_execution(view: ViewKind) -> EnvironmentExecutionObservation:
    """Build a two-exchange execution with one exact response body/view per segment."""
    effective = request()
    first = exchange(
        exchange_number=11,
        execution_number=900,
        attempt_number=1,
        effective_request=effective,
        observed_response=response(
            body=ResponseEntityBody(
                capture=CompleteCapture(),
                handle=body_handle(size=4),
                encoded_byte_count=ByteCount(value=4),
            ),
        ),
    )
    second = exchange(
        exchange_number=12,
        execution_number=900,
        attempt_number=2,
        effective_request=effective,
        observed_response=response(
            body=ResponseEntityBody(
                capture=CompleteCapture(),
                handle=body_handle(size=4),
                encoded_byte_count=ByteCount(value=4),
            ),
        ),
    )
    assert first.response is not None
    assert second.response is not None
    assert type(first.response.body) is ResponseEntityBody
    assert type(second.response.body) is ResponseEntityBody
    assert first.response.body.handle is not None
    assert second.response.body.handle is not None
    views = (
        view_handle(view, source=first.response.body.handle, view_number=101),
        view_handle(view, source=second.response.body.handle, view_number=102),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=900)),
        environment=effective.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=1)),
                retry=None,
                exchanges=(first,),
                timings=attempt_timings(),
            ),
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=2)),
                retry=None,
                exchanges=(second,),
                timings=attempt_timings(),
                stream_reconnect=StreamReconnectTransition(
                    previous_attempt_id=AttemptId(value=UUID(int=1)),
                    previous_exchange_id=ExchangeId(value=UUID(int=11)),
                    previous_stream_view_id=views[0].view_id,
                ),
            ),
        ),
        timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
        views=views,
    )


@cache
def redirect_stream_execution(view: ViewKind) -> EnvironmentExecutionObservation:
    """Build one redirect attempt whose followed exchange is not a stream segment."""
    effective = request(redirect_hops=1)
    first = exchange(
        exchange_number=21,
        execution_number=901,
        attempt_number=21,
        effective_request=effective,
        observed_response=response(
            status=302,
            body=ResponseEntityBody(
                capture=CompleteCapture(),
                handle=body_handle(size=4),
                encoded_byte_count=ByteCount(value=4),
            ),
        ),
        redirect=followed_redirect(effective),
    )
    terminal = exchange(
        exchange_number=22,
        execution_number=901,
        attempt_number=21,
        effective_request=effective,
        observed_response=response(
            body=ResponseEntityBody(
                capture=CompleteCapture(),
                handle=body_handle(size=4),
                encoded_byte_count=ByteCount(value=4),
            ),
        ),
    )
    assert first.response is not None
    assert terminal.response is not None
    assert type(first.response.body) is ResponseEntityBody
    assert type(terminal.response.body) is ResponseEntityBody
    assert first.response.body.handle is not None
    assert terminal.response.body.handle is not None
    views = (
        view_handle(view, source=first.response.body.handle, view_number=201),
        view_handle(view, source=terminal.response.body.handle, view_number=202),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=901)),
        environment=effective.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=21)),
                retry=None,
                exchanges=(first, terminal),
                timings=attempt_timings(),
            ),
        ),
        timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
        views=views,
    )


def stream_timings() -> StreamTimings:
    """Build exact portable stream timing facts."""
    metric = measured()
    return StreamTimings(time_to_first_record=metric, observation_window_elapsed=metric)
