"""Interactive, bounded progress sessions over the production execution service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import final

import msgspec

from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.production_execution import execute_production
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.resolution import RunCancellation
from comparo.core.runtime.models import RuntimeEvent
from comparo.core.runtime.models import RuntimeEventKind


class ExecutionSessionPhase(StrEnum):
    """Closed phases observable by an interactive frontend."""

    PREPARING = "preparing"
    RUNNING = "running"
    FINALIZING = "finalizing"
    CANCELLING = "cancelling"
    FINISHED = "finished"
    CANCELLED = "cancelled"


@final
class ExecutionProgressSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    """One coalescible, value-free view of execution progress."""

    revision: int
    run_id: str
    phase: ExecutionSessionPhase
    total_executions: int
    total_comparisons: int
    queued_executions: int = 0
    started_executions: int = 0
    finished_executions: int = 0
    finished_comparisons: int = 0
    runtime_sequence: int | None = None
    last_execution_id: str | None = None
    last_comparison_id: str | None = None

    def __post_init__(self) -> None:
        """Reject malformed or contradictory progress counters."""
        _validate_progress_identity(self)
        _validate_progress_counters(self)

    @property
    def active_executions(self) -> int:
        """Return the number of started executions without terminal events."""
        return self.started_executions - self.finished_executions


def _validate_progress_identity(snapshot: ExecutionProgressSnapshot) -> None:
    if type(snapshot.run_id) is not str or not snapshot.run_id:
        raise ValueError("execution progress run ID must be non-empty text")
    if type(snapshot.phase) is not ExecutionSessionPhase:
        raise TypeError("execution progress phase must be typed")
    if snapshot.runtime_sequence is not None and (
        type(snapshot.runtime_sequence) is not int or snapshot.runtime_sequence < 0
    ):
        raise ValueError("runtime event sequence must be non-negative or absent")
    for value in (snapshot.last_execution_id, snapshot.last_comparison_id):
        if value is not None and (type(value) is not str or not value):
            raise ValueError("progress occurrence IDs must be non-empty text or absent")


def _validate_progress_counters(snapshot: ExecutionProgressSnapshot) -> None:
    counters = (
        snapshot.revision,
        snapshot.total_executions,
        snapshot.total_comparisons,
        snapshot.queued_executions,
        snapshot.started_executions,
        snapshot.finished_executions,
        snapshot.finished_comparisons,
    )
    if any(type(value) is not int or value < 0 for value in counters):
        raise ValueError("execution progress counters must be non-negative integers")
    if snapshot.queued_executions > snapshot.total_executions:
        raise ValueError("queued execution count exceeds the plan")
    if snapshot.started_executions > snapshot.queued_executions:
        raise ValueError("started execution count exceeds queued work")
    if snapshot.finished_executions > snapshot.started_executions:
        raise ValueError("finished execution count exceeds started work")
    if snapshot.finished_comparisons > snapshot.total_comparisons:
        raise ValueError("finished comparison count exceeds the plan")


class _ProgressMailbox:
    """Keep only the latest immutable snapshot for a potentially slow observer."""

    __slots__ = ("_changed", "_closed", "_snapshot")

    def __init__(self, snapshot: ExecutionProgressSnapshot) -> None:
        self._snapshot = snapshot
        self._changed = asyncio.Event()
        self._closed = False

    @property
    def snapshot(self) -> ExecutionProgressSnapshot:
        return self._snapshot

    async def emit(self, event: RuntimeEvent) -> None:
        """Fold one safe runtime event without retaining an unbounded event history."""
        if type(event) is not RuntimeEvent:
            raise TypeError("execution progress mailbox requires RuntimeEvent")
        current = self._snapshot
        phase = current.phase
        queued = current.queued_executions
        started = current.started_executions
        finished = current.finished_executions
        comparisons = current.finished_comparisons
        last_execution = current.last_execution_id
        last_comparison = current.last_comparison_id

        if event.kind is RuntimeEventKind.RUN_STARTED:
            phase = ExecutionSessionPhase.RUNNING
        elif event.kind is RuntimeEventKind.EXECUTION_QUEUED:
            queued += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
        elif event.kind is RuntimeEventKind.EXECUTION_STARTED:
            started += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
        elif event.kind is RuntimeEventKind.EXECUTION_FINISHED:
            finished += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
        elif event.kind is RuntimeEventKind.COMPARISON_FINISHED:
            comparisons += 1
            last_comparison = str(event.comparison_id.value) if event.comparison_id else None
        elif event.kind is RuntimeEventKind.RUN_FINISHED:
            phase = ExecutionSessionPhase.FINALIZING
        else:  # pragma: no cover - RuntimeEventKind is exhaustively handled above
            raise RuntimeError("unsupported runtime event kind")

        self._publish(
            msgspec.structs.replace(
                current,
                revision=current.revision + 1,
                phase=phase,
                queued_executions=queued,
                started_executions=started,
                finished_executions=finished,
                finished_comparisons=comparisons,
                runtime_sequence=event.sequence.value,
                last_execution_id=last_execution,
                last_comparison_id=last_comparison,
            )
        )

    def transition(self, phase: ExecutionSessionPhase) -> None:
        """Publish one application-owned lifecycle transition."""
        if type(phase) is not ExecutionSessionPhase:
            raise TypeError("execution session transition phase must be typed")
        current = self._snapshot
        if current.phase is phase:
            return
        self._publish(
            msgspec.structs.replace(
                current,
                revision=current.revision + 1,
                phase=phase,
            )
        )

    def close(self, phase: ExecutionSessionPhase) -> None:
        """Publish the terminal phase and wake a waiting observer."""
        self.transition(phase)
        self._closed = True
        self._changed.set()

    async def after(self, revision: int) -> ExecutionProgressSnapshot | None:
        """Wait for a newer coalesced snapshot, or return None after closure."""
        if type(revision) is not int or revision < -1:
            raise ValueError("progress revision cursor must be an integer of at least -1")
        while True:
            snapshot = self._snapshot
            if snapshot.revision > revision:  # pragma: no branch - same-loop race guard
                return snapshot
            if self._closed:  # pragma: no branch - same-loop race guard
                return None
            self._changed.clear()
            await self._changed.wait()

    def _publish(self, snapshot: ExecutionProgressSnapshot) -> None:
        self._snapshot = snapshot
        self._changed.set()


@final
class ExecutionSession:
    """One running production execution with coalesced progress and explicit cancellation."""

    __slots__ = ("_cancellation", "_mailbox", "_task")

    def __init__(self, request: ProductionExecutionRequest) -> None:
        """Start exactly one execution on the caller's active event loop."""
        if type(request) is not ProductionExecutionRequest:
            raise TypeError("execution session requires ProductionExecutionRequest")
        if request.events is not None or request.cancellation is not None:
            raise ValueError("execution session owns progress and cancellation ports")
        prepared = request.project.prepared
        cardinality = prepared.plan.cardinality
        self._cancellation = RunCancellation()
        self._mailbox = _ProgressMailbox(
            ExecutionProgressSnapshot(
                revision=0,
                run_id=str(prepared.plan.run_id.value),
                phase=ExecutionSessionPhase.PREPARING,
                total_executions=cardinality.environment_executions.value,
                total_comparisons=cardinality.comparisons.value,
            )
        )
        owned = msgspec.structs.replace(
            request,
            events=self._mailbox,
            cancellation=self._cancellation,
        )
        self._task = asyncio.create_task(self._execute(owned))

    @property
    def run_id(self) -> str:
        """Return the safe run identifier before or after completion."""
        return self._mailbox.snapshot.run_id

    @property
    def done(self) -> bool:
        """Return whether the underlying production operation is terminal."""
        return self._task.done()

    async def updates(self) -> AsyncIterator[ExecutionProgressSnapshot]:
        """Yield coalesced progress without letting a slow UI grow runtime memory."""
        revision = -1
        while (snapshot := await self._mailbox.after(revision)) is not None:
            revision = snapshot.revision
            yield snapshot

    def cancel(self) -> bool:
        """Request first-wins user cancellation and report whether it was newly accepted."""
        if self._task.done() or self._cancellation.cancelled:
            return False
        self._mailbox.transition(ExecutionSessionPhase.CANCELLING)
        self._cancellation.cancel(CancellationSource.USER)
        return True

    async def result(self) -> ProductionExecutionResult:
        """Wait for the sanitized terminal production result without cancelling it accidentally."""
        return await asyncio.shield(self._task)

    async def close(self) -> None:
        """Cancel unfinished work and wait for production cleanup."""
        self.cancel()
        try:
            await asyncio.shield(self._task)
        except asyncio.CancelledError:
            self._task.cancel()
            raise

    async def _execute(
        self,
        request: ProductionExecutionRequest,
    ) -> ProductionExecutionResult:
        try:
            result = await execute_production(request)
        except asyncio.CancelledError:
            self._mailbox.close(ExecutionSessionPhase.CANCELLED)
            raise
        self._mailbox.close(
            ExecutionSessionPhase.CANCELLED
            if self._cancellation.cancelled
            else ExecutionSessionPhase.FINISHED
        )
        return result


def start_execution_session(request: ProductionExecutionRequest) -> ExecutionSession:
    """Start one interactive execution session on the current event loop."""
    return ExecutionSession(request)


__all__ = (
    "ExecutionProgressSnapshot",
    "ExecutionSession",
    "ExecutionSessionPhase",
    "start_execution_session",
)
