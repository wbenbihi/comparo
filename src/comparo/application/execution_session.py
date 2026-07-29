"""Interactive, bounded progress sessions over the production execution service."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from collections.abc import Callable
from enum import StrEnum
from typing import final

import msgspec

from comparo.application.plan_occurrences import PlanComparisonInspection
from comparo.application.plan_occurrences import PlanOccurrenceInspection
from comparo.application.plan_occurrences import inspect_plan_comparison_window
from comparo.application.plan_occurrences import inspect_plan_occurrence_window
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.application.production_execution import execute_production
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.resolution import RunCancellation
from comparo.core.runtime.models import RuntimeEvent
from comparo.core.runtime.models import RuntimeEventKind
from comparo.core.runtime.models import RuntimeFactEngine
from comparo.core.runtime.models import RuntimeFactOutcome
from comparo.core.runtime.models import RuntimeFactSummary
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
    maximum_facts: int = 10_000

    def __post_init__(self) -> None:
        """Require explicit finite positive window sizes."""
        if any(
            type(value) is not int or value < 1
            for value in (
                self.maximum_occurrences,
                self.maximum_recent_events,
                self.maximum_facts,
            )
        ):
            raise ValueError("execution session window limits must be positive integers")


DEFAULT_EXECUTION_SESSION_LIMITS_V1 = ExecutionSessionLimits(
    maximum_occurrences=10_000,
    maximum_recent_events=128,
    maximum_facts=10_000,
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
    execution: PlanOccurrenceInspection | None = None
    comparison: PlanComparisonInspection | None = None

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
        if self.execution is not None and type(self.execution) is not PlanOccurrenceInspection:
            raise TypeError("live execution occurrence coordinate must be typed or absent")
        if self.comparison is not None and type(self.comparison) is not PlanComparisonInspection:
            raise TypeError("live comparison occurrence coordinate must be typed or absent")
        if self.kind is LiveOccurrenceKind.EXECUTION:
            if self.comparison is not None:
                raise ValueError("live execution cannot retain a comparison coordinate")
            if self.execution is not None and self.execution.execution_id != self.occurrence_id:
                raise ValueError("live execution coordinate contradicts its occurrence ID")
        else:
            if self.execution is not None:
                raise ValueError("live comparison cannot retain an execution coordinate")
            if self.comparison is not None and self.comparison.comparison_id != self.occurrence_id:
                raise ValueError("live comparison coordinate contradicts its occurrence ID")

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
    fact_count: int = 0
    fact_evaluation_complete: bool | None = None

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
        if type(self.fact_count) is not int or self.fact_count < 0:
            raise ValueError("live event fact count must be non-negative")
        fact_kind = self.kind in {
            RuntimeEventKind.ASSERTIONS_FINISHED,
            RuntimeEventKind.COMPARISON_FINISHED,
        }
        if fact_kind != (self.fact_evaluation_complete is not None):
            raise ValueError("live fact completion marker contradicts its event kind")
        if self.fact_evaluation_complete is not None and (
            type(self.fact_evaluation_complete) is not bool
        ):
            raise TypeError("live fact completion marker must be bool or absent")
        if self.fact_count and not fact_kind:
            raise ValueError("live event fact count requires a fact-bearing event")


@final
class LiveFactMetric(msgspec.Struct, frozen=True, kw_only=True):
    """One exact value-free count from a live aggregate rule fact."""

    name: str
    value: int

    def __post_init__(self) -> None:
        """Require one bounded name and non-negative integer value."""
        if type(self.name) is not str or not self.name or len(self.name) > 64:
            raise ValueError("live fact metric name must be bounded non-empty text")
        if type(self.value) is not int or self.value < 0:
            raise ValueError("live fact metric value must be non-negative")


@final
class LiveRuleFactProgress(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded sanitized fact bound to its execution or comparison owner."""

    owner_kind: LiveOccurrenceKind
    owner_id: str
    engine: RuntimeFactEngine
    fact_id: str
    rule_id: str
    effective_rule_id: str
    severity: str
    outcome: RuntimeFactOutcome
    counts: tuple[LiveFactMetric, ...]

    def __post_init__(self) -> None:
        """Require stable value-free identities and canonical metric counts."""
        if type(self.owner_kind) is not LiveOccurrenceKind:
            raise TypeError("live fact owner kind must be typed")
        if type(self.engine) is not RuntimeFactEngine:
            raise TypeError("live fact engine must be typed")
        expected_owner = (
            LiveOccurrenceKind.EXECUTION
            if self.engine is RuntimeFactEngine.ASSERTION
            else LiveOccurrenceKind.COMPARISON
        )
        if self.owner_kind is not expected_owner:
            raise ValueError("live fact owner kind contradicts its engine")
        for value in (
            self.owner_id,
            self.fact_id,
            self.rule_id,
            self.effective_rule_id,
            self.severity,
        ):
            if type(value) is not str or not value:
                raise ValueError("live fact identities and severity must be non-empty text")
        if type(self.outcome) is not RuntimeFactOutcome:
            raise TypeError("live fact outcome must be typed")
        if type(self.counts) is not tuple or any(
            type(item) is not LiveFactMetric for item in self.counts
        ):
            raise TypeError("live fact metrics must be immutable and typed")
        names = tuple(item.name for item in self.counts)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("live fact metric names must be unique and canonical")


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
    facts: tuple[LiveRuleFactProgress, ...] = ()
    omitted_occurrences: int = 0
    omitted_facts: int = 0
    incomplete_fact_scopes: int = 0
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
        if type(self.facts) is not tuple or any(
            type(item) is not LiveRuleFactProgress for item in self.facts
        ):
            raise TypeError("live progress facts must be immutable and typed")
        if len({(item.engine, item.fact_id) for item in self.facts}) != len(self.facts):
            raise ValueError("live progress fact identities must be unique")
        if type(self.omitted_occurrences) is not int or self.omitted_occurrences < 0:
            raise ValueError("omitted live occurrence count must be non-negative")
        if any(
            type(value) is not int or value < 0
            for value in (self.omitted_facts, self.incomplete_fact_scopes)
        ):
            raise ValueError("live fact omission and incompleteness counts must be non-negative")
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
        "_facts",
        "_limits",
        "_occurrences",
        "_project",
        "_recent_events",
        "_snapshot",
    )

    def __init__(
        self,
        snapshot: ExecutionProgressSnapshot,
        *,
        limits: ExecutionSessionLimits = DEFAULT_EXECUTION_SESSION_LIMITS_V1,
        clock: Callable[[], int] = time.monotonic_ns,
        project: PreparedProjectPlan | None = None,
    ) -> None:
        if type(limits) is not ExecutionSessionLimits or not callable(clock):
            raise TypeError("progress mailbox requires typed limits and a callable clock")
        if project is not None and type(project) is not PreparedProjectPlan:
            raise TypeError("progress mailbox project must be prepared or absent")
        self._snapshot = snapshot
        self._limits = limits
        self._project = project
        self._clock = clock
        self._occurrences: dict[tuple[LiveOccurrenceKind, str], LiveOccurrenceProgress] = {}
        self._facts: dict[tuple[RuntimeFactEngine, str], LiveRuleFactProgress] = {}
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
        incomplete_fact_scopes = current.incomplete_fact_scopes
        last_execution = current.last_execution_id
        last_comparison = current.last_comparison_id
        observed_at = self._clock()
        execution_coordinate: PlanOccurrenceInspection | None = None
        comparison_coordinate: PlanComparisonInspection | None = None
        if type(observed_at) is not int or observed_at < 0:
            raise ValueError("progress mailbox clock must return non-negative integer nanoseconds")

        if event.kind is RuntimeEventKind.RUN_STARTED:
            phase = ExecutionSessionPhase.RUNNING
        elif event.kind is RuntimeEventKind.EXECUTION_QUEUED:
            execution_coordinate = self._execution_coordinate(queued, event)
            queued += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
            self._occurrence_event(
                event,
                observed_at,
                last_execution,
                execution=execution_coordinate,
            )
        elif event.kind is RuntimeEventKind.EXECUTION_STARTED:
            started += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
            self._occurrence_event(event, observed_at, last_execution)
        elif event.kind is RuntimeEventKind.EXECUTION_FINISHED:
            finished += 1
            last_execution = str(event.execution_id.value) if event.execution_id else None
            self._occurrence_event(event, observed_at, last_execution)
        elif event.kind is RuntimeEventKind.ASSERTIONS_FINISHED:
            last_execution = str(event.execution_id.value) if event.execution_id else None
            self._fact_event(event, last_execution)
            incomplete_fact_scopes += int(event.fact_evaluation_complete is False)
        elif event.kind is RuntimeEventKind.COMPARISON_FINISHED:
            comparison_coordinate = self._comparison_coordinate(comparisons, event)
            comparisons += 1
            last_comparison = str(event.comparison_id.value) if event.comparison_id else None
            self._occurrence_event(
                event,
                observed_at,
                last_comparison,
                comparison=comparison_coordinate,
            )
            self._fact_event(event, last_comparison)
            incomplete_fact_scopes += int(event.fact_evaluation_complete is False)
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
                fact_count=len(event.facts),
                fact_evaluation_complete=event.fact_evaluation_complete,
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
                facts=tuple(self._facts.values()),
                omitted_occurrences=self._snapshot.omitted_occurrences,
                omitted_facts=self._snapshot.omitted_facts,
                incomplete_fact_scopes=incomplete_fact_scopes,
            )
        )

    def _fact_event(self, event: RuntimeEvent, owner_id: str | None) -> None:
        if owner_id is None:  # pragma: no cover - RuntimeEvent invariant
            return
        owner_kind = (
            LiveOccurrenceKind.EXECUTION
            if event.kind is RuntimeEventKind.ASSERTIONS_FINISHED
            else LiveOccurrenceKind.COMPARISON
        )
        omitted = 0
        for fact in event.facts:
            key = (fact.engine, str(fact.fact_id.value))
            if key in self._facts:
                continue
            if len(self._facts) >= self._limits.maximum_facts:
                omitted += 1
                continue
            self._facts[key] = _live_fact(owner_kind, owner_id, fact)
        if omitted:
            self._snapshot = msgspec.structs.replace(
                self._snapshot,
                omitted_facts=self._snapshot.omitted_facts + omitted,
            )

    def _occurrence_event(
        self,
        event: RuntimeEvent,
        observed_at: int,
        occurrence_id: str | None,
        *,
        execution: PlanOccurrenceInspection | None = None,
        comparison: PlanComparisonInspection | None = None,
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
                execution=execution,
                comparison=comparison,
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

    def _execution_coordinate(
        self,
        ordinal: int,
        event: RuntimeEvent,
    ) -> PlanOccurrenceInspection | None:
        if self._project is None or event.execution_id is None:
            return None
        window = inspect_plan_occurrence_window(
            self._project,
            start_ordinal=ordinal,
            maximum_occurrences=1,
        )
        if not window.occurrences:
            return None  # pragma: no cover - RuntimeEvent counter invariant
        coordinate = window.occurrences[0]
        if coordinate.execution_id != str(event.execution_id.value):
            return None
        return coordinate

    def _comparison_coordinate(
        self,
        ordinal: int,
        event: RuntimeEvent,
    ) -> PlanComparisonInspection | None:
        if self._project is None or event.comparison_id is None:
            return None
        window = inspect_plan_comparison_window(
            self._project,
            start_ordinal=ordinal,
            maximum_comparisons=1,
        )
        if not window.comparisons:
            return None  # pragma: no cover - RuntimeEvent counter invariant
        coordinate = window.comparisons[0]
        if coordinate.comparison_id != str(event.comparison_id.value):
            return None
        return coordinate

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


def _live_fact(
    owner_kind: LiveOccurrenceKind,
    owner_id: str,
    fact: RuntimeFactSummary,
) -> LiveRuleFactProgress:
    return LiveRuleFactProgress(
        owner_kind=owner_kind,
        owner_id=owner_id,
        engine=fact.engine,
        fact_id=str(fact.fact_id.value),
        rule_id=fact.rule_id.value,
        effective_rule_id=fact.effective_rule_id.value,
        severity=fact.severity,
        outcome=fact.outcome,
        counts=tuple(
            LiveFactMetric(name=item.name, value=item.value.value) for item in fact.counts
        ),
    )


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
            project=request.project,
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

    @property
    def snapshot(self) -> ExecutionProgressSnapshot:
        """Return the latest immutable progress snapshot without waiting."""
        return self._mailbox.snapshot

    @property
    def task_cancelled(self) -> bool:
        """Return whether the owned task itself ended through asyncio cancellation."""
        return self._task.cancelled()

    async def after(self, revision: int) -> ExecutionProgressSnapshot | None:
        """Wait for one newer coalesced snapshot using a caller-owned revision cursor."""
        return await self._mailbox.after(revision)

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
    "LiveFactMetric",
    "LiveOccurrenceKind",
    "LiveOccurrenceProgress",
    "LiveOccurrenceState",
    "LiveProgressEvent",
    "LiveRuleFactProgress",
    "start_execution_session",
)
