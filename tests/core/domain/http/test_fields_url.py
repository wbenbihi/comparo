"""Tests for exact HTTP fields, cookies, query values, and structured URLs."""

from __future__ import annotations

import pytest

from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.fields import is_header_value
from comparo.core.domain.http.fields import is_read_only_http_method
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import BaseRelativeTarget
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpBaseUrl
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.http.url import is_http_host
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import provenance


def header(name: str, value: str, *, source: HeaderSource = HeaderSource.AUTHORED) -> HeaderField:
    return HeaderField(
        name=HeaderName(value=name),
        value=HeaderText(value=value),
        source=source,
        provenance=provenance(),
    )


def query(name: str, value: str) -> QueryParameter:
    return QueryParameter(
        name=QueryName(value=name),
        value=QueryText(value=value),
        source=QuerySource.AUTHORED,
        provenance=provenance(),
    )


@pytest.mark.parametrize("value", ["GET", "POST", "M-SEARCH"])
def test_http_method_accepts_canonical_tokens(value: str) -> None:
    assert HttpMethod(value=value).value == value


@pytest.mark.parametrize("value", ["GET", "HEAD", "OPTIONS", "TRACE"])
def test_read_only_http_method_classification_is_shared(value: str) -> None:
    assert is_read_only_http_method(HttpMethod(value=value))


@pytest.mark.parametrize("value", ["POST", "PUT", "PATCH", "DELETE", "PURGE"])
def test_every_other_http_method_is_mutation_capable(value: str) -> None:
    assert not is_read_only_http_method(HttpMethod(value=value))


def test_http_method_classification_rejects_untyped_values() -> None:
    with pytest.raises(TypeError, match="classification"):
        is_read_only_http_method("GET")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "get", "A B", "GÉT", 1])
def test_http_method_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(ValueError, match="canonical uppercase"):
        HttpMethod(value=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["Content-Type", "bad name", "", 1])
def test_header_name_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(ValueError, match="lowercase HTTP token"):
        HeaderName(value=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["line\nfeed", "nul\0", "snowman-☃", "delete\x7f"])
def test_header_text_rejects_injection_and_unsupported_characters(value: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        HeaderText(value=value)


def test_header_text_requires_text_and_allows_tab_and_latin1() -> None:
    with pytest.raises(TypeError, match="must be text"):
        HeaderText(value=1)  # type: ignore[arg-type]
    assert HeaderText(value="a\tb\N{LATIN SMALL LETTER E WITH ACUTE}").value.endswith("é")


def test_headers_preserve_duplicate_order_and_sensitive_repr_is_safe() -> None:
    first = header("x-value", "one")
    secret = "header-secret"
    wrapped = SensitiveValue(
        HeaderText(value=secret),
        origins=(TaintOriginId(value="header-origin"),),
    )
    second = HeaderField(
        name=HeaderName(value="x-value"),
        value=wrapped,
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    headers = RequestHeaders(fields=(first, second))

    assert headers.fields == (first, second)
    assert is_header_value(wrapped)
    assert not is_header_value("raw")
    assert secret not in repr(headers)


@pytest.mark.parametrize(
    ("name", "source", "match"),
    [
        ("host", HeaderSource.AUTHORED, "derived only by the transport"),
        ("content-length", HeaderSource.BODY, "derived only by the transport"),
        ("connection", HeaderSource.AUTHORED, "connection-specific"),
        ("cookie", HeaderSource.AUTHORED, "typed request cookies"),
        ("authorization", HeaderSource.AUTHORED, "known credential header"),
        ("proxy-authorization", HeaderSource.AUTHORED, "known credential header"),
        ("te", HeaderSource.AUTHORED, "connection-specific"),
        ("trailer", HeaderSource.AUTHORED, "connection-specific"),
    ],
)
def test_headers_constructively_reject_ambiguous_or_reserved_fields(
    name: str,
    source: HeaderSource,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        RequestHeaders(fields=(header(name, "value", source=source),))


def test_only_typed_authentication_can_own_authorization_header() -> None:
    wrapped = SensitiveValue(
        HeaderText(value="generated"),
        origins=(TaintOriginId(value="auth-origin"),),
    )
    authorization = HeaderField(
        name=HeaderName(value="authorization"),
        value=wrapped,
        source=HeaderSource.AUTHENTICATION,
        provenance=provenance(),
    )
    assert RequestHeaders(fields=(authorization,)).fields == (authorization,)


def test_query_preserves_bare_empty_and_duplicate_order() -> None:
    bare = QueryParameter(
        name=QueryName(value="flag"),
        value=BareQueryValue(),
        source=QuerySource.AUTHORED,
        provenance=provenance(),
    )
    empty = query("flag", "")
    parameters = QueryParameters(parameters=(bare, empty))

    assert parameters.parameters == (bare, empty)
    assert bare.value != empty.value


@pytest.mark.parametrize(("constructor", "value"), [(QueryName, ""), (CookieName, "bad name")])
def test_query_and_cookie_names_reject_invalid_values(
    constructor: type[QueryName] | type[CookieName],
    value: str,
) -> None:
    with pytest.raises(ValueError, match="must"):
        constructor(value=value)


def test_invalid_unicode_query_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unicode scalar"):
        QueryText(value="\ud800")


@pytest.mark.parametrize("value", ["with space", "semi;colon", 'quote"', "é"])
def test_cookie_text_rejects_delimiters_and_non_ascii(value: str) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        CookieText(value=value)


def test_typed_cookie_collection_rejects_duplicate_names() -> None:
    cookie = RequestCookie(
        name=CookieName(value="session"),
        value=SensitiveValue(
            CookieText(value="one"),
            origins=(TaintOriginId(value="cookie-origin"),),
        ),
        source=CookieSource.EXPLICIT,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="unique"):
        RequestCookies(cookies=(cookie, cookie))


@pytest.mark.parametrize("port", [0, 65_536, -1, True])
def test_http_port_rejects_invalid_values(port: int) -> None:
    expected = TypeError if type(port) is bool else ValueError
    with pytest.raises(expected):
        HttpPort(value=port)


def test_host_types_require_canonical_unambiguous_forms() -> None:
    assert DnsHost(value="api.example.test").value == "api.example.test"
    assert Ipv4Host(value="192.0.2.1").value == "192.0.2.1"
    assert Ipv6Host(value="2001:db8::1").value == "2001:db8::1"
    assert is_http_host(DnsHost(value="localhost"))
    assert not is_http_host("localhost")

    for invalid in ("Example.test", "example.test.", "-bad.test", ""):
        with pytest.raises(ValueError, match="DNS host"):
            DnsHost(value=invalid)
    with pytest.raises(ValueError, match="invalid IPv4"):
        Ipv4Host(value="999.1.1.1")
    with pytest.raises(ValueError, match="invalid IPv6"):
        Ipv6Host(value="not-v6")
    with pytest.raises(ValueError, match="canonical compressed"):
        Ipv6Host(value="2001:0db8:0:0:0:0:0:1")
    with pytest.raises(ValueError, match="zone identifier"):
        Ipv6Host(value="fe80::1%eth0")


def test_path_segment_forms_are_explicit_and_canonical() -> None:
    decoded = DecodedPathSegment(value="hello world")
    encoded = EncodedPathSegment(value="hello%20world")
    path = UrlPath(segments=(decoded, encoded), trailing_slash=True)
    assert path.segments == (decoded, encoded)

    with pytest.raises(ValueError, match="dot segments"):
        DecodedPathSegment(value="..")
    with pytest.raises(ValueError, match="uppercase hexadecimal"):
        EncodedPathSegment(value="bad%2fescape")
    with pytest.raises(ValueError, match="forbidden character"):
        EncodedPathSegment(value="has/slash")
    with pytest.raises(ValueError, match="canonical representation"):
        UrlPath(trailing_slash=True)
    empty = DecodedPathSegment(value="")
    with pytest.raises(ValueError, match="empty path segments"):
        EncodedPathSegment(value="")
    assert (
        UrlPath(
            segments=(DecodedPathSegment(value="a"), empty, DecodedPathSegment(value="b"))
        ).segments[1]
        == empty
    )
    assert UrlPath(segments=(empty,), trailing_slash=True).trailing_slash
    with pytest.raises(ValueError, match="final empty path segment"):
        UrlPath(segments=(empty,))


def test_structured_urls_exclude_userinfo_and_fragments_by_shape() -> None:
    authority = HttpAuthority(host=DnsHost(value="api.example.test"), port=HttpPort(value=8443))
    path = UrlPath(segments=(DecodedPathSegment(value="v1"),))
    absolute = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=authority,
        path=path,
        query=QueryParameters(parameters=(query("page", "1"),)),
    )
    base = HttpBaseUrl(scheme=HttpScheme.HTTPS, authority=authority, path=path)
    target = BaseRelativeTarget(path=path)

    assert absolute.authority == base.authority
    assert target.path == path
    assert effective_port(absolute) == HttpPort(value=8443)
    assert effective_port(
        AbsoluteHttpUrl(
            scheme=HttpScheme.HTTP,
            authority=HttpAuthority(host=DnsHost(value="example.test")),
        )
    ) == HttpPort(value=80)


def test_network_destination_requires_unique_resolved_addresses() -> None:
    address = Ipv4Host(value="192.0.2.1")
    destination = NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="api.example.test"),
        port=HttpPort(value=443),
        resolved_addresses=(address,),
        selected_address=address,
    )
    assert destination.resolved_addresses == (address,)

    with pytest.raises(ValueError, match="at least one"):
        NetworkDestination(
            scheme=HttpScheme.HTTPS,
            host=DnsHost(value="api.example.test"),
            port=HttpPort(value=443),
            resolved_addresses=(),
            selected_address=address,
        )
    with pytest.raises(ValueError, match="unique"):
        NetworkDestination(
            scheme=HttpScheme.HTTPS,
            host=DnsHost(value="api.example.test"),
            port=HttpPort(value=443),
            resolved_addresses=(address, address),
            selected_address=address,
        )


def test_ip_literal_destination_must_exactly_self_map() -> None:
    literal = Ipv4Host(value="192.0.2.1")
    other = Ipv4Host(value="192.0.2.2")
    with pytest.raises(ValueError, match="exactly self-map"):
        NetworkDestination(
            scheme=HttpScheme.HTTP,
            host=literal,
            port=HttpPort(value=80),
            resolved_addresses=(literal, other),
            selected_address=literal,
        )
