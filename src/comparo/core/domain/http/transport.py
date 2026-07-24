"""Negotiated connection metadata and typed HTTP transport failures."""

from __future__ import annotations

import re
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.primitives import Sha256Digest

_TOKEN_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_CONNECTION_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class HttpProtocol(DomainStrEnum):
    """Negotiated ordinary HTTP protocol versions supported by the initial core."""

    HTTP_1_0 = "http/1.0"
    HTTP_1_1 = "http/1.1"
    HTTP_2 = "h2"


class TlsVersion(DomainStrEnum):
    """Negotiated TLS versions supported by the initial HTTPS transport."""

    TLS_1_2 = "tls1.2"
    TLS_1_3 = "tls1.3"


type AppliedTlsPolicy = TlsPolicy | ProxyTlsPolicy


@final
class TlsMetadata(msgspec.Struct, frozen=True, kw_only=True):
    """Safe negotiated TLS facts retained without certificate subject contents."""

    version: TlsVersion
    cipher_suite: str
    alpn: HttpProtocol
    peer_certificate_sha256: Sha256Digest
    session_resumed: bool
    applied_policy: AppliedTlsPolicy

    def __post_init__(self) -> None:
        """Require canonical negotiated TLS metadata."""
        if type(self.version) is not TlsVersion:
            raise TypeError("TLS version must be TlsVersion")
        if (
            type(self.cipher_suite) is not str
            or _TOKEN_PATTERN.fullmatch(self.cipher_suite) is None
        ):
            raise ValueError("TLS cipher suite must be a non-empty token")
        if type(self.alpn) is not HttpProtocol:
            raise TypeError("TLS ALPN protocol must be HttpProtocol")
        if type(self.peer_certificate_sha256) is not Sha256Digest:
            raise TypeError("TLS certificate digest must be Sha256Digest")
        if type(self.session_resumed) is not bool:
            raise TypeError("TLS session-resumed marker must be bool")
        if type(self.applied_policy) not in {TlsPolicy, ProxyTlsPolicy}:
            raise TypeError("TLS applied policy must identify target or proxy TLS authority")


@final
class TlsNotPerformed(msgspec.Struct, frozen=True, kw_only=True):
    """TLS was not performed because the authorized destination used clear text."""

    def __post_init__(self) -> None:
        """Retain a uniform direct-construction validation boundary."""


class TlsUnavailableReason(DomainStrEnum):
    """Reasons negotiated HTTPS metadata could not be retained."""

    INSTRUMENTATION_DISABLED = "instrumentation-disabled"
    INSTRUMENTATION_UNSUPPORTED = "instrumentation-unsupported"
    HANDSHAKE_METADATA_LOST = "handshake-metadata-lost"


@final
class TlsUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """TLS occurred, but its negotiated metadata is not observable."""

    reason: TlsUnavailableReason
    applied_policy: AppliedTlsPolicy

    def __post_init__(self) -> None:
        """Require one stable instrumentation reason."""
        if type(self.reason) is not TlsUnavailableReason:
            raise TypeError("unavailable TLS reason must be TlsUnavailableReason")
        if type(self.applied_policy) not in {TlsPolicy, ProxyTlsPolicy}:
            raise TypeError(
                "unavailable TLS applied policy must identify target or proxy TLS authority"
            )


type TlsObservation = TlsMetadata | TlsNotPerformed | TlsUnavailable


def is_tls_observation(value: object) -> TypeGuard[TlsObservation]:
    """Narrow an arbitrary value to an exhaustive TLS observation."""
    return type(value) in {TlsMetadata, TlsNotPerformed, TlsUnavailable}


@final
class TlsLayers(msgspec.Struct, frozen=True, kw_only=True):
    """TLS facts for the connected peer and an optional tunneled origin.

    A direct route uses only ``connect``. A proxy route records TLS to the proxy in ``connect``
    and, when HTTPS is established through a tunnel, records the independent origin handshake in
    ``origin``. Keeping the layers distinct prevents a proxy scheme from being mistaken for the
    target scheme.
    """

    connect: TlsObservation
    origin: TlsMetadata | TlsUnavailable | None = None

    def __post_init__(self) -> None:
        """Require explicit, non-placeholder facts for every represented TLS layer."""
        if not is_tls_observation(self.connect):
            raise TypeError("connect TLS layer must be a supported TlsObservation")
        if self.origin is not None and type(self.origin) not in {TlsMetadata, TlsUnavailable}:
            raise TypeError("origin TLS layer must be negotiated, unavailable, or absent")


def _validate_remote_endpoint(
    destination: NetworkDestination,
    address: IpHost,
    port: HttpPort,
) -> None:
    if type(address) not in {Ipv4Host, Ipv6Host}:
        raise TypeError("remote endpoint address must be a canonical IP host")
    if address not in destination.resolved_addresses:
        raise ValueError("remote endpoint address was not an authorized resolved destination")
    if address != destination.selected_address:
        raise ValueError("remote endpoint address must match the pinned selected destination")
    if type(port) is not HttpPort or port != destination.port:
        raise ValueError("remote endpoint port must match the authorized destination")


@final
class EndpointMetadata(msgspec.Struct, frozen=True, kw_only=True):
    """The authorized destination, chosen endpoint, and connection-reuse provenance."""

    destination: NetworkDestination
    remote_address: IpHost
    remote_port: HttpPort
    local_address: IpHost | None
    local_port: HttpPort | None
    connection_id: str
    connection_reused: bool

    def __post_init__(self) -> None:
        """Require the peer to be one of the destination's authorized DNS results."""
        if type(self.destination) is not NetworkDestination:
            raise TypeError("endpoint destination must be NetworkDestination")
        _validate_remote_endpoint(self.destination, self.remote_address, self.remote_port)
        if self.local_address is not None and type(self.local_address) not in {Ipv4Host, Ipv6Host}:
            raise TypeError("local endpoint address must be a canonical IP host when present")
        if self.local_port is not None and type(self.local_port) is not HttpPort:
            raise TypeError("local endpoint port must be HttpPort when present")
        if (self.local_address is None) != (self.local_port is None):
            raise ValueError("local endpoint address and port must be present together")
        if (
            type(self.connection_id) is not str
            or _CONNECTION_ID_PATTERN.fullmatch(self.connection_id) is None
        ):
            raise ValueError("connection ID must be a 1-128 character opaque identifier")
        if type(self.connection_reused) is not bool:
            raise TypeError("connection-reused marker must be bool")


class FailurePhase(DomainStrEnum):
    """The transport or capture phase in which execution stopped."""

    DNS = "dns"
    CONNECT = "connect"
    PROXY = "proxy"
    TLS = "tls"
    REQUEST_WRITE = "request-write"
    RESPONSE_HEADERS = "response-headers"
    RESPONSE_HEADERS_TIMEOUT = "response-headers-timeout"
    BODY_READ = "body-read"
    IDLE_TIMEOUT = "idle-timeout"
    PROTOCOL = "protocol"
    CONTENT_DECODING = "content-decoding"
    RESOURCE_BUDGET = "resource-budget"
    STORAGE = "storage"
    CANCELLATION = "cancellation"
    POLICY = "policy"


@final
class TransportFailure(msgspec.Struct, frozen=True, kw_only=True):
    """A typed failure that never masquerades as an HTTP status."""

    phase: FailurePhase
    code: DiagnosticCode
    retryable: bool

    def __post_init__(self) -> None:
        """Require structured, value-free failure facts."""
        if type(self.phase) is not FailurePhase:
            raise TypeError("transport failure phase must be FailurePhase")
        if type(self.code) is not DiagnosticCode:
            raise TypeError("transport failure code must be DiagnosticCode")
        if type(self.retryable) is not bool:
            raise TypeError("transport failure retryable marker must be bool")
