"""Bounded in-memory sanitized blob store for composition tests and ephemeral runs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from hashlib import sha256
from typing import final

from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.ports import ArtifactStoreErrorCode


class SanitizedBlobStoreError(RuntimeError):
    """A value-free sanitized blob staging failure."""

    __slots__ = ("code",)

    def __init__(self, code: ArtifactStoreErrorCode) -> None:
        """Retain only a closed failure code."""
        if type(code) is not ArtifactStoreErrorCode:
            raise TypeError("sanitized blob store error code must be typed")
        self.code = code
        super().__init__(code.value)


class InMemorySanitizedBlobStore:
    """Store already-sanitized blobs with exact caller-owned aggregate budgets."""

    __slots__ = ("_blobs", "_closed", "_limits", "_total_bytes")

    def __init__(self, limits: ArtifactLimits) -> None:
        """Create an empty bounded store for one artifact compilation."""
        if type(limits) is not ArtifactLimits:
            raise TypeError("sanitized blob store requires ArtifactLimits")
        self._limits = limits
        self._blobs: dict[str, bytes] = {}
        self._total_bytes = 0
        self._closed = False

    async def begin(self) -> _MemoryBlobWriter:
        """Create a private writer that publishes only on commit."""
        if self._closed:
            raise SanitizedBlobStoreError(ArtifactStoreErrorCode.CLOSED)
        return _MemoryBlobWriter(self)

    def read(self, blob: ArtifactBlob) -> AsyncIterator[bytes]:
        """Read one exact declared sanitized blob in a single in-memory chunk."""
        if type(blob) is not ArtifactBlob:
            raise TypeError("sanitized blob reads require ArtifactBlob")

        async def chunks() -> AsyncIterator[bytes]:
            if self._closed:
                raise SanitizedBlobStoreError(ArtifactStoreErrorCode.CLOSED)
            value = self._blobs.get(blob.sha256)
            if value is None or len(value) != blob.byte_count:
                raise SanitizedBlobStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
            yield value

        return chunks()

    def close(self) -> None:
        """Wipe safe ephemeral buffers and reject later operations."""
        if self._closed:
            return
        self._blobs.clear()
        self._total_bytes = 0
        self._closed = True

    def _commit(self, value: bytes, media_type: str | None) -> ArtifactBlob:
        digest = sha256(value).hexdigest()
        existing = self._blobs.get(digest)
        if existing is not None:
            if existing != value:
                raise SanitizedBlobStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
            return ArtifactBlob(sha256=digest, byte_count=len(value), media_type=media_type)
        if len(self._blobs) >= self._limits.maximum_blobs.value:
            raise SanitizedBlobStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        if self._total_bytes + len(value) > self._limits.maximum_total_blob_bytes.value:
            raise SanitizedBlobStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        self._blobs[digest] = value
        self._total_bytes += len(value)
        return ArtifactBlob(sha256=digest, byte_count=len(value), media_type=media_type)


@final
class _MemoryBlobWriter:
    """One non-reusable in-memory sanitized blob transaction."""

    __slots__ = ("_buffer", "_finished", "_store")

    def __init__(self, store: InMemorySanitizedBlobStore) -> None:
        self._store = store
        self._buffer = bytearray()
        self._finished = False

    async def write(self, chunk: bytes) -> None:
        """Append a safe exact chunk without crossing the per-blob limit."""
        self._require_active()
        if type(chunk) is not bytes:
            raise TypeError("sanitized blob chunks must be bytes")
        if len(self._buffer) + len(chunk) > self._store._limits.maximum_blob_bytes.value:
            raise SanitizedBlobStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        self._buffer.extend(chunk)

    async def commit(self, *, media_type: str | None) -> ArtifactBlob:
        """Publish the complete sanitized bytes exactly once."""
        self._require_active()
        self._finished = True
        value = bytes(self._buffer)
        self._buffer[:] = b"\x00" * len(self._buffer)
        self._buffer.clear()
        return self._store._commit(value, media_type)

    async def abort(self) -> None:
        """Wipe staged bytes and make future writer calls fail closed."""
        if self._finished:
            return
        self._buffer[:] = b"\x00" * len(self._buffer)
        self._buffer.clear()
        self._finished = True

    def _require_active(self) -> None:
        if self._finished or self._store._closed:
            raise SanitizedBlobStoreError(ArtifactStoreErrorCode.CLOSED)
