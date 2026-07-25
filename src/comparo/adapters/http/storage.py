"""Bounded secure transient storage for repeatable request and captured response bytes."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from typing import final
from uuid import uuid4

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.bodies import BodyDigest
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import BodyStorageToken
from comparo.core.domain.http.response_bodies import MediaTypeMetadata
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId

_LOCATOR_ACCESS = SensitiveValueAccess(purpose="http.body-store.locator")
_LOCATOR_ORIGIN = TaintOriginId(value="runtime/body-store-locator")


class BodyStoreFailureCode(DomainStrEnum):
    """Stable value-free transient-store failures."""

    CLOSED = "closed"
    DUPLICATE_BODY = "duplicate-body"
    BODY_LIMIT_EXCEEDED = "body-limit-exceeded"
    RUN_LIMIT_EXCEEDED = "run-limit-exceeded"
    UNKNOWN_BODY = "unknown-body"
    DESCRIPTOR_MISMATCH = "descriptor-mismatch"
    IO_FAILED = "io-failed"


@final
class BodyStoreError(Exception):
    """A value-free transient-store failure safe to map into runtime diagnostics."""

    __slots__ = ("code",)

    def __init__(self, code: BodyStoreFailureCode) -> None:
        """Retain only a closed failure code, never a path or payload."""
        if type(code) is not BodyStoreFailureCode:
            raise TypeError("body store failure code must be BodyStoreFailureCode")
        self.code = code
        super().__init__(code.value)


@final
class TransientBodyStoreConfig(msgspec.Struct, frozen=True, kw_only=True):
    """Operator-supplied placement and aggregate limits for one run-local store."""

    memory_threshold: ByteCount
    maximum_transient_bytes: ByteCount
    io_chunk_size: PositiveCount
    root: Path | None = None

    def __post_init__(self) -> None:
        """Require finite limits and an optional absolute existing directory."""
        if type(self.memory_threshold) is not ByteCount:
            raise TypeError("body-store memory threshold must be ByteCount")
        if type(self.maximum_transient_bytes) is not ByteCount:
            raise TypeError("body-store maximum bytes must be ByteCount")
        if self.memory_threshold.value > self.maximum_transient_bytes.value:
            raise ValueError("body-store memory threshold cannot exceed its run limit")
        if type(self.io_chunk_size) is not PositiveCount:
            raise TypeError("body-store I/O chunk size must be PositiveCount")
        if self.root is not None:
            if not isinstance(self.root, Path):
                raise TypeError("body-store root must be pathlib.Path")
            if not self.root.is_absolute():
                raise ValueError("body-store root must be absolute")


@dataclass(slots=True)
class _StoredEntry:
    """One committed store-owned byte sequence."""

    body_id: BodyId
    byte_count: int
    digest: BodyDigest
    sensitivity: Sensitivity
    origins: tuple[TaintOriginId, ...]
    token: str
    memory: bytearray | None
    path: Path | None


class StoredBodyStream:
    """One independently positioned asynchronous reader over a committed store entry."""

    __slots__ = ("_entry", "_store")

    def __init__(self, store: TransientBodyStore, entry: _StoredEntry) -> None:
        """Bind the stream to a validated immutable entry snapshot."""
        self._store = store
        self._entry = entry

    def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield bounded chunks and release the active-reader reservation on every path."""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        await self._store._reader_started()
        try:
            if self._entry.memory is not None:
                size = self._store.config.io_chunk_size.value
                for offset in range(0, len(self._entry.memory), size):
                    yield bytes(self._entry.memory[offset : offset + size])
                return
            path = self._entry.path
            if path is None:  # pragma: no cover - committed-entry invariant
                raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)
            stream = await asyncio.to_thread(path.open, "rb", buffering=0)
            try:
                while chunk := await asyncio.to_thread(
                    stream.read, self._store.config.io_chunk_size.value
                ):
                    yield chunk
            finally:
                await asyncio.to_thread(stream.close)
        except BodyStoreError:
            raise
        except (OSError, ValueError) as error:
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED) from error
        finally:
            await self._store._reader_finished()


class TransientBodyWriter:
    """Single-use bounded writer that spills to a private file above its memory threshold."""

    __slots__ = (
        "_body_id",
        "_count",
        "_digest",
        "_done",
        "_file",
        "_maximum_body_bytes",
        "_memory",
        "_origins",
        "_path",
        "_sensitivity",
        "_store",
    )

    def __init__(
        self,
        store: TransientBodyStore,
        body_id: BodyId,
        *,
        maximum_body_bytes: ByteCount,
        sensitivity: Sensitivity,
        origins: tuple[TaintOriginId, ...],
    ) -> None:
        """Create an uncommitted writer after store-level identity admission."""
        self._store = store
        self._body_id = body_id
        self._maximum_body_bytes = maximum_body_bytes
        self._sensitivity = sensitivity
        self._origins = origins
        self._memory = bytearray()
        self._path: Path | None = None
        self._file: BinaryIO | None = None
        self._digest = hashlib.sha256()
        self._count = 0
        self._done = False

    async def __aenter__(self) -> TransientBodyWriter:
        """Return this already-admitted writer."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        """Abort incomplete writes, including cancellation and serializer failures."""
        del exc_type, exc_value, traceback
        if not self._done:
            await self.abort()

    @property
    def byte_count(self) -> ByteCount:
        """Return the exact bytes accepted so far."""
        return ByteCount(value=self._count)

    async def append(self, chunk: bytes) -> None:
        """Append one immutable chunk under both per-body and aggregate reservations."""
        if self._done:
            raise BodyStoreError(BodyStoreFailureCode.CLOSED)
        if type(chunk) is not bytes:
            raise TypeError("body-store chunks must be bytes")
        if not chunk:
            return
        attempted = self._count + len(chunk)
        if attempted > self._maximum_body_bytes.value:
            raise BodyStoreError(BodyStoreFailureCode.BODY_LIMIT_EXCEEDED)
        await self._store._reserve(len(chunk))
        try:
            if self._file is None and attempted > self._store.config.memory_threshold.value:
                await self._roll_to_disk()
            if self._file is None:
                self._memory.extend(chunk)
            else:
                await asyncio.to_thread(self._file.write, chunk)
            self._digest.update(chunk)
            self._count = attempted
        except BaseException:
            await self._store._release(len(chunk))
            raise

    async def commit_request(self, provenance: ValueProvenance) -> RepeatableByteSource:
        """Commit bytes and return a repeatable request-source descriptor."""
        if type(provenance) is not ValueProvenance:
            raise TypeError("request-body commit requires ValueProvenance")
        entry = await self._commit()
        return RepeatableByteSource(
            source_id=entry.body_id,
            byte_count=ByteCount(value=entry.byte_count),
            digest=entry.digest,
            provenance=provenance,
        )

    async def commit_response(
        self,
        *,
        media: MediaTypeMetadata,
        extent: BodyCaptureExtent,
    ) -> BodyHandle:
        """Commit bytes and return an opaque response-body handle."""
        if type(extent) is not BodyCaptureExtent:
            raise TypeError("response-body commit requires BodyCaptureExtent")
        entry = await self._commit()
        return BodyHandle(
            body_id=entry.body_id,
            locator=SensitiveValue(
                BodyStorageToken(value=entry.token),
                origins=(_LOCATOR_ORIGIN,),
            ),
            byte_count=ByteCount(value=entry.byte_count),
            digest=entry.digest,
            media=media,
            sensitivity=entry.sensitivity,
            extent=extent,
        )

    async def abort(self) -> None:
        """Release reservations and remove any incomplete spill file exactly once."""
        if self._done:
            return
        self._done = True
        failed = False
        try:
            await self._close_file()
        except BodyStoreError:
            failed = True
        _wipe(self._memory)
        self._memory = bytearray()
        if self._path is not None:
            try:
                await _unlink(self._path)
            except BodyStoreError:
                failed = True
        await self._store._discard(self._body_id, self._count)
        if failed:
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)

    async def _roll_to_disk(self) -> None:
        path = self._store._directory / f"{self._body_id.value.hex}.body"
        stream: BinaryIO | None = None
        try:
            stream = await asyncio.to_thread(path.open, "xb", buffering=0)
            await asyncio.to_thread(path.chmod, 0o600)
            if self._memory:
                await asyncio.to_thread(stream.write, self._memory)
        except (OSError, ValueError) as error:
            if stream is not None:
                with suppress(OSError):
                    await asyncio.to_thread(stream.close)
            with suppress(BodyStoreError):
                await _unlink(path)
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED) from error
        _wipe(self._memory)
        self._memory = bytearray()
        self._path = path
        self._file = stream

    async def _commit(self) -> _StoredEntry:
        if self._done:
            raise BodyStoreError(BodyStoreFailureCode.CLOSED)
        await self._close_file()
        public_digest = Sha256Digest(hex_value=self._digest.hexdigest())
        digest: BodyDigest = (
            SensitiveValue(public_digest, origins=self._origins)
            if self._sensitivity is Sensitivity.SENSITIVE
            else public_digest
        )
        entry = _StoredEntry(
            body_id=self._body_id,
            byte_count=self._count,
            digest=digest,
            sensitivity=self._sensitivity,
            origins=self._origins,
            token=f"{self._store._store_id}:{self._body_id.value.hex}",
            memory=self._memory if self._path is None else None,
            path=self._path,
        )
        await self._store._commit(entry)
        self._done = True
        return entry

    async def _close_file(self) -> None:
        if self._file is not None:
            stream = self._file
            self._file = None
            failed = False
            try:
                await asyncio.to_thread(stream.flush)
            except OSError:
                failed = True
            try:
                await asyncio.to_thread(stream.close)
            except OSError:
                failed = True
            if failed:
                raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)


class TransientBodyStore:
    """One run-local store with memory placement, secure spill, and aggregate accounting."""

    __slots__ = (
        "_active_readers",
        "_closed",
        "_closing",
        "_condition",
        "_directory",
        "_entries",
        "_pending",
        "_reserved",
        "_store_id",
        "config",
    )

    def __init__(self, config: TransientBodyStoreConfig) -> None:
        """Create a private mode-0700 spool directory without reading ambient configuration."""
        if type(config) is not TransientBodyStoreConfig:
            raise TypeError("transient body store requires TransientBodyStoreConfig")
        root = None
        if config.root is not None:
            try:
                root = config.root.resolve(strict=True)
            except OSError as error:
                raise BodyStoreError(BodyStoreFailureCode.IO_FAILED) from error
            if not root.is_dir():
                raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)
        try:
            directory = Path(tempfile.mkdtemp(prefix="comparo-body-", dir=root))
            directory.chmod(0o700)
        except OSError as error:
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED) from error
        self.config = config
        self._directory = directory
        self._store_id = uuid4().hex
        self._entries: dict[BodyId, _StoredEntry] = {}
        self._pending: set[BodyId] = set()
        self._reserved = 0
        self._active_readers = 0
        self._closing = False
        self._closed = False
        self._condition = asyncio.Condition()

    @property
    def reserved_bytes(self) -> ByteCount:
        """Return current aggregate committed and in-progress storage usage."""
        return ByteCount(value=self._reserved)

    @property
    def memory_bytes(self) -> ByteCount:
        """Return bytes currently retained in memory rather than secure spool files."""
        return ByteCount(
            value=sum(
                entry.byte_count for entry in self._entries.values() if entry.memory is not None
            )
        )

    @property
    def spool_file_count(self) -> int:
        """Return the number of committed disk-backed entries for operational evidence."""
        return sum(entry.path is not None for entry in self._entries.values())

    async def begin(
        self,
        body_id: BodyId,
        *,
        maximum_body_bytes: ByteCount,
        sensitivity: Sensitivity,
        origins: tuple[TaintOriginId, ...] = (),
    ) -> TransientBodyWriter:
        """Admit one unique writer with explicit per-body and sensitivity contracts."""
        if type(body_id) is not BodyId or type(maximum_body_bytes) is not ByteCount:
            raise TypeError("body-store writer requires BodyId and ByteCount")
        if type(sensitivity) is not Sensitivity:
            raise TypeError("body-store writer sensitivity must be Sensitivity")
        if type(origins) is not tuple or any(type(item) is not TaintOriginId for item in origins):
            raise TypeError("body-store origins must be immutable TaintOriginId values")
        if (sensitivity is Sensitivity.SENSITIVE) is (not origins):
            raise ValueError("body-store sensitivity and origins must agree")
        async with self._condition:
            if self._closing or self._closed:
                raise BodyStoreError(BodyStoreFailureCode.CLOSED)
            if body_id in self._entries or body_id in self._pending:
                raise BodyStoreError(BodyStoreFailureCode.DUPLICATE_BODY)
            self._pending.add(body_id)
        return TransientBodyWriter(
            self,
            body_id,
            maximum_body_bytes=maximum_body_bytes,
            sensitivity=sensitivity,
            origins=origins,
        )

    def open_request(self, source: RepeatableByteSource) -> StoredBodyStream:
        """Open one independently positioned stream after exact request-descriptor validation."""
        if type(source) is not RepeatableByteSource:
            raise TypeError("request body reader requires RepeatableByteSource")
        entry = self._entry(source.source_id)
        if (
            entry.byte_count != source.byte_count.value
            or entry.digest != source.digest
            or entry.sensitivity is not source.sensitivity
        ):
            raise BodyStoreError(BodyStoreFailureCode.DESCRIPTOR_MISMATCH)
        return StoredBodyStream(self, entry)

    def open_response(self, handle: BodyHandle) -> StoredBodyStream:
        """Open one response handle only when its opaque locator belongs to this store."""
        if type(handle) is not BodyHandle:
            raise TypeError("response body reader requires BodyHandle")
        entry = self._entry(handle.body_id)
        locator = handle.locator.reveal(access=_LOCATOR_ACCESS)
        if (
            locator.value != entry.token
            or handle.byte_count.value != entry.byte_count
            or handle.digest != entry.digest
            or handle.sensitivity is not entry.sensitivity
        ):
            raise BodyStoreError(BodyStoreFailureCode.DESCRIPTOR_MISMATCH)
        return StoredBodyStream(self, entry)

    async def close(self) -> None:
        """Reject new work, await active readers, wipe memory, and remove private spool state."""
        async with self._condition:
            if self._closed:
                return
            if self._closing:
                await self._condition.wait_for(lambda: self._closed)
                return
            self._closing = True
            await self._condition.wait_for(lambda: self._active_readers == 0 and not self._pending)
            entries = tuple(self._entries.values())
            self._entries.clear()
            self._pending.clear()
            self._reserved = 0
        cleanup = asyncio.create_task(self._finish_close(entries))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _finish_close(self, entries: tuple[_StoredEntry, ...]) -> None:
        """Finish all cleanup attempts before publishing the terminal closed state."""
        failed = False
        for entry in entries:
            if entry.memory is not None:
                _wipe(entry.memory)
            if entry.path is not None:
                try:
                    await _unlink(entry.path)
                except BodyStoreError:
                    failed = True
        try:
            await asyncio.to_thread(self._directory.rmdir)
        except OSError:
            failed = True
        finally:
            async with self._condition:
                self._closed = True
                self._condition.notify_all()
        if failed:
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)

    def _entry(self, body_id: BodyId) -> _StoredEntry:
        if self._closing or self._closed:
            raise BodyStoreError(BodyStoreFailureCode.CLOSED)
        entry = self._entries.get(body_id)
        if entry is None:
            raise BodyStoreError(BodyStoreFailureCode.UNKNOWN_BODY)
        return entry

    async def _reserve(self, count: int) -> None:
        async with self._condition:
            if self._closed:
                raise BodyStoreError(BodyStoreFailureCode.CLOSED)
            if self._reserved + count > self.config.maximum_transient_bytes.value:
                raise BodyStoreError(BodyStoreFailureCode.RUN_LIMIT_EXCEEDED)
            self._reserved += count

    async def _release(self, count: int) -> None:
        async with self._condition:
            self._reserved -= count

    async def _commit(self, entry: _StoredEntry) -> None:
        async with self._condition:
            if self._closed:
                raise BodyStoreError(BodyStoreFailureCode.CLOSED)
            self._pending.remove(entry.body_id)
            self._entries[entry.body_id] = entry
            self._condition.notify_all()

    async def _discard(self, body_id: BodyId, count: int) -> None:
        async with self._condition:
            self._pending.discard(body_id)
            self._reserved -= count
            self._condition.notify_all()

    async def _reader_started(self) -> None:
        async with self._condition:
            if self._closing or self._closed:
                raise BodyStoreError(BodyStoreFailureCode.CLOSED)
            self._active_readers += 1

    async def _reader_finished(self) -> None:
        async with self._condition:
            self._active_readers -= 1
            self._condition.notify_all()


def _wipe(value: bytearray) -> None:
    """Best-effort overwrite of a mutable in-memory buffer before releasing it."""
    value[:] = b"\x00" * len(value)
    value.clear()


async def _unlink(path: Path) -> None:
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError as error:
        raise BodyStoreError(BodyStoreFailureCode.IO_FAILED) from error
