"""Deterministic request-body materialization and secure transient-store tests."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.http.materialization import BodyMaterializationError
from comparo.adapters.http.materialization import BodyMaterializationFailureCode
from comparo.adapters.http.materialization import BodyMaterializationLimits
from comparo.adapters.http.materialization import RequestBodyMaterializer
from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import BodyStoreFailureCode
from comparo.adapters.http.storage import StoredBodyStream
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import FormField
from comparo.core.domain.http.bodies import FormName
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartDispositionName
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
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
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from tests.core.domain.http.factories import provenance

_ORIGIN = TaintOriginId(value="test/body-secret")


def _body_id(value: int) -> BodyId:
    return BodyId(value=UUID(int=value))


def _config(
    root: Path,
    *,
    memory: int | None = None,
    maximum: int = 4_096,
) -> TransientBodyStoreConfig:
    return TransientBodyStoreConfig(
        memory_threshold=ByteCount(value=min(16, maximum) if memory is None else memory),
        maximum_transient_bytes=ByteCount(value=maximum),
        io_chunk_size=PositiveCount(value=3),
        root=root,
    )


def _limits(*, body: int = 2_048, depth: int = 16, nodes: int = 128) -> BodyMaterializationLimits:
    return BodyMaterializationLimits(
        maximum_body_bytes=ByteCount(value=body),
        maximum_json_depth=PositiveCount(value=depth),
        maximum_json_nodes=PositiveCount(value=nodes),
    )


async def _read(stream: StoredBodyStream) -> bytes:
    return b"".join([chunk async for chunk in stream])


def test_materializer_serializes_every_inline_body_shape_deterministically(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        materializer = RequestBodyMaterializer(store, _limits())
        json_value = JsonObject(
            members=(
                JsonObjectMember(name="null", value=JsonNull()),
                JsonObjectMember(name="bool", value=JsonBoolean(value=True)),
                JsonObjectMember(name="int", value=JsonInteger(value=-12)),
                JsonObjectMember(name="decimal", value=JsonDecimal(value=Decimal("1.25"))),
                JsonObjectMember(
                    name="array",
                    value=JsonArray(items=(JsonString(value="é"), JsonBoolean(value=False))),
                ),
            )
        )
        bodies: tuple[tuple[RequestBody, bytes], ...] = (
            (
                JsonBody(value=json_value),
                b'{"null":null,"bool":true,"int":-12,"decimal":1.25,"array":["\xc3\xa9",false]}',
            ),
            (TextBody(value=BodyText(value="héllo")), "héllo".encode()),
            (BytesBody(value=BytesValue(value=b"\x00bytes")), b"\x00bytes"),
            (
                FormBody(
                    fields=(
                        FormField(name=FormName(value="q"), value=BodyText(value="a b")),
                        FormField(name=FormName(value="q"), value=BodyText(value="é&")),
                    )
                ),
                b"q=a+b&q=%C3%A9%26",
            ),
        )
        for index, (body, expected) in enumerate(bodies, start=1):
            result = await materializer.materialize(
                body,
                body_id=_body_id(index),
                provenance=provenance(),
            )
            assert type(result) is MaterializedBody
            assert result.source.byte_count.value == len(expected)
            assert await _read(store.open_request(result.source)) == expected
        no_body = await materializer.materialize(
            NoBody(),
            body_id=_body_id(20),
            provenance=provenance(),
        )
        assert type(no_body) is NoMaterializedBody
        await store.close()

    asyncio.run(scenario())


def test_sensitive_body_remains_tainted_and_spills_without_changing_bytes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=4))
        materializer = RequestBodyMaterializer(store, _limits())
        body = TextBody(value=SensitiveValue(BodyText(value="secret-value"), origins=(_ORIGIN,)))
        result = await materializer.materialize(
            body,
            body_id=_body_id(30),
            provenance=provenance(),
        )
        assert type(result) is MaterializedBody
        assert result.source.sensitivity is Sensitivity.SENSITIVE
        assert is_sensitive_value(result.source.digest)
        assert result.source.digest.origins == (_ORIGIN,)
        assert store.memory_bytes.value == 0
        assert store.spool_file_count == 1
        assert await _read(store.open_request(result.source)) == b"secret-value"
        with pytest.raises(TypeError) as rendering:
            repr(result)
        assert "secret-value" not in str(rendering.value)
        await store.close()

    asyncio.run(scenario())


def test_existing_repeatable_source_can_be_snapshotted_again_for_retry_safe_use(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=2))
        source_writer = await store.begin(
            _body_id(40),
            maximum_body_bytes=ByteCount(value=32),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with source_writer:
            await source_writer.append(b"repeatable")
            source = await source_writer.commit_request(provenance())
        materializer = RequestBodyMaterializer(store, _limits())
        result = await materializer.materialize(
            BytesBody(value=source),
            body_id=_body_id(41),
            provenance=provenance(),
        )
        assert type(result) is MaterializedBody
        assert result.source.source_id != source.source_id
        first, second = await asyncio.gather(
            _read(store.open_request(result.source)),
            _read(store.open_request(result.source)),
        )
        assert first == second == b"repeatable"
        await store.close()

    asyncio.run(scenario())


def test_multipart_preserves_order_types_filename_and_boundary_proof(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=8))
        materializer = RequestBodyMaterializer(store, _limits())
        body = MultipartBody(
            boundary=MultipartBoundary(value="comparo-boundary"),
            parts=(
                MultipartTextPart(
                    name=MultipartDispositionName(value="note"),
                    value=BodyText(value="hello"),
                    media_type=MediaType(type="text", subtype="plain"),
                ),
                MultipartFilePart(
                    name=MultipartDispositionName(value="upload"),
                    filename=SensitiveValue(
                        MultipartDispositionFilename(value="café.txt"), origins=(_ORIGIN,)
                    ),
                    value=SensitiveValue(BytesValue(value=b"file-data"), origins=(_ORIGIN,)),
                    media_type=MediaType(type="application", subtype="octet-stream"),
                ),
            ),
        )
        result = await materializer.materialize(
            body,
            body_id=_body_id(50),
            provenance=provenance(),
        )
        assert type(result) is MaterializedBody
        assert result.multipart is not None
        assert result.multipart.source_id == result.source.source_id
        encoded = await _read(store.open_request(result.source))
        assert encoded.startswith(b"--comparo-boundary\r\n")
        assert b'name="note"' in encoded
        assert b"Content-Type: text/plain" in encoded
        assert b"filename*=UTF-8''caf%C3%A9.txt" in encoded
        assert encoded.endswith(b"--comparo-boundary--\r\n")
        await store.close()

    asyncio.run(scenario())


def test_multipart_collision_is_value_free_and_releases_all_storage(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        materializer = RequestBodyMaterializer(store, _limits())
        body = MultipartBody(
            boundary=MultipartBoundary(value="boundary"),
            parts=(
                MultipartFilePart(
                    name=MultipartDispositionName(value="upload"),
                    filename=MultipartDispositionFilename(value="sample.bin"),
                    value=BytesValue(value=b"prefix-boundary-suffix"),
                    media_type=MediaType(type="application", subtype="octet-stream"),
                ),
            ),
        )
        with pytest.raises(BodyMaterializationError) as captured:
            await materializer.materialize(
                body,
                body_id=_body_id(60),
                provenance=provenance(),
            )
        assert captured.value.code is BodyMaterializationFailureCode.MULTIPART_BOUNDARY_COLLISION
        assert store.reserved_bytes.value == 0
        await store.close()

    asyncio.run(scenario())


def test_json_and_storage_limits_fail_closed_without_retaining_partial_bytes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, maximum=8))
        shallow = RequestBodyMaterializer(store, _limits(depth=1))
        nested = JsonBody(value=JsonArray(items=(JsonArray(items=(JsonNull(),)),)))
        with pytest.raises(BodyMaterializationError) as json_failure:
            await shallow.materialize(
                nested,
                body_id=_body_id(70),
                provenance=provenance(),
            )
        assert json_failure.value.code is BodyMaterializationFailureCode.JSON_LIMIT_EXCEEDED

        small = RequestBodyMaterializer(store, _limits(body=3))
        with pytest.raises(BodyMaterializationError) as body_failure:
            await small.materialize(
                BytesBody(value=BytesValue(value=b"four")),
                body_id=_body_id(71),
                provenance=provenance(),
            )
        assert body_failure.value.code is BodyMaterializationFailureCode.STORE_FAILED
        assert body_failure.value.store_code is BodyStoreFailureCode.BODY_LIMIT_EXCEEDED

        allowed = RequestBodyMaterializer(store, _limits(body=8))
        first = await allowed.materialize(
            BytesBody(value=BytesValue(value=b"12345678")),
            body_id=_body_id(72),
            provenance=provenance(),
        )
        assert type(first) is MaterializedBody
        with pytest.raises(BodyMaterializationError) as aggregate_failure:
            await allowed.materialize(
                BytesBody(value=BytesValue(value=b"x")),
                body_id=_body_id(73),
                provenance=provenance(),
            )
        assert aggregate_failure.value.store_code is BodyStoreFailureCode.RUN_LIMIT_EXCEEDED
        await store.close()

    asyncio.run(scenario())


def test_response_handles_are_opaque_exact_and_store_close_waits_for_readers(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        writer = await store.begin(
            _body_id(80),
            maximum_body_bytes=ByteCount(value=16),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"abcdef")
            handle = await writer.commit_response(
                media=MissingMediaType(),
                extent=BodyCaptureExtent.COMPLETE,
            )
        assert await _read(store.open_response(handle)) == b"abcdef"
        assert "comparo-body" not in repr(handle)

        stream = store.open_response(handle)
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_reader() -> bytes:
            chunks: list[bytes] = []
            async for chunk in stream:
                chunks.append(chunk)
                started.set()
                await release.wait()
            return b"".join(chunks)

        read_task = asyncio.create_task(slow_reader())
        await started.wait()
        close_task = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not close_task.done()
        release.set()
        assert await read_task == b"abcdef"
        await close_task
        await store.close()
        with pytest.raises(BodyStoreError) as closed:
            store.open_response(handle)
        assert closed.value.code is BodyStoreFailureCode.CLOSED

    asyncio.run(scenario())


def test_store_rejects_duplicate_unknown_and_tampered_descriptors(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        writer = await store.begin(
            _body_id(90),
            maximum_body_bytes=ByteCount(value=10),
            sensitivity=Sensitivity.PUBLIC,
        )
        with pytest.raises(BodyStoreError) as duplicate:
            await store.begin(
                _body_id(90),
                maximum_body_bytes=ByteCount(value=10),
                sensitivity=Sensitivity.PUBLIC,
            )
        assert duplicate.value.code is BodyStoreFailureCode.DUPLICATE_BODY
        async with writer:
            await writer.append(b"abc")
            source = await writer.commit_request(provenance())
        tampered = msgspec.structs.replace(source, byte_count=ByteCount(value=2))
        with pytest.raises(BodyStoreError) as mismatch:
            store.open_request(tampered)
        assert mismatch.value.code is BodyStoreFailureCode.DESCRIPTOR_MISMATCH
        unknown = msgspec.structs.replace(source, source_id=_body_id(91))
        with pytest.raises(BodyStoreError) as missing:
            store.open_request(unknown)
        assert missing.value.code is BodyStoreFailureCode.UNKNOWN_BODY
        await store.close()

    asyncio.run(scenario())


def test_adapter_contracts_reject_untyped_operational_inputs(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="memory threshold"):
        TransientBodyStoreConfig(
            memory_threshold=1,  # type: ignore[arg-type]
            maximum_transient_bytes=ByteCount(value=1),
            io_chunk_size=PositiveCount(value=1),
        )
    with pytest.raises(ValueError, match="cannot exceed"):
        _config(tmp_path, memory=2, maximum=1)
    with pytest.raises(ValueError, match="absolute"):
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=1),
            maximum_transient_bytes=ByteCount(value=1),
            io_chunk_size=PositiveCount(value=1),
            root=Path("relative"),
        )
    with pytest.raises(TypeError, match="materialization body limit"):
        BodyMaterializationLimits(
            maximum_body_bytes=1,  # type: ignore[arg-type]
            maximum_json_depth=PositiveCount(value=1),
            maximum_json_nodes=PositiveCount(value=1),
        )
    with pytest.raises(TypeError, match="failure code"):
        BodyStoreError(cast("BodyStoreFailureCode", object()))
    with pytest.raises(ValueError, match="require one store code"):
        BodyMaterializationError(BodyMaterializationFailureCode.STORE_FAILED)


def test_writer_abort_and_invalid_chunk_release_identity_and_reservation(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        writer = await store.begin(
            _body_id(100),
            maximum_body_bytes=ByteCount(value=10),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"abc")
            with pytest.raises(TypeError, match="chunks"):
                await writer.append("bad")  # type: ignore[arg-type]
        assert store.reserved_bytes.value == 0
        replacement = await store.begin(
            _body_id(100),
            maximum_body_bytes=ByteCount(value=10),
            sensitivity=Sensitivity.PUBLIC,
        )
        await replacement.abort()
        await replacement.abort()
        await store.close()

    asyncio.run(scenario())
