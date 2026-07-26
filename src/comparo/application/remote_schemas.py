"""Policy-authorized, route-pinned, bounded remote JSON Schema loading."""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
from enum import StrEnum
from typing import final
from urllib.parse import urlsplit

import httpcore
import msgspec

from comparo.adapters.http.network import RoutePinnedNetworkBackend
from comparo.adapters.http.preparation import HostResolver
from comparo.adapters.http.preparation import SystemHostResolver
from comparo.adapters.http.preparation import network_address_class
from comparo.application.schema_validation import RemoteSchemaDocument
from comparo.application.schema_validation import RemoteSchemaDocuments
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.policy import CompiledExecutionPolicy


class RemoteSchemaFailureCode(StrEnum):
    """Value-free public failures for remote schema preparation."""

    NOT_AUTHORIZED = "not-authorized"
    INVALID_URI = "invalid-uri"
    DESTINATION_DENIED = "destination-denied"
    DNS_FAILED = "dns-failed"
    NETWORK_FAILED = "network-failed"
    STATUS_REJECTED = "status-rejected"
    SIZE_EXCEEDED = "size-exceeded"
    EMPTY = "empty"


@final
class RemoteSchemaLoadError(Exception):
    """A remote loading failure that never retains URI, payload, or dependency text."""

    __slots__ = ("code",)

    def __init__(self, code: RemoteSchemaFailureCode) -> None:
        """Retain only one safe closed failure code."""
        if type(code) is not RemoteSchemaFailureCode:
            raise TypeError("remote schema failure code must be typed")
        self.code = code
        super().__init__(code.value)


@final
class RemoteSchemaFetchLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned transfer and deadline limits for public schemas."""

    maximum_document_bytes: ByteCount
    connect_timeout: Duration
    read_timeout: Duration
    total_timeout: Duration

    def __post_init__(self) -> None:
        """Require typed positive deadlines and one non-zero document bound."""
        if type(self.maximum_document_bytes) is not ByteCount:
            raise TypeError("remote schema size limit must be ByteCount")
        if self.maximum_document_bytes.value < 1:
            raise ValueError("remote schema size limit must be positive")
        for value in (self.connect_timeout, self.read_timeout, self.total_timeout):
            if type(value) is not Duration or value.nanoseconds < 1:
                raise ValueError("remote schema deadlines must be positive Duration values")


DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1 = RemoteSchemaFetchLimits(
    maximum_document_bytes=ByteCount(value=8 * 1024 * 1024),
    connect_timeout=Duration(nanoseconds=10_000_000_000),
    read_timeout=Duration(nanoseconds=30_000_000_000),
    total_timeout=Duration(nanoseconds=60_000_000_000),
)


class RemoteSchemaLoader:
    """Fetch exact schemas through fresh route-pinned connections with no redirects."""

    __slots__ = ("_limits", "_resolver")

    def __init__(
        self,
        limits: RemoteSchemaFetchLimits,
        *,
        resolver: HostResolver | None = None,
    ) -> None:
        """Bind caller-owned limits and an injectable resolver."""
        if type(limits) is not RemoteSchemaFetchLimits:
            raise TypeError("remote schema loader requires typed limits")
        self._limits = limits
        self._resolver = resolver or SystemHostResolver()

    async def load(
        self,
        schemas: tuple[RemoteSchema, ...],
        policy: CompiledExecutionPolicy,
    ) -> RemoteSchemaDocuments:
        """Authorize and fetch unique schema URIs in canonical order."""
        if type(schemas) is not tuple or any(type(item) is not RemoteSchema for item in schemas):
            raise TypeError("remote schema loader requires immutable typed bindings")
        if type(policy) is not CompiledExecutionPolicy:
            raise TypeError("remote schema loader requires compiled ExecutionPolicy")
        unique = tuple(
            sorted(
                {item.uri: item for item in schemas}.values(),
                key=lambda item: item.uri,
            )
        )
        try:
            entries = [
                RemoteSchemaDocument(
                    uri=schema.uri,
                    payload=await self._load_one(schema, policy),
                )
                for schema in unique
            ]
        finally:
            await self._resolver.close()
        return RemoteSchemaDocuments(entries=tuple(entries))

    async def _load_one(
        self,
        schema: RemoteSchema,
        policy: CompiledExecutionPolicy,
    ) -> bytes:
        target = NamedCapabilityTarget(
            kind=CapabilityTargetKind.SCHEMA_SOURCE,
            identifier=schema.uri,
        )
        authority = policy.values.remote_schemas
        if not authority.allow_any and target not in authority.sources:
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.NOT_AUTHORIZED)
        scheme, host, port = _destination(schema.uri)
        http = policy.http
        if (
            scheme not in http.allowed_schemes
            or (not http.allow_any_host and host not in http.allowed_hosts)
            or (not http.allow_any_port and port not in http.allowed_ports)
        ):
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.DESTINATION_DENIED)
        try:
            addresses = await self._addresses(host, port)
        except (OSError, RuntimeError, UnicodeError, ValueError) as error:
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.DNS_FAILED) from error
        if any(
            network_address_class(item) not in http.allowed_address_classes for item in addresses
        ):
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.DESTINATION_DENIED)
        destination = NetworkDestination(
            scheme=scheme,
            host=host,
            port=port,
            resolved_addresses=addresses,
            selected_address=addresses[0],
        )
        try:
            async with asyncio.timeout(_seconds(self._limits.total_timeout)):
                return await self._request(schema.uri, destination)
        except RemoteSchemaLoadError:
            raise
        except (
            OSError,
            TimeoutError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.TimeoutException,
        ) as error:
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.NETWORK_FAILED) from error

    async def _addresses(self, host: HttpHost, port: HttpPort) -> tuple[IpHost, ...]:
        if type(host) is Ipv4Host or type(host) is Ipv6Host:
            return (host,)
        if type(host) is not DnsHost:  # pragma: no cover - closed HttpHost union
            raise TypeError("remote schema host has an unsupported type")
        addresses = await self._resolver.resolve(host, port)
        if not addresses:
            raise ValueError("remote schema DNS returned no addresses")
        return addresses

    async def _request(self, uri: str, destination: NetworkDestination) -> bytes:
        pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
            network_backend=RoutePinnedNetworkBackend(
                destination,
                tls_timeout=_seconds(self._limits.connect_timeout),
            ),
        )
        response: httpcore.Response | None = None
        try:
            response = await pool.request(
                method=b"GET",
                url=uri,
                headers=(
                    (b"accept", b"application/schema+json, application/json"),
                    (b"accept-encoding", b"identity"),
                    (b"user-agent", b"comparo-schema-loader/1"),
                ),
                extensions={
                    "timeout": {
                        "connect": _seconds(self._limits.connect_timeout),
                        "read": _seconds(self._limits.read_timeout),
                        "write": _seconds(self._limits.connect_timeout),
                        "pool": _seconds(self._limits.connect_timeout),
                    },
                    "sni_hostname": destination.host.value,
                },
            )
            if response.status != 200:
                raise RemoteSchemaLoadError(RemoteSchemaFailureCode.STATUS_REJECTED)
            output = bytearray()
            async for chunk in response.aiter_stream():
                if len(output) + len(chunk) > self._limits.maximum_document_bytes.value:
                    raise RemoteSchemaLoadError(RemoteSchemaFailureCode.SIZE_EXCEEDED)
                output.extend(chunk)
            if not output:
                raise RemoteSchemaLoadError(RemoteSchemaFailureCode.EMPTY)
            return bytes(output)
        finally:
            if response is not None:
                await response.aclose()
            await pool.aclose()


def remote_schema_allowed(uri: str, policy: CompiledExecutionPolicy) -> bool:
    """Check exact source authority without acquiring DNS or network capabilities."""
    target = NamedCapabilityTarget(
        kind=CapabilityTargetKind.SCHEMA_SOURCE,
        identifier=uri,
    )
    authority = policy.values.remote_schemas
    return authority.allow_any or target in authority.sources


def _destination(uri: str) -> tuple[HttpScheme, HttpHost, HttpPort]:
    try:
        parsed = urlsplit(uri)
        host_text = parsed.hostname
        port_value = parsed.port
    except ValueError as error:
        raise RemoteSchemaLoadError(RemoteSchemaFailureCode.INVALID_URI) from error
    if parsed.scheme not in {"http", "https"} or host_text is None:
        raise RemoteSchemaLoadError(RemoteSchemaFailureCode.INVALID_URI)
    try:
        parsed_ip = ipaddress.ip_address(host_text)
        host: HttpHost = (
            Ipv4Host(value=parsed_ip.compressed)
            if parsed_ip.version == 4
            else Ipv6Host(value=parsed_ip.compressed)
        )
    except ValueError:
        try:
            host = DnsHost(value=host_text.encode("idna").decode("ascii").lower())
        except (UnicodeError, ValueError) as error:
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.INVALID_URI) from error
    scheme = HttpScheme(parsed.scheme)
    port = HttpPort(value=port_value or (443 if scheme is HttpScheme.HTTPS else 80))
    return scheme, host, port


def _seconds(value: Duration) -> float:
    return value.nanoseconds / 1_000_000_000


__all__ = (
    "DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1",
    "RemoteSchemaFailureCode",
    "RemoteSchemaFetchLimits",
    "RemoteSchemaLoadError",
    "RemoteSchemaLoader",
    "remote_schema_allowed",
)
