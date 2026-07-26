"""Remote schema policy, routing, and value-free failure tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import override

import msgspec
import pytest

from comparo.application.remote_schemas import DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1
from comparo.application.remote_schemas import RemoteSchemaFailureCode
from comparo.application.remote_schemas import RemoteSchemaFetchLimits
from comparo.application.remote_schemas import RemoteSchemaLoader
from comparo.application.remote_schemas import RemoteSchemaLoadError
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
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
