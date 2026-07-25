"""Bounded ordinary-response snapshot adapter behavior and trust boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

import comparo.adapters.http.ordinary as ordinary
from comparo.adapters.http.ordinary import OrdinaryResponseDecodeLimits
from comparo.adapters.http.ordinary import StoredOrdinaryResponseSnapshotProvider
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.adapters.http.streaming import StoredStreamSnapshotProvider
from comparo.adapters.http.streaming import StreamDecodingLimits
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.media_types import MediaTypeParameter
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.http.media_types import render_media_type
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import InvalidMediaType
from comparo.core.domain.http.response_bodies import InvalidMediaTypeReason
from comparo.core.domain.http.response_bodies import MediaTypeMetadata
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ParsedMediaType
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import IncompleteViewReason
from comparo.core.domain.http.views import ViewBudgetExceeded
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewIncomplete
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewUnsupported
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.snapshot import SelectionSnapshotRequirements
from comparo.core.selection.snapshot import body_view_for
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response

_UNAVAILABLE = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
_ACCESS = SensitiveValueAccess(purpose="ordinary-adapter-test")


def _limits(
    *,
    body: int = 4096,
    text: int = 4096,
    depth: int = 64,
    nodes: int = 4096,
    tokens: int = 8192,
) -> OrdinaryResponseDecodeLimits:
    return OrdinaryResponseDecodeLimits(
        maximum_body_bytes=ByteCount(value=body),
        maximum_text_characters=Count(value=text),
        maximum_json_depth=Count(value=depth),
        maximum_json_nodes=Count(value=nodes),
        maximum_json_tokens=Count(value=tokens),
    )


def _store(tmp_path: Path) -> TransientBodyStore:
    return TransientBodyStore(
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=16),
            maximum_transient_bytes=ByteCount(value=32_768),
            io_chunk_size=PositiveCount(value=3),
            root=tmp_path,
        )
    )


def _stream_limits() -> StreamDecodingLimits:
    return StreamDecodingLimits(
        maximum_records=PositiveCount(value=16),
        maximum_controls=PositiveCount(value=16),
        maximum_errors=PositiveCount(value=16),
        maximum_item_bytes=ByteCount(value=4096),
    )


class _FakeBodyStream:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _parsed_media(
    type_: str,
    subtype: str,
    *,
    charset: str | None = None,
) -> ParsedMediaType:
    parameters = (
        ()
        if charset is None
        else (
            MediaTypeParameter(
                name="charset",
                value=MediaTypeTokenValue(value=charset),
            ),
        )
    )
    return ParsedMediaType(value=MediaType(type=type_, subtype=subtype, parameters=parameters))


async def _observation(
    store: TransientBodyStore,
    payload: bytes,
    media: MediaTypeMetadata,
    *,
    partial: bool = False,
    sensitive: bool = False,
) -> EnvironmentExecutionObservation:
    body_id = BodyId(value=UUID(int=10_000 + len(payload)))
    writer = await store.begin(
        body_id,
        maximum_body_bytes=ByteCount(value=16_384),
        sensitivity=Sensitivity.SENSITIVE if sensitive else Sensitivity.PUBLIC,
        origins=(TaintOriginId(value="test/ordinary-body"),) if sensitive else (),
    )
    async with writer:
        await writer.append(payload)
        handle = await writer.commit_response(
            media=media,
            extent=BodyCaptureExtent.PARTIAL if partial else BodyCaptureExtent.COMPLETE,
        )
    entity = ResponseEntityBody(
        capture=CancelledCapture() if partial else CompleteCapture(),
        handle=handle,
        encoded_byte_count=ByteCount(value=len(payload)),
    )
    fields = ResponseFields()
    if type(media) is ParsedMediaType:
        fields = ResponseFields(
            fields=(
                ResponseField(
                    name=HeaderName(value="content-type"),
                    value=render_media_type(media.value),
                ),
            )
        )
    elif type(media) is InvalidMediaType:
        fields = ResponseFields(
            fields=(
                ResponseField(
                    name=HeaderName(value="content-type"),
                    value=HeaderText(value="application"),
                ),
            )
        )
    observed_response = msgspec.structs.replace(
        response(scheme=request().url.scheme),
        media=media,
        body=entity,
        headers=fields,
    )
    effective = request()
    request_occurrence = occurrence(
        execution=1,
        attempt=1,
        exchange=1,
        environment=effective.environment.identity,
    )
    terminal_failure: TransportFailure | None = (
        failure(FailurePhase.CANCELLATION, retryable=False) if partial else None
    )
    exchange = HttpExchange(
        exchange_id=request_occurrence.exchange_id,
        request=authorized_request(effective, request_occurrence=request_occurrence),
        response=observed_response,
        failure=terminal_failure,
        redirect=None,
        timings=exchange_timings(failure_phase=FailurePhase.CANCELLATION if partial else None),
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


def _view(
    snapshot: object,
    body_id: BodyId,
    requested: BodyViewKind,
) -> ResolvedBodyView | FailedBodyView:

    found = body_view_for(cast(SelectionSnapshot, snapshot), body_id, requested)
    assert found is not None
    return found


def test_json_media_produces_bytes_explicit_views_and_auto_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b'{"answer":42,"decimal":1.5}',
            _parsed_media("application", "json"),
        )
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)

        assert len(snapshot.body_bytes) == 1
        assert snapshot.body_bytes[0].value == BytesValue(value=b'{"answer":42,"decimal":1.5}')
        body_id = snapshot.body_bytes[0].body_id
        text = _view(snapshot, body_id, BodyViewKind.TEXT)
        explicit_json = _view(snapshot, body_id, BodyViewKind.JSON)
        automatic = _view(snapshot, body_id, BodyViewKind.AUTO)
        assert type(text) is ResolvedBodyView
        assert text.value == JsonString(value='{"answer":42,"decimal":1.5}')
        assert type(explicit_json) is ResolvedBodyView
        assert type(explicit_json.value) is JsonObject
        assert type(automatic) is ResolvedBodyView
        assert automatic.handle.decoder.view is ViewKind.JSON
        assert automatic.handle.auto_selection is not None
        assert automatic.payload_fingerprint == automatic.handle.fingerprint
        assert set(snapshot.observation.views) == {
            text.handle,
            explicit_json.handle,
            automatic.handle,
        }
        await store.close()

    asyncio.run(scenario())


def test_auto_reuses_the_explicit_semantic_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b'{"value":1}', _parsed_media("application", "json"))
        original = ordinary._decode_json
        calls = 0

        def tracked_decode(
            data: bytes,
            charset: str,
            limits: OrdinaryResponseDecodeLimits,
        ) -> ordinary._DecodedJson:
            nonlocal calls
            calls += 1
            return original(data, charset, limits)

        monkeypatch.setattr(ordinary, "_decode_json", tracked_decode)
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)

        explicit = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.JSON)
        automatic = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.AUTO)
        assert type(explicit) is ResolvedBodyView
        assert type(automatic) is ResolvedBodyView
        assert automatic.value is explicit.value
        assert calls == 1
        await store.close()

    asyncio.run(scenario())


def test_empty_requirements_do_not_read_or_decode_captured_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b'{"value":1}', _parsed_media("application", "json"))

        def forbidden_read(_store: TransientBodyStore, _handle: object) -> _FakeBodyStream:
            raise AssertionError("status-only evaluation must not read a body")

        monkeypatch.setattr(TransientBodyStore, "open_response", forbidden_read)
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            requirements=SelectionSnapshotRequirements(),
        ).snapshot(observed)

        assert snapshot.observation is observed
        assert snapshot.body_bytes == ()
        assert snapshot.body_views == ()
        assert snapshot.stream is None
        await store.close()

    asyncio.run(scenario())


def test_json_only_requirements_materialize_no_extra_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b'{"value":1}', _parsed_media("application", "json"))

        def forbidden_text(*_arguments: object) -> object:
            raise AssertionError("JSON-only evaluation must not decode text")

        monkeypatch.setattr(ordinary, "_decode_text", forbidden_text)
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            requirements=SelectionSnapshotRequirements(body_views=(BodyViewKind.JSON,)),
        ).snapshot(observed)

        assert snapshot.body_bytes == ()
        assert tuple(item.requested_view for item in snapshot.body_views) == (BodyViewKind.JSON,)
        assert type(snapshot.body_views[0]) is ResolvedBodyView
        assert len(snapshot.observation.views) == 1
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("media", "payload", "decoder"),
    [
        (_parsed_media("application", "json"), b"{}", ViewKind.JSON),
        (_parsed_media("text", "plain"), b"hello", ViewKind.TEXT),
    ],
)
def test_auto_only_requirements_decode_only_the_media_selected_view(
    tmp_path: Path,
    media: MediaTypeMetadata,
    payload: bytes,
    decoder: ViewKind,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, payload, media)
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            requirements=SelectionSnapshotRequirements(body_views=(BodyViewKind.AUTO,)),
        ).snapshot(observed)

        assert snapshot.body_bytes == ()
        assert len(snapshot.body_views) == 1
        view = snapshot.body_views[0]
        assert type(view) is ResolvedBodyView
        assert view.requested_view is BodyViewKind.AUTO
        assert view.handle.decoder.view is decoder
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("media", "payload", "kind"),
    [
        (_parsed_media("text", "plain"), b"hello", ViewKind.TEXT),
        (_parsed_media("application", "xml"), b"<x/>", ViewKind.TEXT),
        (_parsed_media("application", "problem+xml"), b"<x/>", ViewKind.TEXT),
        (_parsed_media("application", "problem+json"), b"{}", ViewKind.JSON),
        (_parsed_media("application", "octet-stream"), b"raw", ViewKind.BYTES),
        (MissingMediaType(), b"raw", ViewKind.BYTES),
    ],
)
def test_auto_registry_is_strictly_media_driven(
    tmp_path: Path,
    media: MediaTypeMetadata,
    payload: bytes,
    kind: ViewKind,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, payload, media)
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)
        automatic = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.AUTO)
        assert type(automatic) is ResolvedBodyView
        assert automatic.handle.decoder.view is kind
        await store.close()

    asyncio.run(scenario())


def test_invalid_media_does_not_fall_back_to_bytes(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b"raw",
            InvalidMediaType(reason=InvalidMediaTypeReason.MALFORMED),
        )
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)
        automatic = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.AUTO)
        assert type(automatic) is FailedBodyView
        assert type(automatic.outcome) is ViewDecodeError
        assert automatic.outcome.code is DecodeErrorCode.INVALID_MEDIA_METADATA
        await store.close()

    asyncio.run(scenario())


def test_sensitive_bom_payload_preserves_lineage_and_fingerprints(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b'\xef\xbb\xbf{"secret":true}',
            _parsed_media("application", "json", charset="UTF-8"),
            sensitive=True,
        )
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)
        body = snapshot.body_bytes[0]
        assert is_sensitive_value(body.value)
        automatic = _view(snapshot, body.body_id, BodyViewKind.AUTO)
        assert type(automatic) is ResolvedBodyView
        assert is_sensitive_value(automatic.value)
        assert is_sensitive_value(automatic.handle.fingerprint)
        assert automatic.value.origins == body.value.origins
        value = automatic.value.reveal(access=_ACCESS)
        assert type(value) is JsonObject
        bom = next(item for item in automatic.handle.decoder.options if item.name == "bom")
        assert bom.value.value is True  # type: ignore[union-attr]
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b'{"a":1,"a":2}', DecodeErrorCode.DUPLICATE_MEMBER),
        (b"NaN", DecodeErrorCode.NON_FINITE_NUMBER),
        (b"{} trailing", DecodeErrorCode.TRAILING_CONTENT),
        (b"[", DecodeErrorCode.INVALID_SYNTAX),
        (b'"\\ud800"', DecodeErrorCode.INVALID_TEXT),
        (b"\xff", DecodeErrorCode.INVALID_TEXT),
    ],
)
def test_json_failures_are_typed(
    tmp_path: Path,
    payload: bytes,
    code: DecodeErrorCode,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, payload, _parsed_media("application", "json"))
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)
        automatic = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.AUTO)
        assert type(automatic) is FailedBodyView
        assert type(automatic.outcome) is ViewDecodeError
        assert automatic.outcome.code is code
        await store.close()

    asyncio.run(scenario())


def test_declared_charset_is_strict_for_text_and_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b"{}",
            _parsed_media("application", "json", charset="iso-8859-1"),
        )
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)
        for requested in (BodyViewKind.TEXT, BodyViewKind.JSON, BodyViewKind.AUTO):
            failed = _view(snapshot, snapshot.body_bytes[0].body_id, requested)
            assert type(failed) is FailedBodyView
            assert type(failed.outcome) is ViewDecodeError
            assert failed.outcome.code is DecodeErrorCode.INVALID_ENCODING
        await store.close()

    asyncio.run(scenario())


def test_contracts_require_exact_stores_limits_delegates_and_observations(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(TypeError, match="body byte limit"):
        OrdinaryResponseDecodeLimits(
            maximum_body_bytes=Count(value=1),  # type: ignore[arg-type]
            maximum_text_characters=Count(value=1),
            maximum_json_depth=Count(value=1),
            maximum_json_nodes=Count(value=1),
            maximum_json_tokens=Count(value=1),
        )
    with pytest.raises(TypeError, match="semantic limits"):
        OrdinaryResponseDecodeLimits(
            maximum_body_bytes=ByteCount(value=1),
            maximum_text_characters=ByteCount(value=1),  # type: ignore[arg-type]
            maximum_json_depth=Count(value=1),
            maximum_json_nodes=Count(value=1),
            maximum_json_tokens=Count(value=1),
        )
    with pytest.raises(TypeError, match="TransientBodyStore"):
        StoredOrdinaryResponseSnapshotProvider(
            store=object(),  # type: ignore[arg-type]
            limits=_limits(),
        )
    with pytest.raises(TypeError, match="decode limits"):
        StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="stream delegate"):
        StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            streams=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="requirements"):
        StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            requirements=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="unsupported body view"):
        StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            requirements=SelectionSnapshotRequirements(body_views=(BodyViewKind.HANDLE,)),
        )
    with pytest.raises(ValueError, match="delegate must agree"):
        StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            requirements=SelectionSnapshotRequirements(stream=True),
        )
    streams = StoredStreamSnapshotProvider(store=store, limits=_stream_limits())
    with pytest.raises(ValueError, match="delegate must agree"):
        StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            streams=streams,
            requirements=SelectionSnapshotRequirements(),
        )
    provider = StoredOrdinaryResponseSnapshotProvider(store=store, limits=_limits())
    with pytest.raises(TypeError, match="EnvironmentExecutionObservation"):
        asyncio.run(provider.snapshot(object()))  # type: ignore[arg-type]
    asyncio.run(provider.close())
    asyncio.run(store.close())


def test_body_byte_admission_limit_fails_every_decoded_view_with_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b'{"large":true}',
            _parsed_media("application", "json"),
        )
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(body=4),
        ).snapshot(observed)

        assert not snapshot.body_bytes
        assert len(snapshot.body_views) == 3
        for failed in snapshot.body_views:
            assert type(failed) is FailedBodyView
            assert type(failed.outcome) is ViewBudgetExceeded
            exhaustion = failed.outcome.exhaustion
            assert exhaustion.budget.resource is BudgetResource.MEMORY_BYTES
            assert exhaustion.budget.effective == ByteCount(value=4)
            assert exhaustion.attempted == ByteCount(value=14)
            assert exhaustion.budget.scope.body_id == failed.body_id  # type: ignore[union-attr]
        await store.close()

    asyncio.run(scenario())


def test_closed_store_fails_closed_without_exposing_store_details(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"{}", _parsed_media("application", "json"))
        await store.close()

        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)

        assert not snapshot.body_bytes
        assert len(snapshot.body_views) == 3
        for failed in snapshot.body_views:
            assert type(failed) is FailedBodyView
            assert type(failed.outcome) is ViewUnsupported
            assert failed.outcome.reason.value == "decoder-unavailable"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("chunks", "budget_failure"),
    [
        ((b"123", b"456"), True),
        ((b"1",), False),
    ],
)
def test_reader_overrun_and_descriptor_short_read_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunks: tuple[bytes, ...],
    budget_failure: bool,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"1234", MissingMediaType())

        def fake_open(
            _store_value: TransientBodyStore,
            _handle: object,
        ) -> _FakeBodyStream:
            return _FakeBodyStream(chunks)

        monkeypatch.setattr(TransientBodyStore, "open_response", fake_open)
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(body=4),
        ).snapshot(observed)

        assert not snapshot.body_bytes
        assert len(snapshot.body_views) == 3
        for failed in snapshot.body_views:
            assert type(failed) is FailedBodyView
            assert (type(failed.outcome) is ViewBudgetExceeded) is budget_failure
            if budget_failure:
                assert cast(ViewBudgetExceeded, failed.outcome).exhaustion.attempted == ByteCount(
                    value=6
                )
            else:
                assert type(failed.outcome) is ViewUnsupported
        await store.close()

    asyncio.run(scenario())


def test_text_character_limit_has_typed_count_budget_evidence(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"hello", _parsed_media("text", "plain"))
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(text=4),
        ).snapshot(observed)

        for requested in (BodyViewKind.TEXT, BodyViewKind.AUTO):
            failed = _view(snapshot, snapshot.body_bytes[0].body_id, requested)
            assert type(failed) is FailedBodyView
            assert type(failed.outcome) is ViewBudgetExceeded
            assert failed.outcome.exhaustion.budget.resource is BudgetResource.DECODED_CHARACTERS
            assert failed.outcome.exhaustion.budget.effective == Count(value=4)
            assert failed.outcome.exhaustion.attempted == Count(value=5)
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "limits", "resource"),
    [
        (b"[[0]]", _limits(depth=1), BudgetResource.JSON_DEPTH),
        (b"{}", _limits(tokens=1), BudgetResource.JSON_TOKENS),
        (b"[1,2]", _limits(nodes=2), BudgetResource.JSON_NODES),
        (b"0", _limits(depth=0), BudgetResource.JSON_DEPTH),
    ],
)
def test_json_limits_have_typed_body_scoped_evidence(
    tmp_path: Path,
    payload: bytes,
    limits: OrdinaryResponseDecodeLimits,
    resource: BudgetResource,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, payload, _parsed_media("application", "json"))
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=limits,
        ).snapshot(observed)

        for requested in (BodyViewKind.JSON, BodyViewKind.AUTO):
            failed = _view(snapshot, snapshot.body_bytes[0].body_id, requested)
            assert type(failed) is FailedBodyView
            assert type(failed.outcome) is ViewBudgetExceeded
            exhaustion = failed.outcome.exhaustion
            assert exhaustion.budget.resource is resource
            assert exhaustion.attempted.value > exhaustion.budget.effective.value  # type: ignore[union-attr]
            assert exhaustion.budget.scope.body_id == failed.body_id  # type: ignore[union-attr]
        await store.close()

    asyncio.run(scenario())


def test_partial_entity_allows_prefix_safe_text_and_bytes_but_not_json(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b'{"unfinished":',
            _parsed_media("application", "json"),
            partial=True,
        )
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)
        body_id = snapshot.body_bytes[0].body_id

        text = _view(snapshot, body_id, BodyViewKind.TEXT)
        assert type(text) is ResolvedBodyView
        assert text.handle.completeness is ViewCompleteness.PREFIX
        for requested in (BodyViewKind.JSON, BodyViewKind.AUTO):
            failed = _view(snapshot, body_id, requested)
            assert type(failed) is FailedBodyView
            assert type(failed.outcome) is ViewIncomplete
            assert failed.outcome.reason is IncompleteViewReason.ENTITY_PARTIAL

        missing = await _observation(store, b"raw", MissingMediaType(), partial=True)
        bytes_snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(missing)
        automatic = _view(
            bytes_snapshot,
            bytes_snapshot.body_bytes[0].body_id,
            BodyViewKind.AUTO,
        )
        assert type(automatic) is ResolvedBodyView
        assert automatic.handle.decoder.view is ViewKind.BYTES
        assert automatic.handle.completeness is ViewCompleteness.PREFIX
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("media", "kind"),
    [
        (_parsed_media("text", "event-stream"), ViewKind.SSE),
        (_parsed_media("application", "x-ndjson"), ViewKind.NDJSON),
        (_parsed_media("application", "ndjson"), ViewKind.NDJSON),
        (_parsed_media("application", "json-seq"), ViewKind.JSON_SEQUENCE),
    ],
)
def test_stream_media_requires_the_explicit_stream_delegate(
    tmp_path: Path,
    media: ParsedMediaType,
    kind: ViewKind,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"{}\n", media)
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)

        automatic = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.AUTO)
        assert type(automatic) is FailedBodyView
        assert type(automatic.outcome) is ViewUnsupported
        assert automatic.outcome.decoder.view is kind
        await store.close()

    asyncio.run(scenario())


def test_stream_delegate_composes_one_closed_snapshot_and_rebinds_owner(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b'{"value":1}\n{"value":2}\n',
            _parsed_media("application", "x-ndjson"),
        )
        streams = StoredStreamSnapshotProvider(store=store, limits=_stream_limits())
        provider = StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            streams=streams,
        )
        snapshot = await provider.snapshot(observed)

        assert snapshot.body_bytes
        body_id = snapshot.body_bytes[0].body_id
        assert sum(item.requested_view is BodyViewKind.AUTO for item in snapshot.body_views) == 1
        automatic = _view(snapshot, body_id, BodyViewKind.AUTO)
        assert type(automatic) is ResolvedBodyView
        assert automatic.handle.decoder.view is ViewKind.NDJSON
        assert snapshot.stream is not None
        assert snapshot.stream.records.owner == snapshot.observation
        assert snapshot.stream_items
        await provider.close()

        limited = StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(body=1),
            streams=StoredStreamSnapshotProvider(store=store, limits=_stream_limits()),
        )
        limited_snapshot = await limited.snapshot(observed)
        assert not limited_snapshot.body_bytes
        assert len(limited_snapshot.body_views) == 3
        assert (
            sum(
                item.requested_view is BodyViewKind.AUTO and type(item) is ResolvedBodyView
                for item in limited_snapshot.body_views
            )
            == 1
        )
        assert (
            sum(
                item.requested_view is not BodyViewKind.AUTO
                and type(item) is FailedBodyView
                and type(item.outcome) is ViewBudgetExceeded
                for item in limited_snapshot.body_views
            )
            == 2
        )
        await limited.close()
        await store.close()

    asyncio.run(scenario())


def test_stream_delegate_store_failure_falls_back_to_typed_unavailability(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b"1\n",
            _parsed_media("application", "x-ndjson"),
        )
        provider = StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
            streams=StoredStreamSnapshotProvider(store=store, limits=_stream_limits()),
        )
        await store.close()

        snapshot = await provider.snapshot(observed)

        assert snapshot.stream is None
        assert not snapshot.body_bytes
        assert len(snapshot.body_views) == 3
        assert all(
            type(item) is FailedBodyView and type(item.outcome) is ViewUnsupported
            for item in snapshot.body_views
        )
        await provider.close()

    asyncio.run(scenario())


def test_empty_and_utf8_alias_text_are_valid_but_empty_json_is_not(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(
            store,
            b"",
            _parsed_media("text", "plain", charset="UTF8"),
        )
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)

        text = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.AUTO)
        assert type(text) is ResolvedBodyView
        assert text.value == JsonString(value="")
        json_view = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.JSON)
        assert type(json_view) is FailedBodyView
        assert type(json_view.outcome) is ViewDecodeError
        assert json_view.outcome.code is DecodeErrorCode.INVALID_SYNTAX
        await store.close()

    asyncio.run(scenario())


def test_strict_json_preserves_every_scalar_and_container_kind(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        payload = (
            b' { "none":null, "bool":true, "int":1, "whole":1.0, '
            b'"decimal":1.50, "text":"a\\"b", "array":[false], '
            b'"object":{"x":0} } '
        )
        observed = await _observation(store, payload, _parsed_media("application", "json"))
        snapshot = await StoredOrdinaryResponseSnapshotProvider(
            store=store,
            limits=_limits(),
        ).snapshot(observed)
        resolved = _view(snapshot, snapshot.body_bytes[0].body_id, BodyViewKind.JSON)
        assert type(resolved) is ResolvedBodyView
        value = cast(JsonObject, resolved.value)
        by_name = {member.name: member.value for member in value.members}
        assert type(by_name["none"]) is JsonNull
        assert type(by_name["bool"]) is JsonBoolean
        assert type(by_name["int"]) is JsonInteger
        assert type(by_name["whole"]) is JsonInteger
        assert by_name["decimal"] == JsonDecimal(value=Decimal("1.5"))
        assert by_name["text"] == JsonString(value='a"b')
        assert type(by_name["array"]) is JsonArray
        assert type(by_name["object"]) is JsonObject
        await store.close()

    asyncio.run(scenario())


def test_no_response_entities_and_duplicate_handles_are_not_materialized_twice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        observed = await _observation(store, b"raw", MissingMediaType())
        terminal = observed.attempts[0].exchanges[0]
        response_value = terminal.response
        assert response_value is not None
        entity = response_value.body
        assert type(entity) is ResponseEntityBody
        assert entity.handle is not None
        occurrence_value = terminal.request.occurrence
        request_value = SimpleNamespace(occurrence=occurrence_value)
        without_handle = ResponseEntityBody(
            capture=PolicyOmittedCapture(),
            handle=None,
            encoded_byte_count=None,
        )
        fake = SimpleNamespace(
            attempts=(
                SimpleNamespace(
                    exchanges=(
                        SimpleNamespace(response=None, request=request_value),
                        SimpleNamespace(
                            response=SimpleNamespace(
                                body=NoResponseBody(reason=NoBodyReason.STATUS_204)
                            ),
                            request=request_value,
                        ),
                        SimpleNamespace(
                            response=SimpleNamespace(body=without_handle),
                            request=request_value,
                        ),
                        SimpleNamespace(
                            response=SimpleNamespace(body=entity),
                            request=request_value,
                        ),
                        SimpleNamespace(
                            response=SimpleNamespace(body=entity),
                            request=request_value,
                        ),
                    )
                ),
            )
        )

        targets = ordinary._targets(cast(EnvironmentExecutionObservation, fake))

        assert len(targets) == 1
        assert targets[0].handle == entity.handle
        await store.close()

    asyncio.run(scenario())


def test_internal_closed_conversions_reject_impossible_decoder_results() -> None:
    with pytest.raises(ordinary._NonFiniteNumberError):
        ordinary._json_value(Decimal("NaN"))
    with pytest.raises(TypeError, match="unsupported value"):
        ordinary._json_value(object())
    with pytest.raises(TypeError, match="count or byte"):
        ordinary._budget_amount_value(Duration(nanoseconds=1))
    assert ordinary._scan_json_budgets("]", _limits()) is None
