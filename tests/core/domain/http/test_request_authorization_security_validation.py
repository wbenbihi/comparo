"""Branch-complete request aggregate and outbound authorization validation tests."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.http import authorization as authorization_models
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.authorization import MutationApproval
from comparo.core.domain.http.authorization import ProxyDnsApproval
from comparo.core.domain.http.authorization import network_destination_identifier
from comparo.core.domain.http.authorization import proxy_dns_target_identifier
from comparo.core.domain.http.authorization import resolved_route_identifier
from comparo.core.domain.http.policies import MutationApprovalRequired
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.scopes import AttemptScope
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import direct_intent
from tests.core.domain.http.factories import finalized_request
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import proxy_request_and_route
from tests.core.domain.http.factories import request_occurrence

_OCCURRENCE = request_occurrence()


def test_http_request_occurrence_requires_exact_typed_ids() -> None:
    """Keep every outbound authorization bound to typed execution hierarchy IDs."""
    with pytest.raises(TypeError, match="attempt must be AttemptScope"):
        HttpRequestOccurrence(
            attempt=cast("AttemptScope", object()),
            exchange_id=ExchangeId(value=UUID(int=1)),
        )
    with pytest.raises(TypeError, match="exchange ID"):
        HttpRequestOccurrence(
            attempt=_OCCURRENCE.attempt,
            exchange_id=cast("ExchangeId", object()),
        )


def test_authorization_identifier_helpers_cover_every_route_shape() -> None:
    """Render direct, client-resolved proxy, and delegated proxy identities exactly."""
    with pytest.raises(TypeError, match="destination identifier"):
        network_destination_identifier(cast("NetworkDestination", object()))
    delegated = ProxyResolvedTarget(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="api.example.test")),
    )
    explicit_port = msgspec.structs.replace(
        delegated,
        authority=HttpAuthority(
            host=DnsHost(value="api.example.test"),
            port=HttpPort(value=8443),
        ),
    )
    assert ":443" in proxy_dns_target_identifier(delegated)
    assert ":8443" in proxy_dns_target_identifier(explicit_port)
    with pytest.raises(TypeError, match="requires ProxyResolvedTarget"):
        proxy_dns_target_identifier(cast("ProxyResolvedTarget", object()))

    assert resolved_route_identifier(DirectResolvedRoute(destination=destination())).startswith(
        "direct:"
    )
    _, client_route = proxy_request_and_route(delegated=False)
    _, delegated_route = proxy_request_and_route(delegated=True)
    assert "->client:" in resolved_route_identifier(client_route)
    assert "->proxy-dns:" in resolved_route_identifier(delegated_route)
    with pytest.raises(TypeError, match="requires ResolvedRoute"):
        resolved_route_identifier(cast("ResolvedRoute", object()))


def test_route_request_binding_rejects_every_mismatch() -> None:
    """Validate direct, client-resolved, delegated, and explicit proxy route bindings."""
    request, client_route = proxy_request_and_route(delegated=False)
    assert (
        HttpAuthorizationIntent(
            request=request,
            route=client_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
        ).route
        is client_route
    )

    delegated_request, delegated_route = proxy_request_and_route(delegated=True)
    approval = ProxyDnsApproval(
        request=delegated_request,
        route=delegated_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        authority=authority("b"),
    )
    wrong_target = ProxyResolvedTarget(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="other.example.test")),
    )
    with pytest.raises(ValueError, match="target does not match"):
        HttpAuthorizationIntent(
            request=delegated_request,
            route=msgspec.structs.replace(delegated_route, target=wrong_target),
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=ProxyDnsApproval(
                request=delegated_request,
                route=msgspec.structs.replace(delegated_route, target=wrong_target),
                occurrence=_OCCURRENCE,
                plan_fingerprint=fingerprint("a"),
                authority=authority("b"),
            ),
        )
    with pytest.raises(TypeError, match="route must be ResolvedRoute"):
        authorization_models._validate_route_request(
            cast("ResolvedRoute", object()),
            finalized_request(),
        )
    with pytest.raises(ValueError, match="target does not match"):
        HttpAuthorizationIntent(
            request=finalized_request(),
            route=DirectResolvedRoute(destination=destination("other.example.test")),
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
        )
    with pytest.raises(ValueError, match="no-proxy request"):
        HttpAuthorizationIntent(
            request=finalized_request(),
            route=ProxyResolvedRoute(
                proxy_destination=destination("proxy.example.test", "192.0.2.20"),
                target=ClientResolvedTarget(destination=destination()),
            ),
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
        )
    with pytest.raises(ValueError, match="explicit proxy request"):
        HttpAuthorizationIntent(
            request=request,
            route=DirectResolvedRoute(destination=destination()),
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
        )
    mismatched_proxy = msgspec.structs.replace(
        client_route,
        proxy_destination=destination("other-proxy.example.test", "192.0.2.30"),
    )
    with pytest.raises(ValueError, match="does not match the explicit proxy policy"):
        HttpAuthorizationIntent(
            request=request,
            route=mismatched_proxy,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
        )
    assert approval.route is delegated_route


def test_mutation_and_proxy_dns_approvals_validate_every_component() -> None:
    """Reject untyped approval bindings and capability contract identities."""
    request = finalized_request(method="POST")
    route = DirectResolvedRoute(destination=destination())
    plan = fingerprint("a")
    with pytest.raises(TypeError, match="request must be FinalizedWireRequest"):
        MutationApproval(
            request=cast("FinalizedWireRequest", object()),
            route=route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=plan,
            authority=authority(),
        )
    with pytest.raises(TypeError, match="route must be ResolvedRoute"):
        MutationApproval(
            request=request,
            route=cast("ResolvedRoute", object()),
            occurrence=_OCCURRENCE,
            plan_fingerprint=plan,
            authority=authority(),
        )
    with pytest.raises(TypeError, match="plan binding"):
        MutationApproval(
            request=request,
            route=route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=cast("Fingerprint", object()),
            authority=authority(),
        )
    with pytest.raises(TypeError, match="approval occurrence"):
        MutationApproval(
            request=request,
            route=route,
            occurrence=cast("HttpRequestOccurrence", object()),
            plan_fingerprint=plan,
            authority=authority(),
        )
    with pytest.raises(TypeError, match="contract identity"):
        MutationApproval(
            request=request,
            route=route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=plan,
            authority=authority(),
            capability_id=cast("CapabilityId", object()),
        )

    proxy_request, proxy_route = proxy_request_and_route(delegated=True)
    with pytest.raises(TypeError, match="request must be FinalizedWireRequest"):
        ProxyDnsApproval(
            request=cast("FinalizedWireRequest", object()),
            route=proxy_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=plan,
            authority=authority(),
        )
    with pytest.raises(TypeError, match="route must be ResolvedRoute"):
        ProxyDnsApproval(
            request=proxy_request,
            route=cast("ResolvedRoute", object()),
            occurrence=_OCCURRENCE,
            plan_fingerprint=plan,
            authority=authority(),
        )
    with pytest.raises(ValueError, match="proxy-resolved delegated route"):
        ProxyDnsApproval(
            request=finalized_request(),
            route=DirectResolvedRoute(destination=destination()),
            occurrence=_OCCURRENCE,
            plan_fingerprint=plan,
            authority=authority(),
        )
    with pytest.raises(TypeError, match="plan binding"):
        ProxyDnsApproval(
            request=proxy_request,
            route=proxy_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=cast("Fingerprint", object()),
            authority=authority(),
        )
    with pytest.raises(TypeError, match="approval occurrence"):
        ProxyDnsApproval(
            request=proxy_request,
            route=proxy_route,
            occurrence=cast("HttpRequestOccurrence", object()),
            plan_fingerprint=plan,
            authority=authority(),
        )
    with pytest.raises(ValueError, match="wrong built-in capability"):
        ProxyDnsApproval(
            request=proxy_request,
            route=proxy_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=plan,
            authority=authority(),
            operation=CapabilityOperation(value="dns.inspect"),
        )


def test_authorization_intent_validates_all_components_and_approval_bindings() -> None:
    """Exercise exact intent types, safe-method rules, and approval binding failures."""
    valid = direct_intent()
    with pytest.raises(TypeError, match="intent request"):
        msgspec.structs.replace(valid, request=cast("FinalizedWireRequest", object()))
    with pytest.raises(TypeError, match="intent route"):
        msgspec.structs.replace(valid, route=cast("ResolvedRoute", object()))
    with pytest.raises(TypeError, match="plan binding"):
        msgspec.structs.replace(valid, plan_fingerprint=cast("Fingerprint", object()))
    with pytest.raises(TypeError, match="effective HTTP policy"):
        msgspec.structs.replace(valid, effective_policy_fingerprint=cast("Fingerprint", object()))
    with pytest.raises(TypeError, match="authorization occurrence"):
        msgspec.structs.replace(valid, occurrence=cast("HttpRequestOccurrence", object()))

    safe_request = finalized_request()
    safe_route = DirectResolvedRoute(destination=destination())
    safe_approval = MutationApproval(
        request=safe_request,
        route=safe_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        authority=authority("b"),
    )
    with pytest.raises(ValueError, match="safe HTTP methods"):
        HttpAuthorizationIntent(
            request=safe_request,
            route=safe_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            mutation_approval=safe_approval,
        )

    unsafe_request = finalized_request(method="POST")
    unsafe_route = DirectResolvedRoute(destination=destination())
    with pytest.raises(TypeError, match="must be MutationApproval"):
        HttpAuthorizationIntent(
            request=unsafe_request,
            route=unsafe_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            mutation_approval=cast("MutationApproval", object()),
        )
    required_policies = msgspec.structs.replace(
        unsafe_request.policies,
        mutation_approval=MutationApprovalRequired(),
    )
    required_request = msgspec.structs.replace(
        unsafe_request,
        policies=required_policies,
    )
    denied_mutation_intent = HttpAuthorizationIntent(
        request=required_request,
        route=unsafe_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        effective_policy_fingerprint=fingerprint("b"),
    )
    with pytest.raises(ValueError, match="requires ExecutionPolicy mutation approval"):
        HttpAuthorizationGrant(intent=denied_mutation_intent, authority=authority("b"))
    required_approval = MutationApproval(
        request=required_request,
        route=unsafe_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        authority=authority("b"),
    )
    valid_required = HttpAuthorizationIntent(
        request=required_request,
        route=unsafe_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        effective_policy_fingerprint=fingerprint("b"),
        mutation_approval=required_approval,
    )
    assert valid_required.mutation_approval is required_approval
    mismatched = MutationApproval(
        request=unsafe_request,
        route=unsafe_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("f"),
        authority=authority("b"),
    )
    with pytest.raises(ValueError, match="not bound to this HTTP intent"):
        HttpAuthorizationIntent(
            request=unsafe_request,
            route=unsafe_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            mutation_approval=mismatched,
        )
    replayed = msgspec.structs.replace(
        mismatched,
        occurrence=request_occurrence(exchange=2),
        plan_fingerprint=fingerprint("a"),
    )
    with pytest.raises(ValueError, match="not bound to this HTTP intent"):
        HttpAuthorizationIntent(
            request=unsafe_request,
            route=unsafe_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            mutation_approval=replayed,
        )


def test_proxy_dns_approval_modes_are_exactly_bound() -> None:
    """Reject wrong approval types, client-route approvals, and delegated target mismatches."""
    delegated_request, delegated_route = proxy_request_and_route(delegated=True)
    denied_proxy_dns_intent = HttpAuthorizationIntent(
        request=delegated_request,
        route=delegated_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        effective_policy_fingerprint=fingerprint("b"),
    )
    with pytest.raises(ValueError, match="proxy-side DNS requires"):
        HttpAuthorizationGrant(intent=denied_proxy_dns_intent, authority=authority("b"))
    with pytest.raises(TypeError, match="must be ProxyDnsApproval"):
        HttpAuthorizationIntent(
            request=delegated_request,
            route=delegated_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=cast("ProxyDnsApproval", object()),
        )

    client_request, client_route = proxy_request_and_route(delegated=False)
    approval = ProxyDnsApproval(
        request=delegated_request,
        route=delegated_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        authority=authority("b"),
    )
    with pytest.raises(ValueError, match="client-resolved routes"):
        HttpAuthorizationIntent(
            request=client_request,
            route=client_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=approval,
        )

    other = ProxyResolvedTarget(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="other.example.test")),
    )
    other_route = msgspec.structs.replace(delegated_route, target=other)
    other_url = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="other.example.test")),
    )
    other_request = msgspec.structs.replace(
        delegated_request,
        url=other_url,
    )
    other_approval = ProxyDnsApproval(
        request=other_request,
        route=other_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        authority=authority("b"),
    )
    with pytest.raises(ValueError, match="not bound to this HTTP intent"):
        HttpAuthorizationIntent(
            request=delegated_request,
            route=delegated_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=other_approval,
        )
    replayed = msgspec.structs.replace(
        approval,
        occurrence=request_occurrence(exchange=2),
    )
    with pytest.raises(ValueError, match="not bound to this HTTP intent"):
        HttpAuthorizationIntent(
            request=delegated_request,
            route=delegated_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=replayed,
        )


def test_authorization_grant_and_transport_wrapper_reject_wrong_types() -> None:
    """Keep the final capability grant and transport-facing wrapper structurally closed."""
    with pytest.raises(TypeError, match="grant intent"):
        HttpAuthorizationGrant(
            intent=cast("HttpAuthorizationIntent", object()),
            authority=authority(),
        )
    with pytest.raises(TypeError, match="contract identity"):
        HttpAuthorizationGrant(
            intent=direct_intent(),
            authority=authority(),
            operation=cast("CapabilityOperation", object()),
        )
    with pytest.raises(TypeError, match="requires HttpAuthorizationGrant"):
        AuthorizedExchangeRequest(authorization=cast("HttpAuthorizationGrant", object()))
    assert CapabilityId(namespace="comparo", name="http", version="1").name == "http"


def test_http_authority_revision_and_approval_issuer_are_exact() -> None:
    """Reject untyped fingerprints, approval replay, and same-hash issuer substitution."""
    with pytest.raises(TypeError, match="policy binding"):
        authorization_models._validate_policy_authority(
            authority(),
            cast("Fingerprint", object()),
        )

    delegated_request, delegated_route = proxy_request_and_route(delegated=True)
    mismatched = ProxyDnsApproval(
        request=delegated_request,
        route=delegated_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        authority=authority("c"),
    )
    with pytest.raises(ValueError, match="not bound to this HTTP intent"):
        HttpAuthorizationIntent(
            request=delegated_request,
            route=delegated_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=mismatched,
        )

    approval_authority = ExecutionPolicyAuthority(
        policy=definition(
            ResourceKind.EXECUTION_POLICY,
            "approval-policy",
            fingerprint_character="b",
        )
    )
    grant_authority = ExecutionPolicyAuthority(
        policy=definition(
            ResourceKind.EXECUTION_POLICY,
            "grant-policy",
            fingerprint_character="b",
        )
    )
    intent = HttpAuthorizationIntent(
        request=delegated_request,
        route=delegated_route,
        occurrence=_OCCURRENCE,
        plan_fingerprint=fingerprint("a"),
        effective_policy_fingerprint=fingerprint("b"),
        proxy_dns_approval=ProxyDnsApproval(
            request=delegated_request,
            route=delegated_route,
            occurrence=_OCCURRENCE,
            plan_fingerprint=fingerprint("a"),
            authority=approval_authority,
        ),
    )
    with pytest.raises(ValueError, match="one ExecutionPolicy authority"):
        HttpAuthorizationGrant(intent=intent, authority=grant_authority)
