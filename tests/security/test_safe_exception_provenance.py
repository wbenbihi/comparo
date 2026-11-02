"""Regression tests for value-free exception boundaries and their hidden graphs."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO
from typing import cast
from typing import override
from uuid import UUID

import pytest

from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import local as local_artifacts
from comparo.adapters.http import materialization
from comparo.adapters.http.materialization import BodyMaterializationError
from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.adapters.http.storage import TransientBodyWriter
from comparo.adapters.http.tls import ClientIdentityFiles
from comparo.adapters.http.tls import ExplicitTlsContextProvider
from comparo.adapters.http.tls import TlsConfigurationError
from comparo.application import artifacts
from comparo.application import production_execution
from comparo.application import remote_schemas
from comparo.application.artifacts import ArtifactOperationError
from comparo.application.planning import InlineValueOverride
from comparo.application.planning import InvocationBuildError
from comparo.application.planning import ValueResourceOverride
from comparo.application.remote_schemas import DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1
from comparo.application.remote_schemas import RemoteSchemaLoader
from comparo.application.remote_schemas import RemoteSchemaLoadError
from comparo.application.schema_validation import DEFAULT_SCHEMA_VALIDATION_LIMITS_V1
from comparo.application.schema_validation import prepare_remote_schema
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartDispositionName
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import BytesValue
from comparo.core.policy.trusted import trusted_local_policy
from tests.application.test_artifacts import _limits as artifact_limits
from tests.core.domain.http.factories import provenance


def _assert_severed(error: BaseException, canary: str) -> None:
    """Require a one-node exception graph containing no source-bearing value."""
    graph: list[BaseException] = []
    pending = [error]
    while pending:
        current = pending.pop()
        graph.append(current)
        pending.extend(
            related for related in (current.__cause__, current.__context__) if related is not None
        )
    assert graph == [error]
    assert canary not in repr(error)
    assert canary not in str(error)


def test_artifact_boundary_severs_path_bearing_store_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "artifact-secret-path-canary"

    def fail(*_args: object, **_kwargs: object) -> object:
        try:
            raise OSError(2, "missing", str(tmp_path / canary))
        except OSError as source:
            raise LocalArtifactStoreError(ArtifactStoreErrorCode.IO_FAILED) from source

    monkeypatch.setattr(artifacts, "read_local_artifact_bundle", fail)
    with pytest.raises(ArtifactOperationError) as caught:
        artifacts.verify_artifact(tmp_path.resolve(), "bundle", artifact_limits())
    _assert_severed(caught.value, canary)


def test_local_artifact_codec_boundary_severs_payload_parser_failures() -> None:
    canary = "artifact-payload-canary"
    with pytest.raises(LocalArtifactStoreError) as caught:
        local_artifacts._decode_bundle_manifest(f'{{"{canary}":'.encode())
    _assert_severed(caught.value, canary)


def test_tls_and_body_store_boundaries_sever_missing_paths(tmp_path: Path) -> None:
    canary = "missing-material-canary"
    missing = (tmp_path / canary).resolve()
    with pytest.raises(TlsConfigurationError) as tls:
        ClientIdentityFiles(certificate_chain=missing, private_key=missing)
    _assert_severed(tls.value, canary)
    with pytest.raises(TlsConfigurationError) as registry:
        ExplicitTlsContextProvider(trust_stores={"ca": missing})
    _assert_severed(registry.value, canary)

    config = TransientBodyStoreConfig(
        memory_threshold=ByteCount(value=1),
        maximum_transient_bytes=ByteCount(value=1),
        io_chunk_size=PositiveCount(value=1),
        root=missing,
    )
    with pytest.raises(BodyStoreError) as store:
        TransientBodyStore(config)
    _assert_severed(store.value, canary)


def test_body_store_rejects_and_cleans_up_failed_private_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "body-store-chmod-canary"
    original_chmod = Path.chmod

    def fail_private_chmod(path: Path, mode: int) -> None:
        if path.name.startswith("comparo-body-"):
            raise OSError(1, canary, str(path / canary))
        original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", fail_private_chmod)
    config = TransientBodyStoreConfig(
        memory_threshold=ByteCount(value=1),
        maximum_transient_bytes=ByteCount(value=1),
        io_chunk_size=PositiveCount(value=1),
        root=tmp_path,
    )
    with pytest.raises(BodyStoreError) as caught:
        TransientBodyStore(config)
    _assert_severed(caught.value, canary)
    assert tuple(tmp_path.glob("comparo-body-*")) == ()


def test_body_store_writer_severs_spill_io_failures(tmp_path: Path) -> None:
    canary = "body-spill-path-canary"

    class FailingFile:
        def write(self, _value: bytes) -> int:
            raise OSError(5, canary, str(tmp_path / canary))

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    async def scenario() -> None:
        store = TransientBodyStore(
            TransientBodyStoreConfig(
                memory_threshold=ByteCount(value=1),
                maximum_transient_bytes=ByteCount(value=8),
                io_chunk_size=PositiveCount(value=1),
                root=tmp_path,
            )
        )
        writer = await store.begin(
            BodyId(value=UUID(int=900)),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        writer._file = cast("BinaryIO", FailingFile())
        with pytest.raises(BodyStoreError) as caught:
            await writer.append(b"x")
        _assert_severed(caught.value, canary)
        await writer.abort()
        await store.close()

    asyncio.run(scenario())


def test_body_store_writer_preserves_cancellation_after_releasing_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel_rollover(_writer: TransientBodyWriter) -> None:
        raise asyncio.CancelledError

    async def scenario() -> None:
        store = TransientBodyStore(
            TransientBodyStoreConfig(
                memory_threshold=ByteCount(value=0),
                maximum_transient_bytes=ByteCount(value=8),
                io_chunk_size=PositiveCount(value=1),
                root=tmp_path,
            )
        )
        writer = await store.begin(
            BodyId(value=UUID(int=901)),
            maximum_body_bytes=ByteCount(value=8),
            sensitivity=Sensitivity.PUBLIC,
        )
        monkeypatch.setattr(TransientBodyWriter, "_roll_to_disk", cancel_rollover)
        with pytest.raises(asyncio.CancelledError):
            await writer.append(b"x")
        assert store.reserved_bytes.value == 0
        await writer.abort()
        await store.close()

    asyncio.run(scenario())


def test_materialization_boundary_severs_invalid_secret_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "request-body-secret-canary"
    malformed = cast("BodyText", SimpleNamespace(value=f"{canary}\ud800"))
    with pytest.raises(BodyMaterializationError) as caught:
        materialization._encode_text(malformed)
    _assert_severed(caught.value, canary)

    part = MultipartFilePart(
        name=MultipartDispositionName(value="upload"),
        filename=MultipartDispositionFilename(value="safe.txt"),
        value=BytesValue(value=b"safe"),
        media_type=MediaType(type="application", subtype="octet-stream"),
    )

    def invalid_quote(*_args: object, **_kwargs: object) -> str:
        raise UnicodeEncodeError("utf-8", f"{canary}\ud800", 0, 1, "invalid")

    monkeypatch.setattr(materialization, "quote", invalid_quote)
    with pytest.raises(BodyMaterializationError) as filename:
        materialization._file_part_headers(part)
    _assert_severed(filename.value, canary)


def test_remote_schema_boundaries_sever_dns_and_parser_sources() -> None:
    canary = "remote-schema-secret-canary"

    class Resolver:
        async def resolve(self, host: DnsHost, port: HttpPort) -> tuple[IpHost, ...]:
            del host, port
            raise OSError(2, canary, f"/{canary}/schema.json")

        async def close(self) -> None:
            return None

    loader = RemoteSchemaLoader(
        DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
        resolver=Resolver(),
    )
    binding = RemoteSchema(uri="https://schemas.example.test/schema.json")
    with pytest.raises(RemoteSchemaLoadError) as fetched:
        asyncio.run(loader.load((binding,), trusted_local_policy()))
    _assert_severed(fetched.value, canary)

    with pytest.raises(ValueError, match="not valid UTF-8 JSON") as parsed:
        prepare_remote_schema(
            binding,
            f'{{"{canary}":'.encode(),
            DEFAULT_SCHEMA_VALIDATION_LIMITS_V1,
        )
    _assert_severed(parsed.value, canary)

    with pytest.raises(RemoteSchemaLoadError) as invalid_uri:
        remote_schemas._destination(f"https://schemas.example.test:{canary}/schema.json")
    _assert_severed(invalid_uri.value, canary)
    with pytest.raises(RemoteSchemaLoadError) as invalid_split:
        remote_schemas._destination(f"https://[{canary}")
    _assert_severed(invalid_split.value, canary)


def test_remote_schema_boundary_severs_cleanup_and_transport_plugins() -> None:
    canary = "remote-plugin-secret-canary"
    binding = RemoteSchema(uri="https://schemas.example.test/schema.json")

    class Resolver:
        def __init__(self, *, fail_close: bool) -> None:
            self.fail_close = fail_close

        async def resolve(self, host: DnsHost, port: HttpPort) -> tuple[IpHost, ...]:
            del host, port
            return (Ipv4Host(value="93.184.216.34"),)

        async def close(self) -> None:
            if self.fail_close:
                raise RuntimeError(canary)

    class MemoryLoader(RemoteSchemaLoader):
        @override
        async def _request(self, uri: str, destination: NetworkDestination) -> bytes:
            del uri, destination
            return b"{}"

    class RuntimeFailureLoader(RemoteSchemaLoader):
        @override
        async def _request(self, uri: str, destination: NetworkDestination) -> bytes:
            del uri, destination
            raise RuntimeError(canary)

    with pytest.raises(RemoteSchemaLoadError) as cleanup:
        asyncio.run(
            MemoryLoader(
                DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
                resolver=Resolver(fail_close=True),
            ).load((binding,), trusted_local_policy())
        )
    _assert_severed(cleanup.value, canary)

    with pytest.raises(RemoteSchemaLoadError) as transport:
        asyncio.run(
            RuntimeFailureLoader(
                DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
                resolver=Resolver(fail_close=False),
            ).load((binding,), trusted_local_policy())
        )
    _assert_severed(transport.value, canary)


def test_annotation_and_planning_boundaries_sever_authored_values() -> None:
    canary = "annotation-secret-canary"
    with pytest.raises(ValueError, match="Unicode scalars") as annotation:
        production_execution._annotation_text(f"{canary}\ud800", maximum=100)
    _assert_severed(annotation.value, canary)

    with pytest.raises(InvocationBuildError) as planning:
        ValueResourceOverride(
            namespace=ValueNamespace.SECRETS,
            environment=f"{canary}/invalid",
            resource="credentials",
        )
    _assert_severed(planning.value, canary)
    assert (
        InlineValueOverride(
            namespace=ValueNamespace.VARIABLES,
            environment="staging",
            expression=ObjectExpr(members=(), provenance=provenance()),
        ).environment
        == "staging"
    )


def test_missing_artifact_root_does_not_retain_its_path(tmp_path: Path) -> None:
    canary = "missing-artifact-root-canary"
    with pytest.raises(ArtifactOperationError) as caught:
        artifacts.validate_existing_artifact_root((tmp_path / canary).resolve())
    _assert_severed(caught.value, canary)


def test_artifact_root_fstat_failure_is_collapsed_without_path_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "artifact-root-fstat-canary"
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)

    def fail_fstat(_descriptor: int) -> object:
        raise OSError(5, canary, str(root / canary))

    monkeypatch.setattr(os, "fstat", fail_fstat)
    with pytest.raises(ArtifactOperationError) as caught:
        artifacts.validate_existing_artifact_root(root.resolve())
    _assert_severed(caught.value, canary)


def test_local_artifact_sink_severs_validation_and_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "artifact-sink-root-canary"

    def fail_validation(_descriptor: int) -> None:
        raise OSError(5, canary, f"/{canary}/artifacts")

    def fail_close(_descriptor: int) -> None:
        raise OSError(5, canary, f"/{canary}/descriptor")

    with monkeypatch.context() as context:
        context.setattr(local_artifacts, "_atomic_rename_supported", lambda: True)
        context.setattr(os, "open", lambda *_args, **_kwargs: 42)
        context.setattr(local_artifacts, "_validate_root_fd", fail_validation)
        context.setattr(os, "close", fail_close)
        with pytest.raises(LocalArtifactStoreError) as caught:
            local_artifacts.LocalArtifactBundleSink(Path("/explicit/root"))
    _assert_severed(caught.value, canary)

    with monkeypatch.context() as context:
        context.setattr(os, "close", fail_close)
        context.setattr(os, "rmdir", lambda *_args, **_kwargs: None)
        local_artifacts._cleanup_partial_creation(
            42,
            ".staging-bundle-token",
            {"blob_fd": 43, "staging_fd": 44},
        )
