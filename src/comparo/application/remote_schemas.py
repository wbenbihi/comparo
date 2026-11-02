"""Policy-authorized, route-pinned, bounded remote JSON Schema loading."""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
from contextlib import suppress
from enum import StrEnum
from typing import final
from urllib.parse import SplitResult
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
from comparo.core.domain.http.fields import HttpMethod
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
from comparo.core.domain.primitives import PositiveCount
from comparo.core.policy import CompiledExecutionPolicy

_GET = HttpMethod(value="GET")


class RemoteSchemaFailureCode(StrEnum):
    """Value-free public failures for remote schema preparation."""

    NOT_AUTHORIZED = "not-authorized"
    INVALID_URI = "invalid-uri"
    DESTINATION_DENIED = "destination-denied"
    DNS_FAILED = "dns-failed"
    NETWORK_FAILED = "network-failed"
    STATUS_REJECTED = "status-rejected"
    DOCUMENT_LIMIT_EXCEEDED = "document-limit-exceeded"
    SIZE_EXCEEDED = "size-exceeded"
    AGGREGATE_SIZE_EXCEEDED = "aggregate-size-exceeded"
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
    maximum_documents: PositiveCount
    maximum_total_bytes: ByteCount
    connect_timeout: Duration
    read_timeout: Duration
    total_timeout: Duration
    batch_timeout: Duration

    def __post_init__(self) -> None:
        """Require typed positive deadlines and one non-zero document bound."""
        if (
            type(self.maximum_document_bytes) is not ByteCount
            or type(self.maximum_total_bytes) is not ByteCount
        ):
            raise TypeError("remote schema size limits must be ByteCount")
        if type(self.maximum_documents) is not PositiveCount:
            raise TypeError("remote schema document limit must be PositiveCount")
        if self.maximum_document_bytes.value < 1 or self.maximum_total_bytes.value < 1:
            raise ValueError("remote schema size limits must be positive")
        if self.maximum_document_bytes.value > self.maximum_total_bytes.value:
            raise ValueError("remote schema document size cannot exceed its aggregate limit")
        for value in (
            self.connect_timeout,
            self.read_timeout,
            self.total_timeout,
            self.batch_timeout,
        ):
            if type(value) is not Duration or value.nanoseconds < 1:
                raise ValueError("remote schema deadlines must be positive Duration values")


DEFAULT_REMOTE_SCHEMA_FETCH_LIMITS_V1 = RemoteSchemaFetchLimits(
    maximum_document_bytes=ByteCount(value=8 * 1024 * 1024),
    maximum_documents=PositiveCount(value=64),
    maximum_total_bytes=ByteCount(value=64 * 1024 * 1024),
    connect_timeout=Duration(nanoseconds=10_000_000_000),
    read_timeout=Duration(nanoseconds=30_000_000_000),
    total_timeout=Duration(nanoseconds=60_000_000_000),
    batch_timeout=Duration(nanoseconds=120_000_000_000),
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
        entries: list[RemoteSchemaDocument] = []
        failure: RemoteSchemaFailureCode | None = None
        try:
            if len(unique) > self._limits.maximum_documents.value:
                raise RemoteSchemaLoadError(RemoteSchemaFailureCode.DOCUMENT_LIMIT_EXCEEDED)
            total_bytes = 0
            try:
                async with asyncio.timeout(_seconds(self._limits.batch_timeout)):
                    for schema in unique:
                        payload = await self._load_one(schema, policy)
                        total_bytes += len(payload)
                        if total_bytes > self._limits.maximum_total_bytes.value:
                            raise RemoteSchemaLoadError(
                                RemoteSchemaFailureCode.AGGREGATE_SIZE_EXCEEDED
                            )
                        entries.append(RemoteSchemaDocument(uri=schema.uri, payload=payload))
            except TimeoutError:
                failure = RemoteSchemaFailureCode.NETWORK_FAILED
        except RemoteSchemaLoadError as error:
            failure = error.code
        finally:
            try:
                await self._resolver.close()
            except Exception:  # noqa: BLE001 - resolver details must not cross this boundary
                failure = RemoteSchemaFailureCode.NETWORK_FAILED
        if failure is not None:
            raise RemoteSchemaLoadError(failure)
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
            _GET not in http.allowed_methods
            or scheme not in http.allowed_schemes
            or (not http.allow_any_host and host not in http.allowed_hosts)
            or (not http.allow_any_port and port not in http.allowed_ports)
        ):
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.DESTINATION_DENIED)
        addresses: tuple[IpHost, ...] | None = None
        with suppress(Exception):  # Resolver details stay private.
            addresses = await self._addresses(host, port)
        if addresses is None:
            raise RemoteSchemaLoadError(RemoteSchemaFailureCode.DNS_FAILED)
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
        payload: bytes | None = None
        failure: RemoteSchemaFailureCode | None = None
        try:
            async with asyncio.timeout(_seconds(self._limits.total_timeout)):
                payload = await self._request(schema.uri, destination)
        except RemoteSchemaLoadError as error:
            failure = error.code
        except (
            OSError,
            TimeoutError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.TimeoutException,
        ):
            failure = RemoteSchemaFailureCode.NETWORK_FAILED
        except Exception:  # noqa: BLE001 - transport details stay private
            failure = RemoteSchemaFailureCode.NETWORK_FAILED
        if failure is not None or payload is None:
            raise RemoteSchemaLoadError(failure or RemoteSchemaFailureCode.NETWORK_FAILED)
        return payload

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
    parsed: SplitResult | None = None
    try:
        parsed = urlsplit(uri)
        host_text = parsed.hostname
        port_value = parsed.port
    except ValueError:
        host_text = None
        port_value = None
    if parsed is None:
        raise RemoteSchemaLoadError(RemoteSchemaFailureCode.INVALID_URI)
    if parsed.scheme not in {"http", "https"} or host_text is None:
        raise RemoteSchemaLoadError(RemoteSchemaFailureCode.INVALID_URI)
    host: HttpHost | None = None
    try:
        parsed_ip = ipaddress.ip_address(host_text)
        host = (
            Ipv4Host(value=parsed_ip.compressed)
            if parsed_ip.version == 4
            else Ipv6Host(value=parsed_ip.compressed)
        )
    except ValueError:
        with suppress(UnicodeError, ValueError):
            host = DnsHost(value=host_text.encode("idna").decode("ascii").lower())
    if host is None:
        raise RemoteSchemaLoadError(RemoteSchemaFailureCode.INVALID_URI)
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
