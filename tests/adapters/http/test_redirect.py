"""Production redirect resolution, rewrite, and confidentiality behavior."""

from __future__ import annotations

import asyncio

import msgspec
import pytest

from comparo.adapters.http.redirect import StandardRedirectResolver
from comparo.adapters.http.redirect import _url_text
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.fields import BareQueryValue
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
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.runtime.ports import NoRedirect
from comparo.core.runtime.ports import PreparedRedirect
from comparo.core.runtime.ports import RedirectFailure
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import direct_intent
from tests.core.domain.http.factories import finalized_request
from tests.core.domain.http.factories import policies
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.observation_factories import response

_REVEAL = SensitiveValueAccess(purpose="test.redirect")


def _authorized(
    *,
    method: str = "GET",
    url: AbsoluteHttpUrl | None = None,
    headers: RequestHeaders | None = None,
    cookies: RequestCookies | None = None,
    redirect_hops: int = 4,
    method_mode: RedirectMethodMode = RedirectMethodMode.STRICT_PRESERVE,
    allow_cross_origin: bool = False,
    client_identity: ClientIdentityRef | None = None,
) -> AuthorizedExchangeRequest:
    request_policies = policies()
    request_policies = msgspec.structs.replace(
        request_policies,
        tls=msgspec.structs.replace(
            request_policies.tls,
            client_identity=client_identity,
        ),
        redirects=msgspec.structs.replace(
            request_policies.redirects,
            max_hops=msgspec.structs.replace(
                request_policies.redirects.max_hops,
                value=redirect_hops,
            ),
            method_mode=method_mode,
            allow_cross_origin=allow_cross_origin,
        ),
    )
    wire = finalized_request(
        method=method,
        url=url,
        headers=headers,
        cookies=cookies,
        request_policies=request_policies,
    )
    return AuthorizedExchangeRequest(
        authorization=HttpAuthorizationGrant(
            intent=direct_intent(request=wire),
            authority=authority(),
        )
    )


def _response(
    status: int,
    *locations: HeaderText | SensitiveValue[HeaderText],
) -> ResponseObservation:
    observed = response(status=status)
    fields = tuple(
        ResponseField(name=HeaderName(value="location"), value=location) for location in locations
    )
    return msgspec.structs.replace(observed, headers=ResponseFields(fields=fields))


def _url(
    host: str = "api.example.test",
    *,
    scheme: HttpScheme = HttpScheme.HTTPS,
    path: tuple[EncodedPathSegment | SensitiveValue[EncodedPathSegment], ...] = (),
) -> AbsoluteHttpUrl:
    return AbsoluteHttpUrl(
        scheme=scheme,
        authority=HttpAuthority(host=DnsHost(value=host)),
        path=UrlPath(segments=path),
    )


def test_redirect_resolver_validates_boundary_and_ignores_non_redirects() -> None:
    resolver = StandardRedirectResolver()
    request = _authorized()
    assert resolver.resolve(request, response()) == NoRedirect()
    assert resolver.resolve(request, _response(302)) == NoRedirect()
    with pytest.raises(TypeError, match="AuthorizedExchangeRequest"):
        resolver.resolve(object(), response())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ResponseObservation"):
        resolver.resolve(request, object())  # type: ignore[arg-type]


def test_redirect_requires_one_valid_location_and_supported_http_target() -> None:
    resolver = StandardRedirectResolver()
    request = _authorized()
    for locations in (
        (HeaderText(value="/one"), HeaderText(value="/two")),
        (HeaderText(value="ftp://files.example.test/x"),),
        (HeaderText(value="https://user@example.test/x"),),
        (HeaderText(value="/%0A"),),
        (HeaderText(value="/%zz"),),
        (HeaderText(value="/a//b"),),
        (HeaderText(value="/x?a=1&&b=2"),),
    ):
        outcome = resolver.resolve(request, _response(302, *locations))
        assert type(outcome) is RedirectFailure
        assert outcome.failure.code.value == "http.redirect-invalid"


def test_relative_redirect_preserves_duplicate_and_bare_query_occurrences() -> None:
    resolver = StandardRedirectResolver()
    outcome = resolver.resolve(
        _authorized(url=_url(path=(EncodedPathSegment(value="v1"),))),
        _response(302, HeaderText(value="../v2/%7euser?q=one&q=&flag#ignored")),
    )
    assert type(outcome) is PreparedRedirect
    assert outcome.request.url.path.segments == (
        EncodedPathSegment(value="v2"),
        EncodedPathSegment(value="~user"),
    )
    assert tuple(item.name.value for item in outcome.request.url.query.parameters) == (
        "q",
        "q",
        "flag",
    )
    assert outcome.transition.raw_location == HeaderText(
        value="../v2/%7euser?q=one&q=&flag#ignored"
    )


def test_base_url_rendering_handles_ipv6_port_decoded_paths_and_query_shapes() -> None:
    value = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(
            host=Ipv6Host(value="2001:db8::1"),
            port=HttpPort(value=8443),
        ),
        path=UrlPath(
            segments=(DecodedPathSegment(value="hello world"),),
            trailing_slash=True,
        ),
        query=QueryParameters(
            parameters=(
                QueryParameter(
                    name=QueryName(value="flag"),
                    value=BareQueryValue(),
                    source=QuerySource.AUTHORED,
                    provenance=provenance(),
                ),
                QueryParameter(
                    name=QueryName(value="q"),
                    value=QueryText(value="one two"),
                    source=QuerySource.AUTHORED,
                    provenance=provenance(),
                ),
            )
        ),
    )
    assert _url_text(value) == "https://[2001:db8::1]:8443/hello%20world/?flag&q=one%20two"


def test_redirect_canonicalizes_unicode_and_ip_hosts() -> None:
    resolver = StandardRedirectResolver()
    root = resolver.resolve(
        _authorized(url=_url(path=(EncodedPathSegment(value="before"),))),
        _response(302, HeaderText(value="/")),
    )
    assert type(root) is PreparedRedirect
    assert root.request.url.path == UrlPath()
    unicode_path = resolver.resolve(
        _authorized(),
        _response(302, HeaderText(value="/café")),
    )
    assert type(unicode_path) is PreparedRedirect
    assert unicode_path.request.url.path.segments == (EncodedPathSegment(value="caf%C3%A9"),)

    for target in ("https://192.0.2.5/next", "https://[2001:db8::5]/next"):
        assert (
            resolver.resolve(
                _authorized(),
                _response(302, HeaderText(value=target)),
            )
            == NoRedirect()
        )


@pytest.mark.parametrize("status", [300, 307, 308])
def test_redirect_preserves_method_and_body_for_preserving_statuses(status: int) -> None:
    original = _authorized(method="POST")
    outcome = StandardRedirectResolver().resolve(
        original,
        _response(status, HeaderText(value="/next")),
    )
    assert type(outcome) is PreparedRedirect
    assert outcome.request.method == original.request.method
    assert outcome.request.body == original.request.body


@pytest.mark.parametrize(
    ("status", "mode", "method", "expected"),
    [
        (301, RedirectMethodMode.STRICT_PRESERVE, "POST", "POST"),
        (302, RedirectMethodMode.BROWSER_COMPATIBLE, "POST", "GET"),
        (303, RedirectMethodMode.STRICT_PRESERVE, "PUT", "GET"),
        (303, RedirectMethodMode.STRICT_PRESERVE, "HEAD", "HEAD"),
    ],
)
def test_redirect_method_rewrite_is_policy_and_status_specific(
    status: int,
    mode: RedirectMethodMode,
    method: str,
    expected: str,
) -> None:
    outcome = StandardRedirectResolver().resolve(
        _authorized(method=method, method_mode=mode),
        _response(status, HeaderText(value="/next")),
    )
    assert type(outcome) is PreparedRedirect
    assert outcome.request.method.value == expected


def test_cross_origin_redirect_is_explicit_and_strips_credentials() -> None:
    origin = TaintOriginId(value="credential")
    secret_header = HeaderField(
        name=HeaderName(value="authorization"),
        value=SensitiveValue(HeaderText(value="Bearer secret"), origins=(origin,)),
        source=HeaderSource.AUTHENTICATION,
        provenance=provenance(),
    )
    public_header = HeaderField(
        name=HeaderName(value="accept"),
        value=HeaderText(value="application/json"),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    cookie = RequestCookie(
        name=CookieName(value="session"),
        value=SensitiveValue(CookieText(value="secret"), origins=(origin,)),
        source=CookieSource.EXPLICIT,
        provenance=provenance(),
    )
    denied = StandardRedirectResolver().resolve(
        _authorized(headers=RequestHeaders(fields=(secret_header, public_header))),
        _response(302, HeaderText(value="https://other.example.test/next")),
    )
    assert denied == NoRedirect()

    allowed = StandardRedirectResolver().resolve(
        _authorized(
            headers=RequestHeaders(fields=(secret_header, public_header)),
            cookies=RequestCookies(cookies=(cookie,)),
            allow_cross_origin=True,
        ),
        _response(302, HeaderText(value="https://other.example.test/next")),
    )
    assert type(allowed) is PreparedRedirect
    assert allowed.request.headers == RequestHeaders(fields=(public_header,))
    assert allowed.request.cookies == RequestCookies()


def test_https_downgrade_and_sensitive_cross_origin_target_fail_closed() -> None:
    resolver = StandardRedirectResolver()
    request = _authorized(allow_cross_origin=True)
    assert (
        resolver.resolve(
            request,
            _response(302, HeaderText(value="http://other.example.test/next")),
        )
        == NoRedirect()
    )
    secret = SensitiveValue(
        HeaderText(value="https://other.example.test/next?opaque=value"),
        origins=(TaintOriginId(value="location"),),
    )
    assert resolver.resolve(request, _response(302, secret)) == NoRedirect()
    assert (
        resolver.resolve(
            _authorized(
                allow_cross_origin=True,
                client_identity=ClientIdentityRef(value="service-client"),
            ),
            _response(302, HeaderText(value="https://other.example.test/next")),
        )
        == NoRedirect()
    )


def test_sensitive_location_keeps_resolved_values_and_fingerprint_sealed() -> None:
    origin = TaintOriginId(value="location")
    location = SensitiveValue(
        HeaderText(value="/private/path?cursor=opaque"),
        origins=(origin,),
    )
    outcome = StandardRedirectResolver().resolve(_authorized(), _response(302, location))
    assert type(outcome) is PreparedRedirect
    assert all(is_sensitive_value(segment) for segment in outcome.request.url.path.segments)
    value = outcome.request.url.query.parameters[0].value
    assert is_sensitive_value(value)
    assert value.origins == (origin,)
    fingerprint = outcome.request.materialization.identity.effective_request_fingerprint
    assert is_sensitive_value(fingerprint)
    revealed = fingerprint.reveal(access=_REVEAL)
    assert type(revealed) is Fingerprint
    assert revealed.algorithm == "sha256"


def test_public_relative_location_cannot_unseal_a_sensitive_base_path() -> None:
    origin = TaintOriginId(value="base-path")
    base = _url(
        path=(
            SensitiveValue(EncodedPathSegment(value="private"), origins=(origin,)),
            SensitiveValue(EncodedPathSegment(value="leaf"), origins=(origin,)),
        )
    )
    outcome = StandardRedirectResolver().resolve(
        _authorized(url=base),
        _response(302, HeaderText(value="sibling")),
    )
    assert type(outcome) is RedirectFailure


def test_prepared_redirect_binds_exact_location_transition() -> None:
    outcome = StandardRedirectResolver().resolve(
        _authorized(),
        _response(302, HeaderText(value="https://xn--bcher-kva.example/next")),
    )
    assert outcome == NoRedirect()

    local = StandardRedirectResolver().resolve(
        _authorized(),
        _response(302, HeaderText(value="/next")),
    )
    assert type(local) is PreparedRedirect
    assert local.transition == FollowedRedirect(
        raw_location=HeaderText(value="/next"),
        resolved_url=local.request.url,
    )


def test_resolver_is_pure_and_requires_no_event_loop() -> None:
    async def invoke() -> PreparedRedirect | NoRedirect | RedirectFailure:
        return StandardRedirectResolver().resolve(
            _authorized(),
            _response(302, HeaderText(value="/next")),
        )

    assert type(asyncio.run(invoke())) is PreparedRedirect
