"""Cross-object truth validation for HTTP exchange timing observations."""

from __future__ import annotations

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TlsMetadata
from comparo.core.domain.http.transport import TlsUnavailable
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import ProxyResolvedRoute

_LATER_TIMING_FIELDS = (
    (
        FailurePhase.POLICY,
        (
            "connection_acquisition",
            "dns",
            "connect",
            "proxy_tunnel",
            "tls",
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
    ),
    (
        FailurePhase.DNS,
        (
            "connect",
            "proxy_tunnel",
            "tls",
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
    ),
    (
        FailurePhase.CONNECT,
        (
            "proxy_tunnel",
            "tls",
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
    ),
    (
        FailurePhase.PROXY,
        (
            "tls",
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
    ),
    (
        FailurePhase.TLS,
        (
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
    ),
    (
        FailurePhase.REQUEST_WRITE,
        (
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
    ),
    (FailurePhase.RESPONSE_HEADERS, ("first_body_byte", "body_read")),
    (FailurePhase.RESPONSE_HEADERS_TIMEOUT, ("first_body_byte", "body_read")),
)


def _timing_fields(timings: HttpExchangeTimings) -> dict[str, TimingMetric]:
    return {
        "connection_acquisition": timings.connection_acquisition,
        "dns": timings.dns,
        "connect": timings.connect,
        "proxy_tunnel": timings.proxy_tunnel,
        "tls": timings.tls,
        "request_write": timings.request_write,
        "response_headers": timings.response_headers,
        "server_wait": timings.server_wait,
        "first_body_byte": timings.first_body_byte,
        "body_read": timings.body_read,
    }


def _validate_exchange_elapsed_envelope(timings: HttpExchangeTimings) -> None:
    """Require every measured phase span to fit within measured exchange elapsed time."""
    if type(timings.elapsed) is not Measured:
        return
    elapsed = timings.elapsed.duration.nanoseconds
    for field, metric in _timing_fields(timings).items():
        if type(metric) is Measured and metric.duration.nanoseconds > elapsed:
            raise ValueError(f"{field.replace('_', ' ')} timing exceeds exchange elapsed")


def _require_not_performed(
    metric: TimingMetric,
    reason: NotPerformedReason,
    phase: str,
) -> None:
    if type(metric) is not NotPerformed or metric.reason is not reason:
        raise ValueError(f"{phase} timing must be not performed because of {reason.value}")


def _reject_not_performed_reason(
    metric: TimingMetric,
    reason: NotPerformedReason,
    phase: str,
) -> None:
    if type(metric) is NotPerformed and metric.reason is reason:
        raise ValueError(f"{phase} timing cannot claim {reason.value}")


def _validate_phase_cutoff(
    phase: FailurePhase | None,
    timings: HttpExchangeTimings,
) -> None:
    later_fields = dict(_LATER_TIMING_FIELDS)
    if phase is None or phase not in later_fields:
        return
    metrics = _timing_fields(timings)
    for field in later_fields[phase]:
        if type(metrics[field]) is not TimingUnavailable:
            raise ValueError(
                f"{field.replace('_', ' ')} timing after {phase.value} failure must be unavailable"
            )


def _response_uses_tls(response: ResponseObservation) -> bool:
    layers = (response.tls.connect, response.tls.origin)
    return any(type(layer) in {TlsMetadata, TlsUnavailable} for layer in layers)


def _route_uses_tls(request: AuthorizedExchangeRequest) -> bool:
    route = request.route
    if type(route) is DirectResolvedRoute:
        return route.destination.scheme is HttpScheme.HTTPS
    return (
        route.proxy_destination.scheme is HttpScheme.HTTPS
        or request.request.url.scheme is HttpScheme.HTTPS
    )


def _validate_proxy_timing(
    route: DirectResolvedRoute | ProxyResolvedRoute,
    response: ResponseObservation | None,
    timings: HttpExchangeTimings,
) -> None:
    if type(route) is DirectResolvedRoute and response is not None:
        _require_not_performed(
            timings.proxy_tunnel,
            NotPerformedReason.NO_PROXY,
            "proxy tunnel",
        )
    if type(route) is ProxyResolvedRoute:
        _reject_not_performed_reason(
            timings.proxy_tunnel,
            NotPerformedReason.NO_PROXY,
            "proxy tunnel",
        )


def _validate_unknown_connection_tls(
    request: AuthorizedExchangeRequest,
    timings: HttpExchangeTimings,
) -> None:
    if _route_uses_tls(request):
        _reject_not_performed_reason(
            timings.tls,
            NotPerformedReason.CLEAR_TEXT_CONNECTION,
            "TLS",
        )
    elif type(timings.tls) is Measured:
        raise ValueError("TLS timing cannot be measured for an entirely clear-text route")


def _validate_connection_reuse(
    route: DirectResolvedRoute | ProxyResolvedRoute,
    response: ResponseObservation,
    timings: HttpExchangeTimings,
) -> None:
    if not response.endpoint.connection_reused:
        for phase, metric in (
            ("DNS", timings.dns),
            ("connect", timings.connect),
            ("proxy tunnel", timings.proxy_tunnel),
            ("TLS", timings.tls),
        ):
            _reject_not_performed_reason(metric, NotPerformedReason.CONNECTION_REUSED, phase)
        return
    _require_not_performed(timings.dns, NotPerformedReason.CONNECTION_REUSED, "DNS")
    _require_not_performed(timings.connect, NotPerformedReason.CONNECTION_REUSED, "connect")
    if type(route) is ProxyResolvedRoute:
        _require_not_performed(
            timings.proxy_tunnel,
            NotPerformedReason.CONNECTION_REUSED,
            "proxy tunnel",
        )


def _validate_observed_tls(
    response: ResponseObservation,
    timings: HttpExchangeTimings,
) -> None:
    if _response_uses_tls(response):
        if response.endpoint.connection_reused:
            _require_not_performed(timings.tls, NotPerformedReason.CONNECTION_REUSED, "TLS")
        elif type(timings.tls) is NotPerformed:
            raise ValueError("TLS timing cannot be not performed when a TLS layer was established")
    else:
        _require_not_performed(
            timings.tls,
            NotPerformedReason.CLEAR_TEXT_CONNECTION,
            "TLS",
        )


def _validate_route_and_connection(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation | None,
    timings: HttpExchangeTimings,
) -> None:
    route = request.route
    _validate_proxy_timing(route, response, timings)
    if response is None:
        _validate_unknown_connection_tls(request, timings)
        return
    _validate_connection_reuse(route, response, timings)
    _validate_observed_tls(response, timings)


def _validate_body_timings(
    response: ResponseObservation | None,
    timings: HttpExchangeTimings,
) -> None:
    if response is None:
        return
    if type(response.body) is NoResponseBody:
        for phase, metric in (
            ("first body byte", timings.first_body_byte),
            ("body read", timings.body_read),
        ):
            if type(metric) is Measured:
                raise ValueError(f"{phase} timing cannot be measured without a response body")
            if type(metric) is NotPerformed and (
                metric.reason is not NotPerformedReason.NO_RESPONSE_BODY
            ):
                raise ValueError(f"{phase} timing must use the no-response-body reason")
    else:
        byte_count = (
            response.body.handle.byte_count.value
            if type(response.body) is ResponseEntityBody and response.body.handle is not None
            else None
        )
        for phase, metric in (
            ("first body byte", timings.first_body_byte),
            ("body read", timings.body_read),
        ):
            _validate_entity_timing_metric(metric, phase=phase, byte_count=byte_count)


def _validate_entity_timing_metric(
    metric: TimingMetric,
    *,
    phase: str,
    byte_count: int | None,
) -> None:
    """Validate one entity timing against semantic presence and captured byte extent."""
    if type(metric) is NotPerformed and metric.reason is NotPerformedReason.NO_RESPONSE_BODY:
        raise ValueError(f"{phase} timing cannot omit a present response entity")
    if (
        byte_count is not None
        and byte_count > 0
        and type(metric) is NotPerformed
        and metric.reason is NotPerformedReason.EMPTY_RESPONSE_BODY
    ):
        raise ValueError(f"{phase} timing cannot claim an empty non-empty entity")
    if byte_count == 0 and type(metric) is Measured:
        raise ValueError(f"{phase} timing cannot be measured for a captured zero-byte entity")
    if (
        byte_count == 0
        and type(metric) is NotPerformed
        and metric.reason is not NotPerformedReason.EMPTY_RESPONSE_BODY
    ):
        raise ValueError(f"{phase} timing must use empty-response-body")


def validate_exchange_timing_truth(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation | None,
    failure: TransportFailure | None,
    timings: HttpExchangeTimings,
) -> None:
    """Reject timing claims contradicted by the observed exchange lifecycle."""
    _validate_exchange_elapsed_envelope(timings)
    _validate_phase_cutoff(None if failure is None else failure.phase, timings)
    _validate_route_and_connection(request, response, timings)
    _validate_body_timings(response, timings)


def validate_pre_send_timing_truth(
    phase: FailurePhase,
    timings: HttpExchangeTimings,
) -> None:
    """Validate lifecycle cutoff for work that ended before a route or send grant existed."""
    if phase not in {FailurePhase.DNS, FailurePhase.POLICY}:
        raise ValueError("pre-send timing validation requires DNS or policy phase")
    _validate_exchange_elapsed_envelope(timings)
    _validate_phase_cutoff(phase, timings)
