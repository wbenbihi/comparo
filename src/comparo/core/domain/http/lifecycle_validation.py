"""Timing-envelope validation across exchange, attempt, execution, and stream levels."""

from __future__ import annotations

from typing import Protocol
from typing import cast

from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.primitives import Duration


class AttemptChildTimings(Protocol):
    """A timed exchange or pre-send operation contributing to attempt elapsed time."""

    @property
    def elapsed(self) -> object:
        """Return the operation's elapsed timing metric."""
        ...


def validate_attempt_timing_envelope(
    attempt: HttpAttemptTimings,
    exchanges: tuple[AttemptChildTimings, ...],
) -> None:
    """Require measured attempt elapsed to cover all measured exchange elapsed spans."""
    if type(attempt.elapsed) is not Measured or any(
        type(exchange.elapsed) is not Measured for exchange in exchanges
    ):
        return
    measured_exchanges = tuple(cast(Measured, exchange.elapsed) for exchange in exchanges)
    child_total = sum(metric.duration.nanoseconds for metric in measured_exchanges)
    if attempt.elapsed.duration.nanoseconds < child_total:
        raise ValueError("measured attempt elapsed cannot be shorter than its exchanges")


def validate_execution_timing_envelope(
    execution: EnvironmentExecutionTimings,
    attempts: tuple[HttpAttemptTimings, ...],
    retry_delays: tuple[Duration, ...],
) -> None:
    """Require measured execution elapsed to cover attempt spans and retry delays."""
    if type(execution.elapsed) is not Measured or any(
        type(attempt.elapsed) is not Measured for attempt in attempts
    ):
        return
    measured_attempts = tuple(cast(Measured, attempt.elapsed) for attempt in attempts)
    child_total = sum(metric.duration.nanoseconds for metric in measured_attempts)
    child_total += sum(delay.nanoseconds for delay in retry_delays)
    if execution.elapsed.duration.nanoseconds < child_total:
        raise ValueError("measured execution elapsed cannot be shorter than attempts and backoff")
