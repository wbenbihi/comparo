"""Defensive materialization and transient-store failure-path tests."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import BinaryIO
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.http import storage as storage_module
from comparo.adapters.http.materialization import BodyMaterializationError
from comparo.adapters.http.materialization import BodyMaterializationFailureCode
from comparo.adapters.http.materialization import BodyMaterializationLimits
from comparo.adapters.http.materialization import RequestBodyMaterializer
from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import BodyStoreFailureCode
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartDispositionName
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonString
from tests.core.domain.http.factories import provenance

_ORIGIN = TaintOriginId(value="test/edge-secret")


def _body_id(value: int) -> BodyId:
    return BodyId(value=UUID(int=value))


def _config(root: Path, *, memory: int = 4, maximum: int = 1_024) -> TransientBodyStoreConfig:
    return TransientBodyStoreConfig(
        memory_threshold=ByteCount(value=memory),
        maximum_transient_bytes=ByteCount(value=maximum),
        io_chunk_size=PositiveCount(value=2),
        root=root,
    )


def _limits(*, body: int = 512, depth: int = 8, nodes: int = 32) -> BodyMaterializationLimits:
    return BodyMaterializationLimits(
        maximum_body_bytes=ByteCount(value=body),
        maximum_json_depth=PositiveCount(value=depth),
        maximum_json_nodes=PositiveCount(value=nodes),
    )


async def _read(source: object) -> bytes:
    return b"".join([chunk async for chunk in source])  # type: ignore[attr-defined]


def test_materialization_contracts_reject_invalid_types_and_error_shapes(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="JSON depth"):
        BodyMaterializationLimits(
            maximum_body_bytes=ByteCount(value=1),
            maximum_json_depth=1,  # type: ignore[arg-type]
            maximum_json_nodes=PositiveCount(value=1),
        )
    with pytest.raises(TypeError, match="JSON node"):
        BodyMaterializationLimits(
            maximum_body_bytes=ByteCount(value=1),
            maximum_json_depth=PositiveCount(value=1),
            maximum_json_nodes=1,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="failure code"):
        BodyMaterializationError(cast("BodyMaterializationFailureCode", object()))
    with pytest.raises(TypeError, match="nested body-store"):
        BodyMaterializationError(
            BodyMaterializationFailureCode.STORE_FAILED,
            store_code=cast("BodyStoreFailureCode", object()),
        )
    with pytest.raises(ValueError, match="require one store code"):
        BodyMaterializationError(
            BodyMaterializationFailureCode.INVALID_TEXT,
            store_code=BodyStoreFailureCode.IO_FAILED,
        )
    store = TransientBodyStore(_config(tmp_path))
    with pytest.raises(TypeError, match="requires TransientBodyStore"):
        RequestBodyMaterializer(cast("TransientBodyStore", object()), _limits())
    with pytest.raises(TypeError, match="requires BodyMaterializationLimits"):
        RequestBodyMaterializer(store, cast("BodyMaterializationLimits", object()))

    async def scenario() -> None:
        materializer = RequestBodyMaterializer(store, _limits())
        with pytest.raises(TypeError, match="BodyId and ValueProvenance"):
            await materializer.materialize(
                TextBody(value=BodyText(value="x")),
                body_id=cast("BodyId", object()),
                provenance=provenance(),
            )
        with pytest.raises(TypeError, match="supported RequestBody"):
            async for _chunk in materializer._serialize(cast("RequestBody", object())):
                pass
        await store.close()

    asyncio.run(scenario())


def test_sensitive_json_and_bytes_are_revealed_only_for_materialization(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        materializer = RequestBodyMaterializer(store, _limits())
        json_result = await materializer.materialize(
            JsonBody(value=SensitiveValue(JsonString(value="secret-json"), origins=(_ORIGIN,))),
            body_id=_body_id(201),
            provenance=provenance(),
        )
        bytes_result = await materializer.materialize(
            BytesBody(value=SensitiveValue(BytesValue(value=b"secret-bytes"), origins=(_ORIGIN,))),
            body_id=_body_id(202),
            provenance=provenance(),
        )
        assert type(json_result) is MaterializedBody
        assert type(bytes_result) is MaterializedBody
        assert await _read(store.open_request(json_result.source)) == b'"secret-json"'
        assert await _read(store.open_request(bytes_result.source)) == b"secret-bytes"
        await store.close()

    asyncio.run(scenario())


def test_invalid_unicode_and_json_node_limits_have_typed_value_free_failures(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        materializer = RequestBodyMaterializer(store, _limits(nodes=1))
        with pytest.raises(BodyMaterializationError) as nodes:
            await materializer.materialize(
                JsonBody(value=JsonArray(items=(JsonNull(),))),
                body_id=_body_id(210),
                provenance=provenance(),
            )
        assert nodes.value.code is BodyMaterializationFailureCode.JSON_LIMIT_EXCEEDED

        invalid_text = BodyText(value="valid")
        msgspec.structs.force_setattr(invalid_text, "value", "\ud800")
        with pytest.raises(BodyMaterializationError) as text:
            await materializer.materialize(
                TextBody(value=invalid_text),
                body_id=_body_id(211),
                provenance=provenance(),
            )
        assert text.value.code is BodyMaterializationFailureCode.INVALID_TEXT

        invalid_json = JsonString(value="valid")
        msgspec.structs.force_setattr(invalid_json, "value", "\ud800")
        with pytest.raises(BodyMaterializationError) as json_failure:
            await materializer.materialize(
                JsonBody(value=invalid_json),
                body_id=_body_id(212),
                provenance=provenance(),
            )
        assert json_failure.value.code is BodyMaterializationFailureCode.INVALID_TEXT
        await store.close()

    asyncio.run(scenario())


def test_multipart_scans_repeatable_chunks_and_covers_optional_header_forms(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=64))
        source_writer = await store.begin(
            _body_id(220),
            maximum_body_bytes=ByteCount(value=32),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with source_writer:
            await source_writer.append(b"xxbou")
            await source_writer.append(b"ndaryyy")
            source = await source_writer.commit_request(provenance())
        collision = MultipartBody(
            boundary=MultipartBoundary(value="boundary"),
            parts=(
                MultipartFilePart(
                    name=MultipartDispositionName(value="file"),
                    filename=MultipartDispositionFilename(value="plain.txt"),
                    value=source,
                    media_type=MediaType(type="text", subtype="plain"),
                ),
            ),
        )
        materializer = RequestBodyMaterializer(store, _limits())
        with pytest.raises(BodyMaterializationError) as failure:
            await materializer.materialize(
                collision,
                body_id=_body_id(221),
                provenance=provenance(),
            )
        assert failure.value.code is BodyMaterializationFailureCode.MULTIPART_BOUNDARY_COLLISION

        single_character = MultipartBody(
            boundary=MultipartBoundary(value="x"),
            parts=(
                MultipartTextPart(
                    name=MultipartDispositionName(value="note"),
                    value=BodyText(value="ok"),
                ),
                MultipartFilePart(
                    name=MultipartDispositionName(value="file"),
                    filename=MultipartDispositionFilename(value="plain.txt"),
                    value=BytesValue(value=b"safe"),
                    media_type=MediaType(type="text", subtype="plain"),
                ),
            ),
        )
        result = await materializer.materialize(
            single_character,
            body_id=_body_id(222),
            provenance=provenance(),
        )
        assert type(result) is MaterializedBody
        encoded = await _read(store.open_request(result.source))
        assert b'name="note"\r\n\r\nok' in encoded
        assert b"filename*=UTF-8''plain.txt" in encoded
        await store.close()

    asyncio.run(scenario())


def test_unknown_existing_source_maps_to_source_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        missing = RepeatableByteSource(
            source_id=_body_id(230),
            byte_count=ByteCount(value=1),
            digest=Sha256Digest(hex_value="0" * 64),
            provenance=provenance(),
        )
        with pytest.raises(BodyMaterializationError) as failure:
            await RequestBodyMaterializer(store, _limits()).materialize(
                BytesBody(value=missing),
                body_id=_body_id(231),
                provenance=provenance(),
            )
        assert failure.value.code is BodyMaterializationFailureCode.SOURCE_FAILED
        assert store.reserved_bytes.value == 0
        await store.close()

    asyncio.run(scenario())


def test_store_configuration_and_constructor_contain_filesystem_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="maximum bytes"):
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=1),
            maximum_transient_bytes=1,  # type: ignore[arg-type]
            io_chunk_size=PositiveCount(value=1),
        )
    with pytest.raises(TypeError, match="chunk size"):
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=1),
            maximum_transient_bytes=ByteCount(value=1),
            io_chunk_size=1,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match=r"pathlib\.Path"):
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=1),
            maximum_transient_bytes=ByteCount(value=1),
            io_chunk_size=PositiveCount(value=1),
            root=cast("Path", object()),
        )
    with pytest.raises(TypeError, match="requires TransientBodyStoreConfig"):
        TransientBodyStore(cast("TransientBodyStoreConfig", object()))

    missing = tmp_path / "missing"
    with pytest.raises(BodyStoreError) as unresolved:
        TransientBodyStore(_config(missing))
    assert unresolved.value.code is BodyStoreFailureCode.IO_FAILED
    file_root = tmp_path / "file"
    file_root.write_bytes(b"not-a-directory")
    with pytest.raises(BodyStoreError) as not_directory:
        TransientBodyStore(_config(file_root))
    assert not_directory.value.code is BodyStoreFailureCode.IO_FAILED

    def fail_mkdtemp(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise OSError

    monkeypatch.setattr(tempfile, "mkdtemp", fail_mkdtemp)
    with pytest.raises(BodyStoreError) as creation:
        TransientBodyStore(_config(tmp_path))
    assert creation.value.code is BodyStoreFailureCode.IO_FAILED


def test_store_writer_validates_sensitivity_descriptors_and_terminal_use(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        invalid_calls = (
            {
                "body_id": object(),
                "maximum_body_bytes": ByteCount(value=1),
                "sensitivity": Sensitivity.PUBLIC,
            },
            {
                "body_id": _body_id(240),
                "maximum_body_bytes": ByteCount(value=1),
                "sensitivity": object(),
            },
            {
                "body_id": _body_id(241),
                "maximum_body_bytes": ByteCount(value=1),
                "sensitivity": Sensitivity.PUBLIC,
                "origins": [_ORIGIN],
            },
        )
        for call in invalid_calls:
            with pytest.raises(TypeError):
                await store.begin(**call)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="must agree"):
            await store.begin(
                _body_id(242),
                maximum_body_bytes=ByteCount(value=1),
                sensitivity=Sensitivity.SENSITIVE,
            )
        with pytest.raises(ValueError, match="must agree"):
            await store.begin(
                _body_id(243),
                maximum_body_bytes=ByteCount(value=1),
                sensitivity=Sensitivity.PUBLIC,
                origins=(_ORIGIN,),
            )

        writer = await store.begin(
            _body_id(244),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        assert writer.byte_count.value == 0
        await writer.append(b"")
        await writer.append(b"ab")
        await writer.append(b"cde")
        assert writer.byte_count.value == 5
        source = await writer.commit_request(provenance())
        entry = store._entries[source.source_id]
        assert entry.path is not None
        assert entry.path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(BodyStoreError) as append_closed:
            await writer.append(b"x")
        assert append_closed.value.code is BodyStoreFailureCode.CLOSED
        with pytest.raises(BodyStoreError) as commit_closed:
            await writer.commit_request(provenance())
        assert commit_closed.value.code is BodyStoreFailureCode.CLOSED

        invalid_writer = await store.begin(
            _body_id(245),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        with pytest.raises(TypeError, match="ValueProvenance"):
            await invalid_writer.commit_request(cast("ValueProvenance", object()))
        with pytest.raises(TypeError, match="BodyCaptureExtent"):
            await invalid_writer.commit_response(
                media=MissingMediaType(),
                extent=cast("BodyCaptureExtent", object()),
            )
        await invalid_writer.abort()
        await store.close()

    asyncio.run(scenario())


def test_store_input_types_tampered_response_and_closed_stream_are_rejected(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        with pytest.raises(TypeError, match="RepeatableByteSource"):
            store.open_request(cast("RepeatableByteSource", object()))
        with pytest.raises(TypeError, match="BodyHandle"):
            store.open_response(object())  # type: ignore[arg-type]
        writer = await store.begin(
            _body_id(250),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"body")
            handle = await writer.commit_response(
                media=MissingMediaType(), extent=BodyCaptureExtent.COMPLETE
            )
        tampered = msgspec.structs.replace(handle, byte_count=ByteCount(value=3))
        with pytest.raises(BodyStoreError) as mismatch:
            store.open_response(tampered)
        assert mismatch.value.code is BodyStoreFailureCode.DESCRIPTOR_MISMATCH
        stream = store.open_response(handle)
        await store.close()
        with pytest.raises(BodyStoreError) as closed_stream:
            await _read(stream)
        assert closed_stream.value.code is BodyStoreFailureCode.CLOSED
        with pytest.raises(BodyStoreError) as closed_writer:
            await store.begin(
                _body_id(251),
                maximum_body_bytes=ByteCount(value=1),
                sensitivity=Sensitivity.PUBLIC,
            )
        assert closed_writer.value.code is BodyStoreFailureCode.CLOSED

    asyncio.run(scenario())


def test_disk_reader_failure_is_contained_without_exposing_spool_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=1))
        writer = await store.begin(
            _body_id(260),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"disk")
            source = await writer.commit_request(provenance())
        path = store._entries[source.source_id].path
        assert path is not None
        path.unlink()
        with pytest.raises(BodyStoreError) as failure:
            await _read(store.open_request(source))
        assert failure.value.code is BodyStoreFailureCode.IO_FAILED
        assert str(path) not in str(failure.value)
        await store.close()

    asyncio.run(scenario())


def test_concurrent_close_callers_wait_for_the_same_active_reader(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        writer = await store.begin(
            _body_id(270),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"abcd")
            source = await writer.commit_request(provenance())
        stream = store.open_request(source)
        started = asyncio.Event()
        release = asyncio.Event()

        async def reader() -> None:
            async for _chunk in stream:
                started.set()
                await release.wait()

        read_task = asyncio.create_task(reader())
        await started.wait()
        first = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        second = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not first.done()
        assert not second.done()
        release.set()
        await read_task
        await asyncio.gather(first, second)

    asyncio.run(scenario())


def test_roll_failure_releases_latest_reservation_and_context_aborts_prior_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=2))
        writer = await store.begin(
            _body_id(280),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        await writer.append(b"a")

        async def fail_roll(writer_self: object) -> None:
            del writer_self
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)

        monkeypatch.setattr(storage_module.TransientBodyWriter, "_roll_to_disk", fail_roll)
        with pytest.raises(BodyStoreError) as failure:
            await writer.append(b"bc")
        assert failure.value.code is BodyStoreFailureCode.IO_FAILED
        assert store.reserved_bytes.value == 1
        await writer.abort()
        assert store.reserved_bytes.value == 0
        await store.close()

    asyncio.run(scenario())


def test_unlink_failure_is_mapped_to_value_free_store_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_unlink(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError

    async def scenario() -> None:
        monkeypatch.setattr(asyncio, "to_thread", fail_unlink)
        with pytest.raises(BodyStoreError) as failure:
            await storage_module._unlink(tmp_path / "body")
        assert failure.value.code is BodyStoreFailureCode.IO_FAILED

    asyncio.run(scenario())


def test_default_spool_root_and_invariant_body_stream_failure_are_contained() -> None:
    async def scenario() -> None:
        store = TransientBodyStore(
            TransientBodyStoreConfig(
                memory_threshold=ByteCount(value=4),
                maximum_transient_bytes=ByteCount(value=16),
                io_chunk_size=PositiveCount(value=2),
            )
        )
        writer = await store.begin(
            _body_id(290),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"body")
            source = await writer.commit_request(provenance())
        entry = store._entries[source.source_id]
        entry.memory = None
        entry.path = None
        with pytest.raises(BodyStoreError) as failure:
            await _read(store.open_request(source))
        assert failure.value.code is BodyStoreFailureCode.IO_FAILED
        await store.close()

    asyncio.run(scenario())


def test_aborting_disk_writer_removes_uncommitted_spool_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=1))
        writer = await store.begin(
            _body_id(300),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        await writer.append(b"disk")
        path = writer._path
        assert path is not None
        assert path.exists()
        await writer.abort()
        assert not path.exists()
        await store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_point", ["open", "chmod"])
def test_spool_creation_failures_close_partial_files_and_release_reservations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    original_open = Path.open
    original_chmod = Path.chmod

    def controlled_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> BinaryIO:
        if failure_point == "open" and path.suffix == ".body":
            raise OSError
        return cast(
            "BinaryIO",
            original_open(path, mode, buffering, encoding, errors, newline),
        )

    def controlled_chmod(path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        if failure_point == "chmod" and path.suffix == ".body":
            raise OSError
        original_chmod(path, mode, follow_symlinks=follow_symlinks)

    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=1))
        writer = await store.begin(
            _body_id(310),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        monkeypatch.setattr(Path, "open", controlled_open)
        monkeypatch.setattr(Path, "chmod", controlled_chmod)
        with pytest.raises(BodyStoreError) as failure:
            await writer.append(b"disk")
        assert failure.value.code is BodyStoreFailureCode.IO_FAILED
        assert store.reserved_bytes.value == 0
        await writer.abort()
        await store.close()

    asyncio.run(scenario())


def test_writer_flush_failure_and_closed_store_internal_operations_are_contained(
    tmp_path: Path,
) -> None:
    class FailingFile:
        def flush(self) -> None:
            raise OSError

        def close(self) -> None:
            return

    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        flush_writer = await store.begin(
            _body_id(320),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        flush_writer._file = cast("BinaryIO", FailingFile())
        with pytest.raises(BodyStoreError) as flush_failure:
            await flush_writer.commit_request(provenance())
        assert flush_failure.value.code is BodyStoreFailureCode.IO_FAILED
        await flush_writer.abort()

        commit_writer = await store.begin(
            _body_id(321),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        store._closed = True
        with pytest.raises(BodyStoreError) as commit_failure:
            await commit_writer.commit_request(provenance())
        assert commit_failure.value.code is BodyStoreFailureCode.CLOSED
        with pytest.raises(BodyStoreError) as reserve_failure:
            await store._reserve(1)
        assert reserve_failure.value.code is BodyStoreFailureCode.CLOSED
        store._closed = False
        await commit_writer.abort()
        await store.close()

    asyncio.run(scenario())


def test_abort_attempts_all_cleanup_after_file_close_or_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCloseFile:
        def flush(self) -> None:
            return

        def close(self) -> None:
            raise OSError

    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=1))
        close_writer = await store.begin(
            _body_id(325),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        await close_writer.append(b"disk")
        assert close_writer._file is not None
        await asyncio.to_thread(close_writer._file.close)
        close_writer._file = cast("BinaryIO", FailingCloseFile())
        close_path = close_writer._path
        with pytest.raises(BodyStoreError) as close_failure:
            await close_writer.abort()
        assert close_failure.value.code is BodyStoreFailureCode.IO_FAILED
        assert close_path is not None
        assert not close_path.exists()
        assert store.reserved_bytes.value == 0

        unlink_writer = await store.begin(
            _body_id(326),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        await unlink_writer.append(b"disk")
        unlink_path = unlink_writer._path
        assert unlink_path is not None

        async def fail_unlink(path: Path) -> None:
            del path
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)

        with monkeypatch.context() as context:
            context.setattr(storage_module, "_unlink", fail_unlink)
            with pytest.raises(BodyStoreError) as unlink_failure:
                await unlink_writer.abort()
        assert unlink_failure.value.code is BodyStoreFailureCode.IO_FAILED
        assert store.reserved_bytes.value == 0
        unlink_path.unlink()
        await store.close()

    asyncio.run(scenario())


def test_cancelled_close_still_completes_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        directory = store._directory
        started = asyncio.Event()
        release = asyncio.Event()
        original_finish = TransientBodyStore._finish_close

        async def controlled_finish(
            close_store: TransientBodyStore,
            entries: tuple[storage_module._StoredEntry, ...],
        ) -> None:
            started.set()
            await release.wait()
            await original_finish(close_store, entries)

        monkeypatch.setattr(TransientBodyStore, "_finish_close", controlled_finish)
        close_task = asyncio.create_task(store.close())
        await started.wait()
        close_task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await close_task
        assert store._closed
        assert not directory.exists()

    asyncio.run(scenario())


def test_close_attempts_directory_cleanup_after_spool_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path, memory=1))
        writer = await store.begin(
            _body_id(327),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        async with writer:
            await writer.append(b"disk")
            source = await writer.commit_request(provenance())
        path = store._entries[source.source_id].path
        directory = store._directory
        assert path is not None

        async def fail_unlink(unlink_path: Path) -> None:
            del unlink_path
            raise BodyStoreError(BodyStoreFailureCode.IO_FAILED)

        with monkeypatch.context() as context:
            context.setattr(storage_module, "_unlink", fail_unlink)
            with pytest.raises(BodyStoreError) as failure:
                await store.close()
        assert failure.value.code is BodyStoreFailureCode.IO_FAILED
        assert store._closed
        path.unlink()
        directory.rmdir()

    asyncio.run(scenario())


def test_spool_directory_cleanup_failure_is_value_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = TransientBodyStore(_config(tmp_path))
        directory = store._directory
        original_rmdir = Path.rmdir

        def fail_store_rmdir(path: Path) -> None:
            if path == directory:
                raise OSError
            original_rmdir(path)

        with monkeypatch.context() as context:
            context.setattr(Path, "rmdir", fail_store_rmdir)
            with pytest.raises(BodyStoreError) as failure:
                await store.close()
        assert failure.value.code is BodyStoreFailureCode.IO_FAILED
        assert str(directory) not in str(failure.value)
        directory.rmdir()

    asyncio.run(scenario())
