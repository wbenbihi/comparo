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
from collections.abc import AsyncIterator
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from hashlib import sha256
from pathlib import Path
from secrets import token_hex
from threading import Lock
from typing import Final
from typing import cast
from typing import final

import msgspec

from comparo.core.artifacts.codec import ArtifactCodecError
from comparo.core.artifacts.codec import ArtifactCodecErrorCode
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

    __slots__ = ("_closed", "_lock", "_root_fd")

    def __init__(self, root: Path) -> None:
        """Pin a safe absolute root without trusting that pathname again."""
        if not isinstance(root, Path) or not root.is_absolute():
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
        if not _atomic_rename_supported():
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM)
        root_fd = -1
        with suppress(OSError):
            root_fd = os.open(root, _DIRECTORY_FLAGS)
        if root_fd < 0:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
        failure: ArtifactStoreErrorCode | None = None
        try:
            _validate_root_fd(root_fd)
        except LocalArtifactStoreError as error:
            failure = error.code
        except OSError:
            failure = ArtifactStoreErrorCode.ROOT_UNSAFE
        if failure is not None:
            with suppress(OSError):
                os.close(root_fd)
            raise LocalArtifactStoreError(failure)
        self._root_fd = root_fd
        self._lock = Lock()
        self._closed = False

    async def persist(  # noqa: C901
        self,
        artifact: RunArtifact,
        blobs: SanitizedBlobSource,
        limits: ArtifactLimits,
    ) -> ArtifactPersistenceOutcome:
        """Write, sync, and no-replace publish one complete sanitized bundle."""
        if not _valid_persistence_inputs(artifact, limits):
            return _failure(ArtifactStoreErrorCode.INVALID_ARTIFACT)
        bundle_id = artifact.metadata.artifact_id
        if _BUNDLE_ID.fullmatch(bundle_id) is None:
            return _failure(ArtifactStoreErrorCode.INVALID_ARTIFACT)
        if len(artifact.blobs) > limits.maximum_blobs.value:
            return _failure(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        transaction: _BundleTransaction | None = None
        transaction_root_fd = -1
        try:
            with self._lock:
                if self._closed:
                    return _failure(ArtifactStoreErrorCode.CLOSED)
                transaction_root_fd = os.dup(self._root_fd)
            transaction = await _run_blocking(
                partial(
                    _BundleTransaction.create,
                    transaction_root_fd,
                    bundle_id,
                    owns_root_fd=False,
                ),
                dispose_cancelled_result=_dispose_bundle_transaction,
            )
            transaction.claim_root_ownership()
            transaction_root_fd = -1
            await transaction.write(artifact, blobs, limits)
            await _run_blocking(transaction.publish)
            return ArtifactPersistenceOutcome(
                status=ArtifactPersistenceStatus.PERSISTED,
                bundle_id=bundle_id,
            )
        except asyncio.CancelledError:
            if transaction is not None:
                await _run_blocking(transaction.abort)
            raise
        except LocalArtifactStoreError as error:
            if transaction is not None:
                await _run_blocking(transaction.abort)
            return _failure(error.code)
        except Exception:  # noqa: BLE001 - never copy exception details into the outcome
            if transaction is not None:
                await _run_blocking(transaction.abort)
            return _failure(ArtifactStoreErrorCode.IO_FAILED)
        finally:
            if transaction is not None:
                transaction.close()
            elif transaction_root_fd >= 0:
                with suppress(OSError):
                    os.close(transaction_root_fd)

    def close(self) -> None:
        """Release the pinned root descriptor and reject later writes."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with suppress(OSError):
                os.close(self._root_fd)

    def __enter__(self) -> LocalArtifactBundleSink:
        """Return this pinned sink for synchronous lifetime composition."""
        with self._lock:
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


@final
class LocalArtifactBundle:
    """Authenticated bundle metadata plus a lifetime-bound sanitized blob source."""

    __slots__ = ("_artifact", "_blob_fd", "_closed", "_lock")

    def __init__(self, artifact: RunArtifact, blob_fd: int) -> None:
        """Own one validated artifact and its pinned blob directory descriptor."""
        self._artifact = artifact
        self._blob_fd = blob_fd
        self._lock = Lock()
        self._closed = False

    @property
    def artifact(self) -> RunArtifact:
        """Return the decoded safe artifact whose blobs this source may expose."""
        return self._artifact

    def read(self, blob: ArtifactBlob) -> AsyncIterable[bytes]:
        """Verify one declared blob before streaming its sanitized bytes."""
        if type(blob) is not ArtifactBlob or blob not in self._artifact.blobs:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.INVALID_ARTIFACT)

        async def chunks() -> AsyncIterator[bytes]:
            directory_fd = -1
            descriptor = -1
            failure: ArtifactStoreErrorCode | None = None
            try:
                with self._lock:
                    if self._closed:
                        raise LocalArtifactStoreError(ArtifactStoreErrorCode.CLOSED)
                    directory_fd = os.dup(self._blob_fd)
                descriptor = await _run_blocking(
                    partial(
                        _open_read_file,
                        directory_fd,
                        blob.sha256,
                        maximum=blob.byte_count,
                    ),
                    dispose_cancelled_result=_close_descriptor,
                )
                await _run_blocking(partial(_verify_blob_descriptor, descriptor, blob))
                await _run_blocking(partial(os.lseek, descriptor, 0, os.SEEK_SET))
                while chunk := await _run_blocking(partial(os.read, descriptor, 65_536)):
                    yield chunk
            except LocalArtifactStoreError as error:
                failure = error.code
            except OSError:
                failure = ArtifactStoreErrorCode.IO_FAILED
            finally:
                if descriptor >= 0:
                    with suppress(OSError):
                        os.close(descriptor)
                if directory_fd >= 0:
                    with suppress(OSError):
                        os.close(directory_fd)
            if failure is not None:
                raise LocalArtifactStoreError(failure)

        return chunks()

    def close(self) -> None:
        """Release the pinned blob directory and reject future reads."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with suppress(OSError):
                os.close(self._blob_fd)

    def __enter__(self) -> LocalArtifactBundle:
        """Return this open bundle for explicit lifetime composition."""
        with self._lock:
            if self._closed:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.CLOSED)
        return self

    def __exit__(self, *_error: object) -> None:
        """Release the pinned blob directory."""
        self.close()


class _BundleTransaction:
    """One private sibling staging directory and its open descriptors."""

    __slots__ = (
        "_blob_fd",
        "_bundle_id",
        "_closed",
        "_created_blob_names",
        "_created_files",
        "_owns_root_fd",
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
        owns_root_fd: bool,
    ) -> None:
        self._root_fd = root_fd
        self._bundle_id = bundle_id
        self._staging_name = staging_name
        self._staging_fd = staging_fd
        self._blob_fd = blob_fd
        self._owns_root_fd = owns_root_fd
        self._created_files: list[str] = []
        self._created_blob_names: list[str] = []
        self._published = False
        self._closed = False

    @classmethod
    def create(
        cls,
        root_fd: int,
        bundle_id: str,
        *,
        owns_root_fd: bool = False,
    ) -> _BundleTransaction:
        """Create a random restrictive sibling staging directory without following links."""
        for _attempt in range(_STAGING_ATTEMPTS):
            staging_name = f".staging-{bundle_id}-{token_hex(16)}"
            mkdir_failed = False
            try:
                os.mkdir(staging_name, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                continue
            except OSError:
                mkdir_failed = True
            if mkdir_failed:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)
            setup_failed = False
            staging_fd = -1
            blob_fd = -1
            try:
                staging_fd = os.open(staging_name, _DIRECTORY_FLAGS, dir_fd=root_fd)
                os.mkdir("blobs", mode=0o700, dir_fd=staging_fd)
                blob_fd = os.open("blobs", _DIRECTORY_FLAGS, dir_fd=staging_fd)
            except OSError:
                _cleanup_partial_creation(root_fd, staging_name, locals())
                setup_failed = True
            if setup_failed:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)
            return cls(
                root_fd=root_fd,
                bundle_id=bundle_id,
                staging_name=staging_name,
                staging_fd=staging_fd,
                blob_fd=blob_fd,
                owns_root_fd=owns_root_fd,
            )
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)

    async def write(
        self,
        artifact: RunArtifact,
        blobs: SanitizedBlobSource,
        limits: ArtifactLimits,
    ) -> None:
        """Write all authenticated content and sync it before the completion marker."""
        results: bytes | None = None
        failure: ArtifactStoreErrorCode | None = None
        try:
            results = encode_run_artifact(
                artifact,
                maximum_bytes=limits.maximum_manifest_bytes.value,
            )
        except ArtifactCodecError as error:
            failure = (
                ArtifactStoreErrorCode.BUDGET_EXCEEDED
                if error.code.value == "budget-exceeded"
                else ArtifactStoreErrorCode.INVALID_ARTIFACT
            )
        if failure is not None or results is None:
            raise LocalArtifactStoreError(failure or ArtifactStoreErrorCode.INVALID_ARTIFACT)
        results_digest = sha256(results).hexdigest()
        await _run_blocking(partial(_write_file, self._staging_fd, "results.json", results))
        self._created_files.append("results.json")
        total = 0
        for blob in artifact.blobs:
            if blob.byte_count > limits.maximum_blob_bytes.value:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            total += blob.byte_count
            if total > limits.maximum_total_blob_bytes.value:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            await self._write_blob(blob, blobs.read(blob))
        await _run_blocking(partial(os.fsync, self._blob_fd))
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
        await _run_blocking(partial(_write_file, self._staging_fd, "manifest.json", manifest))
        self._created_files.append("manifest.json")
        await _run_blocking(partial(os.fsync, self._staging_fd))
        marker = f"{sha256(manifest).hexdigest()}\n".encode("ascii")
        await _run_blocking(partial(_write_file, self._staging_fd, "COMPLETE", marker))
        self._created_files.append("COMPLETE")
        await _run_blocking(partial(os.fsync, self._staging_fd))

    def claim_root_ownership(self) -> None:
        """Accept the root descriptor after the creating coroutine claims this result."""
        self._owns_root_fd = True

    async def _write_blob(
        self,
        blob: ArtifactBlob,
        chunks: AsyncIterable[bytes],
    ) -> None:
        """Stream one blob with exact size and digest verification."""
        name = blob.sha256
        descriptor = -1
        with suppress(OSError):
            descriptor = os.open(name, _FILE_FLAGS, 0o600, dir_fd=self._blob_fd)
        if descriptor < 0:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)
        self._created_blob_names.append(name)
        digest = sha256()
        size = 0
        iterator = chunks.__aiter__()
        try:
            async for chunk in iterator:
                if type(chunk) is not bytes:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
                size += len(chunk)
                if size > blob.byte_count:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
                digest.update(chunk)
                await _run_blocking(partial(_write_all, descriptor, chunk))
            if size != blob.byte_count or digest.hexdigest() != blob.sha256:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
            await _run_blocking(partial(os.fsync, descriptor))
        finally:
            try:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()
            finally:
                os.close(descriptor)

    def publish(self) -> None:
        """Atomically publish without overwrite, then durably sync the root directory."""
        failure: ArtifactStoreErrorCode | None = None
        try:
            _atomic_rename_noreplace(
                self._root_fd,
                self._staging_name,
                self._bundle_id,
            )
        except FileExistsError:
            failure = ArtifactStoreErrorCode.ALREADY_EXISTS
        except OSError:
            failure = ArtifactStoreErrorCode.IO_FAILED
        if failure is not None:
            raise LocalArtifactStoreError(failure)
        self._published = True
        sync_failed = False
        try:
            os.fsync(self._root_fd)
        except OSError:
            sync_failed = True
        if sync_failed:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.COMMIT_UNCERTAIN)

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
        self._closed = True
        with suppress(OSError):
            os.close(self._blob_fd)
        with suppress(OSError):
            os.close(self._staging_fd)
        if self._owns_root_fd:
            with suppress(OSError):
                os.close(self._root_fd)


def open_local_artifact_bundle(
    root: Path,
    bundle_id: str,
    *,
    limits: ArtifactLimits,
) -> LocalArtifactBundle:
    """Open one bundle while severing all unsafe exception provenance."""
    try:
        return _open_local_artifact_bundle(root, bundle_id, limits=limits)
    except LocalArtifactStoreError as error:
        failure = error.code
    raise LocalArtifactStoreError(failure)


def list_local_artifact_bundle_ids(
    root: Path,
    *,
    maximum_entries: int,
) -> tuple[str, ...]:
    """List bounded safe bundle identifiers beneath one pinned validated root.

    Every directory entry consumes the caller-owned budget, including names that cannot be
    bundle identifiers. This keeps an attacker-controlled archive directory from hiding
    unbounded scanning work behind ignored names. The returned identifiers are only lexical
    candidates; callers must authenticate each bundle before trusting its contents.
    """
    try:
        return _list_local_artifact_bundle_ids(root, maximum_entries=maximum_entries)
    except LocalArtifactStoreError as error:
        failure = error.code
    raise LocalArtifactStoreError(failure)


def trash_local_artifact_bundle(root: Path, bundle_id: str) -> None:
    """Move one bundle into the private recoverable trash namespace."""
    _move_local_artifact_bundle(root, bundle_id, restore=False)


def restore_local_artifact_bundle(root: Path, bundle_id: str) -> None:
    """Restore one recoverably trashed bundle to the active archive."""
    _move_local_artifact_bundle(root, bundle_id, restore=True)


def _move_local_artifact_bundle(root: Path, bundle_id: str, *, restore: bool) -> None:
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or type(bundle_id) is not str
        or _BUNDLE_ID.fullmatch(bundle_id) is None
    ):
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
    root_fd = -1
    trash_fd = -1
    source_fd = -1
    failure: ArtifactStoreErrorCode | None = None
    try:
        root_fd = os.open(root, _DIRECTORY_FLAGS)
        _validate_root_fd(root_fd)
        if not restore:
            with suppress(FileExistsError):
                os.mkdir(".trash", mode=0o700, dir_fd=root_fd)
        trash_fd = os.open(".trash", _DIRECTORY_FLAGS, dir_fd=root_fd)
        _validate_bundle_fd(trash_fd)
        source_parent = trash_fd if restore else root_fd
        destination_parent = root_fd if restore else trash_fd
        source_fd = os.open(bundle_id, _DIRECTORY_FLAGS, dir_fd=source_parent)
        _validate_bundle_fd(source_fd)
        _atomic_rename_between_noreplace(
            source_parent,
            bundle_id,
            destination_parent,
            bundle_id,
        )
        os.fsync(source_parent)
        os.fsync(destination_parent)
    except LocalArtifactStoreError as error:
        failure = error.code
    except OSError:
        failure = ArtifactStoreErrorCode.IO_FAILED
    finally:
        for descriptor in (source_fd, trash_fd, root_fd):
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
    if failure is not None:
        raise LocalArtifactStoreError(failure)


def _list_local_artifact_bundle_ids(  # noqa: C901
    root: Path,
    *,
    maximum_entries: int,
) -> tuple[str, ...]:
    """Enumerate candidate names without following or re-resolving the archive root."""
    if not isinstance(root, Path) or not root.is_absolute():
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
    if type(maximum_entries) is not int or maximum_entries < 1:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
    root_fd = -1
    failure: ArtifactStoreErrorCode | None = None
    try:
        root_fd = os.open(root, _DIRECTORY_FLAGS)
        _validate_root_fd(root_fd)
        candidates: list[str] = []
        scanned = 0
        with os.scandir(root_fd) as entries:
            for entry in entries:
                if entry.name == ".trash":
                    continue
                scanned += 1
                if scanned > maximum_entries:
                    raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
                name = entry.name
                if type(name) is str and _BUNDLE_ID.fullmatch(name) is not None:
                    candidates.append(name)
        return tuple(sorted(candidates))
    except LocalArtifactStoreError as error:
        failure = error.code
    except OSError:
        failure = ArtifactStoreErrorCode.IO_FAILED
    finally:
        if root_fd >= 0:
            with suppress(OSError):
                os.close(root_fd)
    if failure is not None:
        raise LocalArtifactStoreError(failure)
    raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)  # pragma: no cover


def _open_local_artifact_bundle(  # noqa: C901
    root: Path,
    bundle_id: str,
    *,
    limits: ArtifactLimits,
) -> LocalArtifactBundle:
    """Open authenticated metadata and a pinned source for its declared sanitized blobs."""
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or _BUNDLE_ID.fullmatch(bundle_id) is None
        or type(limits) is not ArtifactLimits
    ):
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
    root_fd = -1
    bundle_fd = -1
    blob_fd = -1
    failure: ArtifactStoreErrorCode | None = None
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
        manifest = _decode_bundle_manifest(manifest_bytes)
        if manifest.artifact_id != bundle_id:
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
        _validate_bundle_fd(blob_fd)
        total = 0
        for blob in artifact.blobs:
            if blob.byte_count > limits.maximum_blob_bytes.value:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
            total += blob.byte_count
            if total > limits.maximum_total_blob_bytes.value:
                raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        opened = LocalArtifactBundle(artifact, blob_fd)
        blob_fd = -1
        return opened
    except LocalArtifactStoreError as error:
        failure = error.code
    except ArtifactCodecError as error:
        failure = (
            ArtifactStoreErrorCode.UNSUPPORTED_VERSION
            if error.code is ArtifactCodecErrorCode.UNSUPPORTED_VERSION
            else ArtifactStoreErrorCode.INVALID_ARTIFACT
        )
    except (OSError, msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError):
        failure = ArtifactStoreErrorCode.IO_FAILED
    finally:
        if blob_fd >= 0:
            with suppress(OSError):
                os.close(blob_fd)
        if bundle_fd >= 0:
            with suppress(OSError):
                os.close(bundle_fd)
        if root_fd >= 0:
            with suppress(OSError):
                os.close(root_fd)
    raise LocalArtifactStoreError(failure or ArtifactStoreErrorCode.IO_FAILED)


def _decode_bundle_manifest(payload: bytes) -> _BundleManifest:
    """Decode one manifest while severing all unsafe parser provenance."""
    try:
        return _decode_bundle_manifest_inner(payload)
    except LocalArtifactStoreError as error:
        failure = error.code
    raise LocalArtifactStoreError(failure)


def _decode_bundle_manifest_inner(payload: bytes) -> _BundleManifest:
    """Classify authenticated version incompatibility before strict shape validation."""
    untyped: object | None = None
    invalid = False
    try:
        untyped = msgspec.json.decode(payload)
    except msgspec.DecodeError:
        invalid = True
    if invalid:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.INVALID_ARTIFACT)
    if type(untyped) is not dict:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.INVALID_ARTIFACT)
    document = cast("dict[object, object]", untyped)
    bundle_version = document.get("api_version")
    if type(bundle_version) is not str:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.INVALID_ARTIFACT)
    if bundle_version != _BUNDLE_API_VERSION:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.UNSUPPORTED_VERSION)
    artifact_version = document.get("artifact_api_version")
    if type(artifact_version) is not str:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.INVALID_ARTIFACT)
    if artifact_version != ARTIFACT_API_VERSION:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.UNSUPPORTED_VERSION)
    converted: _BundleManifest | None = None
    with suppress(msgspec.ValidationError, TypeError, ValueError):
        converted = msgspec.convert(untyped, type=_BundleManifest)
    if converted is None:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.INVALID_ARTIFACT)
    return converted


def read_local_artifact_bundle(
    root: Path,
    bundle_id: str,
    *,
    limits: ArtifactLimits,
) -> RunArtifact:
    """Read and authenticate one complete bundle without retaining blob bytes in memory."""
    try:
        with open_local_artifact_bundle(root, bundle_id, limits=limits) as bundle:
            for blob in bundle.artifact.blobs:
                descriptor = _open_read_file(
                    bundle._blob_fd,
                    blob.sha256,
                    maximum=blob.byte_count,
                )
                try:
                    _verify_blob_descriptor(descriptor, blob)
                finally:
                    with suppress(OSError):
                        os.close(descriptor)
            return bundle.artifact
    except LocalArtifactStoreError:
        raise
    except OSError:
        pass
    raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)


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
    descriptor = _open_read_file(directory_fd, name, maximum=maximum)
    try:
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


def _open_read_file(directory_fd: int, name: str, *, maximum: int) -> int:
    if type(maximum) is not int or maximum < 0:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
        if metadata.st_size > maximum:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _verify_blob_descriptor(descriptor: int, blob: ArtifactBlob) -> None:
    digest = sha256()
    size = 0
    while chunk := os.read(descriptor, 65_536):
        size += len(chunk)
        if size > blob.byte_count:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)
        digest.update(chunk)
    if size != blob.byte_count or digest.hexdigest() != blob.sha256:
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.DIGEST_MISMATCH)


async def _run_blocking[ResultT](
    operation: Callable[[], ResultT],
    *,
    dispose_cancelled_result: Callable[[ResultT], None] | None = None,
) -> ResultT:
    """Run one descriptor operation without blocking or racing cancellation cleanup."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _settle_after_cancellation(task)
        try:
            result = task.result()
        except BaseException as error:  # noqa: BLE001 - cancellation remains authoritative
            del error
        else:
            if dispose_cancelled_result is not None:
                await _dispose_after_cancellation(dispose_cancelled_result, result)
        raise


async def _settle_after_cancellation[ResultT](task: asyncio.Task[ResultT]) -> None:
    """Wait through repeated cancellation until one blocking worker has settled."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:  # noqa: BLE001 - original cancellation remains authoritative
            break


async def _dispose_after_cancellation[ResultT](
    dispose: Callable[[ResultT], None],
    result: ResultT,
) -> None:
    """Dispose an unclaimed worker result off-loop despite repeated cancellation."""
    disposal = asyncio.create_task(asyncio.to_thread(dispose, result))
    await _settle_after_cancellation(disposal)
    try:
        disposal.result()
    except BaseException as error:  # noqa: BLE001 - original cancellation remains authoritative
        del error


def _close_descriptor(descriptor: int) -> None:
    """Release one successfully opened descriptor whose ownership was never transferred."""
    with suppress(OSError):
        os.close(descriptor)


def _dispose_bundle_transaction(transaction: _BundleTransaction) -> None:
    """Abort and close one transaction whose ownership was never transferred."""
    try:
        transaction.abort()
    finally:
        transaction.close()


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


def _valid_persistence_inputs(artifact: object, limits: object) -> bool:
    """Retain runtime guards without weakening the sink's static port signature."""
    return type(artifact) is RunArtifact and type(limits) is ArtifactLimits


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
    if type(blob_fd) is int and blob_fd >= 0:
        with suppress(OSError):
            os.close(blob_fd)
    if type(staging_fd) is int and staging_fd >= 0:
        with suppress(OSError):
            os.rmdir("blobs", dir_fd=staging_fd)
        with suppress(OSError):
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
    _atomic_rename_between_noreplace(root_fd, source, root_fd, destination)


def _atomic_rename_between_noreplace(
    source_fd: int,
    source: str,
    destination_fd: int,
    destination: str,
) -> None:
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
        result = function(
            source_fd,
            source_bytes,
            destination_fd,
            destination_bytes,
            0x00000004,
        )
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
        result = function(
            source_fd,
            source_bytes,
            destination_fd,
            destination_bytes,
            0x00000001,
        )
    else:  # pragma: no cover - constructor rejects unsupported platforms
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM)
    if result != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise FileExistsError(number, os.strerror(number), destination)
        raise OSError(number, os.strerror(number), destination)
