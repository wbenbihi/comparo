"""Cancellation, failure independence, fail-fast, and cleanup scheduler tests."""

from __future__ import annotations

import asyncio
from typing import cast

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.lifecycle import RunCancelled
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.planner import plan_run
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import BlockedExecution
from comparo.core.runtime import NoEvaluationPreparation
from comparo.core.runtime import NullEventSink
from comparo.core.runtime import UnavailableExecution
from comparo.core.runtime import run_prepared
from comparo.core.runtime import scheduling as scheduling_module
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import ExecutionOutcome
from comparo.core.runtime.models import ExecutionWork
from comparo.core.runtime.models import RunResult
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import invocation
from tests.core.runtime.conftest import FixedClock


class FailingDriver:
    """Fail one execution and make every other call a distinct typed failure."""

    def __init__(self) -> None:
        """Create empty call and lifecycle evidence."""
        self.calls: list[int] = []
        self.closed = False

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        """Return a value-free unavailable outcome after one cooperative yield."""
        self.calls.append(work.ordinal.value)
        await asyncio.sleep(0)
        return UnavailableExecution(
            stage=DiagnosticStage.RESOLUTION,
            code=DiagnosticCode(value="runtime.test.unavailable"),
        )

    async def close(self) -> None:
        """Record deterministic resource release."""
        self.closed = True


class BlockingDriver:
    """Block until scheduler cancellation proves active-task cleanup."""

    def __init__(self) -> None:
        """Initialize observable active-state counters."""
        self.started = asyncio.Event()
        self.active = 0
        self.closed = False

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        """Remain cancellable forever after recording transport entry."""
        del work
        self.active += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.active -= 1
        return CancelledExecution(source=CancellationSource.PARENT_SCOPE)  # pragma: no cover

    async def close(self) -> None:
        """Require all active work to unwind before closure."""
        assert self.active == 0
        self.closed = True


def test_blocked_scheduler_event_retains_value_free_failure_coordinates() -> None:
    stage = DiagnosticStage.AUTHORIZATION
    code = DiagnosticCode(value="runtime.test.blocked")
    assert scheduling_module._event_failure(BlockedExecution(stage=stage, code=code)) == (
        stage,
        code,
    )


def _replan(prepared: PreparedRun, *, fail_fast: bool) -> PreparedRun:
    result = plan_run(
        prepared.project,
        invocation(
            fail_fast=SetValue(value=fail_fast),
            budgets=(
                PlanningBudgetValue(
                    resource=BudgetResource.QUEUED_WORK,
                    limit=PositiveCount(value=2),
                ),
                PlanningBudgetValue(
                    resource=BudgetResource.CONCURRENCY,
                    limit=PositiveCount(value=1),
                ),
            ),
        ),
        authority(),
    )
    assert result.prepared is not None
    return result.prepared


def test_unavailable_execution_does_not_abort_independent_work_by_default(
    prepared_comparison_run: PreparedRun,
) -> None:
    prepared = _replan(prepared_comparison_run, fail_fast=False)
    driver = FailingDriver()
    cancellation = RunCancellation()
    result = asyncio.run(
        run_prepared(
            prepared,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=NullEventSink(),
        )
    )

    assert type(result.completion) is RunPartial
    assert len(driver.calls) == prepared.plan.cardinality.environment_executions.value
    assert not cancellation.cancelled
    assert driver.closed


def test_fail_fast_stops_future_work_and_closes_every_active_task(
    prepared_comparison_run: PreparedRun,
) -> None:
    prepared = _replan(prepared_comparison_run, fail_fast=True)
    driver = FailingDriver()
    cancellation = RunCancellation()
    result = asyncio.run(
        run_prepared(
            prepared,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=NullEventSink(),
        )
    )

    assert type(result.completion) is RunCancelled
    assert result.completion.source is CancellationSource.EXECUTION_POLICY
    assert driver.calls == [0]
    assert driver.closed
    assert all(cast(Count, item.usage.in_use).value == 0 for item in result.ledgers)


def test_external_cancellation_interrupts_driver_and_leaves_no_active_work(
    prepared_comparison_run: PreparedRun,
) -> None:
    async def scenario() -> tuple[RunResult, BlockingDriver]:
        prepared = _replan(prepared_comparison_run, fail_fast=False)
        driver = BlockingDriver()
        cancellation = RunCancellation()
        task = asyncio.create_task(
            run_prepared(
                prepared,
                driver=driver,
                evaluation=NoEvaluationPreparation(),
                cancellation=cancellation,
                clock=FixedClock(),
                events=NullEventSink(),
            )
        )
        await driver.started.wait()
        cancellation.cancel(CancellationSource.USER)
        return await task, driver

    result, driver = asyncio.run(scenario())
    assert type(result.completion) is RunCancelled
    assert result.completion.source is CancellationSource.USER
    assert driver.active == 0
    assert driver.closed
