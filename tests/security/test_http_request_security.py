"""Security invariants for outbound HTTP request construction and authorization."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import msgspec
import pytest
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import materialization_fingerprint
from tests.core.domain.http.factories import policies
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.factories import request_occurrence

from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.http.auth import ApiKeyCredential
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import MutationApproval
from comparo.core.domain.http.authorization import ProxyDnsApproval
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import MultipartBoundaryValidation
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.bodies import multipart_content_type
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
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import IdempotencyKeyRetry
from comparo.core.domain.http.policies import MutationApprovalRequired
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId


def sensitive[ValueT](value: ValueT, origin: str = "security-test") -> SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=(TaintOriginId(value=origin),))


def destination(host: DnsHost, address: str = "192.0.2.10") -> NetworkDestination:
    selected = Ipv4Host(value=address)
    return NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=host,
        port=HttpPort(value=443),
        resolved_addresses=(selected,),
        selected_address=selected,
    )


def finalized(
    *,
    headers: RequestHeaders | None = None,
    body: MaterializedBody | None = None,
    request_policies: RequestPolicies | None = None,
) -> FinalizedWireRequest:
    source = RepeatableByteSource(
        source_id=BodyId(value=uuid4()),
        byte_count=ByteCount(value=4),
        digest=digest("a"),
        provenance=provenance(),
    )
    actual_body = body or MaterializedBody(source=source)
    environment = definition(ResourceKind.ENVIRONMENT, "staging")
    request_fingerprint = fingerprint("b")
    method = HttpMethod(value="POST")
    url = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="api.example.test")),
    )
    actual_headers = headers or RequestHeaders()
    actual_cookies = RequestCookies()
    actual_policies = request_policies or policies()
    actual_provenance = provenance()
    effective_body = (
        BytesBody(value=actual_body.source)
        if actual_body.multipart is None
        else MultipartBody(boundary=actual_body.multipart.boundary, parts=())
    )
    effective = EffectiveRequest(
        environment=environment,
        logical_request_fingerprint=request_fingerprint,
        method=method,
        url=url,
        headers=actual_headers,
        cookies=actual_cookies,
        body=effective_body,
        policies=actual_policies,
        provenance=actual_provenance,
    )
    return FinalizedWireRequest(
        environment=environment,
        logical_request_fingerprint=request_fingerprint,
        method=method,
        url=url,
        headers=actual_headers,
        cookies=actual_cookies,
        body=actual_body,
        materialization=prove_request_materialization(
            effective,
            actual_body,
            effective_request_fingerprint=materialization_fingerprint(effective),
        ),
        policies=actual_policies,
        provenance=actual_provenance,
    )


def test_sensitive_containers_validate_their_exact_runtime_payload_type() -> None:
    with pytest.raises(TypeError, match="payload must be HeaderText"):
        HeaderField(
            name=HeaderName(value="x-secret"),
            value=sensitive(QueryText(value="wrong")),  # type: ignore[arg-type]
            source=HeaderSource.AUTHORED,
            provenance=provenance(),
        )
    with pytest.raises(TypeError, match="payload must be QueryText"):
        QueryParameter(
            name=QueryName(value="secret"),
            value=sensitive(HeaderText(value="wrong")),  # type: ignore[arg-type]
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        )
    with pytest.raises(TypeError, match="wrong credential type"):
        BearerAuthentication(token=sensitive(ApiKeyCredential(value="wrong")))  # type: ignore[arg-type]


def test_every_request_cookie_requires_sensitive_exact_cookie_text() -> None:
    with pytest.raises(TypeError, match="must remain sensitive"):
        RequestCookie(
            name=CookieName(value="session"),
            value=CookieText(value="raw"),  # type: ignore[arg-type]
            source=CookieSource.EXPLICIT,
            provenance=provenance(),
        )
    cookie = RequestCookie(
        name=CookieName(value="session"),
        value=sensitive(CookieText(value="contained")),
        source=CookieSource.EXPLICIT,
        provenance=provenance(),
    )
    assert "contained" not in repr(cookie)


@pytest.mark.parametrize(
    "name",
    [
        "access_token",
        "api_key",
        "auth_token",
        "client_secret",
        "id_token",
        "refresh_token",
        "signature",
        "token",
        "X-Amz-Credential",
        "X-Amz-Security-Token",
        "X-Amz-Signature",
    ],
)
def test_builtin_credential_query_names_cannot_carry_public_values(name: str) -> None:
    with pytest.raises(ValueError, match="known credential query"):
        QueryParameter(
            name=QueryName(value=name),
            value=QueryText(value="public-secret"),
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        )


@pytest.mark.parametrize(
    "name",
    [
        "api-key",
        "authorization",
        "ocp-apim-subscription-key",
        "proxy-authorization",
        "x-amz-security-token",
        "x-access-token",
        "x-api-key",
        "x-auth-token",
        "x-functions-key",
        "x-goog-api-key",
        "x-master-key",
        "x-subscription-key",
    ],
)
def test_builtin_credential_headers_cannot_carry_public_values(name: str) -> None:
    with pytest.raises(ValueError, match="known credential header"):
        HeaderField(
            name=HeaderName(value=name),
            value=HeaderText(value="public-secret"),
            source=HeaderSource.AUTHORED,
            provenance=provenance(),
        )


def test_sensitive_body_snapshot_has_opaque_id_lineage_and_fails_serialization() -> None:
    secret_digest = digest("f")
    source = RepeatableByteSource(
        source_id=BodyId(value=uuid4()),
        byte_count=ByteCount(value=10),
        digest=sensitive(secret_digest, "secret-body"),
        provenance=provenance(),
    )
    assert source.taint_origins == (TaintOriginId(value="secret-body"),)
    with pytest.raises(TypeError, match="cannot be rendered"):
        repr(source)
    with pytest.raises(TypeError):
        msgspec.json.encode(source)


@pytest.mark.parametrize("name", ["host", "content-length", "transfer-encoding"])
def test_transport_derived_headers_have_no_caller_constructible_source(name: str) -> None:
    field = HeaderField(
        name=HeaderName(value=name),
        value=HeaderText(value="forged"),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="derived only by the transport"):
        RequestHeaders(fields=(field,))


@pytest.mark.parametrize("name", ["te", "trailer"])
def test_request_te_and_trailer_are_rejected_until_trailers_are_typed(name: str) -> None:
    field = HeaderField(
        name=HeaderName(value=name),
        value=HeaderText(value="trailers"),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="connection-specific"):
        RequestHeaders(fields=(field,))


@pytest.mark.parametrize("filename", ["line\nfeed", "../escape"])
def test_multipart_filenames_reject_disposition_and_path_injection(filename: str) -> None:
    with pytest.raises(ValueError, match="unsafe disposition"):
        MultipartDispositionFilename(value=filename)


def test_multipart_filename_quotes_are_safe_under_filename_star_only_contract() -> None:
    assert MultipartDispositionFilename(value='quote"name').value == 'quote"name'


def test_finalized_multipart_binds_collision_scan_snapshot_and_content_type() -> None:
    boundary = MultipartBoundary(value="comparo-boundary")
    source = RepeatableByteSource(
        source_id=BodyId(value=uuid4()),
        byte_count=ByteCount(value=42),
        digest=digest("9"),
        provenance=provenance(),
    )
    validation = MultipartBoundaryValidation(
        boundary=boundary,
        source_id=source.source_id,
        digest=source.digest,
        encoded_content_type=multipart_content_type(boundary),
    )
    body = MaterializedBody(source=source, multipart=validation)
    content_type = HeaderField(
        name=HeaderName(value="content-type"),
        value=validation.encoded_content_type,
        source=HeaderSource.BODY,
        provenance=provenance(),
    )
    assert finalized(headers=RequestHeaders(fields=(content_type,)), body=body).body == body

    wrong_source = RepeatableByteSource(
        source_id=BodyId(value=uuid4()),
        byte_count=source.byte_count,
        digest=source.digest,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="not bound to the materialized snapshot"):
        MaterializedBody(source=wrong_source, multipart=validation)
    with pytest.raises(ValueError, match="exactly one Content-Type"):
        finalized(body=body)


def test_authorization_binds_the_entire_final_request_and_pinned_direct_route() -> None:
    request = finalized()
    route = DirectResolvedRoute(destination=destination(DnsHost(value="api.example.test")))
    intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=request_occurrence(),
        plan_fingerprint=fingerprint("c"),
        effective_policy_fingerprint=fingerprint("d"),
    )
    authorization = HttpAuthorizationGrant(intent=intent, authority=authority("d"))
    assert AuthorizedExchangeRequest(authorization=authorization).route == route

    changed = finalized(
        headers=RequestHeaders(
            fields=(
                HeaderField(
                    name=HeaderName(value="x-changed"),
                    value=HeaderText(value="yes"),
                    source=HeaderSource.AUTHORED,
                    provenance=provenance(),
                ),
            )
        )
    )
    changed_intent = HttpAuthorizationIntent(
        request=changed,
        route=route,
        occurrence=request_occurrence(),
        plan_fingerprint=fingerprint("c"),
        effective_policy_fingerprint=fingerprint("d"),
    )
    assert changed_intent != authorization.intent


def test_dedicated_http_decision_requires_execution_policy_authority() -> None:
    request = finalized()
    route = DirectResolvedRoute(destination=destination(DnsHost(value="api.example.test")))
    intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=request_occurrence(),
        plan_fingerprint=fingerprint("3"),
        effective_policy_fingerprint=fingerprint("4"),
    )
    with pytest.raises(TypeError, match="exact ExecutionPolicyAuthority"):
        HttpAuthorizationGrant(
            intent=intent,
            authority=cast("ExecutionPolicyAuthority", provenance().entries),
        )


def test_proxy_route_pins_proxy_and_declares_target_resolution_owner() -> None:
    proxy_host = DnsHost(value="proxy.example.test")
    target_host = DnsHost(value="api.example.test")
    proxy_destination = destination(proxy_host, "192.0.2.20")
    route = ProxyResolvedRoute(
        proxy_destination=proxy_destination,
        target=ProxyResolvedTarget(
            scheme=HttpScheme.HTTPS,
            authority=HttpAuthority(host=target_host),
        ),
    )
    defaults = policies()
    request = finalized(
        request_policies=RequestPolicies(
            timeouts=defaults.timeouts,
            retries=defaults.retries,
            tls=defaults.tls,
            proxy=ExplicitProxy(
                scheme=HttpScheme.HTTPS,
                authority=HttpAuthority(host=proxy_host),
                authentication=NoAuthentication(),
                tls=ProxyTlsPolicy(policy=defaults.tls),
            ),
            redirects=defaults.redirects,
        )
    )
    occurrence = request_occurrence()
    denied_proxy_dns_intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=occurrence,
        plan_fingerprint=fingerprint("d"),
        effective_policy_fingerprint=fingerprint("e"),
    )
    with pytest.raises(ValueError, match="proxy-side DNS requires explicit"):
        HttpAuthorizationGrant(intent=denied_proxy_dns_intent, authority=authority("e"))
    delegated_target = route.target
    assert type(delegated_target) is ProxyResolvedTarget
    proxy_dns_approval = ProxyDnsApproval(
        request=request,
        route=route,
        occurrence=occurrence,
        plan_fingerprint=fingerprint("d"),
        authority=authority("e"),
    )
    intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=occurrence,
        plan_fingerprint=fingerprint("d"),
        effective_policy_fingerprint=fingerprint("e"),
        proxy_dns_approval=proxy_dns_approval,
    )
    assert HttpAuthorizationGrant(intent=intent, authority=authority()).intent.route == route

    client_target = ClientResolvedTarget(destination=destination(target_host))
    assert (
        ProxyResolvedRoute(
            proxy_destination=proxy_destination,
            target=client_target,
        ).target
        == client_target
    )


def test_unsafe_method_honors_explicit_execution_policy_approval_requirement() -> None:
    defaults = policies()
    request = finalized(
        request_policies=RequestPolicies(
            timeouts=defaults.timeouts,
            retries=defaults.retries,
            tls=defaults.tls,
            proxy=defaults.proxy,
            redirects=defaults.redirects,
            mutation_approval=MutationApprovalRequired(),
        )
    )
    route = DirectResolvedRoute(destination=destination(DnsHost(value="api.example.test")))
    plan = fingerprint("1")
    occurrence = request_occurrence()
    denied_intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=occurrence,
        plan_fingerprint=plan,
        effective_policy_fingerprint=fingerprint("2"),
    )
    with pytest.raises(ValueError, match="requires ExecutionPolicy mutation approval"):
        HttpAuthorizationGrant(intent=denied_intent, authority=authority("2"))

    approval = MutationApproval(
        request=request,
        route=route,
        occurrence=occurrence,
        plan_fingerprint=plan,
        authority=authority("2"),
    )
    intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=occurrence,
        plan_fingerprint=plan,
        effective_policy_fingerprint=fingerprint("2"),
        mutation_approval=approval,
    )
    assert intent.mutation_approval == approval


def test_finalized_mutation_retry_requires_one_sensitive_authored_key() -> None:
    defaults = policies()
    retry = RetryPolicy(
        max_attempts=PositiveCount(value=2),
        conditions=(RetryCondition.CONNECT_FAILURE,),
        backoff=NoBackoff(),
        jitter=Ratio(numerator=0, denominator=PositiveCount(value=1)),
        mutation_retry=IdempotencyKeyRetry(header_name=HeaderName(value="idempotency-key")),
    )
    request_policies = RequestPolicies(
        timeouts=defaults.timeouts,
        retries=retry,
        tls=defaults.tls,
        proxy=defaults.proxy,
        redirects=defaults.redirects,
    )
    with pytest.raises(ValueError, match="must exist exactly once"):
        finalized(request_policies=request_policies)

    key = HeaderField(
        name=HeaderName(value="idempotency-key"),
        value=sensitive(HeaderText(value="stable-key")),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    assert finalized(
        headers=RequestHeaders(fields=(key,)),
        request_policies=request_policies,
    ).headers.fields == (key,)
