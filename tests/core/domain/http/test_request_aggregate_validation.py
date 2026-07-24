"""Branch-complete request aggregate and outbound authorization validation tests."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.http import requests as request_models
from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BasicWireEncoding
from comparo.core.domain.http.auth import CookieApiKeyPlacement
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.auth import QueryApiKeyPlacement
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import RequestBody
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
from comparo.core.domain.http.policies import DeclaredIdempotentRetry
from comparo.core.domain.http.policies import IdempotencyKeyRetry
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import RequestDefinition
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import BaseRelativeTarget
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import RequestTarget
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.provenance import ValueProvenance
from tests.core.domain.http.factories import api_key_secret
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import finalized_request
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import logical_request
from tests.core.domain.http.factories import multipart_body
from tests.core.domain.http.factories import password_secret
from tests.core.domain.http.factories import policies
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.factories import retry_policies
from tests.core.domain.http.factories import sensitive


def test_request_target_and_retry_compatibility_branches() -> None:
    """Cover both target forms, the defensive target boundary, and read-only retry rules."""
    absolute = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="api.example.test")),
    )
    assert request_models._target_query(absolute) == absolute.query
    with pytest.raises(TypeError, match="request target"):
        request_models._target_query(cast("RequestTarget", object()))

    with pytest.raises(ValueError, match="read-only requests"):
        logical_request(
            request_policies=retry_policies(mutation=DeclaredIdempotentRetry()),
        )


def test_finalized_idempotency_key_must_be_sensitive_and_authored() -> None:
    """Reject public or generated idempotency keys even when exactly one is present."""
    strategy = IdempotencyKeyRetry(header_name=HeaderName(value="idempotency-key"))
    retry = retry_policies(mutation=strategy)
    public = HeaderField(
        name=strategy.header_name,
        value=HeaderText(value="public"),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="remain sensitive"):
        finalized_request(
            method="POST",
            headers=RequestHeaders(fields=(public,)),
            request_policies=retry,
        )
    generated = HeaderField(
        name=strategy.header_name,
        value=sensitive(HeaderText(value="generated")),
        source=HeaderSource.BODY,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="stable authored"):
        finalized_request(
            method="POST",
            headers=RequestHeaders(fields=(generated,)),
            request_policies=retry,
        )


def test_finalized_multipart_content_type_must_match_public_body_metadata() -> None:
    """Reject sensitive, authored, or mismatched multipart Content-Type fields."""
    body, validation = multipart_body()
    wrong_source = HeaderField(
        name=HeaderName(value="content-type"),
        value=validation.encoded_content_type,
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="public body-derived"):
        finalized_request(headers=RequestHeaders(fields=(wrong_source,)), body=body)
    mismatched = HeaderField(
        name=HeaderName(value="content-type"),
        value=HeaderText(value="multipart/form-data; boundary=other"),
        source=HeaderSource.BODY,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="does not match boundary"):
        finalized_request(headers=RequestHeaders(fields=(mismatched,)), body=body)


def test_logical_sources_and_authentication_collisions_are_exhaustive() -> None:
    """Reject generated logical fields and all typed-auth placement collisions."""
    generated_query = QueryParameter(
        name=QueryName(value="generated"),
        value=sensitive(QueryText(value="value")),
        source=QuerySource.AUTHENTICATION,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="only authored parameters"):
        logical_request(
            target=BaseRelativeTarget(
                query=QueryParameters(parameters=(generated_query,)),
            )
        )

    session_cookie = RequestCookie(
        name=CookieName(value="session"),
        value=sensitive(CookieText(value="value")),
        source=CookieSource.SESSION,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="only explicit cookies"):
        logical_request(cookies=RequestCookies(cookies=(session_cookie,)))

    authorization = HeaderField(
        name=HeaderName(value="authorization"),
        value=sensitive(HeaderText(value="generated")),
        source=HeaderSource.AUTHENTICATION,
        provenance=provenance(),
    )
    basic = BasicAuthentication(
        username=BasicUsername(value="user"),
        password=password_secret(),
        wire_encoding=BasicWireEncoding.UTF_8,
    )
    with pytest.raises(ValueError, match="typed authentication collides"):
        request_models._validate_authentication_collisions(
            basic,
            RequestHeaders(fields=(authorization,)),
            QueryParameters(),
            RequestCookies(),
        )
    request_models._validate_authentication_collisions(
        basic,
        RequestHeaders(),
        QueryParameters(),
        RequestCookies(),
    )

    query_name = QueryName(value="custom-key")
    query_auth = ApiKeyAuthentication(
        credential=api_key_secret(),
        placement=QueryApiKeyPlacement(name=query_name),
    )
    authored_query = QueryParameter(
        name=query_name,
        value=QueryText(value="value"),
        source=QuerySource.AUTHORED,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="authored query"):
        logical_request(
            target=BaseRelativeTarget(
                query=QueryParameters(parameters=(authored_query,)),
            ),
            authentication=query_auth,
        )

    cookie_name = CookieName(value="custom-key")
    cookie_auth = ApiKeyAuthentication(
        credential=api_key_secret(),
        placement=CookieApiKeyPlacement(name=cookie_name),
    )
    explicit_cookie = RequestCookie(
        name=cookie_name,
        value=sensitive(CookieText(value="value")),
        source=CookieSource.EXPLICIT,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="explicit cookie"):
        logical_request(
            cookies=RequestCookies(cookies=(explicit_cookie,)),
            authentication=cookie_auth,
        )
    assert logical_request(authentication=cookie_auth).authentication is cookie_auth


def test_effective_field_collisions_cover_content_type_and_loop_continuity() -> None:
    """Reject duplicate Content-Type while accepting duplicate authored query occurrences."""
    first = HeaderField(
        name=HeaderName(value="content-type"),
        value=HeaderText(value="application/json"),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    second = msgspec.structs.replace(first, value=HeaderText(value="text/plain"))
    base = finalized_request()
    with pytest.raises(ValueError, match="multiple Content-Type"):
        msgspec.structs.replace(base, headers=RequestHeaders(fields=(first, second)))

    one = QueryParameter(
        name=QueryName(value="key"),
        value=QueryText(value="one"),
        source=QuerySource.AUTHORED,
        provenance=provenance(),
    )
    two = msgspec.structs.replace(one, value=QueryText(value="two"))
    url = msgspec.structs.replace(
        base.url,
        query=QueryParameters(parameters=(one, two)),
    )
    assert len(msgspec.structs.replace(base, url=url).url.query.parameters) == 2


def test_request_payload_and_logical_request_components_are_closed() -> None:
    """Exercise all aggregate request component and definition validation branches."""
    valid = logical_request()
    with pytest.raises(TypeError, match="request headers"):
        msgspec.structs.replace(valid, headers=cast("RequestHeaders", object()))
    with pytest.raises(TypeError, match="request cookies"):
        msgspec.structs.replace(valid, cookies=cast("RequestCookies", object()))
    with pytest.raises(TypeError, match="request policies"):
        msgspec.structs.replace(valid, policies=cast("RequestPolicies", object()))
    with pytest.raises(TypeError, match="request provenance"):
        msgspec.structs.replace(valid, provenance=cast("ValueProvenance", object()))
    with pytest.raises(TypeError, match="definition origin"):
        msgspec.structs.replace(valid, definition=cast("RequestDefinition", object()))
    with pytest.raises(ValueError, match="identify a Request"):
        msgspec.structs.replace(
            valid,
            definition=definition(ResourceKind.ENVIRONMENT, "staging"),
        )
    with pytest.raises(TypeError, match="logical request method"):
        msgspec.structs.replace(valid, method=cast("HttpMethod", "GET"))
    with pytest.raises(TypeError, match="logical request target"):
        msgspec.structs.replace(valid, target=cast("RequestTarget", object()))
    with pytest.raises(TypeError, match="logical request authentication"):
        msgspec.structs.replace(valid, authentication=cast("NoAuthentication", object()))
    with pytest.raises(TypeError, match="logical request body"):
        msgspec.structs.replace(valid, body=cast("RequestBody", object()))


def test_effective_identity_and_body_components_are_closed() -> None:
    """Reject every invalid Environment identity, fingerprint, method, URL, or body component."""
    valid = EffectiveRequest(
        environment=definition(ResourceKind.ENVIRONMENT, "staging"),
        logical_request_fingerprint=fingerprint("a"),
        method=HttpMethod(value="GET"),
        url=finalized_request().url,
        headers=RequestHeaders(),
        cookies=RequestCookies(),
        body=NoBody(),
        policies=policies(),
        provenance=provenance(),
    )
    with pytest.raises(TypeError, match="environment must be DefinitionRef"):
        msgspec.structs.replace(valid, environment=cast("DefinitionRef", object()))
    with pytest.raises(ValueError, match="identify an Environment"):
        msgspec.structs.replace(valid, environment=definition(ResourceKind.REQUEST, "request"))
    with pytest.raises(TypeError, match="fingerprint"):
        msgspec.structs.replace(valid, logical_request_fingerprint=cast("Fingerprint", object()))
    with pytest.raises(TypeError, match="effective request method"):
        msgspec.structs.replace(valid, method=cast("HttpMethod", "GET"))
    with pytest.raises(TypeError, match="effective request URL"):
        msgspec.structs.replace(valid, url=cast("AbsoluteHttpUrl", object()))
    with pytest.raises(TypeError, match="effective request body"):
        msgspec.structs.replace(valid, body=cast("RequestBody", object()))
    with pytest.raises(TypeError, match="finalized request body"):
        msgspec.structs.replace(
            finalized_request(),
            body=cast("FinalizedBody", object()),
        )
