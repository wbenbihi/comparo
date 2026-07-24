"""Portable monotonic timing facts for HTTP and stream observations."""

from __future__ import annotations

import re
from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import Duration

_SOURCE_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*/[a-z0-9]+(?:[.-][a-z0-9]+)*@[1-9][0-9]*")


@final
class TimingSource(msgspec.Struct, frozen=True, kw_only=True):
    """A versioned identity for the monotonic instrumentation that measured a span."""

    value: str

    def __post_init__(self) -> None:
        """Require a stable namespaced source identity."""
        if type(self.value) is not str or _SOURCE_PATTERN.fullmatch(self.value) is None:
            raise ValueError("timing source must have the form namespace/name@positive-version")


class NotPerformedReason(DomainStrEnum):
    """Reasons for work that did not occur during an exchange."""

    CONNECTION_REUSED = "connection-reused"
    DNS_CACHE_HIT = "dns-cache-hit"
    NO_PROXY = "no-proxy"
    CLEAR_TEXT_CONNECTION = "clear-text-connection"
    NO_RESPONSE_BODY = "no-response-body"
    EMPTY_RESPONSE_BODY = "empty-response-body"
    EMPTY_STREAM = "empty-stream"


class TimingUnavailableReason(DomainStrEnum):
    """Reasons why an applicable interval could not be measured."""

    INSTRUMENTATION_DISABLED = "instrumentation-disabled"
    INSTRUMENTATION_UNSUPPORTED = "instrumentation-unsupported"
    MILESTONE_MISSING = "milestone-missing"
    INTERRUPTED_BEFORE_END = "interrupted-before-end"
    CLOCK_FAILURE = "clock-failure"


@final
class Measured(msgspec.Struct, frozen=True, kw_only=True):
    """An exact monotonic duration together with its uncertainty and source."""

    duration: Duration
    precision: Duration
    source: TimingSource

    def __post_init__(self) -> None:
        """Reject unvalidated timing components at direct construction boundaries."""
        if type(self.duration) is not Duration:
            raise TypeError("measured duration must be Duration")
        if type(self.precision) is not Duration:
            raise TypeError("timing precision must be Duration")
        if type(self.source) is not TimingSource:
            raise TypeError("timing source must be TimingSource")


@final
class NotPerformed(msgspec.Struct, frozen=True, kw_only=True):
    """A timing interval whose underlying work intentionally did not occur."""

    reason: NotPerformedReason

    def __post_init__(self) -> None:
        """Require one closed, portable reason."""
        if type(self.reason) is not NotPerformedReason:
            raise TypeError("not-performed timing reason must be NotPerformedReason")


@final
class TimingUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """An applicable interval for which instrumentation supplied no reliable value."""

    reason: TimingUnavailableReason

    def __post_init__(self) -> None:
        """Require one closed instrumentation reason."""
        if type(self.reason) is not TimingUnavailableReason:
            raise TypeError("unavailable timing reason must be TimingUnavailableReason")


type TimingMetric = Measured | NotPerformed | TimingUnavailable


def is_timing_metric(value: object) -> TypeGuard[TimingMetric]:
    """Narrow an arbitrary value to one exhaustive timing outcome."""
    return type(value) in {Measured, NotPerformed, TimingUnavailable}


def _validate_phase_metric(
    metric: TimingMetric,
    *,
    phase: str,
    allowed_not_performed: frozenset[NotPerformedReason] = frozenset(),
) -> None:
    if not is_timing_metric(metric):
        raise TypeError(f"{phase} timing must be a TimingMetric")
    if type(metric) is NotPerformed and metric.reason not in allowed_not_performed:
        raise ValueError(f"{metric.reason.value} is not valid for {phase} timing")


@final
class HttpExchangeTimings(msgspec.Struct, frozen=True, kw_only=True):
    """Portable exchange spans, which deliberately are not assumed to be additive."""

    connection_acquisition: TimingMetric
    dns: TimingMetric
    connect: TimingMetric
    proxy_tunnel: TimingMetric
    tls: TimingMetric
    request_write: TimingMetric
    response_headers: TimingMetric
    server_wait: TimingMetric
    first_body_byte: TimingMetric
    body_read: TimingMetric
    elapsed: TimingMetric

    def __post_init__(self) -> None:
        """Require every phase to state measured, not performed, or unavailable."""
        reused = frozenset({NotPerformedReason.CONNECTION_REUSED})
        _validate_phase_metric(self.connection_acquisition, phase="connection acquisition")
        _validate_phase_metric(
            self.dns,
            phase="DNS",
            allowed_not_performed=reused | {NotPerformedReason.DNS_CACHE_HIT},
        )
        _validate_phase_metric(self.connect, phase="connect", allowed_not_performed=reused)
        _validate_phase_metric(
            self.proxy_tunnel,
            phase="proxy tunnel",
            allowed_not_performed=reused | {NotPerformedReason.NO_PROXY},
        )
        _validate_phase_metric(
            self.tls,
            phase="TLS",
            allowed_not_performed=reused | {NotPerformedReason.CLEAR_TEXT_CONNECTION},
        )
        _validate_phase_metric(self.request_write, phase="request write")
        _validate_phase_metric(self.response_headers, phase="response headers")
        _validate_phase_metric(self.server_wait, phase="server wait")
        body_absent = frozenset(
            {NotPerformedReason.NO_RESPONSE_BODY, NotPerformedReason.EMPTY_RESPONSE_BODY}
        )
        _validate_phase_metric(
            self.first_body_byte,
            phase="first body byte",
            allowed_not_performed=body_absent,
        )
        _validate_phase_metric(self.body_read, phase="body read", allowed_not_performed=body_absent)
        _validate_phase_metric(self.elapsed, phase="exchange elapsed")


@final
class HttpAttemptTimings(msgspec.Struct, frozen=True, kw_only=True):
    """The monotonic elapsed interval for one redirect-containing HTTP attempt."""

    elapsed: TimingMetric

    def __post_init__(self) -> None:
        """Require an applicable measured or unavailable elapsed interval."""
        _validate_phase_metric(self.elapsed, phase="attempt elapsed")


@final
class EnvironmentExecutionTimings(msgspec.Struct, frozen=True, kw_only=True):
    """Queue and end-to-end execution intervals kept separate from exchange timing."""

    queue: TimingMetric
    elapsed: TimingMetric

    def __post_init__(self) -> None:
        """Require explicit facts for queue and complete execution time."""
        if not is_timing_metric(self.queue) or not is_timing_metric(self.elapsed):
            raise TypeError("execution queue and elapsed timings must be TimingMetric values")
        if type(self.queue) is NotPerformed or type(self.elapsed) is NotPerformed:
            raise ValueError("execution queue and elapsed intervals are always applicable")


@final
class StreamTimings(msgspec.Struct, frozen=True, kw_only=True):
    """Portable timing facts for one bounded stream observation window."""

    time_to_first_record: TimingMetric
    observation_window_elapsed: TimingMetric

    def __post_init__(self) -> None:
        """Require explicit stream timing outcomes rather than implicit zero values."""
        if not is_timing_metric(self.time_to_first_record) or not is_timing_metric(
            self.observation_window_elapsed
        ):
            raise TypeError("stream timing fields must be TimingMetric values")
        if type(self.time_to_first_record) is NotPerformed and (
            self.time_to_first_record.reason is not NotPerformedReason.EMPTY_STREAM
        ):
            raise ValueError("only an empty stream may omit time to first record")
        if type(self.observation_window_elapsed) is NotPerformed:
            raise ValueError("stream observation-window elapsed is always applicable")
