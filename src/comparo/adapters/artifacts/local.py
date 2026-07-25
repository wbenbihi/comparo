"""Containment-safe atomic POSIX storage for sanitized local artifact bundles."""

from __future__ import annotations

import asyncio
import ctypes
import errno
import os
import re
import stat
import sys
from collections.abc import AsyncIterable
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from secrets import token_hex
from typing import Final
from typing import final

import msgspec

from comparo.core.artifacts.codec import ArtifactCodecError
from comparo.core.artifacts.codec import decode_run_artifact
from comparo.core.artifacts.codec import encode_run_artifact
from comparo.core.artifacts.common import ARTIFACT_API_VERSION
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.ports import ArtifactPersistenceOutcome
from comparo.core.artifacts.ports import ArtifactPersistenceStatus
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.artifacts.ports import ArtifactStoreFailure
from comparo.core.artifacts.ports import SanitizedBlobSource

_BUNDLE_API_VERSION: Final = "comparo.bundle/v1alpha1"
_BUNDLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
_STAGING_ATTEMPTS: Final = 8
_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS: Final = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
)


@final
class _BundleManifest(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
):
    """Small physical-bundle manifest authenticated by the completion marker."""

    api_version: str
    artifact_api_version: str
    artifact_id: str
    results_sha256: str
    results_byte_count: int
    blobs: tuple[ArtifactBlob, ...]


class LocalArtifactBundleSink:
    """Persist complete bundles beneath one pinned user-owned POSIX directory."""

    __slots__ = ("_closed", "_root_fd")

    def __init__(self, root: Path) -> None:
        """Pin a safe absolute root without trusting that pathname again."""
        if not isinstance(root, Path) or not root.is_absolute():
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
        if not _atomic_rename_supported():
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM)
        try:
            root_fd = os.open(root, _DIRECTORY_FLAGS)
        except OSError as error:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE) from error
        try:
            _validate_root_fd(root_fd)
        except Exception:
            os.close(root_fd)
            raise
        self._root_fd = root_fd
        self._closed = False

    async def persist(  # noqa: C901
        self,
        artifact: object,
        blobs: SanitizedBlobSource,
        limits: object,
    ) -> ArtifactPersistenceOutcome:
        """Write, sync, and no-replace publish one complete sanitized bundle."""
        if self._closed:
            return _failure(ArtifactStoreErrorCode.CLOSED)
        if type(artifact) is not RunArtifact or type(limits) is not ArtifactLimits:
            return _failure(ArtifactStoreErrorCode.INVALID_ARTIFACT)
        bundle_id = artifact.metadata.artifact_id
        if _BUNDLE_ID.fullmatch(bundle_id) is None:
            return _failure(ArtifactStoreErrorCode.INVALID_ARTIFACT)
        if len(artifact.blobs) > limits.maximum_blobs.value:
            return _failure(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        transaction: _BundleTransaction | None = None
        try:
            transaction = _BundleTransaction.create(self._root_fd, bundle_id)
            await transaction.write(artifact, blobs, limits)
            transaction.publish()
            return ArtifactPersistenceOutcome(
                status=ArtifactPersistenceStatus.PERSISTED,
                bundle_id=bundle_id,
            )
        except asyncio.CancelledError:
            if transaction is not None:
                transaction.abort()
            raise
        except LocalArtifactStoreError as error:
            if transaction is not None:
                transaction.abort()
            return _failure(error.code)
        except Exception:  # noqa: BLE001 - never copy exception details into the outcome
            if transaction is not None:
                transaction.abort()
            return _failure(ArtifactStoreErrorCode.IO_FAILED)
        finally:
            if transaction is not None:
                transaction.close()

    def close(self) -> None:
        """Release the pinned root descriptor and reject later writes."""
        if self._closed:
            return
        os.close(self._root_fd)
        self._closed = True

    def __enter__(self) -> LocalArtifactBundleSink:
        """Return this pinned sink for synchronous lifetime composition."""
        if self._closed:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.CLOSED)
        return self

    def __exit__(self, *_error: object) -> None:
        """Close the pinned root descriptor."""
        self.close()


class LocalArtifactStoreError(RuntimeError):
    """A value-free local-store error safe to map across the persistence port."""

    __slots__ = ("code",)

    def __init__(self, code: ArtifactStoreErrorCode) -> None:
        """Retain only a closed storage failure category."""
        if type(code) is not ArtifactStoreErrorCode:
            raise TypeError("local artifact store error code must be typed")
        self.code = code
        super().__init__(code.value)


class _BundleTransaction:
    """One private sibling staging directory and its open descriptors."""

    __slots__ = (
        "_blob_fd",
        "_bundle_id",
        "_closed",
        "_created_blob_names",
        "_created_files",
        "_published",
        "_root_fd",
        "_staging_fd",
        "_staging_name",
    )

    def __init__(
        self,
        *,
        root_fd: int,
        bundle_id: str,
        staging_name: str,
        staging_fd: int,
        blob_fd: int,
    ) -> None:
        self._root_fd = root_fd
        self._bundle_id = bundle_id
        self._staging_name = staging_name
        self._staging_fd = staging_fd
        self._blob_fd = blob_fd
        self._created_files: list[str] = []
        self._created_blob_names: list[str] = []
        self._published = False
        self._closed = False

    @classmethod
    def create(cls, root_fd: int, bundle_id: str) -> _BundleTransaction:
        """Create a random restrictive sibling staging directory without following links."""
        for _attempt in range(_STAGING_ATTEMPTS):
            staging_name = f".staging-{bundle_id}-{token_hex(16)}"
            try:
                os.mkdir(staging_name, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                continue
            except OSError as error:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED) from error
            try:
                staging_fd = os.open(staging_name, _DIRECTORY_FLAGS, dir_fd=root_fd)
                os.mkdir("blobs", mode=0o700, dir_fd=staging_fd)
                blob_fd = os.open("blobs", _DIRECTORY_FLAGS, dir_fd=staging_fd)
            except OSError as error:
                _cleanup_partial_creation(root_fd, staging_name, locals())
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED) from error
            return cls(
                root_fd=root_fd,
                bundle_id=bundle_id,
                staging_name=staging_name,
                staging_fd=staging_fd,
                blob_fd=blob_fd,
            )
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)

    async def write(
        self,
        artifact: RunArtifact,
        blobs: SanitizedBlobSource,
        limits: ArtifactLimits,
    ) -> None:
        """Write all authenticated content and sync it before the completion marker."""
        try:
            results = encode_run_artifact(
                artifact,
                maximum_bytes=limits.maximum_manifest_bytes.value,
            )
        except ArtifactCodecError as error:
            code = (
                ArtifactStoreErrorCode.BUDGET_EXCEEDED
                if error.code.value == "budget-exceeded"
                else ArtifactStoreErrorCode.INVALID_ARTIFACT
            )
            raise LocalArtifactStoreError(code) from error
        results_digest = sha256(results).hexdigest()
        _write_file(self._staging_fd, "results.json", results)
        self._created_files.append("results.json")
        total = 0
        for blob in artifact.blobs:
            if blob.byte_count > limits.maximum_blob_bytes.value:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            total += blob.byte_count
            if total > limits.maximum_total_blob_bytes.value:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            await self._write_blob(blob, blobs.read(blob))
        os.fsync(self._blob_fd)
        bundle = _BundleManifest(
            api_version=_BUNDLE_API_VERSION,
            artifact_api_version=artifact.api_version,
            artifact_id=artifact.metadata.artifact_id,
            results_sha256=results_digest,
            results_byte_count=len(results),
            blobs=artifact.blobs,
        )
        manifest = msgspec.json.encode(bundle, order="deterministic")
        if len(manifest) > limits.maximum_manifest_bytes.value:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        _write_file(self._staging_fd, "manifest.json", manifest)
        self._created_files.append("manifest.json")
        os.fsync(self._staging_fd)
        marker = f"{sha256(manifest).hexdigest()}\n".encode("ascii")
        _write_file(self._staging_fd, "COMPLETE", marker)
        self._created_files.append("COMPLETE")
        os.fsync(self._staging_fd)

    async def _write_blob(
        self,
        blob: ArtifactBlob,
        chunks: AsyncIterable[bytes],
    ) -> None:
        """Stream one blob with exact size and digest verification."""
        name = blob.sha256
        try:
            descriptor = os.open(name, _FILE_FLAGS, 0o600, dir_fd=self._blob_fd)
        except OSError as error:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED) from error
        self._created_blob_names.append(name)
        digest = sha256()
        size = 0
        try:
            async for chunk in chunks:
                if type(chunk) is not bytes:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
                size += len(chunk)
                if size > blob.byte_count:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
                digest.update(chunk)
                _write_all(descriptor, chunk)
            if size != blob.byte_count or digest.hexdigest() != blob.sha256:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def publish(self) -> None:
        """Atomically publish without overwrite, then durably sync the root directory."""
        try:
            _atomic_rename_noreplace(
                self._root_fd,
                self._staging_name,
                self._bundle_id,
            )
        except FileExistsError as error:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.ALREADY_EXISTS) from error
        except OSError as error:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED) from error
        self._published = True
        try:
            os.fsync(self._root_fd)
        except OSError as error:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.COMMIT_UNCERTAIN) from error

    def abort(self) -> None:
        """Remove only this known private staging tree if it was not published."""
        if self._published:
            return
        for name in reversed(self._created_blob_names):
            _best_effort_unlink(self._blob_fd, name)
        for name in reversed(self._created_files):
            _best_effort_unlink(self._staging_fd, name)
        with suppress(OSError):
            os.rmdir("blobs", dir_fd=self._staging_fd)
        with suppress(OSError):
            os.rmdir(self._staging_name, dir_fd=self._root_fd)

    def close(self) -> None:
        """Release transaction descriptors exactly once."""
        if self._closed:
            return
        os.close(self._blob_fd)
        os.close(self._staging_fd)
        self._closed = True


def read_local_artifact_bundle(  # noqa: C901
    root: Path,
    bundle_id: str,
    *,
    limits: ArtifactLimits,
) -> RunArtifact:
    """Read and authenticate one complete bundle through descriptor-relative operations."""
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or _BUNDLE_ID.fullmatch(bundle_id) is None
        or type(limits) is not ArtifactLimits
    ):
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
    root_fd = -1
    bundle_fd = -1
    try:
        root_fd = os.open(root, _DIRECTORY_FLAGS)
        _validate_root_fd(root_fd)
        bundle_fd = os.open(bundle_id, _DIRECTORY_FLAGS, dir_fd=root_fd)
        _validate_bundle_fd(bundle_fd)
        marker = _read_file(bundle_fd, "COMPLETE", maximum=65)
        manifest_bytes = _read_file(
            bundle_fd,
            "manifest.json",
            maximum=limits.maximum_manifest_bytes.value,
        )
        if marker != f"{sha256(manifest_bytes).hexdigest()}\n".encode("ascii"):
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
        manifest = msgspec.json.decode(manifest_bytes, type=_BundleManifest)
        if (
            manifest.api_version != _BUNDLE_API_VERSION
            or manifest.artifact_api_version != ARTIFACT_API_VERSION
            or manifest.artifact_id != bundle_id
        ):
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
        results = _read_file(
            bundle_fd,
            "results.json",
            maximum=limits.maximum_manifest_bytes.value,
        )
        if (
            len(results) != manifest.results_byte_count
            or sha256(results).hexdigest() != manifest.results_sha256
        ):
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
        artifact = decode_run_artifact(
            results,
            maximum_bytes=limits.maximum_manifest_bytes.value,
        )
        if (
            artifact.api_version != manifest.artifact_api_version
            or artifact.metadata.artifact_id != bundle_id
            or artifact.blobs != manifest.blobs
        ):
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
        if len(artifact.blobs) > limits.maximum_blobs.value:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        blob_fd = os.open("blobs", _DIRECTORY_FLAGS, dir_fd=bundle_fd)
        try:
            _validate_bundle_fd(blob_fd)
            total = 0
            for blob in artifact.blobs:
                if blob.byte_count > limits.maximum_blob_bytes.value:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
                total += blob.byte_count
                if total > limits.maximum_total_blob_bytes.value:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
                value = _read_file(blob_fd, blob.sha256, maximum=blob.byte_count)
                if len(value) != blob.byte_count or sha256(value).hexdigest() != blob.sha256:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
        finally:
            os.close(blob_fd)
        return artifact
    except LocalArtifactStoreError:
        raise
    except ArtifactCodecError as error:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.INVALID_ARTIFACT) from error
    except (OSError, msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError) as error:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED) from error
    finally:
        if bundle_fd >= 0:
            os.close(bundle_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _write_file(directory_fd: int, name: str, value: bytes) -> None:
    descriptor = os.open(name, _FILE_FLAGS, 0o600, dir_fd=directory_fd)
    try:
        _write_all(descriptor, value)
        os.fsync(descriptor)
    except Exception:
        _best_effort_unlink(directory_fd, name)
        raise
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short artifact write")
        view = view[written:]


def _read_file(directory_fd: int, name: str, *, maximum: int) -> bytes:
    if type(maximum) is not int or maximum < 0:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
        if metadata.st_size > maximum:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        remaining = maximum + 1
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        return value
    finally:
        os.close(descriptor)


def _validate_root_fd(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)


def _validate_bundle_fd(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)


def _failure(code: ArtifactStoreErrorCode) -> ArtifactPersistenceOutcome:
    status = (
        ArtifactPersistenceStatus.COMMIT_UNCERTAIN
        if code is ArtifactStoreErrorCode.COMMIT_UNCERTAIN
        else ArtifactPersistenceStatus.FAILED
    )
    return ArtifactPersistenceOutcome(
        status=status,
        failure=ArtifactStoreFailure(code=code),
    )


def _best_effort_unlink(directory_fd: int, name: str) -> None:
    with suppress(OSError):
        os.unlink(name, dir_fd=directory_fd)


def _cleanup_partial_creation(
    root_fd: int,
    staging_name: str,
    local_values: dict[str, object],
) -> None:
    blob_fd = local_values.get("blob_fd")
    staging_fd = local_values.get("staging_fd")
    if type(blob_fd) is int:
        os.close(blob_fd)
    if type(staging_fd) is int:
        with suppress(OSError):
            os.rmdir("blobs", dir_fd=staging_fd)
        os.close(staging_fd)
    with suppress(OSError):
        os.rmdir(staging_name, dir_fd=root_fd)


def _atomic_rename_supported() -> bool:
    if (
        not hasattr(os, "O_NOFOLLOW")
        or os.mkdir not in os.supports_dir_fd
        or os.open not in os.supports_dir_fd
        or os.rename not in os.supports_dir_fd
        or os.unlink not in os.supports_dir_fd
    ):
        return False
    library = ctypes.CDLL(None, use_errno=True)
    platform = _platform_name()
    if platform == "darwin":
        return hasattr(library, "renameatx_np")
    if platform.startswith("linux"):
        return hasattr(library, "renameat2")
    return False


def _platform_name() -> str:
    return sys.platform


def _atomic_rename_noreplace(root_fd: int, source: str, destination: str) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function = library.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(root_fd, source_bytes, root_fd, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(root_fd, source_bytes, root_fd, destination_bytes, 0x00000001)
    else:  # pragma: no cover - constructor rejects unsupported platforms
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM)
    if result != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise FileExistsError(number, os.strerror(number), destination)
        raise OSError(number, os.strerror(number), destination)
