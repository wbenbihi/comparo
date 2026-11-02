"""Pinned, symlink-safe reads beneath one operator-selected Project root."""

from __future__ import annotations

import asyncio
import errno
import os
import stat
from contextlib import suppress
from functools import partial
from pathlib import Path
from threading import Lock
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
_READ_SIZE = 65_536


class LocalFilesystemErrorCode(DomainStrEnum):
    """Value-free construction failures for confined local filesystem access."""

    ROOT_UNSAFE = "root-unsafe"
    UNSUPPORTED_PLATFORM = "unsupported-platform"


class ConfinedReadCode(DomainStrEnum):
    """Closed outcomes for one confined read that never retain a requested path."""

    NOT_FOUND = "not-found"
    PATH_DENIED = "path-denied"
    BUDGET_EXCEEDED = "budget-exceeded"
    IO_FAILED = "io-failed"
    CLOSED = "closed"


@final
class LocalFileLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Finite admission limits applied before and during every local read."""

    maximum_bytes: ByteCount = ByteCount(value=8 * 1024 * 1024)
    maximum_path_bytes: ByteCount = ByteCount(value=4096)
    maximum_segments: PositiveCount = PositiveCount(value=128)

    def __post_init__(self) -> None:
        """Require exact typed limits."""
        if type(self.maximum_bytes) is not ByteCount:
            raise TypeError("local file byte limit must be ByteCount")
        if type(self.maximum_path_bytes) is not ByteCount:
            raise TypeError("local file path limit must be ByteCount")
        if type(self.maximum_segments) is not PositiveCount:
            raise TypeError("local file segment limit must be PositiveCount")


DEFAULT_LOCAL_FILE_LIMITS = LocalFileLimits()


@final
class ConfinedReadResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exact bytes or one value-free confined read failure."""

    data: bytes | None = None
    code: ConfinedReadCode | None = None

    def __post_init__(self) -> None:
        """Require exactly bytes or one typed failure code."""
        if self.data is not None and type(self.data) is not bytes:
            raise TypeError("confined read data must be bytes")
        if self.code is not None and type(self.code) is not ConfinedReadCode:
            raise TypeError("confined read code must be typed")
        if (self.data is None) == (self.code is None):
            raise ValueError("confined read result requires exactly data or a failure")


class LocalFilesystemError(RuntimeError):
    """A value-free local filesystem setup error."""

    __slots__ = ("code",)

    def __init__(self, code: LocalFilesystemErrorCode) -> None:
        """Retain only a stable setup failure category."""
        if type(code) is not LocalFilesystemErrorCode:
            raise TypeError("local filesystem error code must be typed")
        self.code = code
        super().__init__(code.value)


class PinnedProjectFiles:
    """Own one pinned Project directory and perform descriptor-relative reads."""

    __slots__ = ("_closed", "_limits", "_lock", "_root_fd")

    def __init__(
        self,
        root: Path,
        *,
        limits: LocalFileLimits = DEFAULT_LOCAL_FILE_LIMITS,
    ) -> None:
        """Pin a non-symlink absolute directory on a platform with safe open primitives."""
        if type(limits) is not LocalFileLimits:
            raise TypeError("pinned project files require LocalFileLimits")
        if not isinstance(root, Path) or not root.is_absolute():
            raise LocalFilesystemError(LocalFilesystemErrorCode.ROOT_UNSAFE)
        if not _safe_open_supported():
            raise LocalFilesystemError(LocalFilesystemErrorCode.UNSUPPORTED_PLATFORM)
        descriptor = -1
        with suppress(OSError):
            descriptor = os.open(root, _DIRECTORY_FLAGS)
        if descriptor < 0:
            raise LocalFilesystemError(LocalFilesystemErrorCode.ROOT_UNSAFE)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise LocalFilesystemError(LocalFilesystemErrorCode.ROOT_UNSAFE)
        except Exception:
            os.close(descriptor)
            raise
        self._root_fd = descriptor
        self._limits = limits
        self._lock = Lock()
        self._closed = False

    @property
    def limits(self) -> LocalFileLimits:
        """Return the immutable adapter-owned hard limits."""
        return self._limits

    async def read(
        self,
        path: str,
        *,
        maximum_bytes: ByteCount | None = None,
    ) -> ConfinedReadResult:
        """Read a regular project file without following any untrusted path symlink."""
        if type(path) is not str:
            raise TypeError("confined file path must be a string")
        if maximum_bytes is not None and type(maximum_bytes) is not ByteCount:
            raise TypeError("confined file maximum must be ByteCount")
        parts = _path_parts(path, self._limits)
        if parts is None:
            return ConfinedReadResult(code=ConfinedReadCode.PATH_DENIED)
        maximum = self._limits.maximum_bytes.value
        if maximum_bytes is not None:
            maximum = min(maximum, maximum_bytes.value)
        with self._lock:
            if self._closed:
                return ConfinedReadResult(code=ConfinedReadCode.CLOSED)
            try:
                root_fd = os.dup(self._root_fd)
            except OSError:
                return ConfinedReadResult(code=ConfinedReadCode.IO_FAILED)
        return await asyncio.to_thread(partial(_read_from_root, root_fd, parts, maximum))

    def close(self) -> None:
        """Release the pinned root descriptor; repeated closure is harmless."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with suppress(OSError):
                os.close(self._root_fd)

    def __enter__(self) -> PinnedProjectFiles:
        """Return this reader while its descriptor remains owned by the context."""
        with self._lock:
            if self._closed:
                raise LocalFilesystemError(LocalFilesystemErrorCode.ROOT_UNSAFE)
        return self

    def __exit__(self, *_error: object) -> None:
        """Release the pinned Project directory."""
        self.close()


def _safe_open_supported() -> bool:
    return (
        getattr(os, "O_NOFOLLOW", 0) != 0
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
    )


def _path_parts(path: str, limits: LocalFileLimits) -> tuple[str, ...] | None:
    try:
        encoded = path.encode("utf-8")
    except UnicodeError:
        return None
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or path.startswith("/")
        or (len(encoded) > limits.maximum_path_bytes.value)
    ):
        return None
    parts = tuple(path.split("/"))
    if len(parts) > limits.maximum_segments.value or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def _read_from_root(root_fd: int, parts: tuple[str, ...], maximum: int) -> ConfinedReadResult:
    current_fd = root_fd
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=current_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                return ConfinedReadResult(code=ConfinedReadCode.PATH_DENIED)
            data = _bounded_read(file_fd, maximum)
            if data is None:
                return ConfinedReadResult(code=ConfinedReadCode.BUDGET_EXCEEDED)
            return ConfinedReadResult(data=data)
        finally:
            os.close(file_fd)
    except OSError as error:
        return ConfinedReadResult(code=_read_error_code(error.errno))
    finally:
        with suppress(OSError):
            os.close(current_fd)


def _bounded_read(descriptor: int, maximum: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(_READ_SIZE, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    return None if len(data) > maximum else data


def _read_error_code(number: int | None) -> ConfinedReadCode:
    if number == errno.ENOENT:
        return ConfinedReadCode.NOT_FOUND
    if number in {
        errno.EACCES,
        errno.EINVAL,
        errno.EISDIR,
        errno.ELOOP,
        errno.ENAMETOOLONG,
        errno.ENOTDIR,
        errno.EPERM,
    }:
        return ConfinedReadCode.PATH_DENIED
    return ConfinedReadCode.IO_FAILED


__all__ = (
    "DEFAULT_LOCAL_FILE_LIMITS",
    "ConfinedReadCode",
    "ConfinedReadResult",
    "LocalFileLimits",
    "LocalFilesystemError",
    "LocalFilesystemErrorCode",
    "PinnedProjectFiles",
)
