"""Bounded response decoding and terminal capture-state tests."""

from __future__ import annotations

import asyncio
import gzip
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpcore
import pytest

from comparo.adapters.http import capture as capture_module
from comparo.adapters.http.capture import CaptureBudgetProvider
from comparo.adapters.http.capture import CapturedEntity
from comparo.adapters.http.capture import DefaultCaptureBudgetProvider
from comparo.adapters.http.capture import ResponseBodyCapturer
from comparo.adapters.http.capture import ResponseCaptureLimits
from comparo.adapters.http.capture import _ratio
from comparo.adapters.http.capture import _zero_like
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ContentCoding
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request

_ORIGIN = TaintOriginId(value="test/response-secret")


def _limits(
    *,
    encoded: int = 4_096,
    entity: int = 4_096,
    ratio: int = 100,
) -> ResponseCaptureLimits:
    return ResponseCaptureLimits(
        maximum_encoded_body_bytes=ByteCount(value=encoded),
        maximum_entity_body_bytes=ByteCount(value=entity),
        maximum_expansion_ratio=Ratio(
            numerator=ratio,
            denominator=PositiveCount(value=1),
        ),
    )


def _store(tmp_path: Path, *, maximum: int = 16_384) -> TransientBodyStore:
    return TransientBodyStore(
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=min(8, maximum)),
            maximum_transient_bytes=ByteCount(value=maximum),
            io_chunk_size=PositiveCount(value=3),
            root=tmp_path,
        )
    )


async def _chunks(
    *chunks: bytes,
    failure: BaseException | None = None,
) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk
    if failure is not None:
        raise failure


def _response(
    *chunks: bytes,
    failure: BaseException | None = None,
) -> httpcore.Response:
    return httpcore.Response(status=200, content=_chunks(*chunks, failure=failure))


async def _capture(
    store: TransientBodyStore,
    limits: ResponseCaptureLimits,
    response: httpcore.Response,
    *,
    exchange: int,
    encodings: tuple[ContentCoding, ...] = (),
    encoding_valid: bool = True,
    sensitivity: Sensitivity = Sensitivity.PUBLIC,
) -> tuple[CapturedEntity, list[None]]:
    capturer = ResponseBodyCapturer(
        store=store,
        limits=limits,
        budgets=DefaultCaptureBudgetProvider(limits, default_budget_origin()),
    )
    effective = request()
    outbound = authorized_request(
        effective,
        request_occurrence=occurrence(
            exchange=exchange,
            environment=effective.environment.identity,
        ),
    )
    first_bytes: list[None] = []
    result = await capturer.capture(
        response,
        request=outbound,
        media=MissingMediaType(),
        encodings=encodings,
        encoding_valid=encoding_valid,
        sensitivity=sensitivity,
        origins=(_ORIGIN,) if sensitivity is Sensitivity.SENSITIVE else (),
        first_byte=lambda: first_bytes.append(None),
    )
    return result, first_bytes


def test_identity_and_gzip_capture_complete_repeatable_entities(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        identity, identity_marks = await _capture(
            store,
            _limits(),
            _response(b"one", b"", b"two"),
            exchange=701,
            sensitivity=Sensitivity.SENSITIVE,
        )
        assert type(identity.body.capture) is CompleteCapture
        assert identity.body.handle is not None
        assert identity.body.handle.sensitivity is Sensitivity.SENSITIVE
        assert identity.body.encoded_byte_count == ByteCount(value=6)
        assert (
            b"".join([chunk async for chunk in store.open_response(identity.body.handle)])
            == b"onetwo"
        )
        assert len(identity_marks) == 2

        entity = b"compressed response"
        encoded = gzip.compress(entity, mtime=0)
        decoded, _marks = await _capture(
            store,
            _limits(),
            _response(encoded[:5], encoded[5:]),
            exchange=702,
            encodings=(ContentCoding(value="gzip"),),
        )
        assert type(decoded.body.capture) is CompleteCapture
        assert decoded.body.handle is not None
        assert decoded.body.encoded_byte_count == ByteCount(value=len(encoded))
        assert (
            b"".join([chunk async for chunk in store.open_response(decoded.body.handle)]) == entity
        )
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        (_limits(encoded=0, entity=10), CaptureBudgetKind.ENCODED_BYTES),
        (_limits(encoded=2, entity=10), CaptureBudgetKind.ENCODED_BYTES),
        (_limits(encoded=10, entity=2), CaptureBudgetKind.ENTITY_BYTES),
    ],
)
def test_identity_capture_stops_at_exact_byte_budget_frontier(
    tmp_path: Path,
    limits: ResponseCaptureLimits,
    expected: CaptureBudgetKind,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        result, _marks = await _capture(
            store,
            limits,
            _response(b"abcdef"),
            exchange=709 + limits.maximum_encoded_body_bytes.value,
        )
        assert result.failure is not None
        assert result.failure.phase is FailurePhase.RESOURCE_BUDGET
        assert type(result.body.capture) is BudgetExceededCapture
        assert result.body.capture.budget is expected
        assert result.body.handle is not None
        retained = min(2, limits.maximum_encoded_body_bytes.value)
        assert result.body.handle.byte_count == ByteCount(value=retained)
        assert result.body.encoded_byte_count == ByteCount(value=retained)
        assert (
            b"".join([chunk async for chunk in store.open_response(result.body.handle)])
            == b"ab"[:retained]
        )
        await store.close()

    asyncio.run(scenario())


def test_gzip_capture_enforces_entity_and_expansion_budgets(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        entity = b"a" * 1_000
        encoded = gzip.compress(entity, mtime=0)
        ratio_result, _marks = await _capture(
            store,
            _limits(entity=2_000, ratio=2),
            _response(encoded),
            exchange=720,
            encodings=(ContentCoding(value="gzip"),),
        )
        assert type(ratio_result.body.capture) is BudgetExceededCapture
        assert ratio_result.body.capture.budget is CaptureBudgetKind.EXPANSION_RATIO
        assert ratio_result.body.handle is not None
        assert ratio_result.body.handle.byte_count == ByteCount(value=1_000)

        entity_result, _marks = await _capture(
            store,
            _limits(entity=10, ratio=100),
            _response(encoded),
            exchange=721,
            encodings=(ContentCoding(value="gzip"),),
        )
        assert type(entity_result.body.capture) is BudgetExceededCapture
        assert entity_result.body.capture.budget is CaptureBudgetKind.ENTITY_BYTES
        assert entity_result.body.handle is not None
        assert entity_result.body.handle.byte_count == ByteCount(value=10)

        empty_entity, _marks = await _capture(
            store,
            _limits(entity=0, ratio=100),
            _response(encoded),
            exchange=722,
            encodings=(ContentCoding(value="gzip"),),
        )
        assert type(empty_entity.body.capture) is BudgetExceededCapture
        assert empty_entity.body.capture.budget is CaptureBudgetKind.ENTITY_BYTES
        assert empty_entity.body.handle is not None
        assert empty_entity.body.handle.byte_count == ByteCount(value=0)

        nested = gzip.compress(gzip.compress(b"nested", mtime=0), mtime=0)
        nested_result, _marks = await _capture(
            store,
            _limits(),
            _response(nested),
            exchange=723,
            encodings=(ContentCoding(value="gzip"), ContentCoding(value="gzip")),
        )
        assert type(nested_result.body.capture) is CompleteCapture
        assert nested_result.body.handle is not None
        assert (
            b"".join([chunk async for chunk in store.open_response(nested_result.body.handle)])
            == b"nested"
        )
        await store.close()

    asyncio.run(scenario())


def test_decoder_flush_output_is_subject_to_entity_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlushDecoder:
        identity = False
        complete = True

        def __init__(self, encodings: tuple[ContentCoding, ...]) -> None:
            del encodings

        def feed(self, content: bytes) -> bytes:
            del content
            return b""

        def finish(self) -> bytes:
            return b"flush-output"

    async def scenario() -> None:
        store = _store(tmp_path)
        monkeypatch.setattr(capture_module, "_DecoderChain", FlushDecoder)
        result, _marks = await _capture(
            store,
            _limits(entity=1),
            _response(),
            exchange=724,
        )
        assert type(result.body.capture) is BudgetExceededCapture
        assert result.body.capture.budget is CaptureBudgetKind.ENTITY_BYTES
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "capture_type", "phase"),
    [
        (
            httpcore.ReadTimeout("idle"),
            BodyReadFailedCapture,
            FailurePhase.IDLE_TIMEOUT,
        ),
        (
            httpcore.ReadError("read"),
            BodyReadFailedCapture,
            FailurePhase.BODY_READ,
        ),
        (
            OSError(),
            BodyReadFailedCapture,
            FailurePhase.BODY_READ,
        ),
        (
            httpcore.RemoteProtocolError("truncated"),
            RemoteTruncatedCapture,
            FailurePhase.PROTOCOL,
        ),
        (
            asyncio.CancelledError(),
            CancelledCapture,
            FailurePhase.CANCELLATION,
        ),
    ],
)
def test_capture_retains_prefix_for_transport_terminal_states(
    tmp_path: Path,
    failure: BaseException,
    capture_type: type[object],
    phase: FailurePhase,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        result, _marks = await _capture(
            store,
            _limits(),
            _response(b"prefix", failure=failure),
            exchange={
                FailurePhase.IDLE_TIMEOUT: 730,
                FailurePhase.BODY_READ: 731 if type(failure) is httpcore.ReadError else 732,
                FailurePhase.PROTOCOL: 733,
                FailurePhase.CANCELLATION: 734,
            }[phase],
        )
        assert type(result.body.capture) is capture_type
        assert result.failure is not None
        assert result.failure.phase is phase
        assert result.body.handle is not None
        assert (
            b"".join([chunk async for chunk in store.open_response(result.body.handle)])
            == b"prefix"
        )
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("content", "encodings", "valid"),
    [
        (b"invalid", (ContentCoding(value="gzip"),), True),
        (gzip.compress(b"incomplete", mtime=0)[:-4], (ContentCoding(value="gzip"),), True),
        (b"not-read", (ContentCoding(value="br"),), True),
        (b"not-read", (), False),
    ],
)
def test_invalid_incomplete_or_unsupported_content_coding_is_explicit(
    tmp_path: Path,
    content: bytes,
    encodings: tuple[ContentCoding, ...],
    valid: bool,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        result, _marks = await _capture(
            store,
            _limits(),
            _response(content),
            exchange=740 + len(content),
            encodings=encodings,
            encoding_valid=valid,
        )
        assert type(result.body.capture) is ContentDecodingFailedCapture
        assert result.failure is not None
        assert result.failure.phase is FailurePhase.CONTENT_DECODING
        assert result.body.handle is not None
        await store.close()

    asyncio.run(scenario())


def test_storage_failure_and_capturer_boundaries_are_value_free(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _store(tmp_path, maximum=1)
        result, _marks = await _capture(
            store,
            _limits(),
            _response(b"too large"),
            exchange=750,
        )
        assert type(result.body.capture) is StorageFailedCapture
        assert result.body.handle is None
        assert result.failure is not None
        assert result.failure.phase is FailurePhase.STORAGE
        assert store.reserved_bytes.value == 0
        await store.close()

    asyncio.run(scenario())

    limits = _limits()
    store = _store(tmp_path)
    budgets = DefaultCaptureBudgetProvider(limits, default_budget_origin())
    with pytest.raises(TypeError, match="TransientBodyStore"):
        ResponseBodyCapturer(
            store=cast("TransientBodyStore", object()),
            limits=limits,
            budgets=budgets,
        )
    with pytest.raises(TypeError, match="ResponseCaptureLimits"):
        ResponseBodyCapturer(
            store=store,
            limits=cast("ResponseCaptureLimits", object()),
            budgets=budgets,
        )
    with pytest.raises(TypeError, match="CaptureBudgetProvider"):
        ResponseBodyCapturer(
            store=store,
            limits=limits,
            budgets=cast("CaptureBudgetProvider", object()),
        )

    async def invalid_capture_inputs() -> None:
        capturer = ResponseBodyCapturer(store=store, limits=limits, budgets=budgets)
        outbound = authorized_request(request())
        with pytest.raises(TypeError, match=r"httpcore\.Response"):
            await capturer.capture(
                cast("httpcore.Response", object()),
                request=outbound,
                media=MissingMediaType(),
                encodings=(),
                encoding_valid=True,
                sensitivity=Sensitivity.PUBLIC,
                origins=(),
                first_byte=lambda: None,
            )
        with pytest.raises(TypeError, match="AuthorizedExchangeRequest"):
            await capturer.capture(
                _response(),
                request=cast("AuthorizedExchangeRequest", object()),
                media=MissingMediaType(),
                encodings=(),
                encoding_valid=True,
                sensitivity=Sensitivity.PUBLIC,
                origins=(),
                first_byte=lambda: None,
            )

    asyncio.run(invalid_capture_inputs())
    asyncio.run(store.close())


def test_capture_limit_and_budget_provider_validation() -> None:
    with pytest.raises(TypeError, match="encoded"):
        ResponseCaptureLimits(
            maximum_encoded_body_bytes=cast("ByteCount", object()),
            maximum_entity_body_bytes=ByteCount(value=1),
            maximum_expansion_ratio=Ratio(numerator=1, denominator=PositiveCount(value=1)),
        )
    with pytest.raises(TypeError, match="entity"):
        ResponseCaptureLimits(
            maximum_encoded_body_bytes=ByteCount(value=1),
            maximum_entity_body_bytes=cast("ByteCount", object()),
            maximum_expansion_ratio=Ratio(numerator=1, denominator=PositiveCount(value=1)),
        )
    with pytest.raises(TypeError, match="expansion"):
        ResponseCaptureLimits(
            maximum_encoded_body_bytes=ByteCount(value=1),
            maximum_entity_body_bytes=ByteCount(value=1),
            maximum_expansion_ratio=cast("Ratio", object()),
        )
    with pytest.raises(TypeError, match="ResponseCaptureLimits"):
        DefaultCaptureBudgetProvider(
            cast("ResponseCaptureLimits", object()),
            default_budget_origin(),
        )
    with pytest.raises(TypeError, match="DefaultBudgetOrigin"):
        DefaultCaptureBudgetProvider(
            _limits(),
            cast("DefaultBudgetOrigin", object()),
        )

    provider = DefaultCaptureBudgetProvider(_limits(), default_budget_origin())
    outbound = authorized_request(request())
    body_id = BodyId(value=outbound.occurrence.exchange_id.value)
    with pytest.raises(TypeError, match="authorized request"):
        provider.exceeded(
            resource=BudgetResource.ENCODED_BODY_BYTES,
            request=cast("AuthorizedExchangeRequest", object()),
            body_id=body_id,
            attempted=ByteCount(value=4_097),
        )
    with pytest.raises(TypeError, match="authorized request"):
        provider.exceeded(
            resource=BudgetResource.ENCODED_BODY_BYTES,
            request=outbound,
            body_id=cast("BodyId", object()),
            attempted=ByteCount(value=4_097),
        )
    with pytest.raises(ValueError, match="unsupported"):
        provider.exceeded(
            resource=BudgetResource.REQUEST_BYTES,
            request=outbound,
            body_id=BodyId(value=outbound.occurrence.exchange_id.value),
            attempted=ByteCount(value=1),
        )
    with pytest.raises(TypeError, match="wrong unit"):
        provider.exceeded(
            resource=BudgetResource.ENCODED_BODY_BYTES,
            request=outbound,
            body_id=BodyId(value=outbound.occurrence.exchange_id.value),
            attempted=Ratio(numerator=2, denominator=PositiveCount(value=1)),
        )
    assert _ratio(0, 0) == Ratio(numerator=0, denominator=PositiveCount(value=1))
    with pytest.raises(TypeError, match="only byte and ratio"):
        _zero_like(Count(value=1))
