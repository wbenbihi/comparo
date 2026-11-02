"""Tests for response capture, transport metadata, and observation hierarchy semantics."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ProxyTlsDisabled
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyCaptureState
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import ExplicitlyTruncatedCapture
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.transport import EndpointMetadata
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.scopes import BodyBudgetScope
from tests.core.domain.http.budget_factories import capture_exhaustion
from tests.core.domain.http.factories import policies
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import endpoint
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import measured as measured_timing
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.observation_factories import tls


def _resource_budget_body(*, size: int = 0, encoded: int = 0) -> ResponseEntityBody:
    handle = body_handle(size=size, extent=BodyCaptureExtent.PARTIAL)
    return ResponseEntityBody(
        capture=BudgetExceededCapture(
            exhaustion=capture_exhaustion(
                handle=handle,
                kind=CaptureBudgetKind.ENTITY_BYTES,
                encoded_byte_count=ByteCount(value=encoded),
            )
        ),
        handle=handle,
        encoded_byte_count=ByteCount(value=encoded),
    )


def test_capture_budget_scope_binds_the_full_authorized_attempt() -> None:
    """Reject body-matching exhaustion evidence replayed from another attempt scope."""
    effective_request = request()
    valid = exchange(
        exchange_number=77,
        effective_request=effective_request,
        observed_response=response(body=_resource_budget_body()),
        failure=failure(FailurePhase.RESOURCE_BUDGET),
    )
    assert valid.response is not None
    assert type(valid.response.body) is ResponseEntityBody
    capture = valid.response.body.capture
    assert type(capture) is BudgetExceededCapture
    scope = capture.exhaustion.budget.scope
    assert type(scope) is BodyBudgetScope
    foreign_scope = msgspec.structs.replace(
        scope,
        attempt=occurrence(execution=999, attempt=77, exchange=77).attempt,
    )
    foreign_capture = msgspec.structs.replace(
        capture,
        exhaustion=msgspec.structs.replace(
            capture.exhaustion,
            budget=msgspec.structs.replace(capture.exhaustion.budget, scope=foreign_scope),
        ),
    )
    with pytest.raises(ValueError, match="capture budget scope"):
        msgspec.structs.replace(
            valid,
            response=msgspec.structs.replace(
                valid.response,
                body=msgspec.structs.replace(valid.response.body, capture=foreign_capture),
            ),
        )


def test_non_2xx_is_a_successful_observation_and_partial_response_retains_facts() -> None:
    effective_request = request()
    server_error = exchange(
        exchange_number=1,
        effective_request=effective_request,
        observed_response=response(status=500),
    )
    assert server_error.failure is None
    assert server_error.response is not None
    assert server_error.response.status.value == 500

    partial = exchange(
        exchange_number=2,
        effective_request=effective_request,
        observed_response=response(
            body=ResponseEntityBody(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
                handle=body_handle(extent=BodyCaptureExtent.PARTIAL),
                encoded_byte_count=ByteCount(value=0),
            )
        ),
        failure=failure(),
    )
    assert partial.response is not None
    assert partial.failure is not None
    assert partial.failure.phase is FailurePhase.BODY_READ


def test_operational_capture_failures_require_their_matching_typed_failure() -> None:
    effective_request = request()
    cases = (
        (
            ResponseEntityBody(
                capture=RemoteTruncatedCapture(),
                handle=body_handle(extent=BodyCaptureExtent.PARTIAL),
                encoded_byte_count=ByteCount(value=0),
            ),
            FailurePhase.PROTOCOL,
        ),
        (
            _resource_budget_body(),
            FailurePhase.RESOURCE_BUDGET,
        ),
        (
            ResponseEntityBody(
                capture=StorageFailedCapture(),
                handle=None,
                encoded_byte_count=None,
            ),
            FailurePhase.STORAGE,
        ),
        (
            ResponseEntityBody(
                capture=CancelledCapture(),
                handle=None,
                encoded_byte_count=None,
            ),
            FailurePhase.CANCELLATION,
        ),
        (
            ResponseEntityBody(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
                handle=body_handle(extent=BodyCaptureExtent.PARTIAL),
                encoded_byte_count=ByteCount(value=0),
            ),
            FailurePhase.BODY_READ,
        ),
        (
            ResponseEntityBody(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.IDLE_TIMEOUT),
                handle=None,
                encoded_byte_count=ByteCount(value=0),
            ),
            FailurePhase.IDLE_TIMEOUT,
        ),
        (
            ResponseEntityBody(
                capture=ContentDecodingFailedCapture(),
                handle=None,
                encoded_byte_count=ByteCount(value=8),
            ),
            FailurePhase.CONTENT_DECODING,
        ),
    )
    for number, (body, phase) in enumerate(cases, start=1):
        observed_response = response(body=body)
        wrong_phase = (
            FailurePhase.STORAGE
            if phase is not FailurePhase.STORAGE
            else FailurePhase.RESOURCE_BUDGET
        )
        with pytest.raises(ValueError, match="matching failure phase"):
            exchange(
                exchange_number=number,
                effective_request=effective_request,
                observed_response=observed_response,
            )
        with pytest.raises(ValueError, match="matching failure phase"):
            exchange(
                exchange_number=number,
                effective_request=effective_request,
                observed_response=observed_response,
                failure=failure(wrong_phase),
            )
        assert (
            exchange(
                exchange_number=number,
                effective_request=effective_request,
                observed_response=observed_response,
                failure=failure(phase),
            ).failure
            is not None
        )

    for authored_capture in (
        ExplicitlyTruncatedCapture(limit=ByteCount(value=0)),
        PolicyOmittedCapture(),
    ):
        handle = (
            body_handle(extent=BodyCaptureExtent.PARTIAL)
            if type(authored_capture) is ExplicitlyTruncatedCapture
            else None
        )
        assert (
            exchange(
                exchange_number=10,
                effective_request=effective_request,
                observed_response=response(
                    body=ResponseEntityBody(
                        capture=authored_capture,
                        handle=handle,
                        encoded_byte_count=ByteCount(value=0) if handle is not None else None,
                    )
                ),
            ).failure
            is None
        )


def test_failure_phases_cannot_claim_incompatible_response_or_capture_facts() -> None:
    effective_request = request()
    with pytest.raises(ValueError, match="pre-send outcomes"):
        exchange(
            exchange_number=1,
            effective_request=effective_request,
            observed_response=response(),
            failure=failure(FailurePhase.DNS),
        )

    for number, phase in enumerate(
        (
            FailurePhase.BODY_READ,
            FailurePhase.IDLE_TIMEOUT,
            FailurePhase.CONTENT_DECODING,
            FailurePhase.RESOURCE_BUDGET,
            FailurePhase.STORAGE,
            FailurePhase.CANCELLATION,
        ),
        start=2,
    ):
        with pytest.raises(ValueError, match="matching body capture state"):
            exchange(
                exchange_number=number,
                effective_request=effective_request,
                observed_response=response(),
                failure=failure(phase),
            )


_PRE_RESPONSE_ONLY = frozenset(
    {
        FailurePhase.CONNECT,
        FailurePhase.PROXY,
        FailurePhase.TLS,
        FailurePhase.REQUEST_WRITE,
        FailurePhase.RESPONSE_HEADERS,
        FailurePhase.RESPONSE_HEADERS_TIMEOUT,
    }
)
_PRE_SEND_ONLY = frozenset({FailurePhase.DNS, FailurePhase.POLICY})
_POST_RESPONSE_ONLY = frozenset(
    {
        FailurePhase.BODY_READ,
        FailurePhase.IDLE_TIMEOUT,
        FailurePhase.CONTENT_DECODING,
        FailurePhase.RESOURCE_BUDGET,
        FailurePhase.STORAGE,
    }
)
_EITHER_SIDE = frozenset(
    {
        FailurePhase.PROTOCOL,
        FailurePhase.CANCELLATION,
    }
)


def _matching_failed_body(phase: FailurePhase) -> ResponseEntityBody:
    if phase is FailurePhase.RESOURCE_BUDGET:
        return _resource_budget_body(size=4, encoded=4)
    captures: dict[FailurePhase, BodyCaptureState] = {
        FailurePhase.BODY_READ: BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
        FailurePhase.IDLE_TIMEOUT: BodyReadFailedCapture(kind=BodyReadFailureKind.IDLE_TIMEOUT),
        FailurePhase.CONTENT_DECODING: ContentDecodingFailedCapture(),
        FailurePhase.STORAGE: StorageFailedCapture(),
        FailurePhase.PROTOCOL: RemoteTruncatedCapture(),
        FailurePhase.CANCELLATION: CancelledCapture(),
    }
    capture = captures[phase]
    handle = (
        body_handle(size=4, extent=BodyCaptureExtent.PARTIAL)
        if phase in {FailurePhase.BODY_READ, FailurePhase.PROTOCOL, FailurePhase.RESOURCE_BUDGET}
        else None
    )
    return ResponseEntityBody(
        capture=capture,
        handle=handle,
        encoded_byte_count=ByteCount(value=4) if handle is not None else None,
    )


@pytest.mark.parametrize("phase", tuple(FailurePhase))
def test_every_failure_phase_has_explicit_response_directionality(phase: FailurePhase) -> None:
    assert frozenset(FailurePhase) == (
        _PRE_SEND_ONLY | _PRE_RESPONSE_ONLY | _POST_RESPONSE_ONLY | _EITHER_SIDE
    )
    effective = request()
    if phase in _PRE_SEND_ONLY:
        with pytest.raises(ValueError, match="pre-send outcomes"):
            exchange(
                exchange_number=29,
                effective_request=effective,
                observed_response=None,
                failure=failure(phase),
            )
    elif phase in _POST_RESPONSE_ONLY:
        with pytest.raises(ValueError, match="post-response failure"):
            exchange(
                exchange_number=30,
                effective_request=effective,
                observed_response=None,
                failure=failure(phase),
            )
    else:
        assert (
            exchange(
                exchange_number=31,
                effective_request=effective,
                observed_response=None,
                failure=failure(phase),
            ).response
            is None
        )

    if phase in _PRE_SEND_ONLY:
        with pytest.raises(ValueError, match="pre-send outcomes"):
            exchange(
                exchange_number=32,
                effective_request=effective,
                observed_response=response(),
                failure=failure(phase),
            )
    elif phase in _PRE_RESPONSE_ONLY:
        with pytest.raises(ValueError, match="pre-response failure"):
            exchange(
                exchange_number=32,
                effective_request=effective,
                observed_response=response(),
                failure=failure(phase),
            )
    else:
        observed = exchange(
            exchange_number=33,
            effective_request=effective,
            observed_response=response(body=_matching_failed_body(phase)),
            failure=failure(phase),
        )
        assert observed.response is not None


def test_exchange_binds_response_endpoint_to_the_authorized_connect_destination() -> None:
    effective_request = request()
    authorized = authorized_request(effective_request)
    assert authorized.request.url == effective_request.url
    assert authorized.route == DirectResolvedRoute(destination=endpoint().destination)

    other_address = Ipv4Host(value="192.0.2.11")
    other_destination = NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="api.example.test"),
        port=HttpPort(value=443),
        resolved_addresses=(other_address,),
        selected_address=other_address,
    )
    other_endpoint = EndpointMetadata(
        destination=other_destination,
        remote_address=other_address,
        remote_port=HttpPort(value=443),
        local_address=None,
        local_port=None,
        connection_id="other-connection",
        connection_reused=False,
    )
    mismatched = msgspec.structs.replace(response(), endpoint=other_endpoint)
    with pytest.raises(ValueError, match="authorized connect destination"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=1)),
            request=authorized,
            response=mismatched,
            failure=None,
            redirect=None,
            timings=exchange_timings(),
        )

    proxy_address = Ipv4Host(value="192.0.2.20")
    proxy_destination = NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=DnsHost(value="proxy.example.test"),
        port=HttpPort(value=8443),
        resolved_addresses=(proxy_address,),
        selected_address=proxy_address,
    )
    proxy_route = ProxyResolvedRoute(
        proxy_destination=proxy_destination,
        target=ClientResolvedTarget(destination=endpoint().destination),
    )
    proxy_policy = ExplicitProxy(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(
            host=DnsHost(value="proxy.example.test"),
            port=HttpPort(value=8443),
        ),
        authentication=NoAuthentication(),
        tls=ProxyTlsPolicy(policy=policies().tls),
    )
    proxied_request = msgspec.structs.replace(
        effective_request,
        policies=msgspec.structs.replace(effective_request.policies, proxy=proxy_policy),
    )
    assert type(proxy_policy.tls) is ProxyTlsPolicy
    proxy_endpoint = EndpointMetadata(
        destination=proxy_destination,
        remote_address=proxy_address,
        remote_port=proxy_destination.port,
        local_address=None,
        local_port=None,
        connection_id="proxy-connection",
        connection_reused=False,
    )
    proxied_response = msgspec.structs.replace(
        response(),
        endpoint=proxy_endpoint,
        tls=TlsLayers(
            connect=tls(applied_policy=proxy_policy.tls),
            origin=tls(),
        ),
    )
    proxied_exchange = HttpExchange(
        exchange_id=ExchangeId(value=UUID(int=2)),
        request=authorized_request(
            proxied_request,
            proxy_route,
            request_occurrence=occurrence(exchange=2),
        ),
        response=proxied_response,
        failure=None,
        redirect=None,
        timings=exchange_timings(proxy=True),
    )
    assert proxied_exchange.response is not None
    assert proxied_exchange.response.endpoint.destination == proxy_destination


def test_proxy_and_tunneled_origin_tls_are_independent_layers() -> None:
    effective_request = request()
    proxy_address = Ipv4Host(value="192.0.2.30")
    proxy_destination = NetworkDestination(
        scheme=HttpScheme.HTTP,
        host=DnsHost(value="proxy.example.test"),
        port=HttpPort(value=8080),
        resolved_addresses=(proxy_address,),
        selected_address=proxy_address,
    )
    proxy_route = ProxyResolvedRoute(
        proxy_destination=proxy_destination,
        target=ClientResolvedTarget(destination=endpoint().destination),
    )
    proxy_policy = ExplicitProxy(
        scheme=HttpScheme.HTTP,
        authority=HttpAuthority(
            host=DnsHost(value="proxy.example.test"),
            port=HttpPort(value=8080),
        ),
        authentication=NoAuthentication(),
        tls=ProxyTlsDisabled(),
    )
    proxied_request = msgspec.structs.replace(
        effective_request,
        policies=msgspec.structs.replace(effective_request.policies, proxy=proxy_policy),
    )
    proxy_endpoint = EndpointMetadata(
        destination=proxy_destination,
        remote_address=proxy_address,
        remote_port=proxy_destination.port,
        local_address=None,
        local_port=None,
        connection_id="clear-proxy-connection",
        connection_reused=False,
    )
    observed = msgspec.structs.replace(
        response(),
        endpoint=proxy_endpoint,
        tls=TlsLayers(connect=TlsNotPerformed(), origin=tls()),
    )
    authorized = authorized_request(
        proxied_request,
        proxy_route,
        request_occurrence=occurrence(exchange=30),
    )
    assert (
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=30)),
            request=authorized,
            response=observed,
            failure=None,
            redirect=None,
            timings=exchange_timings(proxy=True),
        ).response
        is observed
    )

    with pytest.raises(ValueError, match="separate origin TLS"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=31)),
            request=authorized,
            response=msgspec.structs.replace(
                observed,
                tls=TlsLayers(connect=TlsNotPerformed()),
            ),
            failure=None,
            redirect=None,
            timings=exchange_timings(proxy=True),
        )


def test_exchange_timings_match_connection_route_tls_and_body_truth() -> None:
    effective_request = request()
    observed = response()
    fresh = exchange(
        exchange_number=50,
        effective_request=effective_request,
        observed_response=observed,
    )
    measured = measured_timing()
    reused_marker = NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED)

    with pytest.raises(ValueError, match="cannot claim connection-reused"):
        msgspec.structs.replace(
            fresh,
            timings=msgspec.structs.replace(fresh.timings, dns=reused_marker),
        )
    with pytest.raises(ValueError, match="proxy tunnel timing must be not performed"):
        msgspec.structs.replace(
            fresh,
            timings=msgspec.structs.replace(fresh.timings, proxy_tunnel=measured),
        )
    with pytest.raises(ValueError, match="TLS timing cannot be not performed"):
        msgspec.structs.replace(
            fresh,
            timings=msgspec.structs.replace(
                fresh.timings,
                tls=NotPerformed(reason=NotPerformedReason.CLEAR_TEXT_CONNECTION),
            ),
        )

    no_body = response(
        status=204,
        body=NoResponseBody(reason=NoBodyReason.STATUS_204),
    )
    without_body = exchange(
        exchange_number=51,
        effective_request=effective_request,
        observed_response=no_body,
    )
    with pytest.raises(ValueError, match="body read timing cannot be measured"):
        msgspec.structs.replace(
            without_body,
            timings=msgspec.structs.replace(without_body.timings, body_read=measured),
        )

    clear_request = request(scheme=HttpScheme.HTTP)
    clear_exchange = exchange(
        exchange_number=52,
        effective_request=clear_request,
        observed_response=response(scheme=HttpScheme.HTTP),
    )
    with pytest.raises(ValueError, match="clear-text-connection"):
        msgspec.structs.replace(
            clear_exchange,
            timings=msgspec.structs.replace(clear_exchange.timings, tls=measured),
        )


def test_reused_connection_timings_cannot_claim_fresh_network_work() -> None:
    effective_request = request()
    observed = response()
    reused_response = msgspec.structs.replace(
        observed,
        endpoint=msgspec.structs.replace(observed.endpoint, connection_reused=True),
    )
    reused = HttpExchange(
        exchange_id=ExchangeId(value=UUID(int=53)),
        request=authorized_request(
            effective_request,
            request_occurrence=occurrence(exchange=53),
        ),
        response=reused_response,
        failure=None,
        redirect=None,
        timings=exchange_timings(reused=True),
    )
    measured = measured_timing()
    contradictions = (
        msgspec.structs.replace(reused.timings, dns=measured),
        msgspec.structs.replace(reused.timings, connect=measured),
        msgspec.structs.replace(reused.timings, tls=measured),
    )
    for contradictory in contradictions:
        with pytest.raises(ValueError, match="must be not performed because of connection-reused"):
            msgspec.structs.replace(reused, timings=contradictory)


def test_pre_response_failure_requires_unavailable_later_phase_timings() -> None:
    failed = exchange(
        exchange_number=54,
        effective_request=request(),
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    contradictions = (
        msgspec.structs.replace(failed.timings, tls=measured_timing()),
        msgspec.structs.replace(
            failed.timings,
            body_read=NotPerformed(reason=NotPerformedReason.NO_RESPONSE_BODY),
        ),
    )
    for contradictory in contradictions:
        with pytest.raises(ValueError, match="after connect failure must be unavailable"):
            msgspec.structs.replace(failed, timings=contradictory)
