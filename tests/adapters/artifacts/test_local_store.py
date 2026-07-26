"""Adversarial tests for containment-safe atomic local artifact persistence."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import msgspec
import pytest

from comparo.adapters.artifacts.local import LocalArtifactBundleSink
from comparo.adapters.artifacts.local import LocalArtifactStoreError
from comparo.adapters.artifacts.local import read_local_artifact_bundle
from comparo.adapters.artifacts.memory import InMemorySanitizedBlobStore
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.ports import ArtifactPersistenceStatus
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from tests.core.artifacts.test_artifact_contracts import _artifact
from tests.core.artifacts.test_capture_compiler import _limits


async def _stored_artifact() -> tuple[RunArtifact, InMemorySanitizedBlobStore]:
    artifact = _artifact()
    store = InMemorySanitizedBlobStore(_limits())
    request_writer = await store.begin()
    await request_writer.write(b"left")
    request_blob = await request_writer.commit(media_type="text/plain")
    response_writer = await store.begin()
    await response_writer.write(b"right")
    response_blob = await response_writer.commit(media_type="text/plain")

    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    assert exchange.request.body is not None
    assert exchange.response is not None
    assert exchange.response.body is not None
    request = msgspec.structs.replace(
        exchange.request,
        body=msgspec.structs.replace(exchange.request.body, blob=request_blob),
    )
    response = msgspec.structs.replace(
        exchange.response,
        body=msgspec.structs.replace(exchange.response.body, blob=response_blob),
    )
    exchange = msgspec.structs.replace(exchange, request=request, response=response)
    executions = tuple(
        msgspec.structs.replace(
            item,
            attempts=tuple(
                msgspec.structs.replace(
                    item_attempt,
                    exchanges=tuple(
                        msgspec.structs.replace(
                            exchange,
                            exchange_id=item_exchange.exchange_id,
                        )
                        for item_exchange in item_attempt.exchanges
                    ),
                )
                for item_attempt in item.attempts
            ),
        )
        for item in artifact.executions
    )
    return (
        msgspec.structs.replace(
            artifact,
            executions=executions,
            blobs=tuple(sorted((request_blob, response_blob), key=lambda item: item.sha256)),
        ),
        store,
    )


def _secure_root(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def test_bundle_is_atomic_restrictive_authenticated_and_readable(tmp_path: Path) -> None:
    root = _secure_root(tmp_path / "artifacts")
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)

    outcome = asyncio.run(sink.persist(artifact, blobs, _limits()))
    sink.close()

    assert outcome.status is ArtifactPersistenceStatus.PERSISTED
    bundle = root / artifact.metadata.artifact_id
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
    assert stat.S_IMODE((bundle / "blobs").stat().st_mode) == 0o700
    for name in ("results.json", "manifest.json", "COMPLETE"):
        assert stat.S_IMODE((bundle / name).stat().st_mode) == 0o600
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in (bundle / "blobs").iterdir())
    assert not tuple(root.glob(".staging-*"))
    assert read_local_artifact_bundle(root, "artifact-0", limits=_limits()) == artifact


def test_sink_pins_root_inode_across_path_replacement(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    sink = LocalArtifactBundleSink(root)
    moved = tmp_path / "pinned"
    root.rename(moved)
    root.mkdir(mode=0o700)
    artifact, blobs = asyncio.run(_stored_artifact())

    outcome = asyncio.run(sink.persist(artifact, blobs, _limits()))
    sink.close()

    assert outcome.status is ArtifactPersistenceStatus.PERSISTED
    assert (moved / "artifact-0" / "COMPLETE").is_file()
    assert not (root / "artifact-0").exists()


def test_sink_rejects_symlink_and_shared_writable_roots(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    symlink = tmp_path / "link"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(LocalArtifactStoreError) as linked:
        LocalArtifactBundleSink(symlink)
    assert linked.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE

    target.chmod(0o770)
    with pytest.raises(LocalArtifactStoreError) as writable:
        LocalArtifactBundleSink(target)
    assert writable.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE


def test_existing_destination_is_never_overwritten_even_when_symlinked(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = root / "artifact-0"
    destination.symlink_to(outside, target_is_directory=True)
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)

    outcome = asyncio.run(sink.persist(artifact, blobs, _limits()))
    sink.close()

    assert outcome.status is ArtifactPersistenceStatus.FAILED
    assert outcome.failure is not None
    assert outcome.failure.code is ArtifactStoreErrorCode.ALREADY_EXISTS
    assert destination.is_symlink()
    assert not tuple(root.glob(".staging-*"))


class _WrongBlobSource:
    def read(self, blob: ArtifactBlob) -> AsyncIterator[bytes]:
        del blob

        async def chunks() -> AsyncIterator[bytes]:
            yield b"wrong"

        return chunks()


class _CancelledBlobSource:
    def read(self, blob: ArtifactBlob) -> AsyncIterator[bytes]:
        del blob
        return _CancelledIterator()


class _CancelledIterator:
    def __aiter__(self) -> _CancelledIterator:
        return self

    async def __anext__(self) -> bytes:
        raise asyncio.CancelledError


def test_digest_mismatch_and_cancellation_leave_no_visible_bundle(tmp_path: Path) -> None:
    artifact, _blobs = asyncio.run(_stored_artifact())
    mismatch_root = tmp_path / "mismatch"
    mismatch_root.mkdir(mode=0o700)
    mismatch_sink = LocalArtifactBundleSink(mismatch_root)

    outcome = asyncio.run(mismatch_sink.persist(artifact, _WrongBlobSource(), _limits()))
    mismatch_sink.close()

    assert outcome.failure is not None
    assert outcome.failure.code is ArtifactStoreErrorCode.DIGEST_MISMATCH
    assert not tuple(mismatch_root.iterdir())

    cancelled_root = tmp_path / "cancelled"
    cancelled_root.mkdir(mode=0o700)
    cancelled_sink = LocalArtifactBundleSink(cancelled_root)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled_sink.persist(artifact, _CancelledBlobSource(), _limits()))
    cancelled_sink.close()
    assert not tuple(cancelled_root.iterdir())


def test_reader_rejects_tampered_results_and_blob_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)
    assert asyncio.run(sink.persist(artifact, blobs, _limits())).bundle_id == "artifact-0"
    sink.close()
    bundle = root / "artifact-0"

    (bundle / "results.json").write_bytes(b"tampered")
    with pytest.raises(LocalArtifactStoreError) as results:
        read_local_artifact_bundle(root, "artifact-0", limits=_limits())
    assert results.value.code is ArtifactStoreErrorCode.DIGEST_MISMATCH

    second_root = tmp_path / "second"
    second_root.mkdir(mode=0o700)
    second_artifact, second_blobs = asyncio.run(_stored_artifact())
    second_sink = LocalArtifactBundleSink(second_root)
    asyncio.run(second_sink.persist(second_artifact, second_blobs, _limits()))
    second_sink.close()
    blob = second_artifact.blobs[0]
    blob_path = second_root / "artifact-0" / "blobs" / blob.sha256
    blob_path.unlink()
    blob_path.symlink_to(tmp_path / "outside-blob")
    with pytest.raises(LocalArtifactStoreError) as linked:
        read_local_artifact_bundle(second_root, "artifact-0", limits=_limits())
    assert linked.value.code is ArtifactStoreErrorCode.IO_FAILED


def test_invalid_bundle_identity_is_rejected_before_sink_and_closed_sink_does_not_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    artifact, blobs = asyncio.run(_stored_artifact())
    with pytest.raises(ValueError, match="artifact ID must be a stable ASCII token"):
        msgspec.structs.replace(artifact.metadata, artifact_id="../escape")
    sink = LocalArtifactBundleSink(root)

    sink.close()
    closed = asyncio.run(sink.persist(artifact, blobs, _limits()))

    assert closed.failure is not None
    assert closed.failure.code is ArtifactStoreErrorCode.CLOSED
    assert not tuple(root.iterdir())


def test_reader_rejects_insecure_root_and_invalid_limit(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    root.chmod(0o777)
    with pytest.raises(LocalArtifactStoreError) as insecure:
        read_local_artifact_bundle(root, "artifact-0", limits=_limits())
    assert insecure.value.code is ArtifactStoreErrorCode.ROOT_UNSAFE

    root.chmod(0o700)
    with pytest.raises(LocalArtifactStoreError) as missing:
        read_local_artifact_bundle(root, "artifact-0", limits=_limits())
    assert missing.value.code is ArtifactStoreErrorCode.IO_FAILED


def test_file_modes_are_not_relaxed_by_a_permissive_umask(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    artifact, blobs = asyncio.run(_stored_artifact())
    sink = LocalArtifactBundleSink(root)
    previous = os.umask(0)
    try:
        outcome = asyncio.run(sink.persist(artifact, blobs, _limits()))
    finally:
        os.umask(previous)
        sink.close()
    assert outcome.status is ArtifactPersistenceStatus.PERSISTED
    assert stat.S_IMODE((root / "artifact-0").stat().st_mode) == 0o700
