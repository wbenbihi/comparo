"""Interactive, bounded progress sessions over the production execution service."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from collections.abc import Callable
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
from comparo.core.runtime.models import RuntimeOccurrenceOutcome


class LiveOccurrenceKind(StrEnum):
    """The two runtime occurrence categories represented in a live window."""

    EXECUTION = "execution"
    COMPARISON = "comparison"


class LiveOccurrenceState(StrEnum):
    """Closed coalesced occurrence lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"


@final
class ExecutionSessionLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned memory bounds for interactive progress projections."""

    maximum_occurrences: int
    maximum_recent_events: int

    def __post_init__(self) -> None:
        """Require explicit finite positive window sizes."""
        if any(
            type(value) is not int or value < 1
            for value in (self.maximum_occurrences, self.maximum_recent_events)
        ):
            raise ValueError("execution session window limits must be positive integers")


DEFAULT_EXECUTION_SESSION_LIMITS_V1 = ExecutionSessionLimits(
    maximum_occurrences=10_000,
    maximum_recent_events=128,
)


@final
class LiveOccurrenceProgress(msgspec.Struct, frozen=True, kw_only=True):
    """One safe coalesced execution or comparison occurrence."""

    kind: LiveOccurrenceKind
    occurrence_id: str
    state: LiveOccurrenceState
    outcome: RuntimeOccurrenceOutcome | None = None
    failure_stage: str | None = None
    failure_code: str | None = None
    queued_at_nanoseconds: int | None = None
    started_at_nanoseconds: int | None = None
    finished_at_nanoseconds: int | None = None

    def __post_init__(self) -> None:  # noqa: C901 - closed occurrence-shape validation
        """Require a coherent, safe, monotonic occurrence projection."""
        if type(self.kind) is not LiveOccurrenceKind or type(self.state) is not LiveOccurrenceState:
            raise TypeError("live occurrence kind and state must be typed")
        if type(self.occurrence_id) is not str or not self.occurrence_id:
            raise ValueError("live occurrence ID must be non-empty text")
        if self.outcome is not None and type(self.outcome) is not RuntimeOccurrenceOutcome:
            raise TypeError("live occurrence outcome must be typed or absent")
        if (self.failure_stage is None) is not (self.failure_code is None):
            raise ValueError("live occurrence failure stage and code must be paired")
        for value in (self.failure_stage, self.failure_code):
            if value is not None and (type(value) is not str or not value):
                raise ValueError("live occurrence failure coordinates must be non-empty text")
        timestamps = tuple(
            value
            for value in (
                self.queued_at_nanoseconds,
                self.started_at_nanoseconds,
                self.finished_at_nanoseconds,
            )
            if value is not None
        )
        if any(type(value) is not int or value < 0 for value in timestamps):
            raise ValueError("live occurrence timestamps must be non-negative integers")
        if timestamps != tuple(sorted(timestamps)):
            raise ValueError("live occurrence timestamps must be monotonic")
        if self.state is LiveOccurrenceState.FINISHED and self.finished_at_nanoseconds is None:
            raise ValueError("finished live occurrence requires a finish timestamp")
        if self.outcome is not None and self.state is not LiveOccurrenceState.FINISHED:
            raise ValueError("live occurrence outcome requires a finished state")

    @property
    def elapsed_nanoseconds(self) -> int | None:
        """Return measured application-observer elapsed time when both endpoints exist."""
        if self.started_at_nanoseconds is None or self.finished_at_nanoseconds is None:
            return None
        return self.finished_at_nanoseconds - self.started_at_nanoseconds


@final
class LiveProgressEvent(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded recent safe runtime event."""

    sequence: int
    kind: RuntimeEventKind
    observed_at_nanoseconds: int
    occurrence_id: str | None = None
    outcome: RuntimeOccurrenceOutcome | None = None

    def __post_init__(self) -> None:
        """Require typed value-free event data and a monotonic observer timestamp."""
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("live event sequence must be non-negative")
        if type(self.kind) is not RuntimeEventKind:
            raise TypeError("live event kind must be typed")
        if type(self.observed_at_nanoseconds) is not int or self.observed_at_nanoseconds < 0:
            raise ValueError("live event timestamp must be non-negative")
        if self.occurrence_id is not None and (
            type(self.occurrence_id) is not str or not self.occurrence_id
        ):
            raise ValueError("live event occurrence ID must be non-empty text or absent")
        if self.outcome is not None and type(self.outcome) is not RuntimeOccurrenceOutcome:
            raise TypeError("live event outcome must be typed or absent")


@final
class ExecutionFinalSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Sanitized terminal run facts projected from the final artifact."""

    completion: str
    gate: str
    assertions: int
    diffs: int
    adverse_facts: int

    def __post_init__(self) -> None:
        """Require bounded structural labels and exact non-negative counts."""
        if any(type(value) is not str or not value for value in (self.completion, self.gate)):
            raise ValueError("final execution summary labels must be non-empty text")
        if any(
            type(value) is not int or value < 0
            for value in (self.assertions, self.diffs, self.adverse_facts)
        ):
            raise ValueError("final execution summary counts must be non-negative")


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
    last_event_kind: RuntimeEventKind | None = None
    occurrences: tuple[LiveOccurrenceProgress, ...] = ()
    recent_events: tuple[LiveProgressEvent, ...] = ()
    omitted_occurrences: int = 0
    final_summary: ExecutionFinalSummary | None = None

    def __post_init__(self) -> None:
        """Reject malformed or contradictory progress counters."""
        _validate_progress_identity(self)
        _validate_progress_counters(self)
        if type(self.occurrences) is not tuple or any(
            type(item) is not LiveOccurrenceProgress for item in self.occurrences
        ):
            raise TypeError("live progress occurrences must be immutable and typed")
        if len({(item.kind, item.occurrence_id) for item in self.occurrences}) != len(
            self.occurrences
        ):
            raise ValueError("live progress occurrence identities must be unique")
        if type(self.recent_events) is not tuple or any(
            type(item) is not LiveProgressEvent for item in self.recent_events
        ):
            raise TypeError("recent progress events must be immutable and typed")
        if type(self.omitted_occurrences) is not int or self.omitted_occurrences < 0:
            raise ValueError("omitted live occurrence count must be non-negative")
        if self.final_summary is not None and type(self.final_summary) is not ExecutionFinalSummary:
            raise TypeError("final execution summary must be typed or absent")

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
    if (
        snapshot.last_event_kind is not None
        and type(snapshot.last_event_kind) is not RuntimeEventKind
    ):
        raise TypeError("progress event kind must be RuntimeEventKind or absent")


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

    __slots__ = (
        "_changed",
        "_clock",
        "_closed",
        "_limits",
        "_occurrences",
        "_recent_events",
        "_snapshot",
    )

    def __init__(
        self,
        snapshot: ExecutionProgressSnapshot,
        *,
        limits: ExecutionSessionLimits = DEFAULT_EXECUTION_SESSION_LIMITS_V1,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if type(limits) is not ExecutionSessionLimits or not callable(clock):
            raise TypeError("progress mailbox requires typed limits and a callable clock")
        self._snapshot = snapshot
        self._limits = limits
        self._clock = clock
        self._occurrences: dict[tuple[LiveOccurrenceKind, str], LiveOccurrenceProgress] = {}
        self._recent_events: list[LiveProgressEvent] = []
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
        observed_at = self._clock()
        if type(observed_at) is not int or observed_at < 0:
            raise ValueError("progress mailbox clock must return non-negative integer nanoseconds")

        if event.kind is RuntimeEventKind.RUN_STARTED:
            phase = ExecutionSessionPhase.RUNNING
        elif event.kind is RuntimeEventKind.EXECUTION_QUEUED:
            queued += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
            self._occurrence_event(event, observed_at, last_execution)
        elif event.kind is RuntimeEventKind.EXECUTION_STARTED:
            started += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
            self._occurrence_event(event, observed_at, last_execution)
        elif event.kind is RuntimeEventKind.EXECUTION_FINISHED:
            finished += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
            self._occurrence_event(event, observed_at, last_execution)
        elif event.kind is RuntimeEventKind.COMPARISON_FINISHED:
            comparisons += 1
            last_comparison = str(event.comparison_id.value) if event.comparison_id else None
            self._occurrence_event(event, observed_at, last_comparison)
        elif event.kind is RuntimeEventKind.RUN_FINISHED:
            phase = ExecutionSessionPhase.FINALIZING
        else:  # pragma: no cover - RuntimeEventKind is exhaustively handled above
            raise RuntimeError("unsupported runtime event kind")

        occurrence_id = last_execution if event.execution_id is not None else last_comparison
        self._recent_events.append(
            LiveProgressEvent(
                sequence=event.sequence.value,
                kind=event.kind,
                observed_at_nanoseconds=observed_at,
                occurrence_id=occurrence_id,
                outcome=event.outcome,
            )
        )
        del self._recent_events[: -self._limits.maximum_recent_events]
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
                last_event_kind=event.kind,
                occurrences=tuple(self._occurrences.values()),
                recent_events=tuple(self._recent_events),
                omitted_occurrences=self._snapshot.omitted_occurrences,
            )
        )

    def _occurrence_event(
        self,
        event: RuntimeEvent,
        observed_at: int,
        occurrence_id: str | None,
    ) -> None:
        if occurrence_id is None:  # pragma: no cover - RuntimeEvent invariant
            return
        kind = (
            LiveOccurrenceKind.COMPARISON
            if event.kind is RuntimeEventKind.COMPARISON_FINISHED
            else LiveOccurrenceKind.EXECUTION
        )
        key = (kind, occurrence_id)
        current = self._occurrences.get(key)
        if current is None:
            if event.kind in {
                RuntimeEventKind.EXECUTION_STARTED,
                RuntimeEventKind.EXECUTION_FINISHED,
            }:
                return
            if len(self._occurrences) >= self._limits.maximum_occurrences:
                evicted = next(
                    (
                        found
                        for found, item in self._occurrences.items()
                        if item.state is LiveOccurrenceState.FINISHED
                    ),
                    None,
                )
                if evicted is None:
                    self._snapshot = msgspec.structs.replace(
                        self._snapshot,
                        omitted_occurrences=self._snapshot.omitted_occurrences + 1,
                    )
                    return
                del self._occurrences[evicted]
                self._snapshot = msgspec.structs.replace(
                    self._snapshot,
                    omitted_occurrences=self._snapshot.omitted_occurrences + 1,
                )
            state = (
                LiveOccurrenceState.FINISHED
                if kind is LiveOccurrenceKind.COMPARISON
                else LiveOccurrenceState.QUEUED
            )
            self._occurrences[key] = LiveOccurrenceProgress(
                kind=kind,
                occurrence_id=occurrence_id,
                state=state,
                outcome=event.outcome,
                queued_at_nanoseconds=(
                    observed_at if state is LiveOccurrenceState.QUEUED else None
                ),
                finished_at_nanoseconds=(
                    observed_at if state is LiveOccurrenceState.FINISHED else None
                ),
            )
            return
        if event.kind is RuntimeEventKind.EXECUTION_STARTED:
            self._occurrences[key] = msgspec.structs.replace(
                current,
                state=LiveOccurrenceState.RUNNING,
                started_at_nanoseconds=observed_at,
            )
        elif event.kind is RuntimeEventKind.EXECUTION_FINISHED:
            self._occurrences[key] = msgspec.structs.replace(
                current,
                state=LiveOccurrenceState.FINISHED,
                outcome=event.outcome,
                failure_stage=(None if event.failure_stage is None else event.failure_stage.value),
                failure_code=(None if event.failure_code is None else event.failure_code.value),
                finished_at_nanoseconds=observed_at,
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

    def finalize(self, summary: ExecutionFinalSummary) -> None:
        """Publish sanitized final artifact facts before the terminal transition."""
        if type(summary) is not ExecutionFinalSummary:
            raise TypeError("progress finalization requires ExecutionFinalSummary")
        self._publish(
            msgspec.structs.replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                final_summary=summary,
            )
        )

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

    def __init__(
        self,
        request: ProductionExecutionRequest,
        *,
        limits: ExecutionSessionLimits = DEFAULT_EXECUTION_SESSION_LIMITS_V1,
    ) -> None:
        """Start exactly one execution on the caller's active event loop."""
        if type(request) is not ProductionExecutionRequest:
            raise TypeError("execution session requires ProductionExecutionRequest")
        if request.events is not None or request.cancellation is not None:
            raise ValueError("execution session owns progress and cancellation ports")
        if type(limits) is not ExecutionSessionLimits:
            raise TypeError("execution session requires ExecutionSessionLimits")
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
            ),
            limits=limits,
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
        except Exception:
            self._mailbox.close(ExecutionSessionPhase.FINISHED)
            raise
        if result.artifact is not None:
            artifact = result.artifact
            facts = (
                *(fact for item in artifact.executions for fact in item.assertions),
                *(fact for item in artifact.comparisons for fact in item.diffs),
            )
            self._mailbox.finalize(
                ExecutionFinalSummary(
                    completion=artifact.completion.kind.value,
                    gate=artifact.gate.verdict.value,
                    assertions=sum(len(item.assertions) for item in artifact.executions),
                    diffs=sum(len(item.diffs) for item in artifact.comparisons),
                    adverse_facts=sum(
                        fact.outcome.value not in {"satisfied", "equivalent", "skipped"}
                        for fact in facts
                    ),
                )
            )
        self._mailbox.close(
            ExecutionSessionPhase.CANCELLED
            if self._cancellation.cancelled
            else ExecutionSessionPhase.FINISHED
        )
        return result


def start_execution_session(
    request: ProductionExecutionRequest,
    *,
    limits: ExecutionSessionLimits = DEFAULT_EXECUTION_SESSION_LIMITS_V1,
) -> ExecutionSession:
    """Start one interactive execution session on the current event loop."""
    return ExecutionSession(request, limits=limits)


__all__ = (
    "DEFAULT_EXECUTION_SESSION_LIMITS_V1",
    "ExecutionFinalSummary",
    "ExecutionProgressSnapshot",
    "ExecutionSession",
    "ExecutionSessionLimits",
    "ExecutionSessionPhase",
    "LiveOccurrenceKind",
    "LiveOccurrenceProgress",
    "LiveOccurrenceState",
    "LiveProgressEvent",
    "start_execution_session",
)
