"""Policy-authorized system resolution and route pinning before HTTP transport."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from typing import Protocol
from typing import final

from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.authorization import MutationApproval
from comparo.core.domain.http.policies import MutationApprovalRequired
from comparo.core.domain.http.policies import NoProxy
from comparo.core.domain.http.pre_send import AuthorizedResolutionRequest
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.pre_send import HttpResolutionGrant
from comparo.core.domain.http.pre_send import HttpResolutionIntent
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.primitives import Fingerprint
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import NetworkAddressClass

_RESOLUTION_DENIED = DiagnosticCode(value="http.policy-resolution-denied")
_DESTINATION_DENIED = DiagnosticCode(value="http.policy-destination-denied")
_MUTATION_DENIED = DiagnosticCode(value="http.policy-mutation-approval-required")
_DNS_FAILED = DiagnosticCode(value="http.dns-failed")


class HostResolver(Protocol):
    """Resolve one already-authorized logical DNS host into canonical IP addresses."""

    async def resolve(self, host: DnsHost, port: HttpPort) -> tuple[IpHost, ...]:
        """Return all unique canonical addresses in deterministic order."""
        ...

    async def close(self) -> None:
        """Release resolver-owned resources."""
        ...


class SystemHostResolver:
    """Use the event loop's system resolver only after policy admission."""

    async def resolve(self, host: DnsHost, port: HttpPort) -> tuple[IpHost, ...]:
        """Resolve a DNS host without retaining resolver exception text."""
        if type(host) is not DnsHost or type(port) is not HttpPort:
            raise TypeError("system resolution requires DnsHost and HttpPort")
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(
            host.value,
            port.value,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        return _canonical_addresses(str(record[4][0]) for record in records)

    async def close(self) -> None:
        """Release no resources; the event loop owns its resolver executor."""


@final
class PolicyHttpExchangePreparer:
    """Authorize logical resolution, resolved addresses, mutation, and final send authority."""

    __slots__ = ("_approve_mutations", "_authority", "_closed", "_plan", "_policy", "_resolver")

    def __init__(
        self,
        *,
        policy: CompiledExecutionPolicy,
        plan_fingerprint: Fingerprint,
        resolver: HostResolver,
        approve_mutations: bool = False,
    ) -> None:
        """Bind one run to an exact policy revision and caller-owned resolver."""
        if type(policy) is not CompiledExecutionPolicy:
            raise TypeError("HTTP preparation requires CompiledExecutionPolicy")
        if type(plan_fingerprint) is not Fingerprint:
            raise TypeError("HTTP preparation requires a plan Fingerprint")
        if not hasattr(resolver, "resolve") or not hasattr(resolver, "close"):
            raise TypeError("HTTP preparation requires HostResolver")
        if type(approve_mutations) is not bool:
            raise TypeError("mutation approval choice must be bool")
        self._policy = policy
        self._authority = ExecutionPolicyAuthority(policy=policy.definition)
        self._plan = plan_fingerprint
        self._resolver = resolver
        self._approve_mutations = approve_mutations
        self._closed = False

    async def prepare(
        self,
        request: FinalizedWireRequest,
        occurrence: HttpRequestOccurrence,
    ) -> (
        AuthorizedExchangeRequest
        | HttpResolutionDenial
        | HttpResolutionFailure
        | HttpAuthorizationDenial
        | HttpMutationApprovalDenial
    ):
        """Return one route-pinned grant or a typed, value-free pre-send outcome."""
        if (
            type(request) is not FinalizedWireRequest
            or type(occurrence) is not HttpRequestOccurrence
        ):
            raise TypeError("HTTP preparation requires a finalized request and occurrence")
        if self._closed:
            raise RuntimeError("HTTP exchange preparer is closed")
        resolution_intent = HttpResolutionIntent(
            request=request,
            occurrence=occurrence,
            plan_fingerprint=self._plan,
            effective_policy_fingerprint=self._policy.definition.fingerprint,
        )
        if not _logical_request_allowed(self._policy, request):
            return HttpResolutionDenial(
                intent=resolution_intent,
                authority=self._authority,
                code=_RESOLUTION_DENIED,
                timings=_unavailable_timings(),
            )
        resolution_grant = HttpResolutionGrant(
            intent=resolution_intent,
            authority=self._authority,
        )
        authorized_resolution = AuthorizedResolutionRequest(authorization=resolution_grant)
        try:
            addresses = await self._addresses(
                request.url.authority.host, effective_port(request.url)
            )
        except (OSError, RuntimeError, UnicodeError, ValueError):
            return HttpResolutionFailure(
                request=authorized_resolution,
                failure=TransportFailure(
                    phase=FailurePhase.DNS,
                    code=_DNS_FAILED,
                    retryable=True,
                ),
                timings=_unavailable_timings(),
            )
        destination = NetworkDestination(
            scheme=request.url.scheme,
            host=request.url.authority.host,
            port=effective_port(request.url),
            resolved_addresses=addresses,
            selected_address=addresses[0],
        )
        route = DirectResolvedRoute(destination=destination)
        approval = self._mutation_approval(request, occurrence, route)
        intent = HttpAuthorizationIntent(
            request=request,
            route=route,
            occurrence=occurrence,
            plan_fingerprint=self._plan,
            effective_policy_fingerprint=self._policy.definition.fingerprint,
            mutation_approval=approval,
        )
        if _mutation_required(self._policy, request) and approval is None:
            if type(request.policies.mutation_approval) is MutationApprovalRequired:
                return HttpMutationApprovalDenial(
                    intent=intent,
                    authority=self._authority,
                    code=_MUTATION_DENIED,
                    timings=_unavailable_timings(),
                )
            return HttpAuthorizationDenial(
                intent=intent,
                authority=self._authority,
                code=_MUTATION_DENIED,
                timings=_unavailable_timings(),
            )
        if not _addresses_allowed(self._policy, addresses):
            return HttpAuthorizationDenial(
                intent=intent,
                authority=self._authority,
                code=_DESTINATION_DENIED,
                timings=_unavailable_timings(),
            )
        return AuthorizedExchangeRequest(
            authorization=HttpAuthorizationGrant(intent=intent, authority=self._authority)
        )

    async def _addresses(self, host: HttpHost, port: HttpPort) -> tuple[IpHost, ...]:
        if type(host) is Ipv4Host or type(host) is Ipv6Host:
            return (host,)
        addresses = await self._resolver.resolve(host, port)
        if type(addresses) is not tuple or not addresses:
            raise ValueError("resolver returned no canonical addresses")
        if any(type(address) not in {Ipv4Host, Ipv6Host} for address in addresses):
            raise ValueError("resolver returned an unsupported address")
        return _canonical_addresses(address.value for address in addresses)

    def _mutation_approval(
        self,
        request: FinalizedWireRequest,
        occurrence: HttpRequestOccurrence,
        route: DirectResolvedRoute,
    ) -> MutationApproval | None:
        if not _mutation_required(self._policy, request) or not self._approve_mutations:
            return None
        return MutationApproval(
            request=request,
            route=route,
            occurrence=occurrence,
            plan_fingerprint=self._plan,
            authority=self._authority,
        )

    async def close(self) -> None:
        """Close the resolver exactly once."""
        if self._closed:
            return
        self._closed = True
        await self._resolver.close()


def _logical_request_allowed(
    policy: CompiledExecutionPolicy,
    request: FinalizedWireRequest,
) -> bool:
    http = policy.http
    port = effective_port(request.url)
    host = request.url.authority.host
    return (
        request.url.scheme in http.allowed_schemes
        and request.method in http.allowed_methods
        and (http.allow_any_port or port in http.allowed_ports)
        and (http.allow_any_host or host in http.allowed_hosts)
        and type(request.policies.proxy) is NoProxy
        and request.policies.redirects.max_hops.value == 0
        and request.policies.tls.verify_certificates
        and request.policies.tls.verify_hostname
        and request.policies.tls.custom_trust_store is None
        and request.policies.tls.client_identity is None
    )


def _mutation_required(
    policy: CompiledExecutionPolicy,
    request: FinalizedWireRequest,
) -> bool:
    return policy.http.require_mutation_approval and request.method.value not in {
        "GET",
        "HEAD",
        "OPTIONS",
        "TRACE",
    }


def _addresses_allowed(
    policy: CompiledExecutionPolicy,
    addresses: tuple[IpHost, ...],
) -> bool:
    allowed = frozenset(policy.http.allowed_address_classes)
    return all(_address_class(address) in allowed for address in addresses)


def _address_class(address: IpHost) -> NetworkAddressClass:
    parsed = ipaddress.ip_address(address.value)
    if parsed.is_loopback:
        return NetworkAddressClass.LOOPBACK
    if parsed.is_link_local:
        return NetworkAddressClass.LINK_LOCAL
    if parsed.is_multicast:
        return NetworkAddressClass.MULTICAST
    if parsed.is_unspecified:
        return NetworkAddressClass.UNSPECIFIED
    if parsed.is_private:
        return NetworkAddressClass.PRIVATE
    if parsed.is_reserved or not parsed.is_global:
        return NetworkAddressClass.RESERVED
    return NetworkAddressClass.PUBLIC


def _canonical_addresses(values: Iterable[str]) -> tuple[IpHost, ...]:
    addresses: set[IpHost] = set()
    for value in values:
        parsed = ipaddress.ip_address(value)
        address_type = Ipv4Host if parsed.version == 4 else Ipv6Host
        addresses.add(address_type(value=parsed.compressed))
    return tuple(
        sorted(
            addresses,
            key=lambda item: (ipaddress.ip_address(item.value).version, item.value),
        )
    )


def _unavailable_timings() -> HttpExchangeTimings:
    unavailable = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return HttpExchangeTimings(
        connection_acquisition=unavailable,
        dns=unavailable,
        connect=unavailable,
        proxy_tunnel=unavailable,
        tls=unavailable,
        request_write=unavailable,
        response_headers=unavailable,
        server_wait=unavailable,
        first_body_byte=unavailable,
        body_read=unavailable,
        elapsed=unavailable,
    )


__all__ = (
    "HostResolver",
    "PolicyHttpExchangePreparer",
    "SystemHostResolver",
)
