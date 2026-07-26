"""Remote schema policy, routing, and value-free failure tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from collections.abc import Mapping
from typing import ClassVar
from typing import cast
from typing import override

import msgspec
import pytest

from comparo.application import remote_schemas as remote_module
from comparo.application.remote_schemas import DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1
from comparo.application.remote_schemas import RemoteSchemaFailureCode
from comparo.application.remote_schemas import RemoteSchemaFetchLimits
from comparo.application.remote_schemas import RemoteSchemaLoader
from comparo.application.remote_schemas import RemoteSchemaLoadError
from comparo.application.remote_schemas import remote_schema_allowed
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import NetworkAddressClass
from comparo.core.policy import RemoteSchemaPolicy
from comparo.core.policy.trusted import trusted_local_policy


class _Resolver:
    def __init__(self, addresses: tuple[IpHost, ...]) -> None:
        self.addresses = addresses
        self.calls = 0
        self.closed = False

    async def resolve(self, host: DnsHost, port: HttpPort) -> tuple[IpHost, ...]:
        del host, port
        self.calls += 1
        return self.addresses

    async def close(self) -> None:
        self.closed = True


class _MemoryLoader(RemoteSchemaLoader):
    @override
    async def _request(self, uri: str, destination: NetworkDestination) -> bytes:
        del uri, destination
        return b'{"type":"object"}'


class _PayloadLoader(RemoteSchemaLoader):
    def __init__(
        self,
        payloads: Mapping[str, bytes],
        *,
        delay: float = 0,
        resolver: _Resolver,
        limits: RemoteSchemaFetchLimits = DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
    ) -> None:
        super().__init__(limits, resolver=resolver)
        self._payloads = payloads
        self._delay = delay
        self.calls = 0

    @override
    async def _request(self, uri: str, destination: NetworkDestination) -> bytes:
        del destination
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._payloads[uri]


class _FailingResolver(_Resolver):
    @override
    async def resolve(self, host: DnsHost, port: HttpPort) -> tuple[IpHost, ...]:
        del host, port
        self.calls += 1
        raise OSError("value-free test failure")


class _NetworkFailureLoader(RemoteSchemaLoader):
    @override
    async def _request(self, uri: str, destination: NetworkDestination) -> bytes:
        del uri, destination
        raise OSError("value-free test failure")


class _TypedFailureLoader(RemoteSchemaLoader):
    @override
    async def _request(self, uri: str, destination: NetworkDestination) -> bytes:
        del uri, destination
        raise RemoteSchemaLoadError(RemoteSchemaFailureCode.EMPTY)


class _FakeResponse:
    def __init__(self, status: int, chunks: tuple[bytes, ...]) -> None:
        self.status = status
        self._chunks = chunks
        self.closed = False

    async def aiter_stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _FakePool:
    status = 200
    chunks: tuple[bytes, ...] = (b"{}",)
    request_error: Exception | None = None
    instances: ClassVar[list[_FakePool]] = []

    def __init__(self, **_kwargs: object) -> None:
        self.response: _FakeResponse | None = None
        self.closed = False
        self.__class__.instances.append(self)

    async def request(self, **_kwargs: object) -> _FakeResponse:
        if self.request_error is not None:
            raise self.request_error
        self.response = _FakeResponse(self.status, self.chunks)
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def _limits(
    *,
    documents: int = 64,
    document_bytes: int = 8 * 1024 * 1024,
    total_bytes: int = 64 * 1024 * 1024,
    batch_nanoseconds: int = 120_000_000_000,
) -> RemoteSchemaFetchLimits:
    return msgspec.structs.replace(
        DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
        maximum_documents=PositiveCount(value=documents),
        maximum_document_bytes=ByteCount(value=document_bytes),
        maximum_total_bytes=ByteCount(value=total_bytes),
        batch_timeout=Duration(nanoseconds=batch_nanoseconds),
    )


def test_remote_loader_requires_exact_source_authority_before_dns() -> None:
    policy = trusted_local_policy()
    denied_values = msgspec.structs.replace(
        policy.values,
        remote_schemas=RemoteSchemaPolicy(sources=(), allow_any=False),
    )
    denied = msgspec.structs.replace(policy, values=denied_values)
    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    loader = _MemoryLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=resolver)

    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            loader.load(
                (RemoteSchema(uri="https://schemas.example.test/schema.json"),),
                denied,
            )
        )

    assert captured.value.code is RemoteSchemaFailureCode.NOT_AUTHORIZED
    assert resolver.calls == 0
    assert resolver.closed


def test_remote_loader_rejects_disallowed_resolved_address_classes() -> None:
    policy = trusted_local_policy()
    public_http = msgspec.structs.replace(
        policy.http,
        allowed_address_classes=(NetworkAddressClass.PUBLIC,),
    )
    public_only = msgspec.structs.replace(policy, http=public_http)
    resolver = _Resolver((Ipv4Host(value="127.0.0.1"),))
    loader = _MemoryLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=resolver)

    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            loader.load(
                (RemoteSchema(uri="https://schemas.example.test/schema.json"),),
                public_only,
            )
        )

    assert captured.value.code is RemoteSchemaFailureCode.DESTINATION_DENIED
    assert resolver.calls == 1
    assert resolver.closed


def test_remote_loader_deduplicates_and_canonicalizes_fetched_documents() -> None:
    uri = "https://schemas.example.test/schema.json"
    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    loader = _MemoryLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=resolver)

    documents = asyncio.run(
        loader.load(
            (RemoteSchema(uri=uri), RemoteSchema(uri=uri)),
            trusted_local_policy(),
        )
    )

    assert tuple(item.uri for item in documents.entries) == (uri,)
    assert documents.entries[0].payload == b'{"type":"object"}'
    assert resolver.calls == 1
    assert resolver.closed


def test_remote_loader_denies_get_when_execution_policy_does_not_allow_it() -> None:
    policy = trusted_local_policy()
    denied = msgspec.structs.replace(
        policy,
        http=msgspec.structs.replace(
            policy.http,
            allowed_methods=(HttpMethod(value="POST"),),
        ),
    )
    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    loader = _MemoryLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=resolver)

    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            loader.load(
                (RemoteSchema(uri="https://schemas.example.test/schema.json"),),
                denied,
            )
        )

    assert captured.value.code is RemoteSchemaFailureCode.DESTINATION_DENIED
    assert resolver.calls == 0
    assert resolver.closed


def test_remote_loader_rejects_document_count_before_dns_or_network() -> None:
    first = "https://schemas.example.test/first.json"
    second = "https://schemas.example.test/second.json"
    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    loader = _PayloadLoader(
        {first: b"{}", second: b"{}"},
        resolver=resolver,
        limits=_limits(documents=1),
    )

    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            loader.load(
                (RemoteSchema(uri=first), RemoteSchema(uri=second)),
                trusted_local_policy(),
            )
        )

    assert captured.value.code is RemoteSchemaFailureCode.DOCUMENT_LIMIT_EXCEEDED
    assert resolver.calls == 0
    assert loader.calls == 0
    assert resolver.closed


def test_remote_loader_accounts_aggregate_retained_bytes_incrementally() -> None:
    first = "https://schemas.example.test/first.json"
    second = "https://schemas.example.test/second.json"
    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    loader = _PayloadLoader(
        {first: b"{" + b" " * 14 + b"}", second: b"{" + b" " * 14 + b"}"},
        resolver=resolver,
        limits=_limits(document_bytes=20, total_bytes=20),
    )

    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            loader.load(
                (RemoteSchema(uri=first), RemoteSchema(uri=second)),
                trusted_local_policy(),
            )
        )

    assert captured.value.code is RemoteSchemaFailureCode.AGGREGATE_SIZE_EXCEEDED
    assert resolver.calls == 2
    assert loader.calls == 2
    assert resolver.closed


def test_remote_loader_applies_one_deadline_to_the_complete_batch() -> None:
    uri = "https://schemas.example.test/schema.json"
    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    loader = _PayloadLoader(
        {uri: b"{}"},
        delay=0.01,
        resolver=resolver,
        limits=_limits(batch_nanoseconds=1_000_000),
    )

    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(loader.load((RemoteSchema(uri=uri),), trusted_local_policy()))

    assert captured.value.code is RemoteSchemaFailureCode.NETWORK_FAILED
    assert resolver.calls == 1
    assert loader.calls == 1
    assert resolver.closed


def test_remote_schema_models_and_loader_reject_untyped_limits() -> None:
    with pytest.raises(TypeError, match="failure code"):
        RemoteSchemaLoadError(cast("RemoteSchemaFailureCode", "invalid"))
    for field, value, error in (
        ("maximum_document_bytes", cast("ByteCount", object()), TypeError),
        ("maximum_total_bytes", cast("ByteCount", object()), TypeError),
        ("maximum_documents", cast("PositiveCount", object()), TypeError),
        ("maximum_document_bytes", ByteCount(value=0), ValueError),
        ("maximum_total_bytes", ByteCount(value=0), ValueError),
        ("maximum_document_bytes", ByteCount(value=9), ValueError),
        ("connect_timeout", cast("Duration", object()), ValueError),
        ("read_timeout", Duration(nanoseconds=0), ValueError),
        ("total_timeout", Duration(nanoseconds=0), ValueError),
        ("batch_timeout", Duration(nanoseconds=0), ValueError),
    ):
        changes: dict[str, object] = {field: value}
        if field == "maximum_document_bytes" and value == ByteCount(value=9):
            changes["maximum_total_bytes"] = ByteCount(value=8)
        with pytest.raises(error):
            msgspec.structs.replace(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, **changes)

    with pytest.raises(TypeError, match="typed limits"):
        RemoteSchemaLoader(cast("RemoteSchemaFetchLimits", object()))

    loader = RemoteSchemaLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=_Resolver(()))
    with pytest.raises(TypeError, match="immutable typed bindings"):
        asyncio.run(
            loader.load(
                cast("tuple[RemoteSchema, ...]", [RemoteSchema(uri="https://example.test/a")]),
                trusted_local_policy(),
            )
        )
    loader = RemoteSchemaLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=_Resolver(()))
    with pytest.raises(TypeError, match="compiled ExecutionPolicy"):
        asyncio.run(
            loader.load(
                (),
                cast("CompiledExecutionPolicy", object()),
            )
        )


def test_remote_loader_reports_dns_and_network_failures_without_uri_values() -> None:
    uri = "https://schemas.example.test/schema.json"
    failing = _FailingResolver(())
    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            _MemoryLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=failing).load(
                (RemoteSchema(uri=uri),),
                trusted_local_policy(),
            )
        )
    assert captured.value.code is RemoteSchemaFailureCode.DNS_FAILED
    assert uri not in str(captured.value)

    empty = _Resolver(())
    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            _MemoryLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=empty).load(
                (RemoteSchema(uri=uri),),
                trusted_local_policy(),
            )
        )
    assert captured.value.code is RemoteSchemaFailureCode.DNS_FAILED

    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            _NetworkFailureLoader(
                DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
                resolver=resolver,
            ).load((RemoteSchema(uri=uri),), trusted_local_policy())
        )
    assert captured.value.code is RemoteSchemaFailureCode.NETWORK_FAILED

    resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(
            _TypedFailureLoader(
                DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
                resolver=resolver,
            ).load((RemoteSchema(uri=uri),), trusted_local_policy())
        )
    assert captured.value.code is RemoteSchemaFailureCode.EMPTY


def test_remote_loader_applies_scheme_host_and_port_authority_independently() -> None:
    uri = "https://schemas.example.test/schema.json"
    base = trusted_local_policy()
    restrictions = (
        msgspec.structs.replace(
            base.http,
            allowed_schemes=(HttpScheme.HTTP,),
        ),
        msgspec.structs.replace(
            base.http,
            allow_any_host=False,
            allowed_hosts=(DnsHost(value="other.example.test"),),
        ),
        msgspec.structs.replace(
            base.http,
            allow_any_port=False,
            allowed_ports=(HttpPort(value=8443),),
        ),
    )
    for restriction in restrictions:
        resolver = _Resolver((Ipv4Host(value="93.184.216.34"),))
        policy = msgspec.structs.replace(base, http=restriction)
        with pytest.raises(RemoteSchemaLoadError) as captured:
            asyncio.run(
                _MemoryLoader(
                    DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1,
                    resolver=resolver,
                ).load((RemoteSchema(uri=uri),), policy)
            )
        assert captured.value.code is RemoteSchemaFailureCode.DESTINATION_DENIED
        assert resolver.calls == 0


def test_destination_and_source_authority_helpers_are_closed_and_canonical() -> None:
    https, ipv4, port = remote_module._destination("https://127.0.0.1/schema.json")
    assert https is HttpScheme.HTTPS
    assert ipv4 == Ipv4Host(value="127.0.0.1")
    assert port == HttpPort(value=443)
    http, ipv6, explicit = remote_module._destination("http://[::1]:8080/schema.json")
    assert http is HttpScheme.HTTP
    assert ipv6 == Ipv6Host(value="::1")
    assert explicit == HttpPort(value=8080)

    for uri in (
        "https://example.test:bad/schema.json",
        "file:///tmp/schema.json",
        "https:///schema.json",
        "https://\ud800.example/schema.json",
    ):
        with pytest.raises(RemoteSchemaLoadError) as captured:
            remote_module._destination(uri)
        assert captured.value.code is RemoteSchemaFailureCode.INVALID_URI

    source = "https://schemas.example.test/schema.json"
    base = trusted_local_policy()
    denied = msgspec.structs.replace(
        base,
        values=msgspec.structs.replace(
            base.values,
            remote_schemas=RemoteSchemaPolicy(sources=(), allow_any=False),
        ),
    )
    allowed_target = NamedCapabilityTarget(
        kind=CapabilityTargetKind.SCHEMA_SOURCE,
        identifier=source,
    )
    exact = msgspec.structs.replace(
        denied,
        values=msgspec.structs.replace(
            denied.values,
            remote_schemas=RemoteSchemaPolicy(sources=(allowed_target,), allow_any=False),
        ),
    )
    assert not remote_schema_allowed(source, denied)
    assert remote_schema_allowed(source, exact)
    assert remote_schema_allowed(source, base)


def test_direct_ip_hosts_bypass_dns_resolution() -> None:
    resolver = _Resolver(())
    loader = _MemoryLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=resolver)
    documents = asyncio.run(
        loader.load(
            (RemoteSchema(uri="https://93.184.216.34/schema.json"),),
            trusted_local_policy(),
        )
    )
    assert documents.entries
    assert resolver.calls == 0


@pytest.mark.parametrize(
    ("status", "chunks", "expected"),
    [
        (503, (b"{}",), RemoteSchemaFailureCode.STATUS_REJECTED),
        (200, (), RemoteSchemaFailureCode.EMPTY),
        (200, (b"123", b"456"), RemoteSchemaFailureCode.SIZE_EXCEEDED),
    ],
)
def test_request_stream_enforces_status_size_and_empty_payloads(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    chunks: tuple[bytes, ...],
    expected: RemoteSchemaFailureCode,
) -> None:
    _FakePool.instances = []
    _FakePool.status = status
    _FakePool.chunks = chunks
    _FakePool.request_error = None
    monkeypatch.setattr(
        "comparo.application.remote_schemas.httpcore.AsyncConnectionPool",
        _FakePool,
    )
    loader = RemoteSchemaLoader(
        _limits(document_bytes=5, total_bytes=5),
        resolver=_Resolver(()),
    )
    destination = NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="schemas.example.test"),
        port=HttpPort(value=443),
        resolved_addresses=(Ipv4Host(value="93.184.216.34"),),
        selected_address=Ipv4Host(value="93.184.216.34"),
    )
    with pytest.raises(RemoteSchemaLoadError) as captured:
        asyncio.run(loader._request("https://schemas.example.test/schema.json", destination))
    assert captured.value.code is expected
    pool = _FakePool.instances[0]
    assert pool.closed
    assert pool.response is not None
    assert pool.response.closed


def test_request_stream_returns_payload_and_closes_pool_on_request_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="schemas.example.test"),
        port=HttpPort(value=443),
        resolved_addresses=(Ipv4Host(value="93.184.216.34"),),
        selected_address=Ipv4Host(value="93.184.216.34"),
    )
    loader = RemoteSchemaLoader(DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1, resolver=_Resolver(()))
    _FakePool.instances = []
    _FakePool.status = 200
    _FakePool.chunks = (b"{", b"}")
    _FakePool.request_error = None
    monkeypatch.setattr(
        "comparo.application.remote_schemas.httpcore.AsyncConnectionPool",
        _FakePool,
    )
    assert (
        asyncio.run(loader._request("https://schemas.example.test/schema.json", destination))
        == b"{}"
    )
    assert _FakePool.instances[0].closed
    assert _FakePool.instances[0].response is not None
    assert _FakePool.instances[0].response.closed

    _FakePool.instances = []
    _FakePool.request_error = OSError("value-free test failure")
    with pytest.raises(OSError, match="value-free"):
        asyncio.run(loader._request("https://schemas.example.test/schema.json", destination))
    assert _FakePool.instances[0].closed
    assert _FakePool.instances[0].response is None
