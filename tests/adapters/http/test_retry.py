"""Production retry-delay adapter tests."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import cast

import msgspec
import pytest

from comparo.adapters.http.retry import AsyncioRetrySleeper
from comparo.adapters.http.retry import ProductionRetryWaiter
from comparo.adapters.http.retry import RetryJitterSource
from comparo.adapters.http.retry import RetrySleeper
from comparo.adapters.http.retry import SystemRetryJitterSource
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.resolution.control import RunCancellation
from tests.core.domain.http.factories import policies


class _Sleeper:
    def __init__(self) -> None:
        self.delays: list[Duration] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def sleep(self, delay: Duration) -> None:
        self.delays.append(delay)
        self.started.set()
        if self.block:
            await self.release.wait()


class _Jitter:
    def __init__(self, value: Duration) -> None:
        self.value = value
        self.maximums: list[Duration] = []

    def sample(self, maximum: Duration) -> Duration:
        self.maximums.append(maximum)
        return self.value


def _retry_policy(
    backoff: NoBackoff | FixedBackoff | ExponentialBackoff,
    *,
    jitter: Ratio | None = None,
) -> RetryPolicy:
    return msgspec.structs.replace(
        policies(attempts=2).retries,
        backoff=backoff,
        jitter=jitter or Ratio(numerator=0, denominator=PositiveCount(value=1)),
    )


@pytest.mark.parametrize(
    ("backoff", "retry_index", "expected"),
    [
        (NoBackoff(), 1, 0),
        (FixedBackoff(delay=Duration(nanoseconds=12)), 2, 12),
        (
            ExponentialBackoff(
                initial=Duration(nanoseconds=10),
                multiplier=Ratio.from_decimal(Decimal("2")),
                maximum=Duration(nanoseconds=25),
            ),
            1,
            10,
        ),
        (
            ExponentialBackoff(
                initial=Duration(nanoseconds=10),
                multiplier=Ratio.from_decimal(Decimal("2")),
                maximum=Duration(nanoseconds=25),
            ),
            3,
            25,
        ),
    ],
)
def test_waiter_applies_observable_backoff_and_bounded_jitter(
    backoff: NoBackoff | FixedBackoff | ExponentialBackoff,
    retry_index: int,
    expected: int,
) -> None:
    async def scenario() -> None:
        sleeper = _Sleeper()
        jitter = _Jitter(Duration(nanoseconds=3 if expected else 0))
        ratio = Ratio(numerator=1, denominator=PositiveCount(value=2))
        result = await ProductionRetryWaiter(sleeper=sleeper, jitter=jitter).wait(
            _retry_policy(backoff, jitter=ratio),
            Count(value=retry_index),
            RetryTrigger.CONNECT_FAILURE,
            RunCancellation(),
        )
        assert result.backoff == Duration(nanoseconds=expected)
        assert result.jitter == Duration(nanoseconds=3 if expected else 0)
        assert sleeper.delays == [Duration(nanoseconds=expected + (3 if expected else 0))]
        assert jitter.maximums == [Duration(nanoseconds=expected // 2)]

    asyncio.run(scenario())


def test_waiter_races_run_cancellation_and_cleans_sleep_task() -> None:
    async def scenario() -> None:
        sleeper = _Sleeper()
        sleeper.block = True
        cancellation = RunCancellation()
        waiter = ProductionRetryWaiter(
            sleeper=sleeper,
            jitter=_Jitter(Duration(nanoseconds=0)),
        )
        waiting = asyncio.create_task(
            waiter.wait(
                _retry_policy(FixedBackoff(delay=Duration(nanoseconds=10))),
                Count(value=1),
                RetryTrigger.CONNECT_FAILURE,
                cancellation,
            )
        )
        await sleeper.started.wait()
        cancellation.cancel(CancellationSource.USER)
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

        sleeper = _Sleeper()
        sleeper.block = True
        externally_cancelled = asyncio.create_task(
            ProductionRetryWaiter(
                sleeper=sleeper,
                jitter=_Jitter(Duration(nanoseconds=0)),
            ).wait(
                _retry_policy(FixedBackoff(delay=Duration(nanoseconds=10))),
                Count(value=1),
                RetryTrigger.CONNECT_FAILURE,
                RunCancellation(),
            )
        )
        await sleeper.started.wait()
        externally_cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await externally_cancelled
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

        cancellation = RunCancellation()
        cancellation.cancel(CancellationSource.DEADLINE)
        with pytest.raises(asyncio.CancelledError):
            await waiter.wait(
                _retry_policy(NoBackoff()),
                Count(value=1),
                RetryTrigger.CONNECT_FAILURE,
                cancellation,
            )

    asyncio.run(scenario())


def test_waiter_validates_collaborators_arguments_and_jitter_contract() -> None:
    with pytest.raises(TypeError, match="RetrySleeper"):
        ProductionRetryWaiter(sleeper=cast("RetrySleeper", object()))
    with pytest.raises(TypeError, match="RetryJitterSource"):
        ProductionRetryWaiter(jitter=cast("RetryJitterSource", object()))

    async def scenario() -> None:
        waiter = ProductionRetryWaiter(
            sleeper=_Sleeper(),
            jitter=_Jitter(Duration(nanoseconds=100)),
        )
        policy = _retry_policy(FixedBackoff(delay=Duration(nanoseconds=10)))
        with pytest.raises(TypeError, match="RetryPolicy"):
            await waiter.wait(
                cast("RetryPolicy", object()),
                Count(value=1),
                RetryTrigger.CONNECT_FAILURE,
                RunCancellation(),
            )
        with pytest.raises(TypeError, match="Count"):
            await waiter.wait(
                policy,
                cast("Count", object()),
                RetryTrigger.CONNECT_FAILURE,
                RunCancellation(),
            )
        for index, trigger in (
            (Count(value=0), RetryTrigger.CONNECT_FAILURE),
            (Count(value=1), cast("RetryTrigger", object())),
        ):
            with pytest.raises(ValueError, match="positive index"):
                await waiter.wait(policy, index, trigger, RunCancellation())
        with pytest.raises(TypeError, match="RunCancellation"):
            await waiter.wait(
                policy,
                Count(value=1),
                RetryTrigger.CONNECT_FAILURE,
                cast("RunCancellation", object()),
            )
        with pytest.raises(ValueError, match="jitter source"):
            await waiter.wait(
                policy,
                Count(value=1),
                RetryTrigger.CONNECT_FAILURE,
                RunCancellation(),
            )

    asyncio.run(scenario())


def test_default_effect_adapters_validate_types_and_cover_zero_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        await AsyncioRetrySleeper().sleep(Duration(nanoseconds=0))
        with pytest.raises(TypeError, match="Duration"):
            await AsyncioRetrySleeper().sleep(cast("Duration", object()))

    asyncio.run(scenario())
    monkeypatch.setattr("secrets.randbelow", lambda maximum: maximum - 1)
    assert SystemRetryJitterSource().sample(Duration(nanoseconds=4)) == Duration(nanoseconds=4)
    with pytest.raises(TypeError, match="Duration"):
        SystemRetryJitterSource().sample(cast("Duration", object()))
