"""Production pre-send resolution and ExecutionPolicy authorization tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.adapters.http import PolicyHttpExchangePreparer
from comparo.adapters.http import SystemHostResolver
from comparo.adapters.http import preparation as preparation_module
from comparo.adapters.values import EnvironmentSnapshotRequest
from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import RunEnvironmentSnapshotBuilder
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import MutationApprovalRequired
from comparo.core.domain.http.policies import ProxyTlsDisabled
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.secret_egress import http_secret_egress_origins
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
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_egress import CONFIGURATION_SECRET_MARKER
from comparo.core.domain.value_egress import ValueEgressSourceKind
from comparo.core.domain.value_egress import value_egress_origin
from comparo.core.domain.value_egress import value_egress_source
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import BytesValue
from comparo.core.planning.planner import plan_run
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import NetworkAddressClass
from comparo.core.policy import PolicySecretEgress
from comparo.core.policy import PolicySecretEgressRule
from comparo.core.policy import trusted_local_policy
from comparo.core.policy.compiler import policy_fingerprint
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import RunContext
from comparo.core.runtime import ConfigurationRequestProvider
from comparo.core.runtime import RequestPreparationFailure
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime.http import _attempt_scope
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.factories import request_occurrence
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import request
from tests.core.planning.conftest import authority as planning_authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation
from tests.core.runtime.conftest import FixedClock


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


def test_network_address_classification_rejects_non_ip_hosts() -> None:
    with pytest.raises(TypeError, match="requires an IP host"):
        preparation_module.network_address_class(cast("IpHost", DnsHost(value="example.test")))
    assert preparation_module.network_address_class(Ipv4Host(value="8.8.8.8")).value == "public"


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


def _sensitive_wire(
    *,
    host: str,
    origins: tuple[TaintOriginId, ...],
    value: str = "egress-canary",
) -> FinalizedWireRequest:
    effective = request()
    effective = msgspec.structs.replace(
        effective,
        url=msgspec.structs.replace(
            effective.url,
            authority=HttpAuthority(host=DnsHost(value=host)),
        ),
        headers=RequestHeaders(
            fields=(
                HeaderField(
                    name=HeaderName(value="x-sensitive-value"),
                    value=SensitiveValue(HeaderText(value=value), origins=origins),
                    source=HeaderSource.AUTHORED,
                    provenance=provenance(),
                ),
            )
        ),
    )
    return finalized_wire_request(effective)


def _restricted_egress_policy(
    *sources: tuple[ValueEgressSourceKind, str],
) -> CompiledExecutionPolicy:
    base = trusted_local_policy()
    rule = PolicySecretEgressRule(
        sources=tuple(
            sorted(
                (
                    value_egress_source(kind, SymbolPath(segments=tuple(name.split("."))))
                    for kind, name in sources
                ),
                key=lambda source: (source.kind.value, source.symbol.segments),
            )
        ),
        allowed_schemes=(HttpScheme.HTTPS,),
        allowed_methods=(HttpMethod(value="GET"),),
        allowed_hosts=(DnsHost(value="allowed.example.test"),),
        allowed_ports=(HttpPort(value=443),),
    )
    values = msgspec.structs.replace(
        base.values,
        secret_egress=PolicySecretEgress(rules=(rule,), allow_any=False),
    )
    http = msgspec.structs.replace(
        base.http,
        allowed_hosts=(
            DnsHost(value="allowed.example.test"),
            DnsHost(value="other.example.test"),
        ),
        allow_any_host=False,
    )
    digest = policy_fingerprint(
        base.definition.identity,
        base.metadata,
        base.planning,
        http,
        values,
        base.artifacts,
    )
    return msgspec.structs.replace(
        base,
        definition=msgspec.structs.replace(base.definition, fingerprint=digest),
        http=http,
        values=values,
    )


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


def test_named_secret_egress_is_destination_bound_and_denied_before_dns() -> None:
    async def scenario() -> None:
        secret_symbol = SymbolPath(segments=("chat", "token"))
        environment_symbol = SymbolPath(segments=("API_TOKEN",))
        secret_origin = value_egress_origin(ValueEgressSourceKind.SECRET, secret_symbol)
        environment_origin = value_egress_origin(
            ValueEgressSourceKind.ENVIRONMENT,
            environment_symbol,
        )
        policy = _restricted_egress_policy(
            (ValueEgressSourceKind.ENVIRONMENT, "API_TOKEN"),
            (ValueEgressSourceKind.SECRET, "chat.token"),
        )

        for origin in (secret_origin, environment_origin):
            resolver = _Resolver()
            denied_wire = _sensitive_wire(
                host="other.example.test",
                origins=(origin,),
            )
            denied = await PolicyHttpExchangePreparer(
                policy=policy,
                plan_fingerprint=fingerprint("f"),
                resolver=resolver,
            ).prepare(denied_wire, _occurrence(denied_wire))
            assert type(denied) is HttpResolutionDenial
            assert denied.code.value == "http.policy-secret-egress-denied"
            assert resolver.calls == 0
            assert "egress-canary" not in repr(denied)

        allowed_wire = _sensitive_wire(
            host="allowed.example.test",
            origins=(environment_origin, secret_origin),
        )
        allowed = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=_Resolver(),
        ).prepare(allowed_wire, _occurrence(allowed_wire))
        assert type(allowed) is AuthorizedExchangeRequest
        approval = allowed.authorization.intent.secret_egress_approval
        assert approval is not None
        assert approval.request is allowed_wire
        assert approval.route == allowed.route
        assert approval.authority == allowed.authorization.authority

        unknown_wire = _sensitive_wire(
            host="allowed.example.test",
            origins=(TaintOriginId(value="unknown/provider-origin"), secret_origin),
        )
        unknown_resolver = _Resolver()
        unknown = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=unknown_resolver,
        ).prepare(unknown_wire, _occurrence(unknown_wire))
        assert type(unknown) is HttpResolutionDenial
        assert unknown.code.value == "http.policy-secret-egress-denied"
        assert unknown_resolver.calls == 0

        marker_resolver = _Resolver()
        marker_wire = _sensitive_wire(
            host="allowed.example.test",
            origins=(CONFIGURATION_SECRET_MARKER, environment_origin),
        )
        marker_denied = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=fingerprint("f"),
            resolver=marker_resolver,
        ).prepare(marker_wire, _occurrence(marker_wire))
        assert type(marker_denied) is HttpResolutionDenial
        assert marker_denied.code.value == "http.policy-secret-egress-denied"
        assert marker_resolver.calls == 0
        assert "egress-canary" not in repr(marker_denied)

    asyncio.run(scenario())


def test_configuration_resolution_preserves_egress_origins_until_pre_send(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        ambient_canary = "ambient-provider-run-context-canary"
        configured_canary = "configured-value-run-context-canary"
        policy = _restricted_egress_policy(
            (ValueEgressSourceKind.ENVIRONMENT, "API_TOKEN"),
            (ValueEgressSourceKind.SECRET, "chat.wrapped"),
            (ValueEgressSourceKind.SECRET, "chat.configured"),
        )
        project = compile_project(
            document(
                "Project",
                "demo",
                "{secrets: {chat: {configured: {$use: configured-token}}}, "
                "defaults: {subjects: "
                "[{$use: Request/check}], baseline: {$use: allowed}, "
                "candidates: [{$use: other}]}}",
            ),
            document(
                "Environment",
                "allowed",
                "{baseUrl: 'https://allowed.example.test', env: {inheritProcess: true}, "
                "secrets: {chat: {wrapped: {$use: ambient-token}}}}",
            ),
            document(
                "Environment",
                "other",
                "{baseUrl: 'https://other.example.test', env: {inheritProcess: true}, "
                "secrets: {chat: {wrapped: {$use: ambient-token}}}}",
            ),
            document(
                "Request",
                "check",
                "{request: {method: GET, endpoint: /, headers: {x-direct: {$env: API_TOKEN}, "
                "x-wrapped: {$secret: chat.wrapped}, "
                "x-configured: {$secret: chat.configured}}}}",
            ),
            document("Value", "ambient-token", "{value: {$env: API_TOKEN}}"),
            document("Value", "configured-token", f"{{value: {configured_canary}}}"),
        )
        authority = msgspec.structs.replace(planning_authority(), policy=policy.definition)
        planned = plan_run(project, invocation(), authority)
        assert planned.prepared is not None
        prepared = planned.prepared
        configuration = ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))
        snapshots = []
        with PinnedProjectFiles(tmp_path.resolve()) as files:
            builder = RunEnvironmentSnapshotBuilder(
                files,
                policy,
                ambient={"API_TOKEN": ambient_canary},
            )
            for environment in prepared.effective.environments:
                result = await builder.build(
                    EnvironmentSnapshotRequest(
                        environment=environment,
                        configuration=configuration,
                        policy=policy.definition,
                        inherit_process=True,
                    )
                )
                assert result.snapshot is not None
                snapshots.append(result.snapshot)

        context = RunContext(
            prepared=prepared,
            environment_snapshots=tuple(snapshots),
            clock=FixedClock(),
            generator=DeterministicGenerator(
                SensitiveValue(
                    BytesValue(value=b"egress-integration-seed"),
                    origins=(TaintOriginId(value="test/egress-integration-seed"),),
                )
            ),
        )
        provider = ConfigurationRequestProvider(prepared, context)
        work_by_environment = {
            work.environment.identity.resource_id.value: work
            for work in iter_execution_work(prepared)
        }
        allowed_request = await provider.prepare(
            work_by_environment["allowed"],
            _attempt_scope(work_by_environment["allowed"], 0),
        )
        other_request = await provider.prepare(
            work_by_environment["other"],
            _attempt_scope(work_by_environment["other"], 0),
        )
        assert not isinstance(allowed_request, RequestPreparationFailure)
        assert not isinstance(other_request, RequestPreparationFailure)

        expected_origins = {
            value_egress_origin(
                ValueEgressSourceKind.ENVIRONMENT,
                SymbolPath(segments=("API_TOKEN",)),
            ),
            value_egress_origin(
                ValueEgressSourceKind.SECRET,
                SymbolPath(segments=("chat", "wrapped")),
            ),
            value_egress_origin(
                ValueEgressSourceKind.SECRET,
                SymbolPath(segments=("chat", "configured")),
            ),
        }
        assert set(http_secret_egress_origins(allowed_request)) == expected_origins
        assert ambient_canary not in repr(allowed_request)
        assert configured_canary not in repr(allowed_request)

        allowed_resolver = _Resolver()
        allowed = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=prepared.plan.fingerprint,
            resolver=allowed_resolver,
        ).prepare(allowed_request, _occurrence(allowed_request))
        assert type(allowed) is AuthorizedExchangeRequest
        assert allowed_resolver.calls == 1

        direct_environment_denied_policy = _restricted_egress_policy(
            (ValueEgressSourceKind.SECRET, "chat.wrapped"),
            (ValueEgressSourceKind.SECRET, "chat.configured"),
        )
        direct_environment_resolver = _Resolver()
        direct_environment_denied = await PolicyHttpExchangePreparer(
            policy=direct_environment_denied_policy,
            plan_fingerprint=prepared.plan.fingerprint,
            resolver=direct_environment_resolver,
        ).prepare(allowed_request, _occurrence(allowed_request))
        assert type(direct_environment_denied) is HttpResolutionDenial
        assert direct_environment_denied.code.value == "http.policy-secret-egress-denied"
        assert direct_environment_resolver.calls == 0
        assert ambient_canary not in repr(direct_environment_denied)

        other_resolver = _Resolver()
        denied = await PolicyHttpExchangePreparer(
            policy=policy,
            plan_fingerprint=prepared.plan.fingerprint,
            resolver=other_resolver,
        ).prepare(other_request, _occurrence(other_request))
        assert type(denied) is HttpResolutionDenial
        assert denied.code.value == "http.policy-secret-egress-denied"
        assert other_resolver.calls == 0
        assert ambient_canary not in repr(denied)
        assert configured_canary not in repr(denied)

        await provider.close()
        await context.close()

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
        wire = _wire(method="PURGE", host=Ipv4Host(value="127.0.0.1"))
        policy = _policy(require_mutation=True)
        policy = msgspec.structs.replace(
            policy,
            http=msgspec.structs.replace(
                policy.http,
                allowed_methods=tuple(
                    sorted(
                        (*policy.http.allowed_methods, HttpMethod(value="PURGE")),
                        key=lambda item: item.value,
                    )
                ),
            ),
        )
        outcome = await PolicyHttpExchangePreparer(
            policy=policy,
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
