"""Cancellable wall-clock retry delays with explicit observable jitter."""

from __future__ import annotations

import asyncio
import secrets
from typing import Protocol

from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.resolution.control import RunCancellation


class RetrySleeper(Protocol):
    """Suspend without coupling retry policy to one async runtime implementation."""

    async def sleep(self, delay: Duration) -> None:
        """Wait for exactly the supplied non-negative duration unless cancelled."""
        ...


class RetryJitterSource(Protocol):
    """Sample an additive delay from one explicit inclusive upper bound."""

    def sample(self, maximum: Duration) -> Duration:
        """Return a duration between zero and ``maximum`` inclusive."""
        ...


class AsyncioRetrySleeper:
    """Production monotonic sleep adapter for retry delays."""

    async def sleep(self, delay: Duration) -> None:
        """Delegate one typed duration to asyncio's monotonic scheduler."""
        if type(delay) is not Duration:
            raise TypeError("retry sleep requires Duration")
        await asyncio.sleep(delay.nanoseconds / 1_000_000_000)


class SystemRetryJitterSource:
    """Use operating-system randomness without retaining or logging generator state."""

    def sample(self, maximum: Duration) -> Duration:
        """Sample a uniform additive nanosecond delay within the policy bound."""
        if type(maximum) is not Duration:
            raise TypeError("retry jitter maximum must be Duration")
        return Duration(nanoseconds=secrets.randbelow(maximum.nanoseconds + 1))


class ProductionRetryWaiter:
    """Apply policy backoff and jitter while racing the run cancellation signal."""

    __slots__ = ("_jitter", "_sleeper")

    def __init__(
        self,
        *,
        sleeper: RetrySleeper | None = None,
        jitter: RetryJitterSource | None = None,
    ) -> None:
        """Compose independently replaceable timing and randomness effects."""
        actual_sleeper = sleeper or AsyncioRetrySleeper()
        actual_jitter = jitter or SystemRetryJitterSource()
        if not hasattr(actual_sleeper, "sleep"):
            raise TypeError("production retry waiter requires RetrySleeper")
        if not hasattr(actual_jitter, "sample"):
            raise TypeError("production retry waiter requires RetryJitterSource")
        self._sleeper = actual_sleeper
        self._jitter = actual_jitter

    async def wait(
        self,
        policy: RetryPolicy,
        retry_index: Count,
        trigger: RetryTrigger,
        cancellation: RunCancellation,
    ) -> RetryMetadata:
        """Wait for the exact sampled schedule or propagate cooperative cancellation."""
        if type(policy) is not RetryPolicy or type(retry_index) is not Count:
            raise TypeError("retry waiter requires a RetryPolicy and Count index")
        if retry_index.value < 1 or type(trigger) is not RetryTrigger:
            raise ValueError("retry waiter requires a positive index and RetryTrigger")
        if type(cancellation) is not RunCancellation:
            raise TypeError("retry waiter requires RunCancellation")
        if cancellation.cancelled:
            raise asyncio.CancelledError
        backoff = Duration(nanoseconds=_backoff_nanoseconds(policy, retry_index.value))
        maximum_jitter = Duration(
            nanoseconds=(
                backoff.nanoseconds * policy.jitter.numerator // policy.jitter.denominator.value
            )
        )
        jitter = self._jitter.sample(maximum_jitter)
        if type(jitter) is not Duration or jitter.nanoseconds > maximum_jitter.nanoseconds:
            raise ValueError("retry jitter source exceeded its typed policy bound")
        delay = Duration(nanoseconds=backoff.nanoseconds + jitter.nanoseconds)
        await _sleep_or_cancel(self._sleeper, delay, cancellation)
        return RetryMetadata(trigger=trigger, backoff=backoff, jitter=jitter)


async def _sleep_or_cancel(
    sleeper: RetrySleeper,
    delay: Duration,
    cancellation: RunCancellation,
) -> None:
    """Race one delay against cancellation and clean up both tasks on every path."""
    sleeping = asyncio.create_task(sleeper.sleep(delay))
    cancelled = asyncio.create_task(cancellation.wait())
    try:
        done, _pending = await asyncio.wait(
            (sleeping, cancelled),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancelled in done:
            sleeping.cancel()
            await asyncio.gather(sleeping, return_exceptions=True)
            raise asyncio.CancelledError
        await sleeping
    finally:
        if not sleeping.done():
            sleeping.cancel()
        if not cancelled.done():
            cancelled.cancel()
        await asyncio.gather(sleeping, cancelled, return_exceptions=True)


def _backoff_nanoseconds(policy: RetryPolicy, retry_index: int) -> int:
    backoff = policy.backoff
    if type(backoff) is NoBackoff:
        return 0
    if type(backoff) is FixedBackoff:
        return backoff.delay.nanoseconds
    if type(backoff) is not ExponentialBackoff:  # pragma: no cover - domain union invariant
        raise TypeError("retry policy contains unsupported backoff")
    exponent = retry_index - 1
    scaled: int = (
        backoff.initial.nanoseconds
        * backoff.multiplier.numerator**exponent
        // backoff.multiplier.denominator.value**exponent
    )
    return min(backoff.maximum.nanoseconds, scaled)
