"""Bounded lazy execution scheduling, cancellation, events, and resource accounting."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import cast
from typing import final
from uuid import uuid5

import msgspec

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.planning.iteration import iter_plan_trials
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import ExecutionOutcome
from comparo.core.runtime.models import ExecutionRecord
from comparo.core.runtime.models import ExecutionWork
from comparo.core.runtime.models import ObservedExecution
from comparo.core.runtime.models import RuntimeEvent
from comparo.core.runtime.models import RuntimeEventKind
from comparo.core.runtime.models import UnavailableExecution
from comparo.core.runtime.models import is_execution_outcome
from comparo.core.runtime.ports import ExecutionDriver
from comparo.core.runtime.ports import RuntimeEventSink

_EXECUTION_INTERNAL = DiagnosticCode(value="runtime.execution.internal")


class NullEventSink:
    """Discard structured events for callers that do not request progress reporting."""

    async def emit(self, event: RuntimeEvent) -> None:
        """Validate dispatch through the type boundary and intentionally retain nothing."""
        if type(event) is not RuntimeEvent:
            raise TypeError("event sink requires RuntimeEvent")


class RecordingEventSink:
    """Retain structured events for deterministic tests and in-memory integrations."""

    __slots__ = ("_events",)

    def __init__(self) -> None:
        """Create an empty run-local recording."""
        self._events: list[RuntimeEvent] = []

    @property
    def events(self) -> tuple[RuntimeEvent, ...]:
        """Return an immutable event snapshot."""
        return tuple(self._events)

    async def emit(self, event: RuntimeEvent) -> None:
        """Append one exact event in emitter-assigned sequence order."""
        if type(event) is not RuntimeEvent:
            raise TypeError("event sink requires RuntimeEvent")
        self._events.append(event)


class _EventEmitter:
    """Serialize sequence allocation and sink delivery across worker tasks."""

    __slots__ = ("_lock", "_run_id", "_sequence", "_sink")

    def __init__(self, prepared: PreparedRun, sink: RuntimeEventSink) -> None:
        self._run_id = prepared.plan.run_id
        self._sink = sink
        self._sequence = 0
        self._lock = asyncio.Lock()

    @property
    def next_sequence(self) -> Count:
        """Return the next event sequence after all preceding emissions complete."""
        return Count(value=self._sequence)

    async def emit(
        self,
        kind: RuntimeEventKind,
        *,
        execution_id: EnvironmentExecutionId | None = None,
    ) -> None:
        """Allocate and deliver one event atomically relative to other emitters."""
        async with self._lock:
            event = RuntimeEvent(
                sequence=Count(value=self._sequence),
                run_id=self._run_id,
                kind=kind,
                execution_id=execution_id,
            )
            self._sequence += 1
            await self._sink.emit(event)


class _SchedulingLedger:
    """Mutable run-private accounting that exposes only immutable validated snapshots."""

    __slots__ = (
        "_concurrency",
        "_concurrency_consumed",
        "_concurrency_high",
        "_concurrency_in_use",
        "_queue",
        "_queue_consumed",
        "_queue_high",
        "_queue_in_use",
    )

    def __init__(self, prepared: PreparedRun) -> None:
        self._queue = _budget(prepared, BudgetResource.QUEUED_WORK)
        self._concurrency = _budget(prepared, BudgetResource.CONCURRENCY)
        self._queue_consumed = 0
        self._queue_in_use = 0
        self._queue_high = 0
        self._concurrency_consumed = 0
        self._concurrency_in_use = 0
        self._concurrency_high = 0

    @property
    def queue_limit(self) -> int:
        """Return the effective queue capacity."""
        return cast(Count, self._queue.effective).value

    @property
    def concurrency_limit(self) -> int:
        """Return the effective worker count."""
        return cast(Count, self._concurrency.effective).value

    def queued(self) -> None:
        """Record one work item after bounded-queue admission."""
        self._queue_consumed += 1
        self._queue_in_use += 1
        self._queue_high = max(self._queue_high, self._queue_in_use)
        if self._queue_in_use > self.queue_limit:  # pragma: no cover - queue enforces this
            raise RuntimeError("scheduler queue exceeded its effective budget")

    def started(self) -> None:
        """Transfer one item from queued to active accounting."""
        self._queue_in_use -= 1
        self._concurrency_consumed += 1
        self._concurrency_in_use += 1
        self._concurrency_high = max(self._concurrency_high, self._concurrency_in_use)
        if self._concurrency_in_use > self.concurrency_limit:  # pragma: no cover
            raise RuntimeError("scheduler concurrency exceeded its effective budget")

    def finished(self) -> None:
        """Release one active worker reservation."""
        self._concurrency_in_use -= 1

    def snapshots(self) -> tuple[ResourceBudget, ResourceBudget]:
        """Freeze final scheduling budgets with consumed and high-water evidence."""
        if self._queue_in_use or self._concurrency_in_use:
            raise RuntimeError("cannot snapshot scheduling budgets while work remains")
        queue = msgspec.structs.replace(
            self._queue,
            usage=BudgetUsage(
                consumed=Count(value=self._queue_consumed),
                in_use=Count(value=0),
                high_water=Count(value=self._queue_high),
            ),
        )
        concurrency = msgspec.structs.replace(
            self._concurrency,
            usage=BudgetUsage(
                consumed=Count(value=self._concurrency_consumed),
                in_use=Count(value=0),
                high_water=Count(value=self._concurrency_high),
            ),
        )
        return queue, concurrency


@final
class ScheduledExecutions(msgspec.Struct, frozen=True, kw_only=True):
    """Terminal execution records and final scheduling ledger snapshots."""

    records: tuple[ExecutionRecord, ...]
    ledgers: tuple[ResourceBudget, ResourceBudget]
    next_event_sequence: Count

    def __post_init__(self) -> None:
        """Require immutable typed scheduler products."""
        if type(self.records) is not tuple or any(
            type(item) is not ExecutionRecord for item in self.records
        ):
            raise TypeError("scheduled records must be immutable ExecutionRecord values")
        if type(self.ledgers) is not tuple or any(
            type(item) is not ResourceBudget for item in self.ledgers
        ):
            raise TypeError("scheduled ledgers must be immutable ResourceBudget values")
        if type(self.next_event_sequence) is not Count:
            raise TypeError("scheduled event sequence must be a Count")


async def schedule_executions(
    prepared: PreparedRun,
    driver: ExecutionDriver,
    cancellation: RunCancellation,
    sink: RuntimeEventSink,
) -> ScheduledExecutions:
    """Execute a compact plan through fixed workers and a bounded backpressure queue."""
    if type(prepared) is not PreparedRun:
        raise TypeError("runtime scheduling requires PreparedRun")
    if type(cancellation) is not RunCancellation:
        raise TypeError("runtime scheduling requires RunCancellation")
    ledger = _SchedulingLedger(prepared)
    emitter = _EventEmitter(prepared, sink)
    queue: asyncio.Queue[ExecutionWork | None] = asyncio.Queue(maxsize=ledger.queue_limit)
    queue_slots = asyncio.Semaphore(ledger.queue_limit)
    records: dict[int, ExecutionRecord] = {}

    await emitter.emit(RuntimeEventKind.RUN_STARTED)
    async with asyncio.TaskGroup() as group:
        group.create_task(
            _produce(
                prepared,
                queue,
                queue_slots,
                ledger,
                emitter,
                cancellation,
                ledger.concurrency_limit,
            )
        )
        for _worker_index in range(ledger.concurrency_limit):
            group.create_task(
                _consume(
                    queue,
                    queue_slots,
                    records,
                    ledger,
                    emitter,
                    driver,
                    cancellation,
                    prepared.effective.fail_fast,
                )
            )
    return ScheduledExecutions(
        records=tuple(records[index] for index in sorted(records)),
        ledgers=ledger.snapshots(),
        next_event_sequence=emitter.next_sequence,
    )


async def _produce(
    prepared: PreparedRun,
    queue: asyncio.Queue[ExecutionWork | None],
    queue_slots: asyncio.Semaphore,
    ledger: _SchedulingLedger,
    emitter: _EventEmitter,
    cancellation: RunCancellation,
    worker_count: int,
) -> None:
    try:
        for work in iter_execution_work(prepared):
            if cancellation.cancelled:
                break
            await queue_slots.acquire()
            ledger.queued()
            await queue.put(work)
            await emitter.emit(
                RuntimeEventKind.EXECUTION_QUEUED,
                execution_id=work.scope.execution_id,
            )
    finally:
        for _worker_index in range(worker_count):
            await queue.put(None)


async def _consume(
    queue: asyncio.Queue[ExecutionWork | None],
    queue_slots: asyncio.Semaphore,
    records: dict[int, ExecutionRecord],
    ledger: _SchedulingLedger,
    emitter: _EventEmitter,
    driver: ExecutionDriver,
    cancellation: RunCancellation,
    fail_fast: bool,
) -> None:
    while True:
        work = await queue.get()
        if work is None:
            return
        ledger.started()
        queue_slots.release()
        try:
            await emitter.emit(
                RuntimeEventKind.EXECUTION_STARTED,
                execution_id=work.scope.execution_id,
            )
            outcome = await _execute_cancellable(driver, work, cancellation)
            records[work.ordinal.value] = ExecutionRecord(work=work, outcome=outcome)
            if fail_fast and type(outcome) is not CancelledExecution and _execution_failed(outcome):
                cancellation.cancel(CancellationSource.EXECUTION_POLICY)
            await emitter.emit(
                RuntimeEventKind.EXECUTION_FINISHED,
                execution_id=work.scope.execution_id,
            )
        finally:
            ledger.finished()


async def _execute_cancellable(
    driver: ExecutionDriver,
    work: ExecutionWork,
    cancellation: RunCancellation,
) -> ExecutionOutcome:
    if cancellation.cancelled:
        return CancelledExecution(source=_source(cancellation))
    execution = asyncio.create_task(driver.execute(work))
    cancelled = asyncio.create_task(cancellation.wait())
    try:
        done, _pending = await asyncio.wait(
            (execution, cancelled),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if execution in done:
            cancelled.cancel()
            await _await_cancelled(cancelled)
            outcome = execution.result()
            if not is_execution_outcome(outcome):
                raise TypeError("execution driver returned an unsupported outcome")
            return outcome
        execution.cancel()
        await _await_cancelled(execution)
        return CancelledExecution(source=cancelled.result())
    except asyncio.CancelledError:
        execution.cancel()
        cancelled.cancel()
        await _await_cancelled(execution)
        await _await_cancelled(cancelled)
        raise
    except Exception:  # noqa: BLE001 - driver exceptions are an explicit containment boundary
        execution.cancel()
        cancelled.cancel()
        await _await_cancelled(execution)
        await _await_cancelled(cancelled)
        return UnavailableExecution(stage=DiagnosticStage.TRANSPORT, code=_EXECUTION_INTERNAL)


async def _await_cancelled(task: asyncio.Task[object]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001 - cleanup deliberately discards contained task defects
        return


def iter_execution_work(prepared: PreparedRun) -> Iterator[ExecutionWork]:
    """Lazily enumerate exact execution coordinates in the RunPlan's versioned order."""
    if type(prepared) is not PreparedRun:
        raise TypeError("execution work iteration requires PreparedRun")
    slices = {plan_slice.cells: plan_slice for plan_slice in prepared.plan.slices}
    ordinal = 0
    run = RunScope(run_id=prepared.plan.run_id)
    for trial in iter_plan_trials(prepared.plan):
        plan_slice = slices[trial.cell.recipe]
        trial_scope = TrialScope(
            cell=CellScope(run=run, cell_id=trial.cell.cell_id),
            trial_id=trial.trial_id,
        )
        for environment in plan_slice.environments:
            execution_id = EnvironmentExecutionId(
                value=uuid5(
                    prepared.plan.run_id.value,
                    f"execution:{trial.trial_id.value}:{environment.identity.resource_id.value}",
                )
            )
            yield ExecutionWork(
                ordinal=Count(value=ordinal),
                subject=trial.cell.recipe.subject,
                trial=trial,
                environment=environment,
                scope=ExecutionScope(
                    trial=trial_scope,
                    environment=EnvironmentScope(
                        run=run,
                        environment=environment.identity,
                    ),
                    execution_id=execution_id,
                ),
            )
            ordinal += 1


def _budget(prepared: PreparedRun, resource: BudgetResource) -> ResourceBudget:
    matches = tuple(item for item in prepared.plan.budgets if item.resource is resource)
    if len(matches) != 1:
        raise ValueError("prepared run requires one exact scheduling budget per resource")
    return matches[0]


def _source(cancellation: RunCancellation) -> CancellationSource:
    source = cancellation.source
    return source if source is not None else CancellationSource.PARENT_SCOPE


def _execution_failed(outcome: ExecutionOutcome) -> bool:
    """Return whether fail-fast policy treats one terminal execution as failed."""
    if type(outcome) is not ObservedExecution:
        return True
    terminal = outcome.observation.attempts[-1]
    if terminal.pre_send is not None:
        return True
    return terminal.exchanges[-1].failure is not None
