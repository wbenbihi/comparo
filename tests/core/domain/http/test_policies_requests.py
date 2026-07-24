"""Tests for explicit HTTP policies and authorization-bound request stages."""

from __future__ import annotations

from decimal import Decimal

import pytest

from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import HeaderApiKeyPlacement
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import network_destination_identifier
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
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
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import MutationRetry
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import NoMutationRetry
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.policies import RedirectPolicy
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.policies import TimeoutPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.policies import TrustStoreRef
from comparo.core.domain.http.policies import UnlimitedTimeout
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import LogicalRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import BaseRelativeTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import api_key_secret
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import policies
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.factories import request_occurrence


def retry_policy(
    *,
    attempts: int,
    conditions: tuple[RetryCondition, ...],
    strategy: MutationRetry | None = None,
) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=PositiveCount(value=attempts),
        conditions=conditions,
        backoff=NoBackoff(),
        jitter=Ratio(numerator=0, denominator=PositiveCount(value=1)),
        mutation_retry=strategy or NoMutationRetry(),
    )


def request_policies(retries: RetryPolicy) -> RequestPolicies:
    defaults = policies()
    return RequestPolicies(
        timeouts=defaults.timeouts,
        retries=retries,
        tls=defaults.tls,
        proxy=defaults.proxy,
        redirects=defaults.redirects,
    )


def authored_header(name: str, value: str = "value") -> HeaderField:
    header_value = HeaderText(value=value)
    return HeaderField(
        name=HeaderName(value=name),
        value=(
            SensitiveValue(
                header_value,
                origins=(TaintOriginId(value="authored-credential-header"),),
            )
            if name in {"x-api-key", "x-auth-token", "x-access-token", "api-key"}
            else header_value
        ),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )


def authored_query(name: str, value: str = "value") -> QueryParameter:
    query_value = QueryText(value=value)
    return QueryParameter(
        name=QueryName(value=name),
        value=(
            SensitiveValue(
                query_value,
                origins=(TaintOriginId(value="authored-credential-query"),),
            )
            if name in {"access_token", "api_key"}
            else query_value
        ),
        source=QuerySource.AUTHORED,
        provenance=provenance(),
    )


def effective_request(
    *,
    url: AbsoluteHttpUrl | None = None,
    headers: RequestHeaders | None = None,
    query: QueryParameters | None = None,
) -> EffectiveRequest:
    actual_url = url or AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="api.example.test")),
        query=query or QueryParameters(),
    )
    return EffectiveRequest(
        environment=definition(ResourceKind.ENVIRONMENT, "staging"),
        logical_request_fingerprint=fingerprint("a"),
        method=HttpMethod(value="GET"),
        url=actual_url,
        headers=headers or RequestHeaders(),
        cookies=RequestCookies(),
        body=NoBody(),
        policies=policies(),
        provenance=provenance(),
    )


def destination(*, host: DnsHost | Ipv6Host | None = None) -> NetworkDestination:
    actual_host = host or DnsHost(value="api.example.test")
    address = actual_host if type(actual_host) is Ipv6Host else Ipv4Host(value="192.0.2.10")
    return NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=actual_host,
        port=HttpPort(value=443),
        resolved_addresses=(address,),
        selected_address=address,
    )


def finalized_request(request: EffectiveRequest) -> FinalizedWireRequest:
    body = NoMaterializedBody()
    return FinalizedWireRequest(
        environment=request.environment,
        logical_request_fingerprint=request.logical_request_fingerprint,
        method=request.method,
        url=request.url,
        headers=request.headers,
        cookies=request.cookies,
        body=body,
        materialization=prove_request_materialization(
            request,
            body,
            effective_request_fingerprint=fingerprint("9"),
        ),
        policies=request.policies,
        provenance=request.provenance,
    )


def test_timeout_policy_distinguishes_phases_and_explicit_unlimited() -> None:
    short = Duration(nanoseconds=1)
    unlimited = UnlimitedTimeout()
    policy = TimeoutPolicy(
        connect=short,
        tls_handshake=short,
        response_headers=short,
        idle=unlimited,
        attempt_total=short,
        execution_total=unlimited,
    )
    assert policy.idle is unlimited

    with pytest.raises(ValueError, match="cannot be shorter"):
        TimeoutPolicy(
            connect=short,
            tls_handshake=short,
            response_headers=short,
            idle=short,
            attempt_total=Duration(nanoseconds=2),
            execution_total=short,
        )


def test_retry_policy_has_explicit_conditions_backoff_and_jitter() -> None:
    fixed = FixedBackoff(delay=Duration(nanoseconds=10))
    exponential = ExponentialBackoff(
        initial=Duration(nanoseconds=10),
        multiplier=Ratio.from_decimal(Decimal("2")),
        maximum=Duration(nanoseconds=100),
    )
    policy = RetryPolicy(
        max_attempts=PositiveCount(value=3),
        conditions=(RetryCondition.CONNECT_FAILURE, RetryCondition.STATUS_5XX),
        backoff=exponential,
        jitter=Ratio.from_decimal(Decimal("0.5")),
    )
    assert policy.max_attempts.value == 3
    assert fixed.delay.nanoseconds == 10


def test_retry_policy_rejects_incoherent_schedules() -> None:
    with pytest.raises(ValueError, match="disabled retries"):
        retry_policy(attempts=1, conditions=(RetryCondition.CONNECT_FAILURE,))
    with pytest.raises(ValueError, match="require at least one"):
        retry_policy(attempts=2, conditions=())
    with pytest.raises(ValueError, match="unique"):
        retry_policy(
            attempts=2,
            conditions=(RetryCondition.CONNECT_FAILURE, RetryCondition.CONNECT_FAILURE),
        )
    with pytest.raises(ValueError, match="between zero and one"):
        RetryPolicy(
            max_attempts=PositiveCount(value=2),
            conditions=(RetryCondition.CONNECT_FAILURE,),
            backoff=NoBackoff(),
            jitter=Ratio(numerator=2, denominator=PositiveCount(value=1)),
        )


def test_exponential_backoff_rejects_decreasing_or_inverted_configuration() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ExponentialBackoff(
            initial=Duration(nanoseconds=1),
            multiplier=Ratio.from_decimal(Decimal("0.5")),
            maximum=Duration(nanoseconds=2),
        )
    with pytest.raises(ValueError, match="maximum"):
        ExponentialBackoff(
            initial=Duration(nanoseconds=2),
            multiplier=Ratio.from_decimal(Decimal("1")),
            maximum=Duration(nanoseconds=1),
        )


def test_tls_proxy_and_redirect_policies_are_explicit() -> None:
    tls = TlsPolicy(
        verify_certificates=True,
        verify_hostname=True,
        minimum_version=TlsVersion.TLS_1_3,
        trust_source=TrustSource.CUSTOM,
        custom_trust_store=TrustStoreRef(value="company-ca"),
        client_identity=ClientIdentityRef(value="ci-client"),
    )
    proxy = ExplicitProxy(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="proxy.example.test")),
        authentication=NoAuthentication(),
        tls=ProxyTlsPolicy(
            policy=TlsPolicy(
                verify_certificates=True,
                verify_hostname=True,
                minimum_version=TlsVersion.TLS_1_2,
                trust_source=TrustSource.OPERATING_SYSTEM,
                client_identity=ClientIdentityRef(value="proxy-client"),
            )
        ),
    )
    redirect = RedirectPolicy(
        max_hops=Count(value=3),
        method_mode=RedirectMethodMode.STRICT_PRESERVE,
    )
    assert tls.custom_trust_store == TrustStoreRef(value="company-ca")
    assert proxy.scheme is HttpScheme.HTTPS
    assert type(proxy.tls) is ProxyTlsPolicy
    assert proxy.tls.policy.client_identity == ClientIdentityRef(value="proxy-client")
    assert redirect.block_https_downgrade


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "Company CA",
        "/etc/ssl/private",
        "-----BEGIN " + "PRIVATE KEY-----",
        "credential\nforged",
        "a" * 64,
    ],
)
def test_tls_material_references_are_bounded_logical_slugs(invalid: object) -> None:
    with pytest.raises(ValueError, match="lowercase logical slug"):
        TrustStoreRef(value=invalid)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lowercase logical slug"):
        ClientIdentityRef(value=invalid)  # type: ignore[arg-type]


def test_tls_policy_rejects_ambiguous_trust_and_verification() -> None:
    with pytest.raises(ValueError, match="hostname verification"):
        TlsPolicy(
            verify_certificates=False,
            verify_hostname=True,
            minimum_version=TlsVersion.TLS_1_2,
            trust_source=TrustSource.OPERATING_SYSTEM,
        )
    with pytest.raises(ValueError, match="requires a TrustStoreRef"):
        TlsPolicy(
            verify_certificates=True,
            verify_hostname=True,
            minimum_version=TlsVersion.TLS_1_2,
            trust_source=TrustSource.CUSTOM,
        )
    with pytest.raises(ValueError, match="cannot carry"):
        TlsPolicy(
            verify_certificates=True,
            verify_hostname=True,
            minimum_version=TlsVersion.TLS_1_2,
            trust_source=TrustSource.OPERATING_SYSTEM,
            custom_trust_store=TrustStoreRef(value="unexpected"),
        )


def test_logical_request_is_cell_shared_and_detects_api_key_collisions() -> None:
    api_key = ApiKeyAuthentication(
        credential=api_key_secret(),
        placement=HeaderApiKeyPlacement(name=HeaderName(value="x-api-key")),
    )
    target = BaseRelativeTarget()
    request = LogicalRequest(
        definition=definition(ResourceKind.REQUEST, "health"),
        method=HttpMethod(value="GET"),
        target=target,
        headers=RequestHeaders(),
        cookies=RequestCookies(),
        body=NoBody(),
        authentication=NoAuthentication(),
        policies=policies(),
        provenance=provenance(),
    )
    assert request.target == target

    with pytest.raises(ValueError, match="collides with an authored header"):
        LogicalRequest(
            definition=definition(ResourceKind.REQUEST, "health"),
            method=HttpMethod(value="GET"),
            target=target,
            headers=RequestHeaders(fields=(authored_header("x-api-key"),)),
            cookies=RequestCookies(),
            body=NoBody(),
            authentication=api_key,
            policies=policies(),
            provenance=provenance(),
        )


def test_logical_request_rejects_generated_sources_and_mutation_retry_without_strategy() -> None:
    generated = HeaderField(
        name=HeaderName(value="x-generated"),
        value=HeaderText(value="value"),
        source=HeaderSource.BODY,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="only authored"):
        LogicalRequest(
            definition=definition(ResourceKind.REQUEST, "mutation"),
            method=HttpMethod(value="POST"),
            target=BaseRelativeTarget(),
            headers=RequestHeaders(fields=(generated,)),
            cookies=RequestCookies(),
            body=NoBody(),
            authentication=NoAuthentication(),
            policies=policies(),
            provenance=provenance(),
        )

    retries = retry_policy(attempts=2, conditions=(RetryCondition.CONNECT_FAILURE,))
    with pytest.raises(ValueError, match="idempotency strategy"):
        LogicalRequest(
            definition=definition(ResourceKind.REQUEST, "mutation"),
            method=HttpMethod(value="POST"),
            target=BaseRelativeTarget(),
            headers=RequestHeaders(),
            cookies=RequestCookies(),
            body=NoBody(),
            authentication=NoAuthentication(),
            policies=request_policies(retries),
            provenance=provenance(),
        )


def test_effective_request_rejects_cross_source_and_framing_collisions() -> None:
    auth_field = HeaderField(
        name=HeaderName(value="x-api-key"),
        value=SensitiveValue(
            HeaderText(value="generated"),
            origins=(TaintOriginId(value="generated-auth"),),
        ),
        source=HeaderSource.AUTHENTICATION,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="authentication-generated header"):
        effective_request(headers=RequestHeaders(fields=(authored_header("x-api-key"), auth_field)))

    with pytest.raises(ValueError, match="derived only by the transport"):
        RequestHeaders(fields=(authored_header("content-length", "0"),))


def test_effective_query_rejects_authentication_collision() -> None:
    authored = authored_query("api_key")
    generated = QueryParameter(
        name=QueryName(value="api_key"),
        value=SensitiveValue(
            QueryText(value="generated"),
            origins=(TaintOriginId(value="generated-query-auth"),),
        ),
        source=QuerySource.AUTHENTICATION,
        provenance=provenance(),
    )
    with pytest.raises(ValueError, match="authentication-generated query"):
        effective_request(query=QueryParameters(parameters=(authored, generated)))


def test_authorized_exchange_request_binds_request_destination_plan_and_policy() -> None:
    request = finalized_request(effective_request())
    network_destination = destination()
    route = DirectResolvedRoute(destination=network_destination)
    intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=request_occurrence(),
        plan_fingerprint=fingerprint("c"),
        effective_policy_fingerprint=fingerprint("d"),
    )
    authorization = HttpAuthorizationGrant(intent=intent, authority=authority("d"))
    authorized = AuthorizedExchangeRequest(authorization=authorization)
    assert authorized.request == request
    assert "api.example.test:443" in network_destination_identifier(network_destination)


def test_http_authorization_rejects_wrong_contract_and_route_binding() -> None:
    request = finalized_request(effective_request())
    network_destination = destination()
    route = DirectResolvedRoute(destination=network_destination)
    intent = HttpAuthorizationIntent(
        request=request,
        route=route,
        occurrence=request_occurrence(),
        plan_fingerprint=fingerprint("c"),
        effective_policy_fingerprint=fingerprint("d"),
    )
    with pytest.raises(ValueError, match="wrong built-in capability"):
        HttpAuthorizationGrant(
            intent=intent,
            authority=authority("d"),
            capability_id=CapabilityId(namespace="comparo", name="http", version="1"),
            operation=CapabilityOperation(value="request.inspect"),
        )


def test_network_destination_identifier_brackets_ipv6() -> None:
    network_destination = destination(host=Ipv6Host(value="2001:db8::1"))
    assert network_destination_identifier(network_destination).startswith(
        "https://[2001:db8::1]:443@"
    )
