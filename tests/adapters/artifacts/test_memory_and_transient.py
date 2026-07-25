"""Boundary and lifecycle tests for sanitized in-memory and transient blob adapters."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from comparo.adapters.artifacts.memory import InMemorySanitizedBlobStore
from comparo.adapters.artifacts.memory import SanitizedBlobStoreError
from comparo.adapters.artifacts.transient import TransientArtifactBodyReader
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import Sensitivity
from tests.core.domain.http.factories import provenance


def _limits(*, blobs: int = 2, per_blob: int = 8, total: int = 12) -> ArtifactLimits:
    return ArtifactLimits(
        maximum_blobs=PositiveCount(value=blobs),
        maximum_blob_bytes=ByteCount(value=per_blob),
        maximum_total_blob_bytes=ByteCount(value=total),
        maximum_manifest_bytes=ByteCount(value=32_768),
        maximum_report_bytes=ByteCount(value=32_768),
    )


async def _read(chunks: object) -> bytes:
    return b"".join([chunk async for chunk in chunks])  # type: ignore[attr-defined]


def test_memory_store_validates_boundaries_and_closed_lifecycle() -> None:
    with pytest.raises(TypeError, match="error code"):
        SanitizedBlobStoreError(cast("ArtifactStoreErrorCode", object()))
    with pytest.raises(TypeError, match="requires ArtifactLimits"):
        InMemorySanitizedBlobStore(cast("ArtifactLimits", object()))

    async def scenario() -> None:
        store = InMemorySanitizedBlobStore(_limits())
        with pytest.raises(TypeError, match="reads require ArtifactBlob"):
            store.read(cast("ArtifactBlob", object()))

        writer = await store.begin()
        with pytest.raises(TypeError, match="chunks must be bytes"):
            await writer.write(cast("bytes", bytearray(b"mutable")))
        await writer.write(b"safe")
        blob = await writer.commit(media_type="text/plain")
        assert await _read(store.read(blob)) == b"safe"
        for operation in (
            lambda: writer.write(b"late"),
            lambda: writer.commit(media_type=None),
        ):
            with pytest.raises(SanitizedBlobStoreError) as failure:
                await operation()
            assert failure.value.code is ArtifactStoreErrorCode.CLOSED

        store.close()
        store.close()
        with pytest.raises(SanitizedBlobStoreError) as begin:
            await store.begin()
        assert begin.value.code is ArtifactStoreErrorCode.CLOSED
        with pytest.raises(SanitizedBlobStoreError) as read:
            await _read(store.read(blob))
        assert read.value.code is ArtifactStoreErrorCode.CLOSED

    asyncio.run(scenario())


def test_memory_writer_abort_limits_deduplication_and_mismatch_paths() -> None:
    async def scenario() -> None:
        store = InMemorySanitizedBlobStore(_limits(blobs=2, per_blob=4, total=5))
        oversized = await store.begin()
        await oversized.write(b"1234")
        with pytest.raises(SanitizedBlobStoreError) as per_blob:
            await oversized.write(b"5")
        assert per_blob.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED
        await oversized.abort()
        await oversized.abort()
        with pytest.raises(SanitizedBlobStoreError) as aborted:
            await oversized.commit(media_type=None)
        assert aborted.value.code is ArtifactStoreErrorCode.CLOSED

        first = await store.begin()
        await first.write(b"abc")
        first_blob = await first.commit(media_type="text/plain")
        duplicate = await store.begin()
        await duplicate.write(b"abc")
        duplicate_blob = await duplicate.commit(media_type="application/octet-stream")
        assert duplicate_blob.sha256 == first_blob.sha256
        assert duplicate_blob.media_type == "application/octet-stream"
        assert store._total_bytes == 3

        aggregate = await store.begin()
        await aggregate.write(b"def")
        with pytest.raises(SanitizedBlobStoreError) as aggregate_failure:
            await aggregate.commit(media_type=None)
        assert aggregate_failure.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED

        missing = ArtifactBlob(sha256="0" * 64, byte_count=1)
        with pytest.raises(SanitizedBlobStoreError) as unknown:
            await _read(store.read(missing))
        assert unknown.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH

        wrong_size = ArtifactBlob(sha256=first_blob.sha256, byte_count=2)
        with pytest.raises(SanitizedBlobStoreError) as size:
            await _read(store.read(wrong_size))
        assert size.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH

        store._blobs[first_blob.sha256] = b"collision"
        with pytest.raises(SanitizedBlobStoreError) as collision:
            store._commit(b"abc", None)
        assert collision.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH

        store.close()
        closed_writer_store = InMemorySanitizedBlobStore(_limits())
        pending = await closed_writer_store.begin()
        closed_writer_store.close()
        with pytest.raises(SanitizedBlobStoreError) as closed_writer:
            await pending.write(b"x")
        assert closed_writer.value.code is ArtifactStoreErrorCode.CLOSED

    asyncio.run(scenario())


def test_memory_store_enforces_blob_count_independently() -> None:
    async def scenario() -> None:
        store = InMemorySanitizedBlobStore(_limits(blobs=1, per_blob=8, total=8))
        first = await store.begin()
        await first.write(b"a")
        await first.commit(media_type=None)
        second = await store.begin()
        await second.write(b"b")
        with pytest.raises(SanitizedBlobStoreError) as failure:
            await second.commit(media_type=None)
        assert failure.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED
        store.close()

    asyncio.run(scenario())


def test_transient_body_reader_dispatches_both_exact_source_types(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(
            TransientBodyStoreConfig(
                memory_threshold=ByteCount(value=8),
                maximum_transient_bytes=ByteCount(value=64),
                io_chunk_size=PositiveCount(value=2),
                root=tmp_path,
            )
        )
        reader = TransientArtifactBodyReader(store)
        request_writer = await store.begin(
            BodyId(value=UUID(int=1)),
            maximum_body_bytes=ByteCount(value=16),
            sensitivity=Sensitivity.PUBLIC,
        )
        await request_writer.append(b"request")
        request = await request_writer.commit_request(provenance())
        response_writer = await store.begin(
            BodyId(value=UUID(int=2)),
            maximum_body_bytes=ByteCount(value=16),
            sensitivity=Sensitivity.PUBLIC,
        )
        await response_writer.append(b"response")
        response = await response_writer.commit_response(
            media=MissingMediaType(),
            extent=BodyCaptureExtent.COMPLETE,
        )

        assert await _read(reader.read(request)) == b"request"
        assert await _read(reader.read(response)) == b"response"
        with pytest.raises(TypeError, match="request source or response handle"):
            reader.read(cast("RepeatableByteSource | BodyHandle", object()))
        await store.close()

    with pytest.raises(TypeError, match="requires TransientBodyStore"):
        TransientArtifactBodyReader(cast("TransientBodyStore", object()))
    asyncio.run(scenario())


def test_memory_empty_blob_digest_is_canonical() -> None:
    async def scenario() -> None:
        store = InMemorySanitizedBlobStore(_limits())
        writer = await store.begin()
        blob = await writer.commit(media_type=None)
        assert blob.sha256 == sha256(b"").hexdigest()
        assert await _read(store.read(blob)) == b""
        store.close()

    asyncio.run(scenario())
