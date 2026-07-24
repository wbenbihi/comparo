"""Deterministic retry delay and jitter schedule validation."""

from __future__ import annotations

from typing import Protocol

from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio


class RetryDelayLike(Protocol):
    """Observed retry delay components required by schedule validation."""

    @property
    def backoff(self) -> Duration:
        """Return deterministic backoff before the attempt."""
        ...

    @property
    def jitter(self) -> Duration:
        """Return sampled non-negative jitter before the attempt."""
        ...


def validate_retry_delay(
    backoff: NoBackoff | FixedBackoff | ExponentialBackoff,
    jitter_ratio: Ratio,
    retry_index: int,
    observed: RetryDelayLike,
) -> None:
    """Bind observable delay components to the exact deterministic retry schedule."""
    if type(backoff) is NoBackoff:
        expected = 0
    elif type(backoff) is FixedBackoff:
        expected = backoff.delay.nanoseconds
    else:
        exponent = retry_index - 1
        numerator = backoff.multiplier.numerator**exponent
        denominator = backoff.multiplier.denominator.value**exponent
        expected = min(
            backoff.maximum.nanoseconds,
            backoff.initial.nanoseconds * numerator // denominator,
        )
    if observed.backoff.nanoseconds != expected:
        raise ValueError("observed retry backoff does not match the effective schedule")
    maximum_jitter = expected * jitter_ratio.numerator // jitter_ratio.denominator.value
    if observed.jitter.nanoseconds > maximum_jitter:
        raise ValueError("observed retry jitter exceeds the effective retry ratio")
