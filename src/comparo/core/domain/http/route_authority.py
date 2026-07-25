"""Public route identifiers and finalized-request route coherence checks."""

from __future__ import annotations

from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import NoProxy
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.http.url import effective_port


def _destination_identifier(destination: NetworkDestination) -> str:
    host = destination.host.value
    if type(destination.host) is Ipv6Host:
        host = f"[{host}]"
    addresses = ",".join(address.value for address in destination.resolved_addresses)
    return (
        f"{destination.scheme.value}://{host}:{destination.port.value}"
        f"@{destination.selected_address.value}[{addresses}]"
    )


def network_destination_identifier(destination: NetworkDestination) -> str:
    """Return a public deterministic identifier for one pinned network destination."""
    if type(destination) is not NetworkDestination:
        raise TypeError("destination identifier requires NetworkDestination")
    return _destination_identifier(destination)


def proxy_dns_target_identifier(target: ProxyResolvedTarget) -> str:
    """Return the capability target for DNS resolution deliberately delegated to a proxy."""
    if type(target) is not ProxyResolvedTarget:
        raise TypeError("proxy DNS target identifier requires ProxyResolvedTarget")
    port = target.authority.port or effective_port(
        AbsoluteHttpUrl(scheme=target.scheme, authority=target.authority)
    )
    return f"proxy-dns:{target.scheme.value}://{target.authority.host.value}:{port.value}"


def resolved_route_identifier(route: ResolvedRoute) -> str:
    """Return the capability-target identity including target-resolution ownership."""
    if type(route) is DirectResolvedRoute:
        return f"direct:{_destination_identifier(route.destination)}"
    if type(route) is not ProxyResolvedRoute:
        raise TypeError("route identifier requires ResolvedRoute")
    proxy = _destination_identifier(route.proxy_destination)
    if type(route.target) is ClientResolvedTarget:
        target = f"client:{_destination_identifier(route.target.destination)}"
    else:
        target = proxy_dns_target_identifier(route.target)
    return f"proxy:{proxy}->{target}"


def validate_route_request(route: ResolvedRoute, request: FinalizedWireRequest) -> None:
    """Require one resolved route to preserve its finalized request and proxy intent."""
    if type(route) is DirectResolvedRoute:
        target = route.destination
    elif type(route) is ProxyResolvedRoute and type(route.target) is ClientResolvedTarget:
        target = route.target.destination
    elif type(route) is ProxyResolvedRoute and type(route.target) is ProxyResolvedTarget:
        if (
            route.target.scheme is not request.url.scheme
            or route.target.authority != request.url.authority
        ):
            raise ValueError("proxy-resolved route target does not match the finalized request")
        target = None
    else:
        raise TypeError("authorization route must be ResolvedRoute")
    if target is not None and (
        target.scheme is not request.url.scheme
        or target.host != request.url.authority.host
        or target.port != effective_port(request.url)
    ):
        raise ValueError("resolved route target does not match the finalized request")
    proxy_policy = request.policies.proxy
    if type(proxy_policy) is NoProxy and type(route) is not DirectResolvedRoute:
        raise ValueError("a no-proxy request requires a direct resolved route")
    if type(proxy_policy) is ExplicitProxy:
        if type(route) is not ProxyResolvedRoute:
            raise ValueError("an explicit proxy request requires a proxy resolved route")
        expected_port = proxy_policy.authority.port or effective_port(
            AbsoluteHttpUrl(scheme=proxy_policy.scheme, authority=proxy_policy.authority)
        )
        if (
            route.proxy_destination.scheme is not proxy_policy.scheme
            or route.proxy_destination.host != proxy_policy.authority.host
            or route.proxy_destination.port != expected_port
        ):
            raise ValueError("resolved proxy route does not match the explicit proxy policy")


__all__ = (
    "network_destination_identifier",
    "proxy_dns_target_identifier",
    "resolved_route_identifier",
    "validate_route_request",
)
