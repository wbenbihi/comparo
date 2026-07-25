"""Scheduler, engine, and default-evaluation defensive runtime paths."""

from __future__ import annotations

import asyncio
from typing import cast
from typing import override

import msgspec
import pytest

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.lifecycle import PartialRunReason
from comparo.core.domain.lifecycle import RunInternalError
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.primitives import Count
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import BlockedExecution
from comparo.core.runtime import ComparisonWork
from comparo.core.runtime import ExecutionRecord
from comparo.core.runtime import ExecutionWork
from comparo.core.runtime import NoEvaluationPreparation
from comparo.core.runtime import NullEventSink
from comparo.core.runtime import ProgrammableFakeTransport
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime import UnavailableExecution
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime import run_prepared
from comparo.core.runtime.engine import _comparison_work
from comparo.core.runtime.engine import _roles
from comparo.core.runtime.models import ExecutionOutcome
from comparo.core.runtime.ports import AssertionCall
from comparo.core.runtime.ports import DiffCall
from comparo.core.runtime.scheduling import ScheduledExecutions
from comparo.core.runtime.scheduling import _await_cancelled
from comparo.core.runtime.scheduling import _budget
from comparo.core.runtime.scheduling import _execute_cancellable
from comparo.core.runtime.scheduling import _execution_failed
from comparo.core.runtime.scheduling import _SchedulingLedger
from comparo.core.runtime.scheduling import _source
from comparo.core.runtime.scheduling import schedule_executions
from tests.core.runtime.conftest import FixedClock
from tests.core.runtime.test_scheduler_control import BlockingDriver
from tests.core.runtime.test_scheduler_control import FailingDriver
from tests.core.runtime.test_vertical_runtime import _driver


def _records(prepared: PreparedRun) -> tuple[ExecutionRecord, ...]:
    cancellation = RunCancellation()
    driver, _transport = _driver(prepared, cancellation)
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
    return result.executions


def test_event_sinks_scheduler_inputs_and_work_iterator_validate_types(
    prepared_comparison_run: PreparedRun,
) -> None:
    with pytest.raises(TypeError, match="RuntimeEvent"):
        asyncio.run(NullEventSink().emit(object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="RuntimeEvent"):
        asyncio.run(RecordingEventSink().emit(object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PreparedRun"):
        tuple(iter_execution_work(object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PreparedRun"):
        asyncio.run(
            schedule_executions(
                object(),  # type: ignore[arg-type]
                FailingDriver(),
                RunCancellation(),
                NullEventSink(),
            )
        )
    with pytest.raises(TypeError, match="RunCancellation"):
        asyncio.run(
            schedule_executions(
                prepared_comparison_run,
                FailingDriver(),
                object(),  # type: ignore[arg-type]
                NullEventSink(),
            )
        )


def test_scheduler_ledger_and_terminal_product_reject_inconsistent_state(
    prepared_comparison_run: PreparedRun,
) -> None:
    ledger = _SchedulingLedger(prepared_comparison_run)
    ledger.queued()
    with pytest.raises(RuntimeError, match="while work remains"):
        ledger.snapshots()
    ledger.started()
    ledger.finished()
    ledgers = ledger.snapshots()

    valid = ScheduledExecutions(
        records=(),
        ledgers=ledgers,
        next_event_sequence=Count(value=0),
    )
    with pytest.raises(TypeError, match="scheduled records"):
        msgspec.structs.replace(valid, records=[])
    with pytest.raises(TypeError, match="scheduled records"):
        msgspec.structs.replace(valid, records=(object(),))
    with pytest.raises(TypeError, match="scheduled ledgers"):
        msgspec.structs.replace(valid, ledgers=[])
    with pytest.raises(TypeError, match="scheduled ledgers"):
        msgspec.structs.replace(valid, ledgers=(object(), object()))
    with pytest.raises(TypeError, match="event sequence"):
        msgspec.structs.replace(valid, next_event_sequence=0)
    with pytest.raises(ValueError, match="exact scheduling budget"):
        _budget(prepared_comparison_run, BudgetResource.OPEN_CONNECTIONS)


class InvalidOutcomeDriver:
    """Return an object outside the closed execution outcome union."""

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        del work
        return cast(ExecutionOutcome, object())

    async def close(self) -> None:
        """Release no resources."""


class RaisingDriver:
    """Raise adapter text that must not escape the runtime boundary."""

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        del work
        raise RuntimeError("payload-like adapter exception")

    async def close(self) -> None:
        """Release no resources."""


@pytest.mark.parametrize("driver", [InvalidOutcomeDriver(), RaisingDriver()])
def test_driver_defects_are_contained_per_execution(
    prepared_comparison_run: PreparedRun,
    driver: InvalidOutcomeDriver | RaisingDriver,
) -> None:
    result = asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=RunCancellation(),
            clock=FixedClock(),
            events=NullEventSink(),
        )
    )
    assert type(result.completion) is RunPartial
    assert all(type(item.outcome) is UnavailableExecution for item in result.executions)
    assert all(
        cast(UnavailableExecution, item.outcome).code.value == "runtime.execution.internal"
        for item in result.executions
    )


def test_caller_task_cancellation_unwinds_active_driver_and_cleanup_helpers() -> None:
    async def scenario() -> BlockingDriver:
        driver = BlockingDriver()
        cancellation = RunCancellation()
        work = cast(ExecutionWork, object())
        task = asyncio.create_task(_execute_cancellable(driver, work, cancellation))
        await driver.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return driver

    driver = asyncio.run(scenario())
    assert driver.active == 0

    async def cleanup_error() -> None:
        async def fail() -> object:
            raise RuntimeError("discarded cleanup failure")

        await _await_cancelled(asyncio.create_task(fail()))

    asyncio.run(cleanup_error())
    cancellation = RunCancellation()
    assert _source(cancellation) is CancellationSource.PARENT_SCOPE


class CleanupFailingDriver(FailingDriver):
    """Raise from cleanup after all work has reached a terminal result."""

    @override
    async def close(self) -> None:
        self.closed = True
        raise RuntimeError("payload-like cleanup exception")


def test_cleanup_failure_becomes_a_safe_internal_run_outcome(
    prepared_comparison_run: PreparedRun,
) -> None:
    result = asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=CleanupFailingDriver(),
            evaluation=NoEvaluationPreparation(),
            cancellation=RunCancellation(),
            clock=FixedClock(),
            events=NullEventSink(),
        )
    )
    assert type(result.completion) is RunInternalError
    assert result.completion.diagnostic.code.value == "runtime.cleanup.failed"


class LifecyclePreparation:
    """Prove offline evaluation still owns every live driver-backed dependency."""

    def __init__(self, transport: ProgrammableFakeTransport) -> None:
        self.transport = transport
        self.assertion_calls = 0
        self.diff_calls = 0

    @property
    def gate_profile(self) -> CompiledGateProfile | None:
        return None

    async def assertions(
        self,
        prepared: PreparedRun,
        record: ExecutionRecord,
    ) -> tuple[AssertionCall, ...]:
        del prepared, record
        assert not self.transport.closed
        self.assertion_calls += 1
        return ()

    async def diffs(
        self,
        prepared: PreparedRun,
        work: ComparisonWork,
        baseline: ExecutionRecord,
        candidate: ExecutionRecord,
    ) -> tuple[DiffCall, ...]:
        del prepared, work, baseline, candidate
        assert not self.transport.closed
        self.diff_calls += 1
        return ()


def test_driver_remains_open_through_evaluation_then_closes(
    prepared_comparison_run: PreparedRun,
) -> None:
    cancellation = RunCancellation()
    driver, transport = _driver(prepared_comparison_run, cancellation)
    evaluation = LifecyclePreparation(transport)

    result = asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=evaluation,
            cancellation=cancellation,
            clock=FixedClock(),
            events=NullEventSink(),
        )
    )

    assert evaluation.assertion_calls == len(result.executions)
    assert evaluation.diff_calls == len(result.comparisons)
    assert transport.closed


class InvalidPreparation:
    """Return mutable call collections to exercise evaluation containment."""

    def __init__(self, *, assertion: bool) -> None:
        self.assertion = assertion

    @property
    def gate_profile(self) -> CompiledGateProfile | None:
        return None

    async def assertions(
        self,
        prepared: PreparedRun,
        record: ExecutionRecord,
    ) -> tuple[AssertionCall, ...]:
        del prepared, record
        return cast(tuple[AssertionCall, ...], [] if self.assertion else ())

    async def diffs(
        self,
        prepared: PreparedRun,
        work: ComparisonWork,
        baseline: ExecutionRecord,
        candidate: ExecutionRecord,
    ) -> tuple[DiffCall, ...]:
        del prepared, work, baseline, candidate
        return cast(tuple[DiffCall, ...], () if self.assertion else [])


@pytest.mark.parametrize("assertion", [True, False])
def test_invalid_evaluation_preparation_marks_only_evaluation_incomplete(
    prepared_comparison_run: PreparedRun,
    assertion: bool,
) -> None:
    cancellation = RunCancellation()
    driver, _transport = _driver(prepared_comparison_run, cancellation)
    result = asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=InvalidPreparation(assertion=assertion),
            cancellation=cancellation,
            clock=FixedClock(),
            events=NullEventSink(),
        )
    )
    assert result.completion == RunPartial(
        reasons=(PartialRunReason.EVALUATION_INCOMPLETE,),
        completed_at=FixedClock().now(),
    )


def test_default_evaluation_and_engine_reject_invalid_calls(
    prepared_comparison_run: PreparedRun,
) -> None:
    records = _records(prepared_comparison_run)
    comparison = next(
        item for item in asyncio.run(_comparison_work_for_default(prepared_comparison_run, records))
    )
    evaluation = NoEvaluationPreparation()
    with pytest.raises(TypeError, match="PreparedRun and ExecutionRecord"):
        asyncio.run(evaluation.assertions(object(), records[0]))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PreparedRun and ExecutionRecord"):
        asyncio.run(evaluation.assertions(prepared_comparison_run, object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PreparedRun and ComparisonWork"):
        asyncio.run(
            evaluation.diffs(
                object(),  # type: ignore[arg-type]
                comparison,
                records[0],
                records[1],
            )
        )
    with pytest.raises(TypeError, match="PreparedRun and ComparisonWork"):
        asyncio.run(
            evaluation.diffs(
                prepared_comparison_run,
                object(),  # type: ignore[arg-type]
                records[0],
                records[1],
            )
        )
    with pytest.raises(TypeError, match="ExecutionRecord sides"):
        asyncio.run(
            evaluation.diffs(
                prepared_comparison_run,
                comparison,
                object(),  # type: ignore[arg-type]
                records[1],
            )
        )
    with pytest.raises(TypeError, match="ExecutionRecord sides"):
        asyncio.run(
            evaluation.diffs(
                prepared_comparison_run,
                comparison,
                records[0],
                object(),  # type: ignore[arg-type]
            )
        )

    with pytest.raises(TypeError, match="PreparedRun"):
        asyncio.run(
            run_prepared(
                object(),  # type: ignore[arg-type]
                driver=FailingDriver(),
                evaluation=evaluation,
                cancellation=RunCancellation(),
                clock=FixedClock(),
                events=NullEventSink(),
            )
        )
    with pytest.raises(TypeError, match="RunCancellation"):
        asyncio.run(
            run_prepared(
                prepared_comparison_run,
                driver=FailingDriver(),
                evaluation=evaluation,
                cancellation=object(),  # type: ignore[arg-type]
                clock=FixedClock(),
                events=NullEventSink(),
            )
        )


async def _comparison_work_for_default(
    prepared: PreparedRun,
    records: tuple[ExecutionRecord, ...],
) -> tuple[ComparisonWork, ...]:
    from comparo.core.runtime.engine import _comparison_work

    return _comparison_work(prepared, records)


def test_fail_fast_classification_and_other_role_are_explicit(
    prepared_comparison_run: PreparedRun,
) -> None:
    records = _records(prepared_comparison_run)
    assert _comparison_work(prepared_comparison_run, records[:1]) == ()
    observed = records[0].outcome
    assert _execution_failed(observed) is False
    assert _execution_failed(
        BlockedExecution(
            stage=DiagnosticStage.AUTHORIZATION,
            code=DiagnosticCode(value="runtime.test.blocked"),
        )
    )

    plan_slice = prepared_comparison_run.plan.slices[0]
    msgspec.structs.force_setattr(plan_slice, "comparisons", ())
    assert _roles(prepared_comparison_run, records[0]) == (EnvironmentRole.OTHER,)
