"""Failure injection and tamper tests for the POSIX local artifact bundle store."""

from __future__ import annotations

import asyncio
import ctypes
import errno
import os
import stat
import sys
import traceback
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import msgspec
import pytest

from comparo.adapters.artifacts import local as local_module
from comparo.adapters.artifacts.local import LocalArtifactBundleSink
from comparo.adapters.artifacts.local import LocalArtifactStoreError
from comparo.adapters.artifacts.local import open_local_artifact_bundle
from comparo.adapters.artifacts.local import read_local_artifact_bundle
from comparo.core.artifacts.codec import ArtifactCodecError
from comparo.core.artifacts.codec import ArtifactCodecErrorCode
from comparo.core.artifacts.codec import encode_run_artifact
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.ports import ArtifactPersistenceOutcome
from comparo.core.artifacts.ports import ArtifactPersistenceStatus
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.artifacts.ports import SanitizedBlobSource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from tests.adapters.artifacts.test_local_store import _secure_root
from tests.adapters.artifacts.test_local_store import _stored_artifact
from tests.core.artifacts.test_capture_compiler import _limits


def _with_limits(
    *,
    blobs: int | None = None,
    per_blob: int | None = None,
    total: int | None = None,
    manifest: int | None = None,
) -> ArtifactLimits:
    limits = _limits()
    return msgspec.structs.replace(
        limits,
        maximum_blobs=(limits.maximum_blobs if blobs is None else PositiveCount(value=blobs)),
        maximum_blob_bytes=(
            limits.maximum_blob_bytes if per_blob is None else ByteCount(value=per_blob)
        ),
        maximum_total_blob_bytes=(
            limits.maximum_total_blob_bytes if total is None else ByteCount(value=total)
        ),
        maximum_manifest_bytes=(
            limits.maximum_manifest_bytes if manifest is None else ByteCount(value=manifest)
        ),
    )


def _failure_code(outcome: ArtifactPersistenceOutcome) -> ArtifactStoreErrorCode:
    failure = outcome.failure
    assert failure is not None
    return failure.code


def _persisted(root: Path) -> tuple[RunArtifact, Path]:
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)
    outcome = asyncio.run(sink.persist(artifact, blobs, _limits()))
    sink.close()
    assert outcome.status is ArtifactPersistenceStatus.PERSISTED
    return artifact, root / artifact.metadata.artifact_id


async def _collect(source: SanitizedBlobSource, blob: ArtifactBlob) -> bytes:
    chunks = [chunk async for chunk in source.read(blob)]
    return b"".join(chunks)


def _manifest(bundle: Path) -> dict[str, object]:
    value = msgspec.json.decode((bundle / "manifest.json").read_bytes())
    assert type(value) is dict
    return cast("dict[str, object]", value)


def _write_manifest(bundle: Path, value: dict[str, object]) -> None:
    (bundle / "manifest.json").write_bytes(msgspec.json.encode(value, order="deterministic"))


def _replace_results(bundle: Path, artifact: RunArtifact | bytes) -> None:
    value = (
        artifact
        if type(artifact) is bytes
        else encode_run_artifact(cast("RunArtifact", artifact), maximum_bytes=1_000_000)
    )
    digest = sha256(value).hexdigest()
    (bundle / "results.json").write_bytes(value)
    manifest = _manifest(bundle)
    manifest["results_sha256"] = digest
    manifest["results_byte_count"] = len(value)
    _write_manifest(bundle, manifest)
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    (bundle / "COMPLETE").write_bytes(f"{sha256(manifest_bytes).hexdigest()}\n".encode())


class _NonBytesSource:
    def read(self, blob: ArtifactBlob) -> AsyncIterator[bytes]:
        del blob

        async def chunks() -> AsyncIterator[bytes]:
            yield cast("bytes", bytearray(b"no"))

        return chunks()


class _ExactSource:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def read(self, blob: ArtifactBlob) -> AsyncIterator[bytes]:
        del blob

        async def chunks() -> AsyncIterator[bytes]:
            yield self._value

        return chunks()


class _PausingSource:
    def __init__(self, source: SanitizedBlobSource) -> None:
        self._source = source
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def read(self, blob: ArtifactBlob) -> AsyncIterator[bytes]:
        source = self._source

        async def chunks() -> AsyncIterator[bytes]:
            self.entered.set()
            await self.release.wait()
            async for chunk in source.read(blob):
                yield chunk

        return chunks()


def test_constructor_context_manager_and_error_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="error code"):
        LocalArtifactStoreError(cast("ArtifactStoreErrorCode", object()))
    for root in (Path("relative"), cast("Path", object()), tmp_path / "missing"):
        with pytest.raises(LocalArtifactStoreError) as failure:
            LocalArtifactBundleSink(root)
        assert failure.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE

    root = _secure_root(tmp_path / "root")
    monkeypatch.setattr(local_module, "_atomic_rename_supported", lambda: False)
    with pytest.raises(LocalArtifactStoreError) as unsupported:
        LocalArtifactBundleSink(root)
    assert unsupported.value.code is ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM
    monkeypatch.undo()

    with LocalArtifactBundleSink(root) as sink:
        assert sink._closed is False
    sink.close()
    with pytest.raises(LocalArtifactStoreError) as closed:
        sink.__enter__()
    assert closed.value.code is ArtifactStoreErrorCode.CLOSED


def test_persist_preflight_and_transaction_free_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "root")
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)
    invalid_artifact = asyncio.run(sink.persist(cast("RunArtifact", object()), blobs, _limits()))
    assert _failure_code(invalid_artifact) is ArtifactStoreErrorCode.INVALID_ARTIFACT
    invalid_limits = asyncio.run(sink.persist(artifact, blobs, cast("ArtifactLimits", object())))
    assert _failure_code(invalid_limits) is ArtifactStoreErrorCode.INVALID_ARTIFACT
    limited = asyncio.run(sink.persist(artifact, blobs, _with_limits(blobs=1)))
    assert _failure_code(limited) is ArtifactStoreErrorCode.BUDGET_EXCEEDED

    def fail_local(
        _root_fd: int,
        _bundle_id: str,
        *,
        owns_root_fd: bool,
    ) -> local_module._BundleTransaction:
        del owns_root_fd
        raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED)

    monkeypatch.setattr(local_module._BundleTransaction, "create", fail_local)
    local_failure = asyncio.run(sink.persist(artifact, blobs, _limits()))
    assert _failure_code(local_failure) is ArtifactStoreErrorCode.IO_FAILED

    def fail_unexpected(
        _root_fd: int,
        _bundle_id: str,
        *,
        owns_root_fd: bool,
    ) -> local_module._BundleTransaction:
        del owns_root_fd
        raise RuntimeError("private-path-secret")

    monkeypatch.setattr(local_module._BundleTransaction, "create", fail_unexpected)
    unexpected = asyncio.run(sink.persist(artifact, blobs, _limits()))
    assert _failure_code(unexpected) is ArtifactStoreErrorCode.IO_FAILED

    def cancel(
        _root_fd: int,
        _bundle_id: str,
        *,
        owns_root_fd: bool,
    ) -> local_module._BundleTransaction:
        del owns_root_fd
        raise asyncio.CancelledError

    monkeypatch.setattr(local_module._BundleTransaction, "create", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sink.persist(artifact, blobs, _limits()))
    sink.close()
    assert not tuple(root.iterdir())


def test_close_during_persist_cannot_invalidate_the_transaction_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _secure_root(tmp_path / "root")
        artifact, blobs = await _stored_artifact()
        source = _PausingSource(blobs)
        sink = LocalArtifactBundleSink(root)
        task = asyncio.create_task(sink.persist(artifact, source, _limits()))
        await source.entered.wait()
        sink.close()
        source.release.set()
        outcome = await task

        assert outcome.status is ArtifactPersistenceStatus.PERSISTED
        assert read_local_artifact_bundle(root, "artifact-0", limits=_limits()) == artifact
        assert not tuple(root.glob(".staging-*"))

    asyncio.run(scenario())


def test_public_store_errors_do_not_chain_path_bearing_causes(tmp_path: Path) -> None:
    missing = tmp_path / "sentinel-private-root" / "missing"
    with pytest.raises(LocalArtifactStoreError) as constructor:
        LocalArtifactBundleSink(missing)
    assert str(missing) not in "".join(traceback.format_exception(constructor.value))

    with pytest.raises(LocalArtifactStoreError) as reader:
        read_local_artifact_bundle(missing, "artifact-0", limits=_limits())
    assert str(missing) not in "".join(traceback.format_exception(reader.value))


def test_open_bundle_exposes_only_declared_verified_blobs_for_its_lifetime(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path / "root")
    artifact, _bundle_path = _persisted(root)

    with open_local_artifact_bundle(root, "artifact-0", limits=_limits()) as bundle:
        assert bundle.artifact == artifact
        values = {asyncio.run(_collect(bundle, blob)) for blob in artifact.blobs}
        assert values == {b"left", b"right"}
        unknown = msgspec.structs.replace(artifact.blobs[0], media_type="application/json")
        with pytest.raises(LocalArtifactStoreError) as undeclared:
            bundle.read(unknown)
        assert undeclared.value.code is ArtifactStoreErrorCode.INVALID_ARTIFACT

    bundle.close()
    with pytest.raises(LocalArtifactStoreError) as closed:
        asyncio.run(_collect(bundle, artifact.blobs[0]))
    assert closed.value.code is ArtifactStoreErrorCode.CLOSED
    with pytest.raises(LocalArtifactStoreError) as reenter:
        bundle.__enter__()
    assert reenter.value.code is ArtifactStoreErrorCode.CLOSED


def test_open_bundle_authenticates_a_blob_before_yielding_any_bytes(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "root")
    artifact, bundle_path = _persisted(root)
    blob = artifact.blobs[0]
    (bundle_path / "blobs" / blob.sha256).write_bytes(b"xxxx")

    with (
        open_local_artifact_bundle(root, "artifact-0", limits=_limits()) as bundle,
        pytest.raises(LocalArtifactStoreError) as tampered,
    ):
        asyncio.run(_collect(bundle, blob))
    assert tampered.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH


def test_open_bundle_maps_stream_io_and_detects_post_stat_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "root")
    artifact, _bundle_path = _persisted(root)
    blob = artifact.blobs[0]
    with open_local_artifact_bundle(root, "artifact-0", limits=_limits()) as bundle:
        monkeypatch.setattr(
            os,
            "lseek",
            lambda *_args: (_ for _ in ()).throw(OSError()),
        )
        with pytest.raises(LocalArtifactStoreError) as stream_failure:
            asyncio.run(_collect(bundle, blob))
    assert stream_failure.value.code is ArtifactStoreErrorCode.IO_FAILED
    monkeypatch.undo()

    value = tmp_path / "grown"
    value.write_bytes(b"overlong")
    descriptor = os.open(value, os.O_RDONLY)
    try:
        with pytest.raises(LocalArtifactStoreError) as growth:
            local_module._verify_blob_descriptor(
                descriptor,
                ArtifactBlob(sha256="0" * 64, byte_count=1),
            )
        assert growth.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH
    finally:
        os.close(descriptor)


def test_transaction_creation_collision_and_partial_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "root")
    root_fd = os.open(root, local_module._DIRECTORY_FLAGS)
    try:
        monkeypatch.setattr(local_module, "token_hex", lambda _count: "fixed")
        collision = root / ".staging-artifact-0-fixed"
        collision.mkdir(mode=0o700)
        with pytest.raises(LocalArtifactStoreError) as exhausted:
            local_module._BundleTransaction.create(root_fd, "artifact-0")
        assert exhausted.value.code is ArtifactStoreErrorCode.IO_FAILED
        collision.rmdir()

        original_mkdir = os.mkdir

        def fail_staging(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> None:
            del path, mode, dir_fd
            raise PermissionError

        monkeypatch.setattr(os, "mkdir", fail_staging)
        with pytest.raises(LocalArtifactStoreError) as mkdir_failure:
            local_module._BundleTransaction.create(root_fd, "artifact-0")
        assert mkdir_failure.value.code is ArtifactStoreErrorCode.IO_FAILED
        monkeypatch.setattr(os, "mkdir", original_mkdir)

        original_open = os.open

        def fail_staging_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if str(path).startswith(".staging-") and dir_fd == root_fd:
                raise OSError
            return original_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", fail_staging_open)
        with pytest.raises(LocalArtifactStoreError):
            local_module._BundleTransaction.create(root_fd, "artifact-0")
        monkeypatch.setattr(os, "open", original_open)
        assert not tuple(root.iterdir())

        def fail_blob_mkdir(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> None:
            if path == "blobs":
                raise OSError
            original_mkdir(path, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "mkdir", fail_blob_mkdir)
        with pytest.raises(LocalArtifactStoreError):
            local_module._BundleTransaction.create(root_fd, "artifact-0")
        assert not tuple(root.iterdir())
    finally:
        os.close(root_fd)


def test_persist_maps_codec_manifest_blob_and_aggregate_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, blobs = asyncio.run(_stored_artifact())

    async def run(
        root_name: str,
        limits: ArtifactLimits | None = None,
    ) -> ArtifactPersistenceOutcome:
        root = _secure_root(tmp_path / root_name)
        sink = LocalArtifactBundleSink(root)
        try:
            return await sink.persist(artifact, blobs, _limits() if limits is None else limits)
        finally:
            sink.close()

    too_small = asyncio.run(run("encoded-budget", _with_limits(manifest=1)))
    assert _failure_code(too_small) is ArtifactStoreErrorCode.BUDGET_EXCEEDED
    per_blob = asyncio.run(run("per-blob", _with_limits(per_blob=3)))
    assert _failure_code(per_blob) is ArtifactStoreErrorCode.BUDGET_EXCEEDED
    aggregate = asyncio.run(run("aggregate", _with_limits(per_blob=5, total=8)))
    assert _failure_code(aggregate) is ArtifactStoreErrorCode.BUDGET_EXCEEDED

    def invalid_codec(_artifact: RunArtifact, *, maximum_bytes: int) -> bytes:
        del maximum_bytes
        raise ArtifactCodecError(ArtifactCodecErrorCode.INVALID_INPUT)

    monkeypatch.setattr(local_module, "encode_run_artifact", invalid_codec)
    invalid = asyncio.run(run("invalid-codec"))
    assert _failure_code(invalid) is ArtifactStoreErrorCode.INVALID_ARTIFACT

    monkeypatch.setattr(local_module, "encode_run_artifact", lambda *_args, **_kwargs: b"x")
    physical_manifest = asyncio.run(run("physical-manifest", _with_limits(manifest=2)))
    assert _failure_code(physical_manifest) is ArtifactStoreErrorCode.BUDGET_EXCEEDED


def test_persist_rejects_non_bytes_short_overlong_and_wrong_digest_sources(
    tmp_path: Path,
) -> None:
    artifact, _blobs = asyncio.run(_stored_artifact())
    sources: tuple[tuple[object, ArtifactStoreErrorCode], ...] = (
        (_NonBytesSource(), ArtifactStoreErrorCode.DIGEST_MISMATCH),
        (_ExactSource(b""), ArtifactStoreErrorCode.DIGEST_MISMATCH),
        (_ExactSource(b"too-long"), ArtifactStoreErrorCode.DIGEST_MISMATCH),
        (_ExactSource(b"xxxx"), ArtifactStoreErrorCode.DIGEST_MISMATCH),
    )
    for index, (source, expected) in enumerate(sources):
        root = _secure_root(tmp_path / f"root-{index}")
        sink = LocalArtifactBundleSink(root)
        outcome = asyncio.run(
            sink.persist(artifact, cast("SanitizedBlobSource", source), _limits())
        )
        sink.close()
        assert _failure_code(outcome) is expected
        assert not tuple(root.iterdir())


def test_persist_maps_transaction_exception_with_created_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "root")
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)

    async def fail_write(
        self: local_module._BundleTransaction,
        artifact_value: RunArtifact,
        blobs_value: object,
        limits_value: ArtifactLimits,
    ) -> None:
        del self, artifact_value, blobs_value, limits_value
        raise RuntimeError("never expose this")

    monkeypatch.setattr(local_module._BundleTransaction, "write", fail_write)
    outcome = asyncio.run(sink.persist(artifact, blobs, _limits()))
    sink.close()
    assert _failure_code(outcome) is ArtifactStoreErrorCode.IO_FAILED
    assert not tuple(root.iterdir())


def test_blob_open_publish_and_commit_uncertain_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, blobs = asyncio.run(_stored_artifact())

    blob_root = _secure_root(tmp_path / "blob-open")
    blob_sink = LocalArtifactBundleSink(blob_root)
    original_open = os.open

    def fail_digest_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if type(path) is str and len(path) == 64:
            raise OSError
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_digest_open)
    failed_blob = asyncio.run(blob_sink.persist(artifact, blobs, _limits()))
    blob_sink.close()
    assert _failure_code(failed_blob) is ArtifactStoreErrorCode.IO_FAILED
    monkeypatch.setattr(os, "open", original_open)

    rename_root = _secure_root(tmp_path / "rename")
    rename_sink = LocalArtifactBundleSink(rename_root)
    monkeypatch.setattr(
        local_module,
        "_atomic_rename_noreplace",
        lambda *_args: (_ for _ in ()).throw(OSError()),
    )
    rename_failure = asyncio.run(rename_sink.persist(artifact, blobs, _limits()))
    rename_sink.close()
    assert _failure_code(rename_failure) is ArtifactStoreErrorCode.IO_FAILED
    assert not tuple(rename_root.iterdir())

    uncertain_root = _secure_root(tmp_path / "uncertain")
    uncertain_sink = LocalArtifactBundleSink(uncertain_root)
    original_fsync = os.fsync
    root_metadata = os.fstat(uncertain_sink._root_fd)

    def fail_root_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == (root_metadata.st_dev, root_metadata.st_ino):
            raise OSError
        original_fsync(descriptor)

    monkeypatch.undo()
    monkeypatch.setattr(os, "fsync", fail_root_fsync)
    uncertain = asyncio.run(uncertain_sink.persist(artifact, blobs, _limits()))
    uncertain_sink.close()
    assert uncertain.status is ArtifactPersistenceStatus.COMMIT_UNCERTAIN
    assert _failure_code(uncertain) is ArtifactStoreErrorCode.COMMIT_UNCERTAIN
    assert (uncertain_root / "artifact-0" / "COMPLETE").is_file()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_version", "unknown"),
        ("artifact_id", "different"),
        ("results_sha256", "0" * 64),
    ],
)
def test_reader_rejects_each_manifest_authentication_mismatch(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root = _secure_root(tmp_path / "root")
    _artifact_value, bundle = _persisted(root)
    manifest = _manifest(bundle)
    manifest[field] = value
    _write_manifest(bundle, manifest)
    with pytest.raises(LocalArtifactStoreError) as failure:
        read_local_artifact_bundle(root, "artifact-0", limits=_limits())
    assert failure.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH


def test_reader_rejects_a_resealed_unsupported_artifact_manifest_version(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path / "root")
    _artifact_value, bundle = _persisted(root)
    manifest = _manifest(bundle)
    manifest["artifact_api_version"] = "comparo.artifact/v2"
    _write_manifest(bundle, manifest)
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    (bundle / "COMPLETE").write_bytes(f"{sha256(manifest_bytes).hexdigest()}\n".encode())

    with pytest.raises(LocalArtifactStoreError) as failure:
        read_local_artifact_bundle(root, "artifact-0", limits=_limits())
    assert failure.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH


def test_reader_rejects_results_shape_identity_and_blob_catalog_tampering(tmp_path: Path) -> None:
    malformed_root = _secure_root(tmp_path / "malformed")
    _artifact_value, malformed_bundle = _persisted(malformed_root)
    _replace_results(malformed_bundle, b"not-json")
    with pytest.raises(LocalArtifactStoreError) as malformed:
        read_local_artifact_bundle(malformed_root, "artifact-0", limits=_limits())
    assert malformed.value.code is ArtifactStoreErrorCode.INVALID_ARTIFACT

    identity_root = _secure_root(tmp_path / "identity")
    artifact, identity_bundle = _persisted(identity_root)
    metadata = msgspec.structs.replace(artifact.metadata, artifact_id="different")
    _replace_results(identity_bundle, msgspec.structs.replace(artifact, metadata=metadata))
    with pytest.raises(LocalArtifactStoreError) as identity:
        read_local_artifact_bundle(identity_root, "artifact-0", limits=_limits())
    assert identity.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH

    catalog_root = _secure_root(tmp_path / "catalog")
    _catalog_artifact, catalog_bundle = _persisted(catalog_root)
    catalog_manifest = _manifest(catalog_bundle)
    catalog_manifest["blobs"] = []
    _write_manifest(catalog_bundle, catalog_manifest)
    with pytest.raises(LocalArtifactStoreError) as catalog:
        read_local_artifact_bundle(catalog_root, "artifact-0", limits=_limits())
    assert catalog.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH


def test_reader_enforces_blob_budgets_and_exact_digest(tmp_path: Path) -> None:
    count_root = _secure_root(tmp_path / "count")
    _artifact_value, _bundle = _persisted(count_root)
    with pytest.raises(LocalArtifactStoreError) as count:
        read_local_artifact_bundle(count_root, "artifact-0", limits=_with_limits(blobs=1))
    assert count.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED

    per_root = _secure_root(tmp_path / "per")
    _artifact_value, _bundle = _persisted(per_root)
    with pytest.raises(LocalArtifactStoreError) as per_blob:
        read_local_artifact_bundle(per_root, "artifact-0", limits=_with_limits(per_blob=3))
    assert per_blob.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED

    total_root = _secure_root(tmp_path / "total")
    _artifact_value, _bundle = _persisted(total_root)
    with pytest.raises(LocalArtifactStoreError) as total:
        read_local_artifact_bundle(
            total_root,
            "artifact-0",
            limits=_with_limits(per_blob=5, total=8),
        )
    assert total.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED

    digest_root = _secure_root(tmp_path / "digest")
    digest_artifact, digest_bundle = _persisted(digest_root)
    blob = digest_artifact.blobs[0]
    (digest_bundle / "blobs" / blob.sha256).write_bytes(b"xxxx")
    with pytest.raises(LocalArtifactStoreError) as digest:
        read_local_artifact_bundle(digest_root, "artifact-0", limits=_limits())
    assert digest.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH


def test_reader_input_manifest_file_and_bundle_mode_failures(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "root")
    _artifact_value, bundle = _persisted(root)
    invalid_inputs = (
        (cast("Path", object()), "artifact-0", _limits()),
        (Path("relative"), "artifact-0", _limits()),
        (root, "../escape", _limits()),
        (root, "artifact-0", cast("ArtifactLimits", object())),
    )
    for root_value, bundle_id, limits in invalid_inputs:
        with pytest.raises(LocalArtifactStoreError) as invalid:
            read_local_artifact_bundle(root_value, bundle_id, limits=limits)
        assert invalid.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE

    (bundle / "manifest.json").write_bytes(b"{")
    with pytest.raises(LocalArtifactStoreError) as malformed_manifest:
        read_local_artifact_bundle(root, "artifact-0", limits=_limits())
    assert malformed_manifest.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH

    mode_root = _secure_root(tmp_path / "mode")
    _artifact_value, mode_bundle = _persisted(mode_root)
    mode_bundle.chmod(0o750)
    with pytest.raises(LocalArtifactStoreError) as insecure_bundle:
        read_local_artifact_bundle(mode_root, "artifact-0", limits=_limits())
    assert insecure_bundle.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE


def test_low_level_file_helpers_cover_failures_and_short_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "root")
    directory_fd = os.open(root, local_module._DIRECTORY_FLAGS)
    try:
        with pytest.raises(LocalArtifactStoreError) as invalid_maximum:
            local_module._read_file(directory_fd, "missing", maximum=-1)
        assert invalid_maximum.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED

        insecure = root / "insecure"
        insecure.write_bytes(b"x")
        insecure.chmod(0o644)
        with pytest.raises(LocalArtifactStoreError) as unsafe:
            local_module._read_file(directory_fd, "insecure", maximum=1)
        assert unsafe.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE

        linked = root / "linked"
        linked.write_bytes(b"x")
        linked.chmod(0o600)
        os.link(linked, root / "linked-alias")
        with pytest.raises(LocalArtifactStoreError) as hardlink:
            local_module._read_file(directory_fd, "linked", maximum=1)
        assert hardlink.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE

        oversized = root / "oversized"
        oversized.write_bytes(b"xx")
        oversized.chmod(0o600)
        with pytest.raises(LocalArtifactStoreError) as budget:
            local_module._read_file(directory_fd, "oversized", maximum=1)
        assert budget.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED

        original_write_all = local_module._write_all
        monkeypatch.setattr(
            local_module,
            "_write_all",
            lambda *_args: (_ for _ in ()).throw(OSError("injected write failure")),
        )
        with pytest.raises(OSError, match="injected write failure"):
            local_module._write_file(directory_fd, "failed", b"value")
        assert not (root / "failed").exists()
        monkeypatch.setattr(local_module, "_write_all", original_write_all)

        writes = iter((1, 2))
        monkeypatch.setattr(os, "write", lambda _fd, _value: next(writes))
        local_module._write_all(123, b"abc")
        monkeypatch.setattr(os, "write", lambda _fd, _value: 0)
        with pytest.raises(OSError, match="short artifact write"):
            local_module._write_all(123, b"x")
    finally:
        os.close(directory_fd)


def test_read_file_detects_growth_after_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "root")
    value = root / "value"
    value.write_bytes(b"xx")
    value.chmod(0o600)
    directory_fd = os.open(root, local_module._DIRECTORY_FLAGS)
    original_fstat = os.fstat

    def stale_fstat(descriptor: int) -> object:
        metadata = original_fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode):
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_uid=metadata.st_uid,
                st_nlink=metadata.st_nlink,
                st_size=0,
            )
        return metadata

    monkeypatch.setattr(os, "fstat", stale_fstat)
    try:
        with pytest.raises(LocalArtifactStoreError) as growth:
            local_module._read_file(directory_fd, "value", maximum=1)
        assert growth.value.code is ArtifactStoreErrorCode.BUDGET_EXCEEDED
    finally:
        os.close(directory_fd)


@pytest.mark.parametrize(
    ("validator", "mode", "uid"),
    [
        (local_module._validate_root_fd, stat.S_IFREG | 0o600, os.geteuid()),
        (local_module._validate_root_fd, stat.S_IFDIR | 0o700, os.geteuid() + 1),
        (local_module._validate_bundle_fd, stat.S_IFREG | 0o600, os.geteuid()),
        (local_module._validate_bundle_fd, stat.S_IFDIR | 0o700, os.geteuid() + 1),
        (local_module._validate_bundle_fd, stat.S_IFDIR | 0o750, os.geteuid()),
    ],
)
def test_descriptor_validators_reject_wrong_type_owner_and_mode(
    monkeypatch: pytest.MonkeyPatch,
    validator: object,
    mode: int,
    uid: int,
) -> None:
    monkeypatch.setattr(
        os,
        "fstat",
        lambda _descriptor: SimpleNamespace(st_mode=mode, st_uid=uid),
    )
    with pytest.raises(LocalArtifactStoreError) as failure:
        validator(1)  # type: ignore[operator]
    assert failure.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE


def test_abort_close_cleanup_and_failure_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _secure_root(tmp_path / "root")
    root_fd = os.open(root, local_module._DIRECTORY_FLAGS)
    transaction = local_module._BundleTransaction.create(root_fd, "artifact-0")
    transaction._published = True
    transaction.abort()
    transaction.close()
    transaction.close()
    (root / transaction._staging_name / "blobs").rmdir()
    (root / transaction._staging_name).rmdir()

    blob_directory = root / "partial"
    blob_directory.mkdir(mode=0o700)
    staging_fd = os.open(blob_directory, local_module._DIRECTORY_FLAGS)
    nested = blob_directory / "blobs"
    nested.mkdir(mode=0o700)
    blob_fd = os.open(nested, local_module._DIRECTORY_FLAGS)
    local_module._cleanup_partial_creation(
        root_fd,
        "partial",
        {"staging_fd": staging_fd, "blob_fd": blob_fd},
    )
    assert not blob_directory.exists()

    monkeypatch.setattr(
        os,
        "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )
    local_module._best_effort_unlink(root_fd, "absent")
    uncertain = local_module._failure(ArtifactStoreErrorCode.COMMIT_UNCERTAIN)
    assert uncertain.status is ArtifactPersistenceStatus.COMMIT_UNCERTAIN
    os.close(root_fd)


class _FakeFunction:
    def __init__(self, result: int) -> None:
        self.result = result
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *_args: object) -> int:
        return self.result


class _FakeLibrary:
    def __init__(self, *, darwin: int | None = None, linux: int | None = None) -> None:
        if darwin is not None:
            self.renameatx_np = _FakeFunction(darwin)
        if linux is not None:
            self.renameat2 = _FakeFunction(linux)


def test_atomic_support_capability_and_platform_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    assert local_module._platform_name() == sys.platform
    with monkeypatch.context() as context:
        context.delattr(os, "O_NOFOLLOW")
        assert local_module._atomic_rename_supported() is False

    required = {os.mkdir, os.open, os.rename, os.unlink}
    for missing in (os.mkdir, os.open, os.rename, os.unlink):
        monkeypatch.setattr(os, "supports_dir_fd", required - {missing})
        assert local_module._atomic_rename_supported() is False
    monkeypatch.setattr(os, "supports_dir_fd", required)

    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: _FakeLibrary())
    monkeypatch.setattr(local_module, "_platform_name", lambda: "freebsd")
    assert local_module._atomic_rename_supported() is False
    monkeypatch.setattr(local_module, "_platform_name", lambda: "darwin")
    assert local_module._atomic_rename_supported() is False
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _FakeLibrary(darwin=0),
    )
    assert local_module._atomic_rename_supported() is True
    monkeypatch.setattr(local_module, "_platform_name", lambda: "linux")
    assert local_module._atomic_rename_supported() is False
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _FakeLibrary(linux=0),
    )
    assert local_module._atomic_rename_supported() is True


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (0, None),
        (errno.EEXIST, FileExistsError),
        (errno.EIO, OSError),
    ],
)
def test_linux_atomic_noreplace_result_mapping(
    monkeypatch: pytest.MonkeyPatch,
    number: int,
    expected: type[BaseException] | None,
) -> None:
    result = 0 if number == 0 else -1
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _FakeLibrary(linux=result),
    )
    monkeypatch.setattr(ctypes, "get_errno", lambda: number)
    if expected is None:
        local_module._atomic_rename_noreplace(1, "source", "destination")
    else:
        with pytest.raises(expected):
            local_module._atomic_rename_noreplace(1, "source", "destination")


def test_atomic_noreplace_rejects_runtime_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "freebsd")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: _FakeLibrary())
    with pytest.raises(LocalArtifactStoreError) as failure:
        local_module._atomic_rename_noreplace(1, "source", "destination")
    assert failure.value.code is ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM


def test_file_node_type_is_rejected_without_reading(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path / "root")
    directory = root / "directory"
    directory.mkdir(mode=0o700)
    root_fd = os.open(root, local_module._DIRECTORY_FLAGS)
    try:
        with pytest.raises(LocalArtifactStoreError) as failure:
            local_module._read_file(root_fd, "directory", maximum=1)
        assert failure.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE
    finally:
        os.close(root_fd)
