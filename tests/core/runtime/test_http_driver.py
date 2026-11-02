"""Authorized HTTP driver retry, observation, and fake-transport lifecycle tests."""

from __future__ import annotations

import asyncio
from uuid import UUID
from uuid import uuid5

from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Count
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import FakeExchangeAction
from comparo.core.runtime import HttpExecutionDriver
from comparo.core.runtime import HttpTransportResult
from comparo.core.runtime import ImmediateRetryWaiter
from comparo.core.runtime import ObservationOnlySnapshotProvider
from comparo.core.runtime import ObservedExecution
from comparo.core.runtime import ProgrammableFakeTransport
from comparo.core.runtime import iter_execution_work
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import response
from tests.core.runtime.conftest import AllowingExchangePreparer
from tests.core.runtime.conftest import StaticRequestProvider


def _exchange_id(execution_id: UUID, attempt_index: int) -> ExchangeId:
    attempt_id = uuid5(execution_id, f"attempt:{attempt_index}")
    return ExchangeId(value=uuid5(attempt_id, "exchange:0"))


def test_retryable_failure_creates_an_explicit_second_attempt(
    prepared_comparison_run: PreparedRun,
) -> None:
    work = next(iter_execution_work(prepared_comparison_run))
    first = _exchange_id(work.scope.execution_id.value, 0)
    second = _exchange_id(work.scope.execution_id.value, 1)
    transport = ProgrammableFakeTransport(
        {
            first: FakeExchangeAction(
                result=HttpTransportResult(
                    response=None,
                    failure=failure(FailurePhase.CONNECT),
                    timings=exchange_timings(failure_phase=FailurePhase.CONNECT),
                ),
                yields=Count(value=1),
            ),
            second: FakeExchangeAction(
                result=HttpTransportResult(
                    response=response(),
                    failure=None,
                    timings=exchange_timings(),
                )
            ),
        }
    )
    driver = HttpExecutionDriver(
        request_provider=StaticRequestProvider(attempts=2),
        exchange_preparer=AllowingExchangePreparer(),
        transport=transport,
        snapshot_provider=ObservationOnlySnapshotProvider(),
        retry_waiter=ImmediateRetryWaiter(),
        cancellation=RunCancellation(),
        maximum_attempts=prepared_comparison_run.effective.maximum_attempts,
    )

    outcome = asyncio.run(driver.execute(work))
    asyncio.run(driver.close())

    assert type(outcome) is ObservedExecution
    attempts = outcome.observation.attempts
    assert len(attempts) == 2
    assert attempts[0].retry is None
    assert attempts[1].retry is not None
    assert attempts[1].retry.trigger is RetryTrigger.CONNECT_FAILURE
    assert transport.calls == (first, second)
    assert transport.closed


def test_non_retryable_failure_stops_after_one_observed_attempt(
    prepared_comparison_run: PreparedRun,
) -> None:
    work = next(iter_execution_work(prepared_comparison_run))
    first = _exchange_id(work.scope.execution_id.value, 0)
    transport = ProgrammableFakeTransport(
        {
            first: FakeExchangeAction(
                result=HttpTransportResult(
                    response=None,
                    failure=failure(FailurePhase.CONNECT, retryable=False),
                    timings=exchange_timings(failure_phase=FailurePhase.CONNECT),
                )
            )
        }
    )
    driver = HttpExecutionDriver(
        request_provider=StaticRequestProvider(attempts=2),
        exchange_preparer=AllowingExchangePreparer(),
        transport=transport,
        snapshot_provider=ObservationOnlySnapshotProvider(),
        retry_waiter=ImmediateRetryWaiter(),
        cancellation=RunCancellation(),
        maximum_attempts=prepared_comparison_run.effective.maximum_attempts,
    )

    outcome = asyncio.run(driver.execute(work))
    asyncio.run(driver.close())

    assert type(outcome) is ObservedExecution
    assert len(outcome.observation.attempts) == 1
    assert transport.calls == (first,)
