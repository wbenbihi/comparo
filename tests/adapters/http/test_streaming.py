"""Bounded storage-backed SSE and streaming-JSON adapter contracts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

import comparo.adapters.http.streaming as streaming
from comparo.adapters.http.storage import StoredBodyStream
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.adapters.http.streaming import SseDataJsonPolicy
from comparo.adapters.http.streaming import StoredStreamSnapshotProvider
from comparo.adapters.http.streaming import StreamDecodingLimits
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ParsedMediaType
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.stream_items import JsonStreamRecord
from comparo.core.domain.http.stream_items import JsonStreamRecordError
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseJsonReady
from comparo.core.domain.http.stream_termination import FramingDecodeFailureEvidence
from comparo.core.domain.http.stream_termination import SafetyCountLimitEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationReason
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonObject
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_failure
from tests.core.domain.http.observation_factories import response

_UNAVAILABLE = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)


def _limits(
    *,
    records: int = 8,
    controls: int = 8,
    errors: int = 8,
    item_bytes: int = 1_024,
) -> StreamDecodingLimits:
    return StreamDecodingLimits(
        maximum_records=PositiveCount(value=records),
        maximum_controls=PositiveCount(value=controls),
        maximum_errors=PositiveCount(value=errors),
        maximum_item_bytes=ByteCount(value=item_bytes),
    )


def _store(tmp_path: Path) -> TransientBodyStore:
    return TransientBodyStore(
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=16),
            maximum_transient_bytes=ByteCount(value=8_192),
            io_chunk_size=PositiveCount(value=3),
            root=tmp_path,
        )
    )


def _media(kind: StreamKind) -> ParsedMediaType:
    media_type = {
        StreamKind.SSE: ("text", "event-stream"),
        StreamKind.NDJSON: ("application", "x-ndjson"),
        StreamKind.JSON_SEQUENCE: ("application", "json-seq"),
    }[kind]
    from comparo.core.domain.http.media_types import MediaType

    return ParsedMediaType(value=MediaType(type=media_type[0], subtype=media_type[1]))


async def _observation(
    store: TransientBodyStore,
    payload: bytes,
    kind: StreamKind,
    *,
    cancelled: bool = False,
    sensitive: bool = False,
) -> EnvironmentExecutionObservation:
    media = _media(kind)
    body_id = BodyId(value=UUID(int=900 + len(payload)))
    writer = await store.begin(
        body_id,
        maximum_body_bytes=ByteCount(value=4_096),
        sensitivity=Sensitivity.SENSITIVE if sensitive else Sensitivity.PUBLIC,
        origins=(TaintOriginId(value="test/stream-body"),) if sensitive else (),
    )
    async with writer:
        await writer.append(payload)
        handle = await writer.commit_response(
            media=media,
            extent=BodyCaptureExtent.PARTIAL if cancelled else BodyCaptureExtent.COMPLETE,
        )
    entity = ResponseEntityBody(
        capture=CancelledCapture() if cancelled else CompleteCapture(),
        handle=handle,
        encoded_byte_count=ByteCount(value=len(payload)),
    )
    observed = msgspec.structs.replace(
        response(scheme=request().url.scheme),
        media=media,
        body=entity,
        headers=ResponseFields(
            fields=(
                ResponseField(
                    name=HeaderName(value="content-type"),
                    value=render_media_type(media.value),
                ),
            )
        ),
    )
    effective = request()
    request_occurrence = occurrence(
        execution=1,
        attempt=1,
        exchange=1,
        environment=effective.environment.identity,
    )
    terminal_failure: TransportFailure | None = (
        failure(FailurePhase.CANCELLATION, retryable=False) if cancelled else None
    )
    exchange = HttpExchange(
        exchange_id=request_occurrence.exchange_id,
        request=authorized_request(effective, request_occurrence=request_occurrence),
        response=observed,
        failure=terminal_failure,
        redirect=None,
        timings=exchange_timings(failure_phase=FailurePhase.CANCELLATION if cancelled else None),
    )
    return EnvironmentExecutionObservation(
        execution_id=request_occurrence.execution_id,
        environment=effective.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=request_occurrence.attempt_id,
                retry=None,
                exchanges=(exchange,),
                timings=attempt_timings(),
            ),
        ),
        timings=EnvironmentExecutionTimings(queue=_UNAVAILABLE, elapsed=_UNAVAILABLE),
    )


def test_sse_preserves_controls_dispatch_and_explicit_nested_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        payload = (
            b'\xef\xbb\xbf: heartbeat\r\nid: 7\nretry: 1500\revent: delta\ndata: {"token":"x"}\n\n'
        )
        observed = await _observation(store, payload, StreamKind.SSE)
        provider = StoredStreamSnapshotProvider(
            store=store,
            limits=_limits(),
            sse_json=SseDataJsonPolicy(event_types=("delta",)),
        )

        snapshot = await provider.snapshot(observed)

        assert snapshot.observation.views
        assert snapshot.stream is not None
        assert snapshot.stream.extent.completeness is StreamCompleteness.FULL
        assert snapshot.stream.extent.termination is StreamTerminationReason.REMOTE_EOF
        assert snapshot.stream.records.record_count.value == 1
        assert snapshot.stream.records.control_count.value == 3
        event = next(
            envelope.item for envelope in snapshot.stream_items if type(envelope.item) is SseEvent
        )
        assert type(event) is SseEvent
        assert type(event.nested_data) is SseJsonReady
        assert type(event.nested_data.value) is JsonObject
        assert snapshot.body_views[0].handle == snapshot.observation.views[0]  # type: ignore[union-attr]

        await provider.close()
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "payload", "records", "errors"),
    [
        (
            StreamKind.NDJSON,
            b'{"value":1}\r\n\n{"value":2}',
            2,
            0,
        ),
        (
            StreamKind.JSON_SEQUENCE,
            b'\x1e{"value":1}\n\x1e{bad}\n\x1e{"value":3}',
            2,
            1,
        ),
    ],
)
def test_json_stream_framings_retain_complete_records_and_errors(
    tmp_path: Path,
    kind: StreamKind,
    payload: bytes,
    records: int,
    errors: int,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, payload, kind)
        provider = StoredStreamSnapshotProvider(store=store, limits=_limits())

        snapshot = await provider.snapshot(observed)

        assert snapshot.stream is not None
        assert snapshot.stream.records.record_count.value == records
        assert snapshot.stream.records.error_count.value == errors
        assert sum(type(item.item) is JsonStreamRecord for item in snapshot.stream_items) == records
        assert (
            sum(type(item.item) is JsonStreamRecordError for item in snapshot.stream_items)
            == errors
        )
        if errors:
            assert snapshot.stream.extent.termination is StreamTerminationReason.DECODE_FAILURE
            assert type(snapshot.stream.termination_evidence) is FramingDecodeFailureEvidence
        else:
            assert snapshot.stream.extent.termination is StreamTerminationReason.REMOTE_EOF

        await store.close()

    asyncio.run(scenario())


def test_record_limit_retains_a_closed_incomplete_prefix(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        payload = b"1\n2\n3\n"
        observed = await _observation(store, payload, StreamKind.NDJSON)
        provider = StoredStreamSnapshotProvider(store=store, limits=_limits(records=2))

        snapshot = await provider.snapshot(observed)

        assert snapshot.stream is not None
        assert snapshot.stream.records.record_count.value == 2
        assert snapshot.stream.extent.completeness is StreamCompleteness.INCOMPLETE
        assert snapshot.stream.extent.termination is StreamTerminationReason.SAFETY_COUNT_LIMIT
        assert type(snapshot.stream.termination_evidence) is SafetyCountLimitEvidence
        assert snapshot.stream.segments[0].end_reason is SegmentEndReason.FAILURE
        assert snapshot.stream.trailing_incomplete[0].byte_range.start.value == 4

        await store.close()

    asyncio.run(scenario())


def test_cancelled_capture_retains_complete_records_and_trailing_item(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b'{"value":1}\n{"value":',
            StreamKind.NDJSON,
            cancelled=True,
        )
        provider = StoredStreamSnapshotProvider(store=store, limits=_limits())

        snapshot = await provider.snapshot(observed)

        assert snapshot.stream is not None
        assert snapshot.stream.records.record_count.value == 1
        assert snapshot.stream.extent.termination is StreamTerminationReason.CANCELLED
        assert snapshot.stream.segments[0].end_reason is SegmentEndReason.CANCELLED
        assert snapshot.stream.trailing_incomplete
        assert snapshot.stream.termination_evidence is None

        await store.close()

    asyncio.run(scenario())


def test_non_stream_media_returns_an_observation_only_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"{}", StreamKind.NDJSON)
        terminal = observed.attempts[0].exchanges[0]
        response_value = terminal.response
        assert response_value is not None
        body = response_value.body
        assert type(body) is ResponseEntityBody
        assert body.handle is not None
        from comparo.core.domain.http.response_bodies import MissingMediaType

        plain_handle = msgspec.structs.replace(body.handle, media=MissingMediaType())
        plain_response = msgspec.structs.replace(
            response_value,
            media=MissingMediaType(),
            headers=ResponseFields(),
            body=msgspec.structs.replace(body, handle=plain_handle),
        )
        plain_exchange = msgspec.structs.replace(terminal, response=plain_response)
        plain = msgspec.structs.replace(
            observed,
            attempts=(msgspec.structs.replace(observed.attempts[0], exchanges=(plain_exchange,)),),
        )
        provider = StoredStreamSnapshotProvider(store=store, limits=_limits())

        snapshot = await provider.snapshot(plain)

        assert snapshot.observation == plain
        assert snapshot.stream is None
        assert not snapshot.body_views
        await store.close()

    asyncio.run(scenario())


def test_stream_adapter_contracts_reject_untyped_or_zero_limits(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        StreamDecodingLimits(
            maximum_records=PositiveCount(value=1),
            maximum_controls=PositiveCount(value=1),
            maximum_errors=PositiveCount(value=1),
            maximum_item_bytes=ByteCount(value=0),
        )
    with pytest.raises(TypeError, match="TransientBodyStore"):
        StoredStreamSnapshotProvider(store=object(), limits=_limits())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="stream item limits"):
        StreamDecodingLimits(
            maximum_records=1,  # type: ignore[arg-type]
            maximum_controls=PositiveCount(value=1),
            maximum_errors=PositiveCount(value=1),
            maximum_item_bytes=ByteCount(value=1),
        )
    with pytest.raises(TypeError, match="byte limit"):
        StreamDecodingLimits(
            maximum_records=PositiveCount(value=1),
            maximum_controls=PositiveCount(value=1),
            maximum_errors=PositiveCount(value=1),
            maximum_item_bytes=1,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="StreamDecodingLimits"):
        StoredStreamSnapshotProvider(store=store, limits=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="nested JSON policy"):
        StoredStreamSnapshotProvider(
            store=store,
            limits=_limits(),
            sse_json=object(),  # type: ignore[arg-type]
        )
    provider = StoredStreamSnapshotProvider(store=store, limits=_limits())
    with pytest.raises(TypeError, match="EnvironmentExecutionObservation"):
        asyncio.run(provider.snapshot(object()))  # type: ignore[arg-type]
    asyncio.run(store.close())


def test_sse_policy_contract_and_event_filtering() -> None:
    with pytest.raises(TypeError, match="event types"):
        SseDataJsonPolicy(event_types=["message"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="event types"):
        SseDataJsonPolicy(event_types=("",))
    with pytest.raises(ValueError, match="unique"):
        SseDataJsonPolicy(event_types=("message", "message"))
    assert SseDataJsonPolicy().applies_to("anything")
    policy = SseDataJsonPolicy(event_types=("delta",))
    assert policy.applies_to("delta")
    assert not policy.applies_to("message")


@pytest.mark.parametrize(
    ("payload", "controls", "records"),
    [
        (b"\n", 0, 0),
        (b"id:\nid:\nid: bad\x00id\nretry: nope\nunknown:value\ndata:x\n\n", 2, 1),
        (b"data:one\ndata:two\n\n", 0, 1),
        (b"event:\ndata\n\n", 0, 1),
    ],
)
def test_sse_protocol_edge_lines(
    tmp_path: Path,
    payload: bytes,
    controls: int,
    records: int,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, payload, StreamKind.SSE)
        provider = StoredStreamSnapshotProvider(store=store, limits=_limits())
        snapshot = await provider.snapshot(observed)
        assert snapshot.stream is not None
        assert snapshot.stream.records.control_count.value == controls
        assert snapshot.stream.records.record_count.value == records
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "limits"),
    [
        (b":one\n:two\n", _limits(controls=1)),
        (b"data:one\n\ndata:two\n\n", _limits(records=1)),
        (b"data:toolong\n\n", _limits(item_bytes=3)),
        (b"\xff\n", _limits()),
        (b"data:x", _limits()),
        (b"data:x\n\xff\n", _limits()),
        (b"data:a\ndata:b\n\n", _limits(item_bytes=8)),
        (b":one\nid:two\n", _limits(controls=1)),
        (b":one\nretry:2\n", _limits(controls=1)),
        (b":one\nunknown:two\n", _limits(controls=1)),
    ],
)
def test_sse_limits_and_decode_failures_are_incomplete(
    tmp_path: Path,
    payload: bytes,
    limits: StreamDecodingLimits,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, payload, StreamKind.SSE)
        snapshot = await StoredStreamSnapshotProvider(store=store, limits=limits).snapshot(observed)
        assert snapshot.stream is not None
        assert snapshot.stream.extent.completeness is StreamCompleteness.INCOMPLETE
        assert snapshot.stream.trailing_incomplete
        await store.close()

    asyncio.run(scenario())


def test_sse_nested_json_failure_is_local_and_sensitive(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b"event: delta\ndata: nope\n\nevent: text\ndata: nope\n\n",
            StreamKind.SSE,
            sensitive=True,
        )
        provider = StoredStreamSnapshotProvider(
            store=store,
            limits=_limits(),
            sse_json=SseDataJsonPolicy(event_types=("delta",)),
        )
        snapshot = await provider.snapshot(observed)
        events = [item.item for item in snapshot.stream_items if type(item.item) is SseEvent]
        assert len(events) == 2
        assert type(events[0]) is SseEvent
        assert events[0].nested_data is not None
        assert events[1].nested_data is None
        assert snapshot.observation.views[0].sensitivity is Sensitivity.SENSITIVE
        await store.close()

    asyncio.run(scenario())


def test_json_scalar_types_duplicate_members_and_nonfinite_values(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        payload = b'null\ntrue\n1\n1.5\n1.0\n"x"\n[1]\n{"a":1,"a":2}\nNaN\n\xff\n'
        observed = await _observation(store, payload, StreamKind.NDJSON)
        snapshot = await StoredStreamSnapshotProvider(store=store, limits=_limits()).snapshot(
            observed
        )
        assert snapshot.stream is not None
        assert snapshot.stream.records.record_count.value == 7
        assert snapshot.stream.records.error_count.value == 3
        assert snapshot.stream.extent.termination is StreamTerminationReason.DECODE_FAILURE
        await store.close()

    asyncio.run(scenario())


def test_json_error_and_sequence_framing_limits_stop_before_overflow(tmp_path: Path) -> None:
    async def scenario() -> None:
        ndjson_store = _store(tmp_path)
        ndjson = await _observation(ndjson_store, b"bad\nworse\n", StreamKind.NDJSON)
        failed = await StoredStreamSnapshotProvider(
            store=ndjson_store,
            limits=_limits(errors=1),
        ).snapshot(ndjson)
        assert failed.stream is not None
        assert failed.stream.records.error_count.value == 1
        assert failed.stream.trailing_incomplete
        await ndjson_store.close()

        sequence_store = _store(tmp_path)
        sequence = await _observation(
            sequence_store,
            b"preamble\x1e1\x1e2",
            StreamKind.JSON_SEQUENCE,
        )
        limited = await StoredStreamSnapshotProvider(
            store=sequence_store,
            limits=_limits(errors=1, records=1),
        ).snapshot(sequence)
        assert limited.stream is not None
        assert limited.stream.records.error_count.value == 1
        assert limited.stream.records.record_count.value == 1
        assert limited.stream.trailing_incomplete
        await sequence_store.close()

    asyncio.run(scenario())


def test_json_sequence_without_record_separator_is_a_decode_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"not-a-sequence", StreamKind.JSON_SEQUENCE)
        snapshot = await StoredStreamSnapshotProvider(store=store, limits=_limits()).snapshot(
            observed
        )
        assert snapshot.stream is not None
        assert snapshot.stream.records.error_count.value == 1
        await store.close()

    asyncio.run(scenario())


def test_private_json_conversion_rejects_unknown_runtime_values() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        streaming._json_value(object())
    value, error = streaming._decode_json(b"[" * 1_100 + b"]" * 1_100)
    assert value is None
    assert error is not None


def test_cancelled_sse_marks_an_unterminated_line_incomplete(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b"data:x\nunfinished",
            StreamKind.SSE,
            cancelled=True,
        )
        snapshot = await StoredStreamSnapshotProvider(store=store, limits=_limits()).snapshot(
            observed
        )
        assert snapshot.stream is not None
        assert snapshot.stream.extent.termination is StreamTerminationReason.CANCELLED
        assert snapshot.stream.trailing_incomplete
        await store.close()

    asyncio.run(scenario())


def test_sensitive_json_records_remain_sealed_in_items_and_views(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b'{"secret":true}\n',
            StreamKind.NDJSON,
            sensitive=True,
        )
        snapshot = await StoredStreamSnapshotProvider(store=store, limits=_limits()).snapshot(
            observed
        )
        record = snapshot.stream_items[0].item
        assert type(record) is JsonStreamRecord
        from comparo.core.domain.sensitivity import is_sensitive_value

        assert is_sensitive_value(record.value)
        await store.close()

    asyncio.run(scenario())


class _Chunks:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def _iterate(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()


def test_incremental_line_and_json_sequence_framing_edges() -> None:
    async def scenario() -> None:
        with pytest.raises(streaming._FramingLimitError):
            _ = [
                item
                async for item in streaming._iter_lines(
                    cast(StoredBodyStream, _Chunks(b"abcd\n")),
                    3,
                )
            ]
        cr = [
            item
            async for item in streaming._iter_lines(
                cast(StoredBodyStream, _Chunks(b"\r")),
                2,
            )
        ]
        assert cr[0].terminated

        whitespace = [
            item
            async for item in streaming._iter_sequence_frames(
                cast(StoredBodyStream, _Chunks(b" \t\x1e1")),
                8,
            )
        ]
        assert len(whitespace) == 1
        empty = [
            item
            async for item in streaming._iter_sequence_frames(
                cast(StoredBodyStream, _Chunks(b"   ")),
                8,
            )
        ]
        assert not empty
        with pytest.raises(streaming._FramingLimitError):
            _ = [
                item
                async for item in streaming._iter_sequence_frames(
                    cast(StoredBodyStream, _Chunks(b"long-preamble")),
                    3,
                )
            ]
        with pytest.raises(streaming._FramingLimitError):
            _ = [
                item
                async for item in streaming._iter_sequence_frames(
                    cast(StoredBodyStream, _Chunks(b"\x1elong-record")),
                    3,
                )
            ]

    asyncio.run(scenario())


def test_terminal_target_and_media_helpers_cover_absent_shapes(tmp_path: Path) -> None:
    assert (
        streaming._terminal_stream_target(
            cast(EnvironmentExecutionObservation, SimpleNamespace(attempts=()))
        )
        is None
    )
    assert (
        streaming._terminal_stream_target(
            cast(
                EnvironmentExecutionObservation,
                SimpleNamespace(attempts=(SimpleNamespace(exchanges=()),)),
            )
        )
        is None
    )
    for body in (
        None,
        NoResponseBody(reason=NoBodyReason.STATUS_204),
        ResponseEntityBody(capture=CancelledCapture(), handle=None, encoded_byte_count=None),
    ):
        target = streaming._terminal_stream_target(
            cast(
                EnvironmentExecutionObservation,
                SimpleNamespace(
                    attempts=(
                        SimpleNamespace(
                            exchanges=(
                                SimpleNamespace(
                                    response=(None if body is None else SimpleNamespace(body=body))
                                ),
                            )
                        ),
                    )
                ),
            )
        )
        assert target is None

    async def unknown_media() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"{}", StreamKind.NDJSON)
        response_value = observed.attempts[0].exchanges[0].response
        assert response_value is not None
        entity = response_value.body
        assert type(entity) is ResponseEntityBody
        assert entity.handle is not None
        from comparo.core.domain.http.media_types import MediaType

        unknown = msgspec.structs.replace(
            entity.handle,
            media=ParsedMediaType(value=MediaType(type="application", subtype="octet-stream")),
        )
        assert streaming._stream_kind(unknown) is None
        await store.close()

    asyncio.run(unknown_media())


def test_non_segment_attempt_projection_keeps_pre_send_failure_and_response() -> None:
    effective = request()
    pre_occurrence = occurrence(
        execution=1,
        attempt=10,
        exchange=10,
        environment=effective.environment.identity,
    )
    pre_send = resolution_failure(effective, request_occurrence=pre_occurrence)
    pre_attempt = SimpleNamespace(
        attempt_id=pre_occurrence.attempt_id,
        pre_send=pre_send,
        exchanges=(),
    )
    failed_attempt = SimpleNamespace(
        attempt_id=AttemptId(value=UUID(int=11)),
        pre_send=None,
        exchanges=(
            SimpleNamespace(
                exchange_id=ExchangeId(value=UUID(int=11)),
                response=None,
                failure=failure(FailurePhase.CONNECT),
            ),
        ),
    )
    response_attempt = SimpleNamespace(
        attempt_id=AttemptId(value=UUID(int=12)),
        pre_send=None,
        exchanges=(
            SimpleNamespace(
                exchange_id=ExchangeId(value=UUID(int=12)),
                response=response(),
                failure=None,
            ),
        ),
    )
    segment_attempt = SimpleNamespace(
        attempt_id=AttemptId(value=UUID(int=13)),
        pre_send=None,
        exchanges=(),
    )
    projected = streaming._non_segment_attempts(
        cast(
            EnvironmentExecutionObservation,
            SimpleNamespace(
                attempts=(pre_attempt, failed_attempt, response_attempt, segment_attempt)
            ),
        ),
        cast(HttpAttempt, segment_attempt),
    )
    assert len(projected) == 3
