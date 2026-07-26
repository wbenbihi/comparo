"""Remote schema policy, routing, and value-free failure tests."""

from __future__ import annotations

import asyncio
from typing import override

import msgspec
import pytest

from comparo.application.remote_schemas import DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1
from comparo.application.remote_schemas import RemoteSchemaFailureCode
from comparo.application.remote_schemas import RemoteSchemaLoader
from comparo.application.remote_schemas import RemoteSchemaLoadError
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
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
