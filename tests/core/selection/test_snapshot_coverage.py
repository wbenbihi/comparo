"""Close branch coverage for immutable selector snapshot integrity."""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderRegistryIdentity
from comparo.core.domain.http.views import IncompleteViewReason
from comparo.core.domain.http.views import ReadyView
from comparo.core.domain.http.views import ViewCancelled
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewIncomplete
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewNoBody
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonString
from comparo.core.selection import snapshot as snapshot_module
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import ResolvedBodyBytes
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.snapshot import SelectionSnapshotRequirements
from comparo.core.selection.snapshot import body_bytes_for
from comparo.core.selection.snapshot import body_view_for
from comparo.core.selection.snapshot import decoded_outcome
from comparo.core.selection.snapshot import is_failed_view_outcome
from tests.core.domain.http.factories import finalized_request
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_failure
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import single_stream_execution
from tests.core.domain.http.stream_factories import stream_timings
from tests.core.domain.http.stream_factories import view_handle

_BODY_ORIGIN = TaintOriginId(value="body-content")
_VIEW_ORIGIN = TaintOriginId(value="view-content")


def _repeatable(data: bytes = b"data", *, sensitive: bool = False) -> RepeatableByteSource:
    digest = Sha256Digest(hex_value=sha256(data).hexdigest())
    contained = SensitiveValue(digest, origins=(_BODY_ORIGIN,)) if sensitive else digest
    return RepeatableByteSource(
        source_id=BodyId(value=UUID(int=800 + len(data))),
        byte_count=ByteCount(value=len(data)),
        digest=contained,
        provenance=provenance(),
    )


def _sensitive_body_handle(data: bytes = b"data") -> BodyHandle:
    original = body_handle(size=len(data))
    return msgspec.structs.replace(
        original,
        digest=SensitiveValue(
            Sha256Digest(hex_value=sha256(data).hexdigest()),
            origins=(_BODY_ORIGIN,),
        ),
    )


def _resolved_view(
    kind: ViewKind,
    value: BytesValue | JsonString | JsonArray,
    *,
    requested: BodyViewKind | None = None,
) -> ResolvedBodyView:
    actual_requested = (
        requested
        or {
            ViewKind.TEXT: BodyViewKind.TEXT,
            ViewKind.JSON: BodyViewKind.JSON,
            ViewKind.SSE: BodyViewKind.SSE_EVENTS,
            ViewKind.NDJSON: BodyViewKind.NDJSON_RECORDS,
            ViewKind.JSON_SEQUENCE: BodyViewKind.JSON_SEQUENCE_RECORDS,
        }[kind]
    )
    handle = view_handle(kind, source=_repeatable(sensitive=True), view_number=810)
    if actual_requested is BodyViewKind.AUTO:
        handle = msgspec.structs.replace(
            handle,
            auto_selection=AutoViewSelection(
                registry=DecoderRegistryIdentity(value="comparo/auto@1"),
                decoder=handle.decoder,
            ),
        )
    return ResolvedBodyView(
        handle=handle,
        requested_view=actual_requested,
        value=SensitiveValue(value, origins=(_VIEW_ORIGIN,)),
        payload_fingerprint=handle.fingerprint,
    )


def _stream(*, records: int = 0) -> StreamObservation:
    return StreamObservation(
        records=records_handle(StreamKind.SSE, records=records),
        segments=(segment(index=0, first=0, count=records),),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=stream_timings() if records else _empty_stream_timings(),
    )


def _empty_stream_timings() -> StreamTimings:
    empty = NotPerformed(reason=NotPerformedReason.EMPTY_STREAM)
    return StreamTimings(
        time_to_first_record=empty,
        observation_window_elapsed=stream_timings().observation_window_elapsed,
    )


def test_body_bytes_validate_public_and_sensitive_integrity() -> None:
    data = b"data"
    public_source = _repeatable(data)
    sensitive_source = _repeatable(data, sensitive=True)
    public = ResolvedBodyBytes(source=public_source, value=BytesValue(value=data))
    sensitive = ResolvedBodyBytes(
        source=sensitive_source,
        value=SensitiveValue(BytesValue(value=data), origins=(_BODY_ORIGIN,)),
    )
    assert public.body_id == public_source.source_id
    assert sensitive.body_id == sensitive_source.source_id

    with pytest.raises(TypeError, match="source descriptor"):
        ResolvedBodyBytes(
            source=cast("RepeatableByteSource", "bad"),
            value=BytesValue(value=data),
        )
    with pytest.raises(TypeError, match="BytesValue containment"):
        ResolvedBodyBytes(source=public_source, value=cast("BytesValue", "bad"))
    with pytest.raises(TypeError, match="contain BytesValue"):
        ResolvedBodyBytes(
            source=public_source,
            value=cast(
                "SensitiveValue[BytesValue]",
                SensitiveValue(JsonString(value="bad"), origins=(_BODY_ORIGIN,)),
            ),
        )
    with pytest.raises(ValueError, match="length"):
        ResolvedBodyBytes(source=public_source, value=BytesValue(value=b"short"))
    with pytest.raises(ValueError, match="digest"):
        ResolvedBodyBytes(source=public_source, value=BytesValue(value=b"DATA"))
    with pytest.raises(ValueError, match="sensitivity"):
        ResolvedBodyBytes(
            source=public_source,
            value=SensitiveValue(BytesValue(value=data), origins=(_BODY_ORIGIN,)),
        )
    with pytest.raises(ValueError, match="sensitivity"):
        ResolvedBodyBytes(source=sensitive_source, value=BytesValue(value=data))
    with pytest.raises(ValueError, match="taint origins"):
        ResolvedBodyBytes(
            source=sensitive_source,
            value=SensitiveValue(
                BytesValue(value=data),
                origins=(TaintOriginId(value="different"),),
            ),
        )


def test_body_handle_integrity_covers_handle_identity_and_sensitive_digest() -> None:
    data = b"data"
    handle = _sensitive_body_handle(data)
    resolved = ResolvedBodyBytes(
        source=handle,
        value=SensitiveValue(BytesValue(value=data), origins=(_BODY_ORIGIN,)),
    )
    assert resolved.body_id == handle.body_id


def test_resolved_views_cover_decoder_payload_and_sensitivity_rules() -> None:
    text = _resolved_view(ViewKind.TEXT, JsonString(value="text"))
    json = _resolved_view(ViewKind.JSON, JsonString(value="json"))
    sse = _resolved_view(ViewKind.SSE, JsonArray(items=()))
    ndjson = _resolved_view(ViewKind.NDJSON, JsonArray(items=()))
    sequence = _resolved_view(ViewKind.JSON_SEQUENCE, JsonArray(items=()))
    bytes_view = _resolved_view(
        ViewKind.BYTES,
        BytesValue(value=b"data"),
        requested=BodyViewKind.AUTO,
    )
    assert tuple(
        view.handle.decoder.view for view in (text, json, sse, ndjson, sequence, bytes_view)
    ) == (
        ViewKind.TEXT,
        ViewKind.JSON,
        ViewKind.SSE,
        ViewKind.NDJSON,
        ViewKind.JSON_SEQUENCE,
        ViewKind.BYTES,
    )

    with pytest.raises(TypeError, match="DecodedViewHandle"):
        msgspec.structs.replace(text, handle=cast("object", "bad"))
    with pytest.raises(TypeError, match="BodyViewKind"):
        msgspec.structs.replace(text, requested_view=cast("BodyViewKind", "bad"))
    for requested in (BodyViewKind.HANDLE, BodyViewKind.ENTITY_BYTES):
        with pytest.raises(ValueError, match="not decoded"):
            msgspec.structs.replace(text, requested_view=requested)
    with pytest.raises(TypeError, match="unsupported payload"):
        msgspec.structs.replace(text, value=cast("object", "bad"))
    with pytest.raises(ValueError, match="preserve handle sensitivity"):
        msgspec.structs.replace(text, value=JsonString(value="public"))
    with pytest.raises(ValueError, match="BytesValue"):
        msgspec.structs.replace(
            bytes_view,
            value=SensitiveValue(JsonString(value="bad"), origins=(_VIEW_ORIGIN,)),
        )
    with pytest.raises(ValueError, match="JsonString"):
        msgspec.structs.replace(
            text,
            value=SensitiveValue(BytesValue(value=b"data"), origins=(_VIEW_ORIGIN,)),
        )
    with pytest.raises(ValueError, match="exact JSON"):
        msgspec.structs.replace(
            json,
            value=SensitiveValue(BytesValue(value=b"data"), origins=(_VIEW_ORIGIN,)),
        )
    with pytest.raises(ValueError, match="JsonArray"):
        msgspec.structs.replace(
            sse,
            value=SensitiveValue(JsonString(value="bad"), origins=(_VIEW_ORIGIN,)),
        )


def test_view_fingerprint_proof_handles_public_mixed_and_malformed_values() -> None:
    sensitive = _resolved_view(ViewKind.TEXT, JsonString(value="text"))
    public_base = view_handle(
        ViewKind.TEXT,
        source=_repeatable(),
        view_number=811,
    )
    public_handle = msgspec.structs.replace(
        public_base,
        fingerprint=fingerprint("b"),
        sensitivity=Sensitivity.PUBLIC,
    )
    public = ResolvedBodyView(
        handle=public_handle,
        requested_view=BodyViewKind.TEXT,
        value=JsonString(value="text"),
        payload_fingerprint=public_handle.fingerprint,
    )
    assert public.payload_fingerprint == public_handle.fingerprint
    with pytest.raises(ValueError, match="fingerprint"):
        msgspec.structs.replace(public, payload_fingerprint=fingerprint("c"))
    with pytest.raises(ValueError, match="fingerprint"):
        msgspec.structs.replace(public, payload_fingerprint=sensitive.handle.fingerprint)
    with pytest.raises(ValueError, match="fingerprint"):
        msgspec.structs.replace(
            sensitive,
            payload_fingerprint=cast("Fingerprint", "bad"),
        )
    with pytest.raises(ValueError, match="fingerprint"):
        msgspec.structs.replace(
            sensitive,
            payload_fingerprint=cast(
                "SensitiveValue[Fingerprint]",
                SensitiveValue(JsonString(value="bad"), origins=(_VIEW_ORIGIN,)),
            ),
        )


def test_failed_view_contract_covers_all_direct_boundaries() -> None:
    source = _repeatable()
    outcome = ViewDecodeError(
        decoder=decoder(ViewKind.JSON),
        code=DecodeErrorCode.INVALID_SYNTAX,
    )
    failed = FailedBodyView(
        source=source,
        requested_view=BodyViewKind.JSON,
        outcome=outcome,
    )
    assert failed.body_id == source.source_id
    assert (
        FailedBodyView(
            source=_sensitive_body_handle(),
            requested_view=BodyViewKind.JSON,
            outcome=outcome,
        ).body_id
        == _sensitive_body_handle().body_id
    )

    with pytest.raises(TypeError, match="source descriptor"):
        msgspec.structs.replace(failed, source=cast("RepeatableByteSource", "bad"))
    with pytest.raises(TypeError, match="BodyViewKind"):
        msgspec.structs.replace(failed, requested_view=cast("BodyViewKind", "bad"))
    for requested in (BodyViewKind.HANDLE, BodyViewKind.ENTITY_BYTES):
        with pytest.raises(ValueError, match="not decoded"):
            msgspec.structs.replace(failed, requested_view=requested)
    ready = ReadyView(handle=view_handle(), completeness=ViewCompleteness.FULL)
    with pytest.raises(TypeError, match="non-ready"):
        msgspec.structs.replace(failed, outcome=cast("ViewDecodeError", ready))


def test_snapshot_components_reject_untyped_and_duplicate_values() -> None:
    observation = single_stream_execution(ViewKind.SSE)
    snapshot = SelectionSnapshot(observation=observation)
    with pytest.raises(TypeError, match="EnvironmentExecutionObservation"):
        msgspec.structs.replace(
            snapshot,
            observation=cast("EnvironmentExecutionObservation", "bad"),
        )
    with pytest.raises(TypeError, match="body bytes"):
        msgspec.structs.replace(snapshot, body_bytes=cast("tuple[ResolvedBodyBytes, ...]", []))
    with pytest.raises(TypeError, match="body bytes"):
        msgspec.structs.replace(
            snapshot,
            body_bytes=(cast("ResolvedBodyBytes", "bad"),),
        )
    with pytest.raises(TypeError, match="body views"):
        msgspec.structs.replace(snapshot, body_views=cast("tuple[ResolvedBodyView, ...]", []))
    with pytest.raises(TypeError, match="body views"):
        msgspec.structs.replace(
            snapshot,
            body_views=(cast("ResolvedBodyView", "bad"),),
        )

    handle = observation.views[0]
    resolved = ResolvedBodyView(
        handle=handle,
        requested_view=BodyViewKind.SSE_EVENTS,
        value=SensitiveValue(JsonArray(items=()), origins=(_VIEW_ORIGIN,)),
        payload_fingerprint=handle.fingerprint,
    )
    with pytest.raises(ValueError, match="unique"):
        SelectionSnapshot(observation=observation, body_views=(resolved, resolved))


def test_snapshot_requirements_reject_mutable_duplicate_and_untyped_fields() -> None:
    valid = SelectionSnapshotRequirements(
        body_views=(BodyViewKind.TEXT,),
        stream=True,
    )
    assert valid.stream
    with pytest.raises(TypeError, match="immutable typed tuple"):
        SelectionSnapshotRequirements(body_views=cast("tuple[BodyViewKind, ...]", []))
    with pytest.raises(TypeError, match="immutable typed tuple"):
        SelectionSnapshotRequirements(
            body_views=(cast("BodyViewKind", object()),),
        )
    with pytest.raises(ValueError, match="must be unique"):
        SelectionSnapshotRequirements(
            body_views=(BodyViewKind.TEXT, BodyViewKind.TEXT),
        )
    with pytest.raises(TypeError, match="must be bool"):
        SelectionSnapshotRequirements(stream=cast("bool", 1))


def test_owned_body_discovery_covers_exchange_and_pre_send_paths() -> None:
    source = _repeatable()
    materialized = MaterializedBody(source=source)
    response = single_stream_execution(ViewKind.SSE).attempts[0].exchanges[0].response
    assert response is not None
    entity = cast("ResponseEntityBody", response.body)
    fake = SimpleNamespace(
        attempts=(
            SimpleNamespace(
                exchanges=(
                    SimpleNamespace(
                        request=SimpleNamespace(request=SimpleNamespace(body=materialized)),
                        response=None,
                    ),
                    SimpleNamespace(
                        request=SimpleNamespace(request=SimpleNamespace(body=materialized)),
                        response=SimpleNamespace(body=entity),
                    ),
                ),
                pre_send=SimpleNamespace(request=finalized_request(body=materialized)),
            ),
            SimpleNamespace(
                exchanges=(),
                pre_send=SimpleNamespace(request=finalized_request()),
            ),
        )
    )
    owned = snapshot_module._owned_body_sources(
        cast("EnvironmentExecutionObservation", fake),
    )
    assert source in owned
    assert entity.handle in owned

    effective = request()
    failure = resolution_failure(
        effective,
        request_occurrence=occurrence(environment=effective.environment.identity),
    )
    assert snapshot_module._pre_send_request(failure) == failure.request.request


def test_stream_snapshot_validation_covers_absence_type_owner_count_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = single_stream_execution(ViewKind.SSE)
    with pytest.raises(ValueError, match="require a stream observation"):
        SelectionSnapshot(
            observation=observation,
            stream_items=(cast("StreamItemEnvelope", "bad"),),
        )
    with pytest.raises(TypeError, match="StreamObservation"):
        SelectionSnapshot(
            observation=observation,
            stream=cast("StreamObservation", "bad"),
        )

    stream = _stream()
    with pytest.raises(ValueError, match="different execution"):
        SelectionSnapshot(
            observation=single_stream_execution(ViewKind.NDJSON),
            stream=stream,
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        SelectionSnapshot(
            observation=stream.records.owner,
            stream=stream,
            stream_items=cast("tuple[StreamItemEnvelope, ...]", []),
        )

    populated = _stream(records=2)
    with pytest.raises(ValueError, match="complete closed stream index"):
        SelectionSnapshot(observation=populated.records.owner, stream=populated)

    monkeypatch.setattr(StreamObservation, "validate_item_envelopes", lambda *_args: None)
    fake_envelopes = tuple(
        SimpleNamespace(
            item=SimpleNamespace(
                location=SimpleNamespace(item_index=Count(value=index)),
            )
        )
        for index in (1, 0)
    )
    fake_snapshot = SimpleNamespace(
        observation=populated.records.owner,
        stream=populated,
        stream_items=fake_envelopes,
    )
    with pytest.raises(ValueError, match="contiguous"):
        snapshot_module._validate_stream(cast("SelectionSnapshot", fake_snapshot))


def test_snapshot_lookup_and_outcome_adapters_are_total() -> None:
    observation = single_stream_execution(ViewKind.SSE)
    handle = observation.views[0]
    resolved = ResolvedBodyView(
        handle=handle,
        requested_view=BodyViewKind.SSE_EVENTS,
        value=SensitiveValue(JsonArray(items=()), origins=(_VIEW_ORIGIN,)),
        payload_fingerprint=handle.fingerprint,
    )
    failed = FailedBodyView(
        source=handle.source,
        requested_view=BodyViewKind.JSON,
        outcome=ViewIncomplete(
            decoder=decoder(ViewKind.JSON),
            reason=IncompleteViewReason.ENTITY_PARTIAL,
        ),
    )
    snapshot = SelectionSnapshot(observation=observation, body_views=(resolved, failed))
    assert body_view_for(snapshot, handle.body_id, BodyViewKind.SSE_EVENTS) is resolved
    assert body_view_for(snapshot, handle.body_id, BodyViewKind.TEXT) is None
    assert body_bytes_for(snapshot, handle.body_id) is None
    assert decoded_outcome(failed) is failed.outcome
    assert type(decoded_outcome(resolved)) is ReadyView
    assert is_failed_view_outcome(ViewNoBody(decoder=decoder(ViewKind.JSON)))
    assert is_failed_view_outcome(ViewCancelled(decoder=decoder(ViewKind.JSON)))
    assert not is_failed_view_outcome(decoded_outcome(resolved))
