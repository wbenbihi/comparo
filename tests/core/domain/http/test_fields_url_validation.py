"""Branch-complete direct-construction tests for HTTP fields and structured URLs."""

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import QueryValue
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import BaseRelativeTarget
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpBaseUrl
from comparo.core.domain.http.url import HttpHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import PathSegment
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.http.url import ProxyTarget
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import provenance


def sensitive[ValueT](value: ValueT) -> SensitiveValue[ValueT]:
    """Seal a deeply immutable field value for validation tests."""
    return SensitiveValue(value, origins=(TaintOriginId(value="validation-test"),))


def header_field(
    name: str = "x-value",
    *,
    value: HeaderText | SensitiveValue[HeaderText] | None = None,
    source: HeaderSource = HeaderSource.AUTHORED,
) -> HeaderField:
    """Build one valid request header field."""
    return HeaderField(
        name=HeaderName(value=name),
        value=value or HeaderText(value="value"),
        source=source,
        provenance=provenance(),
    )


def query_parameter(
    name: str = "key",
    *,
    value: QueryText | SensitiveValue[QueryText] | None = None,
    source: QuerySource = QuerySource.AUTHORED,
) -> QueryParameter:
    """Build one valid request query occurrence."""
    return QueryParameter(
        name=QueryName(value=name),
        value=value or QueryText(value="value"),
        source=source,
        provenance=provenance(),
    )


def request_cookie() -> RequestCookie:
    """Build one valid sealed request cookie."""
    return RequestCookie(
        name=CookieName(value="session"),
        value=sensitive(CookieText(value="value")),
        source=CookieSource.EXPLICIT,
        provenance=provenance(),
    )


def network_destination() -> NetworkDestination:
    """Build one valid DNS-origin destination."""
    address = Ipv4Host(value="192.0.2.10")
    return NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="api.example.test"),
        port=HttpPort(value=443),
        resolved_addresses=(address,),
        selected_address=address,
    )


def test_field_text_and_provenance_boundaries_reject_wrong_types() -> None:
    """Keep UTF-8 text and provenance exact at field construction boundaries."""
    with pytest.raises(TypeError, match="query name must be text"):
        QueryName(value=cast("str", 1))
    with pytest.raises(TypeError, match="provenance"):
        HeaderField(
            name=HeaderName(value="x-value"),
            value=HeaderText(value="value"),
            source=HeaderSource.AUTHORED,
            provenance=cast("ValueProvenance", object()),
        )


def test_header_field_rejects_every_untyped_or_unsealed_component() -> None:
    """Exercise exact names, values, sources, immutable collections, and auth containment."""
    with pytest.raises(TypeError, match="field name"):
        HeaderField(
            name=cast("HeaderName", "x-value"),
            value=HeaderText(value="value"),
            source=HeaderSource.AUTHORED,
            provenance=provenance(),
        )
    with pytest.raises(TypeError, match="field value"):
        HeaderField(
            name=HeaderName(value="x-value"),
            value=cast("HeaderText", "value"),
            source=HeaderSource.AUTHORED,
            provenance=provenance(),
        )
    with pytest.raises(TypeError, match="field source"):
        HeaderField(
            name=HeaderName(value="x-value"),
            value=HeaderText(value="value"),
            source=cast("HeaderSource", "authored"),
            provenance=provenance(),
        )
    with pytest.raises(ValueError, match="must remain sensitive"):
        header_field(source=HeaderSource.AUTHENTICATION)
    with pytest.raises(TypeError, match="immutable tuple"):
        RequestHeaders(fields=cast("tuple[HeaderField, ...]", []))

    authorization = header_field(
        "authorization",
        value=sensitive(HeaderText(value="secret")),
    )
    with pytest.raises(ValueError, match="typed authentication"):
        RequestHeaders(fields=(authorization,))
    proxy_authorization = header_field(
        "proxy-authorization",
        value=sensitive(HeaderText(value="secret")),
    )
    with pytest.raises(ValueError, match="typed proxy authentication"):
        RequestHeaders(fields=(proxy_authorization,))


def test_query_components_reject_wrong_types_and_unsealed_auth_values() -> None:
    """Cover the complete query parameter and collection validation surface."""
    with pytest.raises(TypeError, match="parameter name"):
        QueryParameter(
            name=cast("QueryName", "key"),
            value=QueryText(value="value"),
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        )
    with pytest.raises(TypeError, match="supported QueryValue"):
        QueryParameter(
            name=QueryName(value="key"),
            value=cast("QueryValue", object()),
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        )
    with pytest.raises(TypeError, match="parameter source"):
        QueryParameter(
            name=QueryName(value="key"),
            value=QueryText(value="value"),
            source=cast("QuerySource", "authored"),
            provenance=provenance(),
        )
    with pytest.raises(ValueError, match="must remain sensitive"):
        query_parameter(source=QuerySource.AUTHENTICATION)
    with pytest.raises(TypeError, match="immutable tuple"):
        QueryParameters(parameters=cast("tuple[QueryParameter, ...]", []))


def test_cookie_components_reject_wrong_types_and_mutable_collections() -> None:
    """Require typed names, exact sealed values, sources, and immutable collections."""
    cookie = request_cookie()
    with pytest.raises(TypeError, match="cookie name"):
        RequestCookie(
            name=cast("CookieName", "session"),
            value=cookie.value,
            source=cookie.source,
            provenance=cookie.provenance,
        )
    with pytest.raises(TypeError, match="cookie payload"):
        RequestCookie(
            name=cookie.name,
            value=cast("SensitiveValue[CookieText]", sensitive(HeaderText(value="wrong"))),
            source=cookie.source,
            provenance=cookie.provenance,
        )
    with pytest.raises(TypeError, match="cookie source"):
        RequestCookie(
            name=cookie.name,
            value=cookie.value,
            source=cast("CookieSource", "explicit"),
            provenance=cookie.provenance,
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        RequestCookies(cookies=cast("tuple[RequestCookie, ...]", []))


def test_host_authority_and_path_components_reject_wrong_runtime_types() -> None:
    """Reject non-text hosts and untyped structured path or authority components."""
    with pytest.raises(TypeError, match="IPv4 host must be text"):
        Ipv4Host(value=cast("str", 1))
    with pytest.raises(ValueError, match="invalid IPv4"):
        Ipv4Host(value="192.168.001.001")
    with pytest.raises(TypeError, match="IPv6 host must be text"):
        Ipv6Host(value=cast("str", 1))
    with pytest.raises(TypeError, match="authority host"):
        HttpAuthority(host=cast("HttpHost", object()))
    with pytest.raises(TypeError, match="authority port"):
        HttpAuthority(
            host=DnsHost(value="api.example.test"),
            port=cast("HttpPort", 443),
        )
    with pytest.raises(TypeError, match="decoded path segment"):
        DecodedPathSegment(value=cast("str", 1))
    with pytest.raises(ValueError, match="Unicode scalars"):
        DecodedPathSegment(value="\ud800")
    with pytest.raises(ValueError, match="ASCII text"):
        EncodedPathSegment(value="café")
    with pytest.raises(TypeError, match="immutable tuple"):
        UrlPath(segments=cast("tuple[PathSegment, ...]", []))
    with pytest.raises(TypeError, match="trailing-slash marker"):
        UrlPath(trailing_slash=cast("bool", 1))


def test_url_aggregates_reject_every_untyped_component() -> None:
    """Exercise absolute URL, base URL, and base-relative target construction boundaries."""
    authority = HttpAuthority(host=DnsHost(value="api.example.test"))
    path = UrlPath()
    query = QueryParameters()
    with pytest.raises(TypeError, match="URL scheme"):
        AbsoluteHttpUrl(
            scheme=cast("HttpScheme", "https"),
            authority=authority,
        )
    with pytest.raises(TypeError, match="URL authority"):
        AbsoluteHttpUrl(
            scheme=HttpScheme.HTTPS,
            authority=cast("HttpAuthority", object()),
        )
    with pytest.raises(TypeError, match="URL path"):
        AbsoluteHttpUrl(
            scheme=HttpScheme.HTTPS,
            authority=authority,
            path=cast("UrlPath", object()),
        )
    with pytest.raises(TypeError, match="URL query"):
        AbsoluteHttpUrl(
            scheme=HttpScheme.HTTPS,
            authority=authority,
            query=cast("QueryParameters", object()),
        )
    with pytest.raises(TypeError, match="base URL scheme"):
        HttpBaseUrl(scheme=cast("HttpScheme", "https"), authority=authority)
    with pytest.raises(TypeError, match="base URL authority"):
        HttpBaseUrl(scheme=HttpScheme.HTTPS, authority=cast("HttpAuthority", object()))
    with pytest.raises(TypeError, match="base URL path"):
        HttpBaseUrl(
            scheme=HttpScheme.HTTPS,
            authority=authority,
            path=cast("UrlPath", object()),
        )
    with pytest.raises(TypeError, match="target path"):
        BaseRelativeTarget(path=cast("UrlPath", object()), query=query)
    with pytest.raises(TypeError, match="target query"):
        BaseRelativeTarget(path=path, query=cast("QueryParameters", object()))


def test_network_destination_rejects_every_untyped_component() -> None:
    """Require an exact scheme, host, port, resolved set, and selected address."""
    valid = network_destination()
    with pytest.raises(TypeError, match="destination scheme"):
        NetworkDestination(
            scheme=cast("HttpScheme", "https"),
            host=valid.host,
            port=valid.port,
            resolved_addresses=valid.resolved_addresses,
            selected_address=valid.selected_address,
        )
    with pytest.raises(TypeError, match="destination host"):
        NetworkDestination(
            scheme=valid.scheme,
            host=cast("HttpHost", object()),
            port=valid.port,
            resolved_addresses=valid.resolved_addresses,
            selected_address=valid.selected_address,
        )
    with pytest.raises(TypeError, match="destination port"):
        NetworkDestination(
            scheme=valid.scheme,
            host=valid.host,
            port=cast("HttpPort", 443),
            resolved_addresses=valid.resolved_addresses,
            selected_address=valid.selected_address,
        )
    with pytest.raises(TypeError, match="resolved destination addresses"):
        NetworkDestination(
            scheme=valid.scheme,
            host=valid.host,
            port=valid.port,
            resolved_addresses=cast(
                "tuple[IpHost, ...]",
                (DnsHost(value="not-an-address.example"),),
            ),
            selected_address=valid.selected_address,
        )
    with pytest.raises(TypeError, match="selected destination address"):
        NetworkDestination(
            scheme=valid.scheme,
            host=valid.host,
            port=valid.port,
            resolved_addresses=valid.resolved_addresses,
            selected_address=cast("IpHost", DnsHost(value="not-an-address.example")),
        )


def test_resolved_target_and_route_components_are_closed() -> None:
    """Reject invalid proxy resolution ownership and untyped route components."""
    authority = HttpAuthority(host=DnsHost(value="api.example.test"))
    valid = network_destination()
    with pytest.raises(TypeError, match="target scheme"):
        ProxyResolvedTarget(scheme=cast("HttpScheme", "https"), authority=authority)
    with pytest.raises(TypeError, match="target authority"):
        ProxyResolvedTarget(
            scheme=HttpScheme.HTTPS,
            authority=cast("HttpAuthority", object()),
        )
    with pytest.raises(ValueError, match="IP-literal"):
        ProxyResolvedTarget(
            scheme=HttpScheme.HTTPS,
            authority=HttpAuthority(host=Ipv4Host(value="192.0.2.10")),
        )
    with pytest.raises(TypeError, match="client-resolved"):
        ClientResolvedTarget(destination=cast("NetworkDestination", object()))
    with pytest.raises(TypeError, match="direct route"):
        DirectResolvedRoute(destination=cast("NetworkDestination", object()))
    with pytest.raises(TypeError, match="proxy route destination"):
        ProxyResolvedRoute(
            proxy_destination=cast("NetworkDestination", object()),
            target=ClientResolvedTarget(destination=valid),
        )
    with pytest.raises(TypeError, match="resolution owner"):
        ProxyResolvedRoute(
            proxy_destination=valid,
            target=cast("ProxyTarget", object()),
        )
    with pytest.raises(TypeError, match="effective port"):
        effective_port(cast("AbsoluteHttpUrl", object()))
