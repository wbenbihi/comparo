"""Secret-free monotonic instrumentation for low-level HTTP exchanges."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.http.timings import TimingSource
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.primitives import Duration

_SOURCE = TimingSource(value="comparo.http/monotonic@1")
_UNAVAILABLE = TimingUnavailable(reason=TimingUnavailableReason.MILESTONE_MISSING)
_INTERRUPTED = TimingUnavailable(reason=TimingUnavailableReason.INTERRUPTED_BEFORE_END)


class ExchangeTraceRecorder:
    """Record named monotonic milestones while discarding trace payloads immediately."""

    __slots__ = (
        "_clock",
        "_events",
        "_first_body_byte",
        "_idle_timeout",
        "_precision",
        "_response_headers_complete",
        "_timeouts",
        "started_at",
    )

    def __init__(
        self,
        *,
        idle_timeout: float | None,
        clock: Callable[[], int] = time.monotonic_ns,
        precision: Duration | None = None,
    ) -> None:
        """Bind an injectable monotonic clock and post-header idle timeout."""
        if idle_timeout is not None and (type(idle_timeout) is not float or idle_timeout < 0):
            raise ValueError("idle timeout must be a non-negative float or None")
        if not callable(clock):
            raise TypeError("exchange trace clock must be callable")
        self._clock = clock
        resolution = int(time.get_clock_info("monotonic").resolution * 1_000_000_000)
        self._precision = precision or Duration(nanoseconds=max(1, resolution))
        if type(self._precision) is not Duration:
            raise TypeError("exchange trace precision must be Duration")
        self._idle_timeout = idle_timeout
        self.started_at = self._clock()
        self._events: dict[str, list[int]] = {}
        self._first_body_byte: int | None = None
        self._response_headers_complete: int | None = None
        self._timeouts: dict[str, Any] | None = None

    async def trace(self, name: str, info: dict[str, Any]) -> None:
        """Retain only a library event name and timestamp, never request or response values."""
        now = self._clock()
        self._events.setdefault(name, []).append(now)
        if name.endswith("receive_response_headers.started"):
            request = info.get("request")
            extensions = getattr(request, "extensions", None)
            timeout = extensions.get("timeout") if isinstance(extensions, dict) else None
            self._timeouts = timeout if isinstance(timeout, dict) else None
        if name.endswith("receive_response_headers.complete"):
            self._response_headers_complete = now
            if self._timeouts is not None:
                self._timeouts["read"] = self._idle_timeout
                self._timeouts = None
        del info

    def mark_first_body_byte(self) -> None:
        """Record the first non-empty encoded body chunk exactly once."""
        if self._first_body_byte is None:
            self._first_body_byte = self._clock()

    def started(self, operation: str) -> bool:
        """Return whether the named low-level operation emitted a start milestone."""
        return self._event(operation, "started", first=True) is not None

    def completed(self, operation: str) -> bool:
        """Return whether the named low-level operation emitted a completion milestone."""
        return self._event(operation, "complete", first=False) is not None

    def timings(
        self,
        *,
        route: ResolvedRoute,
        response: ResponseObservation | None,
        failure: TransportFailure | None,
        finished_at: int | None = None,
    ) -> HttpExchangeTimings:
        """Project internal milestones into the closed portable timing vector."""
        if type(route) not in {DirectResolvedRoute, ProxyResolvedRoute}:
            raise TypeError("exchange timings require ResolvedRoute")
        if response is not None and type(response) is not ResponseObservation:
            raise TypeError("exchange timing response must be ResponseObservation")
        if failure is not None and type(failure) is not TransportFailure:
            raise TypeError("exchange timing failure must be TransportFailure")
        end = self._clock() if finished_at is None else finished_at
        reused = response is not None and response.endpoint.connection_reused
        has_tls = _route_uses_tls(route)
        no_body = response is not None and type(response.body) is NoResponseBody
        body_bytes = _response_body_bytes(response)
        metrics: dict[str, TimingMetric] = {
            "connection_acquisition": self._from_start_to_send(end),
            "dns": (
                NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED)
                if reused
                else _UNAVAILABLE
            ),
            "connect": self._connection_metric(reused),
            "proxy_tunnel": self._proxy_metric(route, reused),
            "tls": self._tls_metric(has_tls, reused),
            "request_write": self._operation_span("send_request_headers", "send_request_body"),
            "response_headers": self._span("receive_response_headers"),
            "server_wait": self._server_wait(),
            "first_body_byte": self._first_byte_metric(no_body, body_bytes),
            "body_read": self._body_read_metric(no_body, body_bytes, end),
            "elapsed": self._measured(self.started_at, end),
        }
        if failure is not None:
            _apply_failure_cutoff(metrics, failure.phase)
        return HttpExchangeTimings(**metrics)

    def _measured(self, start: int, end: int) -> Measured:
        return Measured(
            duration=Duration(nanoseconds=max(0, end - start)),
            precision=self._precision,
            source=_SOURCE,
        )

    def _span(self, operation: str) -> TimingMetric:
        starts = [
            value
            for name, values in self._events.items()
            if name.endswith(f"{operation}.started")
            for value in values
        ]
        completes = [
            value
            for name, values in self._events.items()
            if name.endswith(f"{operation}.complete")
            for value in values
        ]
        return self._measured(min(starts), max(completes)) if starts and completes else _UNAVAILABLE

    def _operation_span(self, first: str, last: str) -> TimingMetric:
        start = self._event(first, "started", first=True)
        end = self._event(last, "complete", first=False)
        return self._measured(start, end) if start is not None and end is not None else _UNAVAILABLE

    def _event(self, operation: str, state: str, *, first: bool) -> int | None:
        values = [
            value
            for name, entries in self._events.items()
            if name.endswith(f"{operation}.{state}")
            for value in entries
        ]
        if not values:
            return None
        return min(values) if first else max(values)

    def _from_start_to_send(self, end: int) -> Measured:
        send = self._event("send_request_headers", "started", first=True)
        return self._measured(self.started_at, send if send is not None else end)

    def _connection_metric(self, reused: bool) -> TimingMetric:
        if reused:
            return NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED)
        return self._span("connect_tcp")

    def _proxy_metric(self, route: ResolvedRoute, reused: bool) -> TimingMetric:
        if type(route) is DirectResolvedRoute:
            return NotPerformed(reason=NotPerformedReason.NO_PROXY)
        if reused:
            return NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED)
        return _UNAVAILABLE

    def _tls_metric(self, has_tls: bool, reused: bool) -> TimingMetric:
        if not has_tls:
            return NotPerformed(reason=NotPerformedReason.CLEAR_TEXT_CONNECTION)
        if reused:
            return NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED)
        return self._span("start_tls")

    def _server_wait(self) -> TimingMetric:
        start = self._event("send_request_body", "complete", first=False)
        end = self._event("receive_response_headers", "complete", first=False)
        return self._measured(start, end) if start is not None and end is not None else _UNAVAILABLE

    def _first_byte_metric(self, no_body: bool, body_bytes: int | None) -> TimingMetric:
        if no_body:
            return NotPerformed(reason=NotPerformedReason.NO_RESPONSE_BODY)
        if body_bytes == 0:
            return NotPerformed(reason=NotPerformedReason.EMPTY_RESPONSE_BODY)
        if self._response_headers_complete is None or self._first_body_byte is None:
            return _UNAVAILABLE
        return self._measured(self._response_headers_complete, self._first_body_byte)

    def _body_read_metric(
        self,
        no_body: bool,
        body_bytes: int | None,
        end: int,
    ) -> TimingMetric:
        if no_body:
            return NotPerformed(reason=NotPerformedReason.NO_RESPONSE_BODY)
        if body_bytes == 0:
            return NotPerformed(reason=NotPerformedReason.EMPTY_RESPONSE_BODY)
        if self._first_body_byte is None:
            return _UNAVAILABLE
        return self._measured(self._first_body_byte, end)


def _route_uses_tls(route: ResolvedRoute) -> bool:
    if type(route) is DirectResolvedRoute:
        return route.destination.scheme is HttpScheme.HTTPS
    target_scheme = (
        route.target.destination.scheme
        if type(route.target) is ClientResolvedTarget
        else route.target.scheme
    )
    return route.proxy_destination.scheme is HttpScheme.HTTPS or target_scheme is HttpScheme.HTTPS


def _response_body_bytes(response: ResponseObservation | None) -> int | None:
    if response is None or type(response.body) is NoResponseBody:
        return None
    return response.body.handle.byte_count.value if response.body.handle is not None else None


def _apply_failure_cutoff(metrics: dict[str, TimingMetric], phase: FailurePhase) -> None:
    later = {
        FailurePhase.CONNECT: (
            "proxy_tunnel",
            "tls",
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
        FailurePhase.PROXY: (
            "tls",
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
        FailurePhase.TLS: (
            "request_write",
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
        FailurePhase.REQUEST_WRITE: (
            "response_headers",
            "server_wait",
            "first_body_byte",
            "body_read",
        ),
        FailurePhase.RESPONSE_HEADERS: ("first_body_byte", "body_read"),
        FailurePhase.RESPONSE_HEADERS_TIMEOUT: ("first_body_byte", "body_read"),
    }.get(phase, ())
    for field in later:
        metrics[field] = _INTERRUPTED
