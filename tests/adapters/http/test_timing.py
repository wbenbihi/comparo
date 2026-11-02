"""Monotonic HTTP timing projection and cutoff tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from typing import cast

import httpcore
import msgspec
import pytest

from comparo.adapters.http.timing import ExchangeTraceRecorder
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from tests.core.domain.http.factories import destination
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import response


class _Clock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 10
        return self.value


def _failure(phase: FailurePhase) -> TransportFailure:
    return TransportFailure(
        phase=phase,
        code=DiagnosticCode(value="http.test-failure"),
        retryable=False,
    )


def _entity_response(size: int = 4) -> ResponseObservation:
    return response(
        body=ResponseEntityBody(
            capture=CompleteCapture(),
            handle=body_handle(size=size),
            encoded_byte_count=ByteCount(value=size),
        )
    )


def test_trace_projects_complete_exchange_and_mutates_only_timeout_metadata() -> None:
    async def scenario() -> None:
        clock = _Clock()
        trace = ExchangeTraceRecorder(
            idle_timeout=2.0,
            clock=clock,
            precision=Duration(nanoseconds=1),
        )
        library_request = httpcore.Request(
            method=b"GET",
            url=b"https://service.example/",
            extensions={"timeout": {"read": 1.0}},
        )
        events: tuple[tuple[str, dict[str, Any]], ...] = (
            ("connection.connect_tcp.started", {}),
            ("connection.connect_tcp.complete", {}),
            ("connection.start_tls.started", {}),
            ("connection.start_tls.complete", {}),
            ("http11.send_request_headers.started", {}),
            ("http11.send_request_headers.complete", {}),
            ("http11.send_request_body.started", {}),
            ("http11.send_request_body.complete", {}),
            ("http11.receive_response_headers.started", {"request": library_request}),
            ("http11.receive_response_headers.complete", {}),
        )
        for name, info in events:
            await trace.trace(name, info)
        trace.mark_first_body_byte()
        trace.mark_first_body_byte()
        await trace.trace("http11.receive_response_body.started", {})
        await trace.trace("http11.receive_response_body.complete", {})

        assert library_request.extensions["timeout"]["read"] == 2.0
        assert trace.started("connect_tcp")
        assert trace.completed("connect_tcp")
        assert not trace.started("missing")
        timings = trace.timings(
            route=DirectResolvedRoute(destination=destination()),
            response=_entity_response(),
            failure=None,
            finished_at=150,
        )
        for metric in (
            timings.connection_acquisition,
            timings.connect,
            timings.tls,
            timings.request_write,
            timings.response_headers,
            timings.server_wait,
            timings.first_body_byte,
            timings.body_read,
            timings.elapsed,
        ):
            assert type(metric) is Measured

    asyncio.run(scenario())


def test_timing_handles_reuse_clear_text_empty_absent_and_unavailable_bodies() -> None:
    reused = _entity_response()
    reused = msgspec.structs.replace(
        reused,
        endpoint=msgspec.structs.replace(reused.endpoint, connection_reused=True),
    )
    reused_timings = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock()).timings(
        route=DirectResolvedRoute(destination=destination()),
        response=reused,
        failure=None,
        finished_at=50,
    )
    for metric in (reused_timings.dns, reused_timings.connect, reused_timings.tls):
        assert type(metric) is NotPerformed
        assert metric.reason is NotPerformedReason.CONNECTION_REUSED

    clear_route = DirectResolvedRoute(destination=destination(scheme=HttpScheme.HTTP))
    absent = response(
        status=204,
        body=NoResponseBody(reason=NoBodyReason.STATUS_204),
        scheme=HttpScheme.HTTP,
    )
    absent_timings = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock()).timings(
        route=clear_route,
        response=absent,
        failure=None,
        finished_at=50,
    )
    assert type(absent_timings.tls) is NotPerformed
    assert absent_timings.tls.reason is NotPerformedReason.CLEAR_TEXT_CONNECTION
    assert type(absent_timings.proxy_tunnel) is NotPerformed
    assert absent_timings.proxy_tunnel.reason is NotPerformedReason.NO_PROXY
    for metric in (absent_timings.first_body_byte, absent_timings.body_read):
        assert type(metric) is NotPerformed
        assert metric.reason is NotPerformedReason.NO_RESPONSE_BODY

    empty_timings = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock()).timings(
        route=clear_route,
        response=response(scheme=HttpScheme.HTTP),
        failure=None,
        finished_at=50,
    )
    assert type(empty_timings.first_body_byte) is NotPerformed
    assert empty_timings.first_body_byte.reason is NotPerformedReason.EMPTY_RESPONSE_BODY

    unavailable_body = response(
        body=ResponseEntityBody(
            capture=BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
            handle=None,
            encoded_byte_count=None,
        )
    )
    unavailable_timings = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock()).timings(
        route=DirectResolvedRoute(destination=destination()),
        response=unavailable_body,
        failure=_failure(FailurePhase.BODY_READ),
        finished_at=50,
    )
    assert type(unavailable_timings.first_body_byte) is TimingUnavailable


@pytest.mark.parametrize(
    "phase",
    [
        FailurePhase.CONNECT,
        FailurePhase.PROXY,
        FailurePhase.TLS,
        FailurePhase.REQUEST_WRITE,
        FailurePhase.RESPONSE_HEADERS,
        FailurePhase.RESPONSE_HEADERS_TIMEOUT,
        FailurePhase.BODY_READ,
    ],
)
def test_failure_phase_cutoffs_replace_all_later_claims(phase: FailurePhase) -> None:
    trace = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock())
    timings = trace.timings(
        route=DirectResolvedRoute(destination=destination()),
        response=None,
        failure=_failure(phase),
        finished_at=50,
    )
    assert type(timings.elapsed) is Measured
    if phase is FailurePhase.CONNECT:
        assert type(timings.request_write) is TimingUnavailable
        assert timings.request_write.reason is TimingUnavailableReason.INTERRUPTED_BEFORE_END


def test_proxy_route_timing_distinguishes_resolution_owners_and_reuse() -> None:
    proxy_destination = destination(host="proxy.example", scheme=HttpScheme.HTTP)
    delegated = ProxyResolvedRoute(
        proxy_destination=proxy_destination,
        target=ProxyResolvedTarget(
            scheme=HttpScheme.HTTPS,
            authority=HttpAuthority(host=DnsHost(value="target.example")),
        ),
    )
    client = ProxyResolvedRoute(
        proxy_destination=proxy_destination,
        target=ClientResolvedTarget(destination=destination()),
    )
    for route in (delegated, client):
        timings = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock()).timings(
            route=route,
            response=None,
            failure=_failure(FailurePhase.PROXY),
            finished_at=50,
        )
        assert type(timings.proxy_tunnel) is TimingUnavailable
        assert type(timings.tls) is TimingUnavailable

    reused = _entity_response()
    reused = msgspec.structs.replace(
        reused,
        endpoint=msgspec.structs.replace(reused.endpoint, connection_reused=True),
    )
    timings = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock()).timings(
        route=delegated,
        response=reused,
        failure=None,
        finished_at=50,
    )
    assert type(timings.proxy_tunnel) is NotPerformed
    assert timings.proxy_tunnel.reason is NotPerformedReason.CONNECTION_REUSED


def test_trace_validates_boundary_types_and_missing_timeout_extension() -> None:
    for idle in (-1.0, 1):
        with pytest.raises(ValueError, match="idle timeout"):
            ExchangeTraceRecorder(idle_timeout=cast("float", idle))
    with pytest.raises(TypeError, match="clock"):
        ExchangeTraceRecorder(
            idle_timeout=None,
            clock=cast("Callable[[], int]", object()),
        )
    with pytest.raises(TypeError, match="precision"):
        ExchangeTraceRecorder(
            idle_timeout=None,
            precision=cast("Duration", object()),
        )

    async def scenario() -> None:
        trace = ExchangeTraceRecorder(idle_timeout=None, clock=_Clock())
        await trace.trace(
            "http11.receive_response_headers.started",
            {"request": httpcore.Request(method=b"GET", url=b"http://example.test/")},
        )
        await trace.trace("http11.receive_response_headers.complete", {})
        with pytest.raises(TypeError, match="ResolvedRoute"):
            trace.timings(
                route=cast("DirectResolvedRoute", object()),
                response=None,
                failure=None,
            )
        route = DirectResolvedRoute(destination=destination())
        with pytest.raises(TypeError, match="ResponseObservation"):
            trace.timings(
                route=route,
                response=cast("ResponseObservation", object()),
                failure=None,
            )
        with pytest.raises(TypeError, match="TransportFailure"):
            trace.timings(
                route=route,
                response=None,
                failure=cast("TransportFailure", object()),
            )

    asyncio.run(scenario())
