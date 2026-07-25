"""Bounded secure disk spool for already-sanitized artifact blobs."""

from __future__ import annotations

import asyncio
import errno
import os
import stat
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from secrets import token_hex
from typing import Final
from typing import final

from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount

_CREATION_ATTEMPTS: Final = 8
_DIRECTORY_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_CREATE_FILE_FLAGS: Final = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_READ_FILE_FLAGS: Final = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class SanitizedSpoolError(RuntimeError):
    """A value-free sanitized spool failure."""

    __slots__ = ("code",)

    def __init__(self, code: ArtifactStoreErrorCode) -> None:
        """Retain only one closed failure category."""
        if type(code) is not ArtifactStoreErrorCode:
            raise TypeError("sanitized spool error code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class _SpoolEntry:
    """One immutable committed content-addressed file."""

    __slots__ = ("byte_count", "name")

    def __init__(self, *, name: str, byte_count: int) -> None:
        self.name = name
        self.byte_count = byte_count


class PosixSanitizedBlobSpool:
    """Stream sanitized blobs through a private bounded POSIX disk directory."""

    __slots__ = (
        "_active_readers",
        "_close_task",
        "_closed",
        "_closing",
        "_condition",
        "_directory_fd",
        "_directory_name",
        "_entries",
        "_orphans",
        "_parent_fd",
        "_pending",
        "_reserved",
        "io_chunk_size",
        "limits",
    )

    def __init__(
        self,
        limits: ArtifactLimits,
        *,
        io_chunk_size: PositiveCount,
        root: Path | None = None,
    ) -> None:
        """Create and pin one private directory beneath an authorized temporary root."""
        if type(limits) is not ArtifactLimits:
            raise TypeError("sanitized spool requires ArtifactLimits")
        if type(io_chunk_size) is not PositiveCount:
            raise TypeError("sanitized spool chunk size must be PositiveCount")
        if root is not None and (not isinstance(root, Path) or not root.is_absolute()):
            raise SanitizedSpoolError(ArtifactStoreErrorCode.ROOT_UNSAFE)
        if not _descriptor_operations_supported():
            raise SanitizedSpoolError(ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM)
        selected_root = Path(tempfile.gettempdir()) if root is None else root
        parent_fd = _open_parent(selected_root)
        try:
            directory_name, directory_fd = _create_directory(parent_fd)
        except BaseException:
            _close_descriptor(parent_fd)
            raise
        self.limits = limits
        self.io_chunk_size = io_chunk_size
        self._parent_fd = parent_fd
        self._directory_name = directory_name
        self._directory_fd = directory_fd
        self._entries: dict[str, _SpoolEntry] = {}
        self._pending: set[str] = set()
        self._orphans: set[str] = set()
        self._reserved = 0
        self._active_readers = 0
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._condition = asyncio.Condition()

    @property
    def reserved_bytes(self) -> ByteCount:
        """Return exact committed plus in-progress sanitized bytes."""
        return ByteCount(value=self._reserved)

    @property
    def spool_file_count(self) -> int:
        """Return the number of unique committed content files."""
        return len(self._entries)

    async def begin(self) -> _SpoolBlobWriter:
        """Create one admitted exclusive pending file."""
        async with self._condition:
            if self._closing or self._closed:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.CLOSED)
            if len(self._pending) >= self.limits.maximum_blobs.value:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            name, descriptor = _create_pending_file(self._directory_fd)
            self._pending.add(name)
        return _SpoolBlobWriter(self, name=name, descriptor=descriptor)

    def read(self, blob: ArtifactBlob) -> AsyncIterator[bytes]:
        """Open one independently positioned verified sanitized stream."""
        if type(blob) is not ArtifactBlob:
            raise TypeError("sanitized spool reads require ArtifactBlob")

        async def chunks() -> AsyncIterator[bytes]:
            descriptor = await self._reader_started(blob)
            try:
                await _authenticate_blob(
                    descriptor,
                    blob,
                    chunk_size=self.io_chunk_size.value,
                )
                _rewind_descriptor(descriptor)
                while True:
                    chunk = await _read_chunk(
                        descriptor,
                        self.io_chunk_size.value,
                    )
                    if not chunk:
                        break
                    yield chunk
            finally:
                _close_descriptor(descriptor)
                await self._reader_finished()

        return chunks()

    async def close(self) -> None:
        """Reject new work and share one cancellation-safe cleanup task."""
        async with self._condition:
            if self._closed:
                return
            if self._close_task is None:
                self._closing = True
                self._close_task = asyncio.create_task(self._finish_close())
                self._close_task.add_done_callback(_consume_task_exception)
            task = self._close_task
        await asyncio.shield(task)

    async def __aenter__(self) -> PosixSanitizedBlobSpool:
        """Return this open spool for asynchronous lifetime composition."""
        if self._closing or self._closed:
            raise SanitizedSpoolError(ArtifactStoreErrorCode.CLOSED)
        return self

    async def __aexit__(self, *_error: object) -> None:
        """Close and clean the private spool directory."""
        await self.close()

    async def _reserve(self, count: int) -> None:
        async with self._condition:
            if self._closed:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.CLOSED)
            if self._reserved + count > self.limits.maximum_total_blob_bytes.value:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            self._reserved += count

    async def _release(self, count: int) -> None:
        async with self._condition:
            self._reserved -= count

    async def _commit_pending(self, pending_name: str, blob: ArtifactBlob) -> None:
        async with self._condition:
            existing = self._entries.get(blob.sha256)
            if existing is not None:
                if existing.byte_count != blob.byte_count:
                    raise SanitizedSpoolError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
                if not _unlink(self._directory_fd, pending_name):
                    self._orphans.add(pending_name)
                self._pending.remove(pending_name)
                self._reserved -= blob.byte_count
                self._condition.notify_all()
                return
            if len(self._entries) >= self.limits.maximum_blobs.value:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            outcome = _link_noreplace(
                self._directory_fd,
                pending_name,
                blob.sha256,
            )
            if outcome is _LinkOutcome.EXISTS:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
            if outcome is _LinkOutcome.FAILED:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)
            if not _unlink(self._directory_fd, pending_name):
                if not _unlink(self._directory_fd, blob.sha256):
                    self._orphans.add(blob.sha256)
                raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)
            self._entries[blob.sha256] = _SpoolEntry(
                name=blob.sha256,
                byte_count=blob.byte_count,
            )
            self._pending.remove(pending_name)
            self._condition.notify_all()

    async def _discard(self, pending_name: str, count: int) -> bool:
        removed = _unlink(self._directory_fd, pending_name)
        async with self._condition:
            self._pending.discard(pending_name)
            self._reserved -= count
            if not removed:
                self._orphans.add(pending_name)
            self._condition.notify_all()
        return removed

    async def _reader_started(self, blob: ArtifactBlob) -> int:
        async with self._condition:
            if self._closing or self._closed:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.CLOSED)
            entry = self._entries.get(blob.sha256)
            if entry is None or entry.byte_count != blob.byte_count:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
            descriptor = _open_committed_file(self._directory_fd, entry)
            self._active_readers += 1
            return descriptor

    async def _reader_finished(self) -> None:
        async with self._condition:
            self._active_readers -= 1
            self._condition.notify_all()

    async def _finish_close(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: not self._pending and self._active_readers == 0)
            names = tuple(entry.name for entry in self._entries.values()) + tuple(self._orphans)
            self._entries.clear()
            self._orphans.clear()
            self._reserved = 0
        failed = False
        for name in names:
            if not await asyncio.to_thread(_unlink, self._directory_fd, name):
                failed = True
        if not await asyncio.to_thread(
            _rmdir,
            self._parent_fd,
            self._directory_name,
        ):
            failed = True
        if not _close_descriptor(self._directory_fd):
            failed = True
        if not _close_descriptor(self._parent_fd):
            failed = True
        async with self._condition:
            self._closed = True
            self._condition.notify_all()
        if failed:
            raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)


@final
class _SpoolBlobWriter:
    """One single-use private sanitized blob file transaction."""

    __slots__ = (
        "_count",
        "_descriptor",
        "_digest",
        "_done",
        "_lock",
        "_name",
        "_store",
    )

    def __init__(
        self,
        store: PosixSanitizedBlobSpool,
        *,
        name: str,
        descriptor: int,
    ) -> None:
        self._store = store
        self._name = name
        self._descriptor = descriptor
        self._digest = sha256()
        self._count = 0
        self._done = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> _SpoolBlobWriter:
        """Return this admitted writer."""
        return self

    async def __aexit__(self, *_error: object) -> None:
        """Abort any uncommitted file on every exit path."""
        await self.abort()

    async def write(self, chunk: bytes) -> None:
        """Append one chunk without retaining complete content in memory."""
        async with self._lock:
            self._require_active()
            if type(chunk) is not bytes:
                raise TypeError("sanitized spool chunks must be bytes")
            if not chunk:
                return
            attempted = self._count + len(chunk)
            if attempted > self._store.limits.maximum_blob_bytes.value:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            await self._store._reserve(len(chunk))
            task = asyncio.create_task(asyncio.to_thread(_write_all, self._descriptor, chunk))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                failed = False
                try:
                    await task
                except OSError:
                    failed = True
                if failed:
                    await self._store._release(len(chunk))
                else:
                    self._digest.update(chunk)
                    self._count = attempted
                raise
            except OSError:
                await self._store._release(len(chunk))
                raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED) from None
            self._digest.update(chunk)
            self._count = attempted

    async def commit(self, *, media_type: str | None) -> ArtifactBlob:
        """Verify, content-address, and publish this complete pending file."""
        async with self._lock:
            self._require_active()
            blob = ArtifactBlob(
                sha256=self._digest.hexdigest(),
                byte_count=self._count,
                media_type=media_type,
            )
            await _sync_descriptor(self._descriptor)
            descriptor = self._descriptor
            self._descriptor = -1
            if not _close_descriptor(descriptor):
                raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)
            await self._store._commit_pending(self._name, blob)
            self._done = True
            return blob

    async def abort(self) -> None:
        """Finish cleanup even when the aborting caller is cancelled."""
        task = asyncio.create_task(self._abort())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _abort(self) -> None:
        async with self._lock:
            if self._done:
                return
            self._done = True
            descriptor = self._descriptor
            self._descriptor = -1
            close_ok = descriptor < 0 or _close_descriptor(descriptor)
            unlink_ok = await self._store._discard(self._name, self._count)
            if not close_ok or not unlink_ok:
                raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)

    def _require_active(self) -> None:
        if self._done or self._store._closed:
            raise SanitizedSpoolError(ArtifactStoreErrorCode.CLOSED)


class _LinkOutcome:
    """Private identity sentinels for no-replace link outcomes."""

    CREATED = object()
    EXISTS = object()
    FAILED = object()


def _descriptor_operations_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_CLOEXEC")
        and hasattr(os, "O_NOFOLLOW")
        and os.mkdir in os.supports_dir_fd
        and os.open in os.supports_dir_fd
        and os.link in os.supports_dir_fd
        and os.link in os.supports_follow_symlinks
        and os.unlink in os.supports_dir_fd
        and os.rmdir in os.supports_dir_fd
    )


def _open_parent(root: Path) -> int:
    failed = False
    descriptor = -1
    try:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
        metadata = os.fstat(descriptor)
        private = metadata.st_uid == os.geteuid() and not metadata.st_mode & (
            stat.S_IWGRP | stat.S_IWOTH
        )
        sticky = bool(metadata.st_mode & stat.S_ISVTX)
        if not stat.S_ISDIR(metadata.st_mode) or not (private or sticky):
            failed = True
    except OSError:
        failed = True
    if failed:
        if descriptor >= 0:
            _close_descriptor(descriptor)
        raise SanitizedSpoolError(ArtifactStoreErrorCode.ROOT_UNSAFE)
    return descriptor


def _create_directory(parent_fd: int) -> tuple[str, int]:
    for _attempt in range(_CREATION_ATTEMPTS):
        name = f".comparo-sanitized-{token_hex(16)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError:
            raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED) from None
        descriptor = -1
        failed = False
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            os.fchmod(descriptor, 0o700)
            _validate_private_directory(descriptor)
        except (OSError, SanitizedSpoolError):
            failed = True
        if failed:
            if descriptor >= 0:
                _close_descriptor(descriptor)
            _rmdir(parent_fd, name)
            raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)
        return name, descriptor
    raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)


def _create_pending_file(directory_fd: int) -> tuple[str, int]:
    for _attempt in range(_CREATION_ATTEMPTS):
        name = f".pending-{token_hex(16)}"
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                _CREATE_FILE_FLAGS,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        except OSError:
            raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED) from None
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            _close_descriptor(descriptor)
            _unlink(directory_fd, name)
            raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED) from None
        return name, descriptor
    raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)


def _open_committed_file(directory_fd: int, entry: _SpoolEntry) -> int:
    descriptor = -1
    failed = False
    try:
        descriptor = os.open(entry.name, _READ_FILE_FLAGS, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or metadata.st_size != entry.byte_count
        ):
            failed = True
    except OSError:
        failed = True
    if failed:
        if descriptor >= 0:
            _close_descriptor(descriptor)
        raise SanitizedSpoolError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
    return descriptor


def _validate_private_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise SanitizedSpoolError(ArtifactStoreErrorCode.ROOT_UNSAFE)


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short sanitized spool write")
        view = view[written:]


async def _authenticate_blob(
    descriptor: int,
    blob: ArtifactBlob,
    *,
    chunk_size: int,
) -> None:
    digest = sha256()
    count = 0
    while True:
        chunk = await _read_chunk(descriptor, chunk_size)
        if not chunk:
            break
        count += len(chunk)
        if count > blob.byte_count:
            raise SanitizedSpoolError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
        digest.update(chunk)
    if count != blob.byte_count or digest.hexdigest() != blob.sha256:
        raise SanitizedSpoolError(ArtifactStoreErrorCode.DIGEST_MISMATCH)


def _rewind_descriptor(descriptor: int) -> None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED) from None


async def _sync_descriptor(descriptor: int) -> None:
    task = asyncio.create_task(asyncio.to_thread(os.fsync, descriptor))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(OSError):
            await task
        raise
    except OSError:
        raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED) from None


async def _read_chunk(descriptor: int, count: int) -> bytes:
    task = asyncio.create_task(asyncio.to_thread(os.read, descriptor, count))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(OSError):
            await task
        raise
    except OSError:
        raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED) from None


def _link_noreplace(directory_fd: int, source: str, destination: str) -> object:
    try:
        os.link(
            source,
            destination,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        return _LinkOutcome.EXISTS
    except OSError:
        return _LinkOutcome.FAILED
    return _LinkOutcome.CREATED


def _unlink(directory_fd: int, name: str) -> bool:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _rmdir(parent_fd: int, name: str) -> bool:
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _close_descriptor(descriptor: int) -> bool:
    try:
        os.close(descriptor)
    except OSError:
        return False
    return True


def _consume_task_exception(task: asyncio.Task[None]) -> None:
    """Retrieve background close failures without changing later await behavior."""
    if not task.cancelled():
        task.exception()
