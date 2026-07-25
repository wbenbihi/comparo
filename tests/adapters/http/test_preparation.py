"""Production pre-send resolution and ExecutionPolicy authorization tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterable
from typing import cast

import msgspec
import pytest

from comparo.adapters.http import PolicyHttpExchangePreparer
from comparo.adapters.http import SystemHostResolver
from comparo.adapters.http import preparation as preparation_module
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import MutationApprovalRequired
from comparo.core.domain.http.policies import ProxyTlsDisabled
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import IpHost
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import NetworkAddressClass
from comparo.core.policy import trusted_local_policy
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import request_occurrence
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import request


class _Resolver:
    def __init__(
        self,
        addresses: tuple[IpHost, ...] = (Ipv4Host(value="203.0.113.10"),),
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.addresses = addresses
        self.failure = failure
        self.calls = 0
        self.closed = 0

    async def resolve(self, host: DnsHost, port: HttpPort) -> tuple[IpHost, ...]:
        assert type(host) is DnsHost
        assert type(port) is HttpPort
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.addresses

    async def close(self) -> None:
        self.closed += 1


def _policy(
    *,
    hosts: tuple[DnsHost, ...] = (),
    allow_any_host: bool = True,
    address_classes: tuple[NetworkAddressClass, ...] | None = None,
    require_mutation: bool = False,
) -> CompiledExecutionPolicy:
    policy = trusted_local_policy()
    allowed = policy.http.allowed_address_classes if address_classes is None else address_classes
    return msgspec.structs.replace(
        policy,
        http=msgspec.structs.replace(
            policy.http,
            allowed_hosts=hosts,
            allow_any_host=allow_any_host,
            allowed_address_classes=allowed,
            require_mutation_approval=require_mutation,
        ),
    )


def _wire(
    *,
    method: str = "GET",
    host: DnsHost | Ipv4Host | Ipv6Host | None = None,
    mutation_required: bool = False,
) -> FinalizedWireRequest:
    effective = request(method=method)
    effective = msgspec.structs.replace(
        effective,
        url=msgspec.structs.replace(
            effective.url,
            authority=HttpAuthority(host=host or DnsHost(value="api.example.test")),
        ),
        policies=msgspec.structs.replace(
            effective.policies,
            mutation_approval=(
                MutationApprovalRequired()
                if mutation_required
                else effective.policies.mutation_approval
            ),
        ),
    )
    return finalized_wire_request(effective)


def _occurrence(wire: FinalizedWireRequest) -> HttpRequestOccurrence:
    return request_occurrence(environment=wire.environment.identity)


def test_preparer_authorizes_dns_and_pins_canonical_route() -> None:
    async def scenario() -> None:
        resolver = _Resolver(
            addresses=(
                Ipv6Host(value="2001:db8::2"),
                Ipv4Host(value="203.0.113.10"),
                Ipv4Host(value="203.0.113.10"),
            )
        )
        wire = _wire()
        preparer = PolicyHttpExchangePreparer(
            policy=_policy(),
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
        )
        outcome = await preparer.prepare(wire, _occurrence(wire))
        assert type(outcome) is AuthorizedExchangeRequest
        assert type(outcome.route) is DirectResolvedRoute
        assert tuple(address.value for address in outcome.route.destination.resolved_addresses) == (
            "203.0.113.10",
            "2001:db8::2",
        )
        assert outcome.route.destination.selected_address == Ipv4Host(value="203.0.113.10")
        assert resolver.calls == 1
        await preparer.close()
        await preparer.close()
        assert resolver.closed == 1
        with pytest.raises(RuntimeError, match="closed"):
            await preparer.prepare(wire, _occurrence(wire))

    asyncio.run(scenario())


def test_logical_denial_occurs_before_dns_and_contains_only_safe_code() -> None:
    async def scenario() -> None:
        resolver = _Resolver()
        wire = _wire()
        policy = _policy(
            hosts=(DnsHost(value="allowed.example.test"),),
            allow_any_host=False,
        )
        outcome = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
        ).prepare(wire, _occurrence(wire))
        assert type(outcome) is HttpResolutionDenial
        assert outcome.code.value == "http.policy-resolution-denied"
        assert resolver.calls == 0

    asyncio.run(scenario())


def test_dns_failure_and_resolved_address_denial_are_distinct() -> None:
    async def scenario() -> None:
        wire = _wire()
        failed = await PolicyHttpExchangePreparer(
            policy=_policy(),
            plan_fingerprint=fingerprint("f"),
            resolver=_Resolver(failure=OSError("private resolver detail")),
        ).prepare(wire, _occurrence(wire))
        assert type(failed) is HttpResolutionFailure
        assert failed.failure.code.value == "http.dns-failed"
        denied = await PolicyHttpExchangePreparer(
            policy=_policy(address_classes=(NetworkAddressClass.PUBLIC,)),
            plan_fingerprint=fingerprint("f"),
            resolver=_Resolver(addresses=(Ipv4Host(value="127.0.0.1"),)),
        ).prepare(wire, _occurrence(wire))
        assert type(denied) is HttpAuthorizationDenial
        assert denied.code.value == "http.policy-destination-denied"

    asyncio.run(scenario())


def test_ip_literals_skip_dns_and_mutations_require_explicit_run_approval() -> None:
    async def scenario() -> None:
        resolver = _Resolver()
        wire = _wire(
            method="POST",
            host=Ipv4Host(value="127.0.0.1"),
            mutation_required=True,
        )
        policy = _policy(require_mutation=True)
        denied = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
        ).prepare(wire, _occurrence(wire))
        assert type(denied) is HttpMutationApprovalDenial
        approved = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
            approve_mutations=True,
        ).prepare(wire, _occurrence(wire))
        assert type(approved) is AuthorizedExchangeRequest
        assert approved.authorization.intent.mutation_approval is not None
        assert resolver.calls == 0

    asyncio.run(scenario())


def test_policy_level_mutation_requirement_denies_an_unmarked_request() -> None:
    async def scenario() -> None:
        wire = _wire(method="POST", host=Ipv4Host(value="127.0.0.1"))
        outcome = await PolicyHttpExchangePreparer(
            policy=_policy(require_mutation=True),
            plan_fingerprint=fingerprint("f"),
            resolver=_Resolver(),
        ).prepare(wire, _occurrence(wire))
        assert type(outcome) is HttpAuthorizationDenial
        assert outcome.code.value == "http.policy-mutation-approval-required"

    asyncio.run(scenario())


@pytest.mark.parametrize("addresses", [(), cast("tuple[IpHost, ...]", (object(),))])
def test_invalid_resolver_results_become_safe_dns_failures(
    addresses: tuple[IpHost, ...],
) -> None:
    async def scenario() -> None:
        wire = _wire()
        outcome = await PolicyHttpExchangePreparer(
            policy=_policy(),
            plan_fingerprint=fingerprint("f"),
            resolver=_Resolver(addresses=addresses),
        ).prepare(wire, _occurrence(wire))
        assert type(outcome) is HttpResolutionFailure
        assert outcome.failure.code.value == "http.dns-failed"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1", NetworkAddressClass.LOOPBACK),
        ("169.254.1.1", NetworkAddressClass.LINK_LOCAL),
        ("224.0.0.1", NetworkAddressClass.MULTICAST),
        ("0.0.0.0", NetworkAddressClass.UNSPECIFIED),  # noqa: S104
        ("10.0.0.1", NetworkAddressClass.PRIVATE),
        ("100.64.0.1", NetworkAddressClass.RESERVED),
        ("8.8.8.8", NetworkAddressClass.PUBLIC),
    ],
)
def test_address_classification_is_closed(address: str, expected: NetworkAddressClass) -> None:
    assert preparation_module._address_class(Ipv4Host(value=address)) is expected


def test_canonical_address_conversion_is_deterministic_and_rejects_invalid_input() -> None:
    assert preparation_module._canonical_addresses(
        cast("Iterable[str]", ["2001:0db8::1", "192.0.2.1", "192.0.2.1"])
    ) == (
        Ipv4Host(value="192.0.2.1"),
        Ipv6Host(value="2001:db8::1"),
    )
    with pytest.raises(ValueError, match="does not appear"):
        preparation_module._canonical_addresses(["not-an-address"])


def test_system_resolver_is_typed_and_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Loop:
        async def getaddrinfo(
            self, *_args: object, **_kwargs: object
        ) -> list[tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]]:
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:0db8::1", 443, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
            ]

    async def scenario() -> None:
        monkeypatch.setattr(
            asyncio,
            "get_running_loop",
            lambda: cast("asyncio.AbstractEventLoop", _Loop()),
        )
        resolver = SystemHostResolver()
        assert await resolver.resolve(DnsHost(value="example.test"), HttpPort(value=443)) == (
            Ipv4Host(value="192.0.2.1"),
            Ipv6Host(value="2001:db8::1"),
        )
        await resolver.close()
        with pytest.raises(TypeError, match="DnsHost"):
            await resolver.resolve(cast("DnsHost", object()), HttpPort(value=443))

    asyncio.run(scenario())


def test_preparer_constructor_and_prepare_boundaries_are_typed() -> None:
    policy = _policy()
    resolver = _Resolver()
    with pytest.raises(TypeError, match="CompiledExecutionPolicy"):
        PolicyHttpExchangePreparer(
            policy=cast("CompiledExecutionPolicy", object()),
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
        )
    with pytest.raises(TypeError, match="Fingerprint"):
        PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=cast("Fingerprint", object()),
            resolver=resolver,
        )
    with pytest.raises(TypeError, match="HostResolver"):
        PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=cast("_Resolver", object()),
        )
    with pytest.raises(TypeError, match="bool"):
        PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
            approve_mutations=cast("bool", 1),
        )

    async def scenario() -> None:
        preparer = PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
        )
        with pytest.raises(TypeError, match="finalized request"):
            await preparer.prepare(cast("FinalizedWireRequest", object()), _occurrence(_wire()))

    asyncio.run(scenario())


def test_unsupported_request_policies_are_denied_before_resolution() -> None:
    base = request()
    variants: list[EffectiveRequest] = [
        msgspec.structs.replace(
            base,
            policies=msgspec.structs.replace(
                base.policies,
                redirects=msgspec.structs.replace(
                    base.policies.redirects,
                    max_hops=Count(value=1),
                ),
            ),
        ),
        msgspec.structs.replace(
            base,
            policies=msgspec.structs.replace(
                base.policies,
                tls=msgspec.structs.replace(base.policies.tls, verify_hostname=False),
            ),
        ),
        msgspec.structs.replace(
            base,
            policies=msgspec.structs.replace(
                base.policies,
                proxy=ExplicitProxy(
                    scheme=HttpScheme.HTTP,
                    authority=HttpAuthority(
                        host=DnsHost(value="proxy.example.test"),
                        port=HttpPort(value=8080),
                    ),
                    authentication=NoAuthentication(),
                    tls=ProxyTlsDisabled(),
                ),
            ),
        ),
    ]

    async def scenario() -> None:
        resolver = _Resolver()
        preparer = PolicyHttpExchangePreparer(
            policy=_policy(),
            plan_fingerprint=fingerprint("f"),
            resolver=resolver,
        )
        for variant in variants:
            wire = finalized_wire_request(variant)
            outcome = await preparer.prepare(wire, _occurrence(wire))
            assert type(outcome) is HttpResolutionDenial
        assert resolver.calls == 0

    asyncio.run(scenario())
