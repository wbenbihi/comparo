"""Structured HTTP URLs whose shape excludes user information and fragments."""

from __future__ import annotations

import ipaddress
import re
from enum import StrEnum
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.http.fields import QueryParameters

_DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_ENCODED_PCHAR = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=:@"
)
_UNRESERVED_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class HttpScheme(StrEnum):
    """The only schemes in Comparo's HTTP transport boundary."""

    HTTP = "http"
    HTTPS = "https"


@final
class HttpPort(msgspec.Struct, frozen=True, kw_only=True):
    """A concrete TCP port number."""

    value: int

    def __post_init__(self) -> None:
        """Require an exact integer in the TCP port range."""
        if type(self.value) is not int:
            raise TypeError("HTTP port must be an integer")
        if not 1 <= self.value <= 65_535:
            raise ValueError("HTTP port must be between 1 and 65535")


@final
class DnsHost(msgspec.Struct, frozen=True, kw_only=True):
    """A canonical lowercase ASCII DNS host without a trailing root dot."""

    value: str

    def __post_init__(self) -> None:
        """Require canonical lowercase ASCII DNS syntax."""
        if type(self.value) is not str or not self.value or len(self.value) > 253:
            raise ValueError("DNS host must be non-empty ASCII text of at most 253 characters")
        if not self.value.isascii() or self.value.lower() != self.value:
            raise ValueError("DNS host must use canonical lowercase ASCII")
        if self.value.endswith("."):
            raise ValueError("DNS host must not contain an ambiguous trailing root dot")
        if all(character in "0123456789." for character in self.value):
            raise ValueError("DNS host must not use ambiguous numeric-only notation")
        labels = self.value.split(".")
        if any(_DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels):
            raise ValueError("DNS host contains an invalid label")


@final
class Ipv4Host(msgspec.Struct, frozen=True, kw_only=True):
    """A canonical dotted-decimal IPv4 host."""

    value: str

    def __post_init__(self) -> None:
        """Require canonical dotted-decimal notation."""
        if type(self.value) is not str:
            raise TypeError("IPv4 host must be text")
        try:
            ipaddress.IPv4Address(self.value)
        except ipaddress.AddressValueError as error:
            raise ValueError("invalid IPv4 host") from error


@final
class Ipv6Host(msgspec.Struct, frozen=True, kw_only=True):
    """A canonical compressed lowercase IPv6 host without URL brackets."""

    value: str

    def __post_init__(self) -> None:
        """Require canonical compressed lowercase notation."""
        if type(self.value) is not str:
            raise TypeError("IPv6 host must be text")
        try:
            parsed = ipaddress.IPv6Address(self.value)
        except ipaddress.AddressValueError as error:
            raise ValueError("invalid IPv6 host") from error
        if parsed.compressed != self.value:
            raise ValueError("IPv6 host must use canonical compressed lowercase notation")


type HttpHost = DnsHost | Ipv4Host | Ipv6Host
type IpHost = Ipv4Host | Ipv6Host


def is_http_host(value: object) -> TypeGuard[HttpHost]:
    """Narrow an arbitrary value to one exact host representation."""
    return type(value) in {DnsHost, Ipv4Host, Ipv6Host}


@final
class HttpAuthority(msgspec.Struct, frozen=True, kw_only=True):
    """An HTTP host and optional explicit port, with no user-information slot."""

    host: HttpHost
    port: HttpPort | None = None

    def __post_init__(self) -> None:
        """Require one typed host and an optional typed port."""
        if not is_http_host(self.host):
            raise TypeError("HTTP authority host must be a supported HttpHost")
        if self.port is not None and type(self.port) is not HttpPort:
            raise TypeError("HTTP authority port must be HttpPort")


@final
class DecodedPathSegment(msgspec.Struct, frozen=True, kw_only=True):
    """A Unicode path segment that a transport will percent-encode as UTF-8."""

    value: str

    def __post_init__(self) -> None:
        """Require valid Unicode and exclude ambiguous dot segments."""
        if type(self.value) is not str:
            raise TypeError("decoded path segment must be text")
        try:
            self.value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("decoded path segment must contain valid Unicode scalars") from error
        if self.value in {".", ".."}:
            raise ValueError("literal dot segments are ambiguous and not supported")


@final
class EncodedPathSegment(msgspec.Struct, frozen=True, kw_only=True):
    """An explicitly percent-encoded canonical ASCII path segment."""

    value: str

    def __post_init__(self) -> None:
        """Require canonical ASCII path-segment and escape syntax."""
        if type(self.value) is not str or not self.value.isascii():
            raise ValueError("encoded path segment must be ASCII text")
        if not self.value:
            raise ValueError("empty path segments are ambiguous and not supported")
        index = 0
        while index < len(self.value):
            character = self.value[index]
            if character == "%":
                escape = self.value[index + 1 : index + 3]
                if len(escape) != 2 or any(part not in "0123456789ABCDEF" for part in escape):
                    raise ValueError(
                        "percent escapes must contain two uppercase hexadecimal digits"
                    )
                octet = int(escape, 16)
                if octet in _UNRESERVED_BYTES:
                    raise ValueError("unreserved URL characters must not be percent encoded")
                if octet < 32 or octet == 127:
                    raise ValueError("encoded path segment contains an encoded control character")
                index += 3
                continue
            if character not in _ENCODED_PCHAR:
                raise ValueError("encoded path segment contains a forbidden character")
            index += 1
        if self.value in {".", ".."}:
            raise ValueError("literal dot segments are ambiguous and not supported")


type PathSegment = DecodedPathSegment | EncodedPathSegment


@final
class UrlPath(msgspec.Struct, frozen=True, kw_only=True):
    """An absolute hierarchical path retaining segment and trailing-slash intent."""

    segments: tuple[PathSegment, ...] = ()
    trailing_slash: bool = False

    def __post_init__(self) -> None:
        """Require immutable typed segments and one root-path representation."""
        if type(self.segments) is not tuple or any(
            type(segment) not in {DecodedPathSegment, EncodedPathSegment}
            for segment in self.segments
        ):
            raise TypeError("URL path segments must be an immutable tuple of PathSegment values")
        if type(self.trailing_slash) is not bool:
            raise TypeError("URL trailing-slash marker must be bool")
        if not self.segments and self.trailing_slash:
            raise ValueError("the root path has one canonical representation")
        if (
            self.segments
            and type(self.segments[-1]) is DecodedPathSegment
            and not self.segments[-1].value
            and not self.trailing_slash
        ):
            raise ValueError("a final empty path segment must use the trailing-slash marker")


@final
class AbsoluteHttpUrl(msgspec.Struct, frozen=True, kw_only=True):
    """A complete absolute HTTP(S) URL with no user-information or fragment fields."""

    scheme: HttpScheme
    authority: HttpAuthority
    path: UrlPath = UrlPath()
    query: QueryParameters = QueryParameters()

    def __post_init__(self) -> None:
        """Require the complete structured absolute HTTP URL components."""
        if type(self.scheme) is not HttpScheme:
            raise TypeError("absolute HTTP URL scheme must be HttpScheme")
        if type(self.authority) is not HttpAuthority:
            raise TypeError("absolute HTTP URL authority must be HttpAuthority")
        if type(self.path) is not UrlPath:
            raise TypeError("absolute HTTP URL path must be UrlPath")
        if type(self.query) is not QueryParameters:
            raise TypeError("absolute HTTP URL query must be QueryParameters")


@final
class HttpBaseUrl(msgspec.Struct, frozen=True, kw_only=True):
    """An Environment base URL whose type cannot carry query or fragment data."""

    scheme: HttpScheme
    authority: HttpAuthority
    path: UrlPath = UrlPath()

    def __post_init__(self) -> None:
        """Require an absolute origin and base path only."""
        if type(self.scheme) is not HttpScheme:
            raise TypeError("HTTP base URL scheme must be HttpScheme")
        if type(self.authority) is not HttpAuthority:
            raise TypeError("HTTP base URL authority must be HttpAuthority")
        if type(self.path) is not UrlPath:
            raise TypeError("HTTP base URL path must be UrlPath")


@final
class BaseRelativeTarget(msgspec.Struct, frozen=True, kw_only=True):
    """A request target appended to an Environment's declared API base path."""

    path: UrlPath = UrlPath()
    query: QueryParameters = QueryParameters()

    def __post_init__(self) -> None:
        """Require a typed path and source-ordered query."""
        if type(self.path) is not UrlPath:
            raise TypeError("base-relative target path must be UrlPath")
        if type(self.query) is not QueryParameters:
            raise TypeError("base-relative target query must be QueryParameters")


type RequestTarget = BaseRelativeTarget | AbsoluteHttpUrl


@final
class NetworkDestination(msgspec.Struct, frozen=True, kw_only=True):
    """A concrete origin with complete resolution and one pinned connect address."""

    scheme: HttpScheme
    host: HttpHost
    port: HttpPort
    resolved_addresses: tuple[IpHost, ...]
    selected_address: IpHost

    def __post_init__(self) -> None:
        """Require complete unique results and exact IP-literal self-resolution."""
        if type(self.scheme) is not HttpScheme:
            raise TypeError("network destination scheme must be HttpScheme")
        if not is_http_host(self.host):
            raise TypeError("network destination host must be HttpHost")
        if type(self.port) is not HttpPort:
            raise TypeError("network destination port must be HttpPort")
        if type(self.resolved_addresses) is not tuple or not self.resolved_addresses:
            raise ValueError("network destination requires at least one resolved IP address")
        if any(type(address) not in {Ipv4Host, Ipv6Host} for address in self.resolved_addresses):
            raise TypeError("resolved destination addresses must be canonical IP hosts")
        if len(set(self.resolved_addresses)) != len(self.resolved_addresses):
            raise ValueError("resolved destination addresses must be unique")
        if type(self.selected_address) not in {Ipv4Host, Ipv6Host}:
            raise TypeError("selected destination address must be a canonical IP host")
        if self.selected_address not in self.resolved_addresses:
            raise ValueError("selected destination address must be one of the resolved addresses")
        if type(self.host) in {Ipv4Host, Ipv6Host} and (
            self.resolved_addresses != (self.host,) or self.selected_address != self.host
        ):
            raise ValueError("an IP-literal destination must exactly self-map")


@final
class ProxyResolvedTarget(msgspec.Struct, frozen=True, kw_only=True):
    """A DNS target whose name resolution is explicitly delegated to the proxy."""

    scheme: HttpScheme
    authority: HttpAuthority

    def __post_init__(self) -> None:
        """Allow proxy-side resolution only for an actual DNS name."""
        if type(self.scheme) is not HttpScheme:
            raise TypeError("proxy-resolved target scheme must be HttpScheme")
        if type(self.authority) is not HttpAuthority:
            raise TypeError("proxy-resolved target authority must be HttpAuthority")
        if type(self.authority.host) is not DnsHost:
            raise ValueError("IP-literal proxy targets must use exact client resolution")


@final
class ClientResolvedTarget(msgspec.Struct, frozen=True, kw_only=True):
    """A proxy target resolved and safety-checked by Comparo before execution."""

    destination: NetworkDestination

    def __post_init__(self) -> None:
        """Require a fully resolved and pinned target destination."""
        if type(self.destination) is not NetworkDestination:
            raise TypeError("client-resolved proxy target must be NetworkDestination")


type ProxyTarget = ProxyResolvedTarget | ClientResolvedTarget


@final
class DirectResolvedRoute(msgspec.Struct, frozen=True, kw_only=True):
    """A direct route pinned to the selected address of its target destination."""

    destination: NetworkDestination

    def __post_init__(self) -> None:
        """Require a complete direct network destination."""
        if type(self.destination) is not NetworkDestination:
            raise TypeError("direct route destination must be NetworkDestination")


@final
class ProxyResolvedRoute(msgspec.Struct, frozen=True, kw_only=True):
    """A route pinned to a proxy destination with explicit target-resolution ownership."""

    proxy_destination: NetworkDestination
    target: ProxyTarget

    def __post_init__(self) -> None:
        """Bind the connect address to the proxy and type the target-resolution mode."""
        if type(self.proxy_destination) is not NetworkDestination:
            raise TypeError("proxy route destination must be NetworkDestination")
        if type(self.target) not in {ProxyResolvedTarget, ClientResolvedTarget}:
            raise TypeError("proxy route target must declare its resolution owner")


type ResolvedRoute = DirectResolvedRoute | ProxyResolvedRoute


def is_resolved_route(value: object) -> TypeGuard[ResolvedRoute]:
    """Narrow an arbitrary value to a direct or explicit proxy route."""
    return type(value) in {DirectResolvedRoute, ProxyResolvedRoute}


def effective_port(url: AbsoluteHttpUrl) -> HttpPort:
    """Return the explicit or scheme-defined canonical destination port."""
    if type(url) is not AbsoluteHttpUrl:
        raise TypeError("effective port requires an AbsoluteHttpUrl")
    if url.authority.port is not None:
        return url.authority.port
    return HttpPort(value=443 if url.scheme is HttpScheme.HTTPS else 80)
