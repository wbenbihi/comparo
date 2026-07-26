"""Interactive execution session progress and cancellation contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid5

import msgspec
import pytest

from comparo.application import execution_session as sessions
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.production_execution import ProductionExecutionFailure
from comparo.application.production_execution import ProductionExecutionFailureKind
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Count
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime.models import RuntimeEvent
from comparo.core.runtime.models import RuntimeEventKind
from comparo.core.runtime.models import RuntimeOccurrenceOutcome
from tests.application.test_production_execution import _request


def _failure() -> ProductionExecutionResult:
    return ProductionExecutionResult(
        failure=ProductionExecutionFailure(
            kind=ProductionExecutionFailureKind.INTERNAL,
            diagnostics=(
                ApplicationDiagnostic(
                    code="test.finished",
                    severity="error",
                    stage="execution",
                ),
            ),
        )
    )


def _event(
    request: ProductionExecutionRequest,
    sequence: int,
    kind: RuntimeEventKind,
) -> RuntimeEvent:
    execution = (
        EnvironmentExecutionId(
            value=uuid5(
                request.project.prepared.plan.run_id.value,
                f"test-execution:{0 if sequence < 4 else 1}",
            )
        )
        if kind
        in {
            RuntimeEventKind.EXECUTION_QUEUED,
            RuntimeEventKind.EXECUTION_STARTED,
            RuntimeEventKind.EXECUTION_FINISHED,
        }
        else None
    )
    comparison = (
        ComparisonId(value=uuid5(request.project.prepared.plan.run_id.value, "test-comparison"))
        if kind is RuntimeEventKind.COMPARISON_FINISHED
        else None
    )
    return RuntimeEvent(
        sequence=Count(value=sequence),
        run_id=request.project.prepared.plan.run_id,
        kind=kind,
        execution_id=execution,
        comparison_id=comparison,
        outcome=(
            RuntimeOccurrenceOutcome.OBSERVED
            if kind is RuntimeEventKind.EXECUTION_FINISHED
            else RuntimeOccurrenceOutcome.EVALUATED
            if kind is RuntimeEventKind.COMPARISON_FINISHED
            else None
        ),
    )


def test_session_coalesces_safe_runtime_progress_and_returns_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)

        async def execute(
            owned: ProductionExecutionRequest,
        ) -> ProductionExecutionResult:
            assert owned.events is not None
            for sequence, kind in enumerate(
                (
                    RuntimeEventKind.RUN_STARTED,
                    RuntimeEventKind.EXECUTION_QUEUED,
                    RuntimeEventKind.EXECUTION_STARTED,
                    RuntimeEventKind.EXECUTION_FINISHED,
                    RuntimeEventKind.EXECUTION_QUEUED,
                    RuntimeEventKind.EXECUTION_STARTED,
                    RuntimeEventKind.EXECUTION_FINISHED,
                    RuntimeEventKind.COMPARISON_FINISHED,
                    RuntimeEventKind.RUN_FINISHED,
                )
            ):
                await owned.events.emit(_event(owned, sequence, kind))
            return _failure()

        monkeypatch.setattr(sessions, "execute_production", execute)
        session = sessions.start_execution_session(request)
        snapshots = tuple([snapshot async for snapshot in session.updates()])
        result = await session.result()

        assert result == _failure()
        assert session.done
        assert session.run_id == str(request.project.prepared.plan.run_id.value)
        assert snapshots[0].phase is sessions.ExecutionSessionPhase.PREPARING
        assert snapshots[0].revision < snapshots[-1].revision
        final = snapshots[-1]
        assert final.phase is sessions.ExecutionSessionPhase.FINISHED
        assert final.queued_executions == final.started_executions == 2
        assert final.finished_executions == 2
        assert final.finished_comparisons == 1
        assert final.active_executions == 0
        assert final.runtime_sequence == 8
        assert final.last_event_kind is RuntimeEventKind.RUN_FINISHED
        assert final.last_execution_id is not None
        assert final.last_comparison_id is not None
        assert len(final.occurrences) == 3
        assert all(
            item.state is sessions.LiveOccurrenceState.FINISHED for item in final.occurrences
        )
        assert {item.outcome for item in final.occurrences} == {
            RuntimeOccurrenceOutcome.OBSERVED,
            RuntimeOccurrenceOutcome.EVALUATED,
        }
        assert final.recent_events[-1].kind is RuntimeEventKind.RUN_FINISHED
        assert not session.cancel()
        await session.close()

    asyncio.run(scenario())


def test_session_uses_explicit_user_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        started = asyncio.Event()

        async def execute(
            owned: ProductionExecutionRequest,
        ) -> ProductionExecutionResult:
            assert owned.events is not None
            assert owned.cancellation is not None
            await owned.events.emit(_event(owned, 0, RuntimeEventKind.RUN_STARTED))
            started.set()
            await owned.cancellation.wait()
            await owned.events.emit(_event(owned, 1, RuntimeEventKind.RUN_FINISHED))
            return _failure()

        monkeypatch.setattr(sessions, "execute_production", execute)
        session = sessions.start_execution_session(request)
        await started.wait()
        assert session.cancel()
        assert not session.cancel()
        result = await session.result()
        snapshots = tuple([snapshot async for snapshot in session.updates()])

        assert result == _failure()
        assert snapshots[-1].phase is sessions.ExecutionSessionPhase.CANCELLED
        assert snapshots[-1].runtime_sequence == 1

    asyncio.run(scenario())


def test_unexpected_execution_failure_closes_progress_before_result_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def execute(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            raise RuntimeError("test execution failure")

        monkeypatch.setattr(sessions, "execute_production", execute)
        session = sessions.start_execution_session(_request(tmp_path))

        snapshots = await asyncio.wait_for(
            _collect_updates(session),
            timeout=1,
        )
        with pytest.raises(RuntimeError, match="test execution failure"):
            await session.result()

        assert session.done
        assert snapshots[-1].phase is sessions.ExecutionSessionPhase.FINISHED

    async def _collect_updates(
        session: sessions.ExecutionSession,
    ) -> tuple[sessions.ExecutionProgressSnapshot, ...]:
        return tuple([snapshot async for snapshot in session.updates()])

    asyncio.run(scenario())


def test_session_rejects_invalid_ownership_and_progress_values(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    with pytest.raises(TypeError, match="requires ProductionExecutionRequest"):
        sessions.ExecutionSession(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="owns progress"):
        sessions.ExecutionSession(msgspec.structs.replace(request, events=RecordingEventSink()))

    valid = sessions.ExecutionProgressSnapshot(
        revision=0,
        run_id="run",
        phase=sessions.ExecutionSessionPhase.PREPARING,
        total_executions=2,
        total_comparisons=1,
    )
    assert valid.active_executions == 0
    replacements = (
        ({"revision": -1}, "non-negative"),
        ({"run_id": ""}, "non-empty"),
        ({"phase": "bad"}, "phase must be typed"),
        ({"queued_executions": 3}, "exceeds the plan"),
        ({"queued_executions": 1, "started_executions": 2}, "exceeds queued"),
        (
            {"queued_executions": 1, "started_executions": 1, "finished_executions": 2},
            "exceeds started",
        ),
        ({"finished_comparisons": 2}, "exceeds the plan"),
        ({"runtime_sequence": -1}, "event sequence"),
        ({"last_execution_id": ""}, "occurrence IDs"),
        ({"last_event_kind": "run-started"}, "event kind"),
    )
    for changes, message in replacements:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(valid, **changes)


def test_mailbox_rejects_invalid_inputs() -> None:
    async def scenario() -> None:
        snapshot = sessions.ExecutionProgressSnapshot(
            revision=0,
            run_id="run",
            phase=sessions.ExecutionSessionPhase.PREPARING,
            total_executions=0,
            total_comparisons=0,
        )
        mailbox = sessions._ProgressMailbox(snapshot)
        with pytest.raises(TypeError, match="requires RuntimeEvent"):
            await mailbox.emit(object())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="phase must be typed"):
            mailbox.transition("bad")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="at least -1"):
            await mailbox.after(-2)
        mailbox.transition(sessions.ExecutionSessionPhase.PREPARING)

    asyncio.run(scenario())


def test_live_projection_is_bounded_and_measures_occurrence_elapsed_time(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        ticks = iter((10, 20, 35, 40, 50, 60))
        mailbox = sessions._ProgressMailbox(
            sessions.ExecutionProgressSnapshot(
                revision=0,
                run_id="run",
                phase=sessions.ExecutionSessionPhase.PREPARING,
                total_executions=2,
                total_comparisons=0,
            ),
            limits=sessions.ExecutionSessionLimits(
                maximum_occurrences=1,
                maximum_recent_events=2,
            ),
            clock=lambda: next(ticks),
        )
        await mailbox.emit(_event(request, 0, RuntimeEventKind.RUN_STARTED))
        await mailbox.emit(_event(request, 1, RuntimeEventKind.EXECUTION_QUEUED))
        await mailbox.emit(_event(request, 2, RuntimeEventKind.EXECUTION_STARTED))
        await mailbox.emit(_event(request, 3, RuntimeEventKind.EXECUTION_FINISHED))
        assert mailbox.snapshot.occurrences[0].elapsed_nanoseconds == 5
        await mailbox.emit(_event(request, 4, RuntimeEventKind.EXECUTION_QUEUED))

        snapshot = mailbox.snapshot
        assert len(snapshot.occurrences) == 1
        assert snapshot.omitted_occurrences == 1
        assert len(snapshot.recent_events) == 2
        assert snapshot.occurrences[0].state is sessions.LiveOccurrenceState.QUEUED

    asyncio.run(scenario())


def test_cancelling_session_close_forces_task_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        async def execute(
            owned: ProductionExecutionRequest,
        ) -> ProductionExecutionResult:
            del owned
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(sessions, "execute_production", execute)
        session = sessions.start_execution_session(_request(tmp_path))
        await entered.wait()
        close = asyncio.create_task(session.close())
        await asyncio.sleep(0)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
        with pytest.raises(asyncio.CancelledError):
            await session.result()
        snapshots = tuple([snapshot async for snapshot in session.updates()])
        assert snapshots[-1].phase is sessions.ExecutionSessionPhase.CANCELLED

    asyncio.run(scenario())
