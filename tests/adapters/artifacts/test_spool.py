"""Security, streaming, failure, and lifecycle tests for the POSIX sanitized spool."""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
import threading
from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from comparo.adapters.artifacts import spool as spool_module
from comparo.adapters.artifacts.spool import PosixSanitizedBlobSpool
from comparo.adapters.artifacts.spool import SanitizedSpoolError
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import SanitizationLimits
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount


def _limits(*, blobs: int = 3, per_blob: int = 16, total: int = 32) -> ArtifactLimits:
    return ArtifactLimits(
        maximum_blobs=PositiveCount(value=blobs),
        maximum_blob_bytes=ByteCount(value=per_blob),
        maximum_total_blob_bytes=ByteCount(value=total),
        maximum_manifest_bytes=ByteCount(value=1024),
        maximum_report_bytes=ByteCount(value=1024),
        sanitization=SanitizationLimits(
            maximum_patterns=PositiveCount(value=16),
            maximum_pattern_bytes=ByteCount(value=1024),
            maximum_value_bytes=ByteCount(value=256),
            maximum_value_nodes=PositiveCount(value=64),
        ),
    )


def _root(tmp_path: Path, name: str = "authorized") -> Path:
    root = tmp_path / name
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    return root


def _spool(
    tmp_path: Path,
    *,
    blobs: int = 3,
    per_blob: int = 16,
    total: int = 32,
) -> PosixSanitizedBlobSpool:
    return PosixSanitizedBlobSpool(
        _limits(blobs=blobs, per_blob=per_blob, total=total),
        io_chunk_size=PositiveCount(value=2),
        root=_root(tmp_path),
    )


async def _read(chunks: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in chunks])


def _assert_code(
    error: pytest.ExceptionInfo[SanitizedSpoolError], code: ArtifactStoreErrorCode
) -> None:
    assert error.value.code is code
    assert str(error.value) == code.value
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_spool_streams_private_files_deduplicates_and_cleans_up(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        spool = PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=2),
            root=root,
        )
        private = root / spool._directory_name
        assert stat.S_IMODE(private.stat().st_mode) == 0o700

        writer = await spool.begin()
        await writer.write(b"ab")
        await writer.write(b"")
        await writer.write(b"cd")
        blob = await writer.commit(media_type="text/plain")
        stored = private / blob.sha256
        assert blob.byte_count == 4
        assert stat.S_IMODE(stored.stat().st_mode) == 0o600
        assert spool.reserved_bytes == ByteCount(value=4)
        assert spool.spool_file_count == 1
        chunks = [chunk async for chunk in spool.read(blob)]
        assert chunks == [b"ab", b"cd"]

        duplicate = await spool.begin()
        await duplicate.write(b"abcd")
        duplicate_blob = await duplicate.commit(media_type="application/octet-stream")
        assert duplicate_blob.sha256 == blob.sha256
        assert duplicate_blob.media_type == "application/octet-stream"
        assert spool.spool_file_count == 1
        assert spool.reserved_bytes == ByteCount(value=4)
        assert [item.name for item in private.iterdir()] == [blob.sha256]

        await spool.close()
        await spool.close()
        assert not private.exists()

    asyncio.run(scenario())


def test_spool_context_manager_default_root_and_empty_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(root))
        async with PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=1),
        ) as spool:
            private = root / spool._directory_name
            writer = await spool.begin()
            blob = await writer.commit(media_type=None)
            assert blob.sha256 == sha256(b"").hexdigest()
            assert await _read(spool.read(blob)) == b""
        assert not private.exists()

    asyncio.run(scenario())


def test_spool_validates_public_boundaries_and_terminal_writer_state(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="error code"):
        SanitizedSpoolError(cast("ArtifactStoreErrorCode", object()))
    with pytest.raises(TypeError, match="requires ArtifactLimits"):
        PosixSanitizedBlobSpool(
            cast("ArtifactLimits", object()),
            io_chunk_size=PositiveCount(value=1),
            root=_root(tmp_path),
        )
    with pytest.raises(TypeError, match="chunk size"):
        PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=cast("PositiveCount", object()),
            root=_root(tmp_path, "second"),
        )
    for invalid in (Path("relative"), cast("Path", "string")):
        with pytest.raises(SanitizedSpoolError) as failure:
            PosixSanitizedBlobSpool(
                _limits(),
                io_chunk_size=PositiveCount(value=1),
                root=invalid,
            )
        _assert_code(failure, ArtifactStoreErrorCode.ROOT_UNSAFE)

    async def scenario() -> None:
        spool = _spool(tmp_path)
        with pytest.raises(TypeError, match="reads require ArtifactBlob"):
            spool.read(cast("ArtifactBlob", object()))
        writer = await spool.begin()
        async with writer:
            with pytest.raises(TypeError, match="chunks must be bytes"):
                await writer.write(cast("bytes", bytearray(b"mutable")))
            await writer.write(b"ok")
            blob = await writer.commit(media_type=None)
        await writer.abort()
        for operation in (
            lambda: writer.write(b"late"),
            lambda: writer.commit(media_type=None),
        ):
            with pytest.raises(SanitizedSpoolError) as closed_writer:
                await operation()
            _assert_code(closed_writer, ArtifactStoreErrorCode.CLOSED)
        await spool.close()
        with pytest.raises(SanitizedSpoolError) as enter:
            await spool.__aenter__()
        _assert_code(enter, ArtifactStoreErrorCode.CLOSED)
        with pytest.raises(SanitizedSpoolError) as begin:
            await spool.begin()
        _assert_code(begin, ArtifactStoreErrorCode.CLOSED)
        with pytest.raises(SanitizedSpoolError) as read:
            await _read(spool.read(blob))
        _assert_code(read, ArtifactStoreErrorCode.CLOSED)

    asyncio.run(scenario())


def test_spool_enforces_pending_per_blob_aggregate_and_committed_count_limits(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path, blobs=1, per_blob=4, total=4)
        pending = await spool.begin()
        with pytest.raises(SanitizedSpoolError) as pending_limit:
            await spool.begin()
        _assert_code(pending_limit, ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        await pending.write(b"1234")
        with pytest.raises(SanitizedSpoolError) as per_blob:
            await pending.write(b"5")
        _assert_code(per_blob, ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        await pending.abort()
        await pending.abort()
        assert spool.reserved_bytes == ByteCount(value=0)

        first = await spool.begin()
        await first.write(b"a")
        await first.commit(media_type=None)
        second = await spool.begin()
        await second.write(b"b")
        with pytest.raises(SanitizedSpoolError) as count:
            await second.commit(media_type=None)
        _assert_code(count, ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        await second.abort()
        await spool.close()

        aggregate = _spool(tmp_path, blobs=2, per_blob=3, total=3)
        one = await aggregate.begin()
        two = await aggregate.begin()
        await one.write(b"ab")
        with pytest.raises(SanitizedSpoolError) as total:
            await two.write(b"cd")
        _assert_code(total, ArtifactStoreErrorCode.BUDGET_EXCEEDED)
        await one.abort()
        await two.abort()
        await aggregate.close()

    asyncio.run(scenario())


def test_spool_rejects_unknown_mismatched_and_tampered_content(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        spool = PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=2),
            root=root,
        )
        writer = await spool.begin()
        await writer.write(b"safe")
        blob = await writer.commit(media_type=None)
        private = root / spool._directory_name

        for descriptor in (
            ArtifactBlob(sha256="0" * 64, byte_count=1),
            ArtifactBlob(sha256=blob.sha256, byte_count=3),
        ):
            with pytest.raises(SanitizedSpoolError) as mismatch:
                await _read(spool.read(descriptor))
            _assert_code(mismatch, ArtifactStoreErrorCode.DIGEST_MISMATCH)

        stored = private / blob.sha256
        stored.write_bytes(b"evil")
        stored.chmod(0o600)
        stream = spool.read(blob)
        with pytest.raises(SanitizedSpoolError) as digest:
            await anext(stream)
        _assert_code(digest, ArtifactStoreErrorCode.DIGEST_MISMATCH)

        stored.write_bytes(b"safe!")
        stored.chmod(0o600)
        with pytest.raises(SanitizedSpoolError) as size:
            await _read(spool.read(blob))
        _assert_code(size, ArtifactStoreErrorCode.DIGEST_MISMATCH)

        stored.write_bytes(b"safe")
        stored.chmod(0o644)
        with pytest.raises(SanitizedSpoolError) as mode:
            await _read(spool.read(blob))
        _assert_code(mode, ArtifactStoreErrorCode.DIGEST_MISMATCH)
        stored.chmod(0o600)
        await spool.close()

    asyncio.run(scenario())


def test_stream_rejects_content_that_exceeds_declared_size_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        spool = PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=8),
            root=root,
        )
        writer = await spool.begin()
        await writer.write(b"safe")
        blob = await writer.commit(media_type=None)
        stored = root / spool._directory_name / blob.sha256
        stored.write_bytes(b"unsafe")
        stored.chmod(0o600)

        def unchecked_open(directory_fd: int, entry: spool_module._SpoolEntry) -> int:
            return os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)

        monkeypatch.setattr(spool_module, "_open_committed_file", unchecked_open)
        with pytest.raises(SanitizedSpoolError) as failure:
            await _read(spool.read(blob))
        _assert_code(failure, ArtifactStoreErrorCode.DIGEST_MISMATCH)
        monkeypatch.undo()
        await spool.close()

    asyncio.run(scenario())


def test_spool_refuses_symlink_substitution_without_touching_target(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        spool = PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=2),
            root=root,
        )
        writer = await spool.begin()
        await writer.write(b"safe")
        blob = await writer.commit(media_type=None)
        stored = root / spool._directory_name / blob.sha256
        stored.unlink()
        outside = tmp_path / "outside-secret"
        outside.write_bytes(b"outside")
        stored.symlink_to(outside)
        with pytest.raises(SanitizedSpoolError) as failure:
            await _read(spool.read(blob))
        _assert_code(failure, ArtifactStoreErrorCode.DIGEST_MISMATCH)
        assert outside.read_bytes() == b"outside"
        stored.unlink()
        await spool.close()

    asyncio.run(scenario())


def test_spool_rejects_multi_linked_committed_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        spool = PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=2),
            root=root,
        )
        writer = await spool.begin()
        await writer.write(b"safe")
        blob = await writer.commit(media_type=None)
        private = root / spool._directory_name
        linked = private / "unexpected-link"
        os.link(private / blob.sha256, linked)
        with pytest.raises(SanitizedSpoolError) as failure:
            await anext(spool.read(blob))
        _assert_code(failure, ArtifactStoreErrorCode.DIGEST_MISMATCH)
        linked.unlink()
        await spool.close()

    asyncio.run(scenario())


def test_spool_maps_rewind_failure_before_yielding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        await writer.write(b"safe")
        blob = await writer.commit(media_type=None)
        monkeypatch.setattr(
            os,
            "lseek",
            lambda *_arguments: (_ for _ in ()).throw(OSError("sensitive")),
        )
        with pytest.raises(SanitizedSpoolError) as failure:
            await anext(spool.read(blob))
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.undo()
        await spool.close()

    asyncio.run(scenario())


def test_close_waits_for_admitted_writer_and_rejects_new_operations(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        spool = PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=1),
            root=root,
        )
        private = root / spool._directory_name
        writer = await spool.begin()
        await writer.write(b"x")
        first_close = asyncio.create_task(spool.close())
        second_close = asyncio.create_task(spool.close())
        await asyncio.sleep(0)
        assert not first_close.done()
        with pytest.raises(SanitizedSpoolError) as begin:
            await spool.begin()
        _assert_code(begin, ArtifactStoreErrorCode.CLOSED)
        with pytest.raises(SanitizedSpoolError) as enter:
            await spool.__aenter__()
        _assert_code(enter, ArtifactStoreErrorCode.CLOSED)
        await writer.commit(media_type=None)
        await asyncio.gather(first_close, second_close)
        assert not private.exists()

    asyncio.run(scenario())


def test_close_waits_for_active_reader_and_cancelled_close_still_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        spool = PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=1),
            root=root,
        )
        private = root / spool._directory_name
        writer = await spool.begin()
        await writer.write(b"abc")
        blob = await writer.commit(media_type=None)
        stream = spool.read(blob)
        assert await anext(stream) == b"a"
        close = asyncio.create_task(spool.close())
        await asyncio.sleep(0)
        assert not close.done()
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert await _read(stream) == b"bc"
        assert spool._close_task is not None
        await spool._close_task
        assert not private.exists()

    asyncio.run(scenario())


def test_cancelled_write_accounts_completed_io_and_abort_releases_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    original = spool_module._write_all

    def blocked_write(descriptor: int, value: bytes) -> None:
        started.set()
        release.wait(timeout=5)
        original(descriptor, value)

    async def scenario() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        monkeypatch.setattr(spool_module, "_write_all", blocked_write)
        operation = asyncio.create_task(writer.write(b"safe"))
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert spool.reserved_bytes == ByteCount(value=4)
        assert writer._count == 4
        await writer.abort()
        assert spool.reserved_bytes == ByteCount(value=0)
        await spool.close()

    asyncio.run(scenario())


def test_write_io_failure_and_cancelled_failed_io_release_reservations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def normal_failure() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()

        def fail_write(_descriptor: int, _value: bytes) -> None:
            raise OSError("sensitive path and value")

        monkeypatch.setattr(spool_module, "_write_all", fail_write)
        with pytest.raises(SanitizedSpoolError) as failure:
            await writer.write(b"secret")
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        assert spool.reserved_bytes == ByteCount(value=0)
        await writer.abort()
        await spool.close()

    async def cancelled_failure() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        started = threading.Event()
        release = threading.Event()

        def fail_later(_descriptor: int, _value: bytes) -> None:
            started.set()
            release.wait(timeout=5)
            raise OSError("sensitive path and value")

        monkeypatch.setattr(spool_module, "_write_all", fail_later)
        operation = asyncio.create_task(writer.write(b"secret"))
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert spool.reserved_bytes == ByteCount(value=0)
        assert writer._count == 0
        await writer.abort()
        await spool.close()

    asyncio.run(normal_failure())
    asyncio.run(cancelled_failure())


def test_abort_is_cancellation_safe_and_reports_cleanup_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancellation() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        await writer.write(b"x")
        await writer._lock.acquire()
        operation = asyncio.create_task(writer.abort())
        await asyncio.sleep(0)
        operation.cancel()
        writer._lock.release()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert not spool._pending
        assert spool.reserved_bytes == ByteCount(value=0)
        await spool.close()

    async def cleanup_failure() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        original_close = spool_module._close_descriptor

        def close_but_report_failure(descriptor: int) -> bool:
            original_close(descriptor)
            return False

        monkeypatch.setattr(spool_module, "_close_descriptor", close_but_report_failure)
        monkeypatch.setattr(spool_module, "_unlink", lambda _descriptor, _name: False)
        with pytest.raises(SanitizedSpoolError) as failure:
            await writer.abort()
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        assert writer._name in spool._orphans
        monkeypatch.undo()
        await spool.close()

    asyncio.run(cancellation())
    asyncio.run(cleanup_failure())


def test_commit_maps_sync_close_publication_collision_and_io_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def sync_failure() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        monkeypatch.setattr(os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError()))
        with pytest.raises(SanitizedSpoolError) as failure:
            await writer.commit(media_type=None)
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.undo()
        await writer.abort()
        await spool.close()

    async def close_failure() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        original = spool_module._close_descriptor

        def fail_once(descriptor: int) -> bool:
            original(descriptor)
            return False

        monkeypatch.setattr(spool_module, "_close_descriptor", fail_once)
        with pytest.raises(SanitizedSpoolError) as failure:
            await writer.commit(media_type=None)
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.undo()
        await writer.abort()
        await spool.close()

    async def publication(outcome: object, expected: ArtifactStoreErrorCode) -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        monkeypatch.setattr(spool_module, "_link_noreplace", lambda *_arguments: outcome)
        with pytest.raises(SanitizedSpoolError) as failure:
            await writer.commit(media_type=None)
        _assert_code(failure, expected)
        monkeypatch.undo()
        await writer.abort()
        await spool.close()

    asyncio.run(sync_failure())
    asyncio.run(close_failure())
    asyncio.run(
        publication(spool_module._LinkOutcome.EXISTS, ArtifactStoreErrorCode.DIGEST_MISMATCH)
    )
    asyncio.run(publication(spool_module._LinkOutcome.FAILED, ArtifactStoreErrorCode.IO_FAILED))


def test_duplicate_metadata_collision_and_orphan_tracking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        first = await spool.begin()
        await first.write(b"x")
        blob = await first.commit(media_type=None)
        spool._entries[blob.sha256] = spool_module._SpoolEntry(
            name=blob.sha256,
            byte_count=2,
        )
        duplicate = await spool.begin()
        await duplicate.write(b"x")
        with pytest.raises(SanitizedSpoolError) as collision:
            await duplicate.commit(media_type=None)
        _assert_code(collision, ArtifactStoreErrorCode.DIGEST_MISMATCH)
        await duplicate.abort()
        spool._entries[blob.sha256] = spool_module._SpoolEntry(
            name=blob.sha256,
            byte_count=1,
        )

        orphan = await spool.begin()
        await orphan.write(b"x")
        pending_name = orphan._name
        original_unlink = spool_module._unlink

        def fail_pending(descriptor: int, name: str) -> bool:
            if name == pending_name:
                return False
            return original_unlink(descriptor, name)

        monkeypatch.setattr(spool_module, "_unlink", fail_pending)
        await orphan.commit(media_type=None)
        assert pending_name in spool._orphans
        monkeypatch.undo()
        await spool.close()

    asyncio.run(scenario())


def test_unique_publication_tracks_pending_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        await writer.write(b"unique")
        pending_name = writer._name
        original_unlink = spool_module._unlink

        def fail_pending(descriptor: int, name: str) -> bool:
            if name == pending_name:
                return False
            return original_unlink(descriptor, name)

        monkeypatch.setattr(spool_module, "_unlink", fail_pending)
        with pytest.raises(SanitizedSpoolError) as failure:
            await writer.commit(media_type=None)
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.undo()
        await writer.abort()
        assert spool.spool_file_count == 0
        assert spool.reserved_bytes == ByteCount(value=0)
        await spool.close()

    asyncio.run(scenario())


def test_publication_tracks_destination_when_both_unlinks_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        await writer.write(b"unique")
        digest = sha256(b"unique").hexdigest()
        monkeypatch.setattr(spool_module, "_unlink", lambda _descriptor, _name: False)
        with pytest.raises(SanitizedSpoolError) as failure:
            await writer.commit(media_type=None)
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        assert digest in spool._orphans
        monkeypatch.undo()
        await writer.abort()
        await spool.close()

    asyncio.run(scenario())


def test_read_io_failure_releases_reader_and_close_can_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        await writer.write(b"safe")
        blob = await writer.commit(media_type=None)

        async def fail_read(_descriptor: int, _count: int) -> bytes:
            raise SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)

        monkeypatch.setattr(spool_module, "_read_chunk", fail_read)
        with pytest.raises(SanitizedSpoolError) as failure:
            await _read(spool.read(blob))
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        assert spool._active_readers == 0
        monkeypatch.undo()
        await spool.close()

    asyncio.run(scenario())


def test_low_level_root_and_platform_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    monkeypatch.setattr(spool_module, "_descriptor_operations_supported", lambda: False)
    with pytest.raises(SanitizedSpoolError) as unsupported:
        PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=1),
            root=root,
        )
    _assert_code(unsupported, ArtifactStoreErrorCode.UNSUPPORTED_PLATFORM)
    monkeypatch.undo()

    missing = tmp_path / "missing"
    with pytest.raises(SanitizedSpoolError) as absent:
        spool_module._open_parent(missing)
    _assert_code(absent, ArtifactStoreErrorCode.ROOT_UNSAFE)
    regular = tmp_path / "file"
    regular.touch()
    with pytest.raises(SanitizedSpoolError) as non_directory:
        spool_module._open_parent(regular)
    _assert_code(non_directory, ArtifactStoreErrorCode.ROOT_UNSAFE)
    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o777)
    insecure.chmod(0o777)
    with pytest.raises(SanitizedSpoolError) as unsafe:
        spool_module._open_parent(insecure)
    _assert_code(unsafe, ArtifactStoreErrorCode.ROOT_UNSAFE)
    sticky = tmp_path / "sticky"
    sticky.mkdir(mode=0o777)
    sticky.chmod(0o1777)
    descriptor = spool_module._open_parent(sticky)
    assert spool_module._close_descriptor(descriptor)


def test_constructor_closes_parent_when_private_directory_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    closed: list[int] = []
    original_close = spool_module._close_descriptor

    def recording_close(descriptor: int) -> bool:
        closed.append(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(
        spool_module,
        "_create_directory",
        lambda _descriptor: (_ for _ in ()).throw(
            SanitizedSpoolError(ArtifactStoreErrorCode.IO_FAILED)
        ),
    )
    monkeypatch.setattr(spool_module, "_close_descriptor", recording_close)
    with pytest.raises(SanitizedSpoolError) as failure:
        PosixSanitizedBlobSpool(
            _limits(),
            io_chunk_size=PositiveCount(value=1),
            root=root,
        )
    _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
    assert len(closed) == 1


def test_reserve_rejects_work_after_final_close(tmp_path: Path) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        await spool.close()
        with pytest.raises(SanitizedSpoolError) as failure:
            await spool._reserve(1)
        _assert_code(failure, ArtifactStoreErrorCode.CLOSED)

    asyncio.run(scenario())


def test_descriptor_support_checks_every_required_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert spool_module._descriptor_operations_supported()
    for operation in (os.mkdir, os.open, os.link, os.unlink, os.rmdir):
        monkeypatch.setattr(
            os,
            "supports_dir_fd",
            os.supports_dir_fd - {operation},
        )
        assert not spool_module._descriptor_operations_supported()
        monkeypatch.undo()
    monkeypatch.delattr(os, "O_NOFOLLOW")
    assert not spool_module._descriptor_operations_supported()


def test_low_level_directory_and_pending_creation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    parent_fd = spool_module._open_parent(root)
    try:
        monkeypatch.setattr(spool_module, "token_hex", lambda _count: "collision")
        (root / ".comparo-sanitized-collision").mkdir()
        with pytest.raises(SanitizedSpoolError) as collisions:
            spool_module._create_directory(parent_fd)
        _assert_code(collisions, ArtifactStoreErrorCode.IO_FAILED)
        (root / ".comparo-sanitized-collision").rmdir()
        monkeypatch.undo()

        original_mkdir = os.mkdir

        def fail_mkdir(*_arguments: object, **_keywords: object) -> None:
            raise OSError("sensitive")

        monkeypatch.setattr(os, "mkdir", fail_mkdir)
        with pytest.raises(SanitizedSpoolError) as mkdir_failure:
            spool_module._create_directory(parent_fd)
        _assert_code(mkdir_failure, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.setattr(os, "mkdir", original_mkdir)

        original_open = os.open

        def fail_open(*_arguments: object, **_keywords: object) -> int:
            raise OSError("sensitive")

        monkeypatch.setattr(os, "open", fail_open)
        with pytest.raises(SanitizedSpoolError) as open_failure:
            spool_module._create_directory(parent_fd)
        _assert_code(open_failure, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.setattr(os, "open", original_open)

        monkeypatch.setattr(
            spool_module,
            "_validate_private_directory",
            lambda _descriptor: (_ for _ in ()).throw(
                SanitizedSpoolError(ArtifactStoreErrorCode.ROOT_UNSAFE)
            ),
        )
        with pytest.raises(SanitizedSpoolError) as validation:
            spool_module._create_directory(parent_fd)
        _assert_code(validation, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.undo()

        name, directory_fd = spool_module._create_directory(parent_fd)
        try:
            monkeypatch.setattr(spool_module, "token_hex", lambda _count: "collision")
            descriptor = os.open(
                ".pending-collision",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
            os.close(descriptor)
            with pytest.raises(SanitizedSpoolError) as pending_collisions:
                spool_module._create_pending_file(directory_fd)
            _assert_code(pending_collisions, ArtifactStoreErrorCode.IO_FAILED)
            os.unlink(".pending-collision", dir_fd=directory_fd)
            monkeypatch.undo()

            original_fchmod = os.fchmod

            def fail_fchmod(_descriptor: int, _mode: int) -> None:
                raise OSError("sensitive")

            monkeypatch.setattr(os, "fchmod", fail_fchmod)
            with pytest.raises(SanitizedSpoolError) as chmod_failure:
                spool_module._create_pending_file(directory_fd)
            _assert_code(chmod_failure, ArtifactStoreErrorCode.IO_FAILED)
            monkeypatch.setattr(os, "fchmod", original_fchmod)
            assert not list((root / name).iterdir())

            monkeypatch.setattr(os, "open", fail_open)
            with pytest.raises(SanitizedSpoolError) as pending_open_failure:
                spool_module._create_pending_file(directory_fd)
            _assert_code(pending_open_failure, ArtifactStoreErrorCode.IO_FAILED)
            monkeypatch.setattr(os, "open", original_open)
        finally:
            spool_module._close_descriptor(directory_fd)
            spool_module._rmdir(parent_fd, name)
    finally:
        spool_module._close_descriptor(parent_fd)


def test_private_directory_validation_rejects_unsafe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for metadata in (
        SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.geteuid()),
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=os.geteuid() + 1),
        SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=os.geteuid()),
    ):
        monkeypatch.setattr(os, "fstat", lambda _descriptor, value=metadata: value)
        with pytest.raises(SanitizedSpoolError) as failure:
            spool_module._validate_private_directory(1)
        _assert_code(failure, ArtifactStoreErrorCode.ROOT_UNSAFE)
        monkeypatch.undo()


@pytest.mark.parametrize(
    ("metadata", "valid"),
    [
        (
            SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=os.geteuid(),
                st_nlink=1,
                st_size=1,
            ),
            True,
        ),
        (
            SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o700,
                st_uid=os.geteuid(),
                st_nlink=1,
                st_size=1,
            ),
            False,
        ),
        (
            SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=os.geteuid() + 1,
                st_nlink=1,
                st_size=1,
            ),
            False,
        ),
        (
            SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=os.geteuid(),
                st_nlink=2,
                st_size=1,
            ),
            False,
        ),
        (
            SimpleNamespace(
                st_mode=stat.S_IFREG | 0o640,
                st_uid=os.geteuid(),
                st_nlink=1,
                st_size=1,
            ),
            False,
        ),
        (
            SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=os.geteuid(),
                st_nlink=1,
                st_size=2,
            ),
            False,
        ),
    ],
)
def test_open_committed_file_validates_descriptor_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: SimpleNamespace,
    valid: bool,
) -> None:
    root = _root(tmp_path)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    file_descriptor = os.open("blob", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=descriptor)
    os.write(file_descriptor, b"x")
    os.close(file_descriptor)
    original_fstat = os.fstat
    monkeypatch.setattr(os, "fstat", lambda _descriptor: metadata)
    entry = spool_module._SpoolEntry(name="blob", byte_count=1)
    try:
        if valid:
            opened = spool_module._open_committed_file(descriptor, entry)
            os.close(opened)
        else:
            with pytest.raises(SanitizedSpoolError) as failure:
                spool_module._open_committed_file(descriptor, entry)
            _assert_code(failure, ArtifactStoreErrorCode.DIGEST_MISMATCH)
    finally:
        monkeypatch.setattr(os, "fstat", original_fstat)
        os.unlink("blob", dir_fd=descriptor)
        os.close(descriptor)


def test_low_level_file_io_link_and_cleanup_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    source_fd = os.open("source", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
    try:
        writes = iter((1, 2))
        monkeypatch.setattr(os, "write", lambda _descriptor, _value: next(writes))
        spool_module._write_all(source_fd, b"abc")
        monkeypatch.setattr(os, "write", lambda _descriptor, _value: 0)
        with pytest.raises(OSError, match="short sanitized spool write"):
            spool_module._write_all(source_fd, b"x")
        monkeypatch.undo()

        assert (
            spool_module._link_noreplace(directory_fd, "source", "destination")
            is spool_module._LinkOutcome.CREATED
        )
        assert (
            spool_module._link_noreplace(directory_fd, "source", "destination")
            is spool_module._LinkOutcome.EXISTS
        )
        monkeypatch.setattr(
            os,
            "link",
            lambda *_arguments, **_keywords: (_ for _ in ()).throw(OSError()),
        )
        assert (
            spool_module._link_noreplace(directory_fd, "source", "other")
            is spool_module._LinkOutcome.FAILED
        )
        monkeypatch.undo()

        assert spool_module._unlink(directory_fd, "destination")
        assert spool_module._unlink(directory_fd, "destination")
        monkeypatch.setattr(
            os,
            "unlink",
            lambda *_arguments, **_keywords: (_ for _ in ()).throw(OSError()),
        )
        assert not spool_module._unlink(directory_fd, "source")
        monkeypatch.undo()

        empty = root / "empty"
        empty.mkdir()
        assert spool_module._rmdir(directory_fd, "empty")
        assert spool_module._rmdir(directory_fd, "empty")
        (root / "full").mkdir()
        (root / "full" / "child").touch()
        assert not spool_module._rmdir(directory_fd, "full")

        extra = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        assert spool_module._close_descriptor(extra)
        assert not spool_module._close_descriptor(extra)
    finally:
        with pytest.MonkeyPatch.context() as cleanup_patch:
            cleanup_patch.undo()
            for child in (root / "full" / "child", root / "full"):
                if child.is_dir():
                    child.rmdir()
                elif child.exists():
                    child.unlink()
            source = root / "source"
            if source.exists():
                source.unlink()
            os.close(source_fd)
            os.close(directory_fd)


def test_async_io_helpers_are_cancellation_safe_and_value_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failure_paths() -> None:
        monkeypatch.setattr(
            os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError("secret"))
        )
        with pytest.raises(SanitizedSpoolError) as sync:
            await spool_module._sync_descriptor(1)
        _assert_code(sync, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.setattr(
            os,
            "read",
            lambda _descriptor, _count: (_ for _ in ()).throw(OSError("secret")),
        )
        with pytest.raises(SanitizedSpoolError) as read:
            await spool_module._read_chunk(1, 1)
        _assert_code(read, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.undo()

    async def cancellation(helper: object, patch_name: str) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocked(*_arguments: object) -> bytes | None:
            started.set()
            release.wait(timeout=5)
            return b"x" if patch_name == "read" else None

        monkeypatch.setattr(os, patch_name, blocked)

        async def invoke() -> object:
            if patch_name == "read":
                return await spool_module._read_chunk(1, 1)
            await spool_module._sync_descriptor(1)
            return None

        operation = asyncio.create_task(invoke())
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        monkeypatch.undo()

    asyncio.run(failure_paths())
    asyncio.run(cancellation(spool_module._sync_descriptor, "fsync"))
    asyncio.run(cancellation(spool_module._read_chunk, "read"))


def test_close_reports_cleanup_failure_and_consumes_background_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        writer = await spool.begin()
        await writer.write(b"x")
        await writer.commit(media_type=None)
        original_unlink = spool_module._unlink
        monkeypatch.setattr(spool_module, "_unlink", lambda _descriptor, _name: False)
        with pytest.raises(SanitizedSpoolError) as failure:
            await spool.close()
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)
        with pytest.raises(SanitizedSpoolError) as repeated:
            await spool.close()
        _assert_code(repeated, ArtifactStoreErrorCode.IO_FAILED)
        monkeypatch.setattr(spool_module, "_unlink", original_unlink)

        async def fail() -> None:
            raise RuntimeError("retrieved")

        task = asyncio.create_task(fail())
        with pytest.raises(RuntimeError):
            await task
        spool_module._consume_task_exception(task)
        cancelled = asyncio.create_task(asyncio.sleep(1))
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        spool_module._consume_task_exception(cancelled)

    asyncio.run(scenario())


def test_close_reports_both_descriptor_close_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        spool = _spool(tmp_path)
        original_close = spool_module._close_descriptor

        def close_but_report_failure(descriptor: int) -> bool:
            original_close(descriptor)
            return False

        monkeypatch.setattr(spool_module, "_close_descriptor", close_but_report_failure)
        with pytest.raises(SanitizedSpoolError) as failure:
            await spool.close()
        _assert_code(failure, ArtifactStoreErrorCode.IO_FAILED)

    asyncio.run(scenario())
