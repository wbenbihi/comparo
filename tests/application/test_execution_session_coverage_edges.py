"""Contract and state-machine edges for interactive execution sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from typing import cast

import msgspec
import pytest

from comparo.application import execution_session as sessions
from comparo.application.inspection import ResourceCoordinate
from comparo.application.plan_occurrences import PlanComparisonInspection
from comparo.application.plan_occurrences import PlanOccurrenceInspection
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.core.runtime.models import RuntimeEventKind
from comparo.core.runtime.models import RuntimeOccurrenceOutcome
from tests.application.test_execution_session import _event
from tests.application.test_production_execution import _request
from tests.core.artifacts.test_artifact_contracts import _artifact


def _snapshot(*, total_executions: int = 2) -> sessions.ExecutionProgressSnapshot:
    return sessions.ExecutionProgressSnapshot(
        revision=0,
        run_id="run",
        phase=sessions.ExecutionSessionPhase.PREPARING,
        total_executions=total_executions,
        total_comparisons=1,
    )


def _coordinate(kind: str, resource_id: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="local",
        kind=kind,
        resource_id=resource_id,
        fingerprint="f" * 64,
    )


def _execution_coordinate(execution_id: str = "execution-1") -> PlanOccurrenceInspection:
    return PlanOccurrenceInspection(
        ordinal=0,
        execution_id=execution_id,
        subject=_coordinate("Request", "ping"),
        environment=_coordinate("Environment", "staging"),
        cell_id="cell-1",
        trial_id="trial-1",
        trial_ordinal=0,
    )


def _comparison_coordinate(comparison_id: str = "comparison-1") -> PlanComparisonInspection:
    return PlanComparisonInspection(
        ordinal=0,
        comparison_id=comparison_id,
        subject=_coordinate("Request", "ping"),
        cell_id="cell-1",
        trial_id="trial-1",
        trial_ordinal=0,
        baseline_environment=_coordinate("Environment", "staging"),
        baseline_execution_id="execution-1",
        candidate_environment=_coordinate("Environment", "prod"),
        candidate_execution_id="execution-2",
    )


def test_session_limits_require_exact_positive_integers() -> None:
    for changes in (
        {"maximum_occurrences": 0, "maximum_recent_events": 1},
        {"maximum_occurrences": 1, "maximum_recent_events": -1},
        {"maximum_occurrences": cast("int", True), "maximum_recent_events": 1},
    ):
        with pytest.raises(ValueError, match="positive integers"):
            sessions.ExecutionSessionLimits(**changes)


def test_live_occurrence_contract_rejects_each_incoherent_shape() -> None:
    valid = sessions.LiveOccurrenceProgress(
        kind=sessions.LiveOccurrenceKind.EXECUTION,
        occurrence_id="execution-1",
        state=sessions.LiveOccurrenceState.QUEUED,
        queued_at_nanoseconds=1,
    )
    cases: tuple[tuple[dict[str, Any], type[Exception], str], ...] = (
        ({"kind": "execution"}, TypeError, "kind and state"),
        ({"state": "queued"}, TypeError, "kind and state"),
        ({"occurrence_id": ""}, ValueError, "ID must be non-empty"),
        ({"outcome": "observed"}, TypeError, "outcome must be typed"),
        ({"failure_stage": "transport"}, ValueError, "must be paired"),
        ({"failure_stage": "", "failure_code": "code"}, ValueError, "non-empty"),
        ({"queued_at_nanoseconds": -1}, ValueError, "non-negative"),
        (
            {"started_at_nanoseconds": 3, "finished_at_nanoseconds": 2},
            ValueError,
            "monotonic",
        ),
        ({"state": sessions.LiveOccurrenceState.FINISHED}, ValueError, "finish timestamp"),
        (
            {"outcome": RuntimeOccurrenceOutcome.OBSERVED},
            ValueError,
            "finished state",
        ),
    )
    for changes, error, message in cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(valid, **changes)

    coordinate_cases: tuple[tuple[dict[str, Any], type[Exception], str], ...] = (
        ({"execution": object()}, TypeError, "execution occurrence coordinate"),
        ({"comparison": object()}, TypeError, "comparison occurrence coordinate"),
        ({"comparison": _comparison_coordinate()}, ValueError, "cannot retain"),
        ({"execution": _execution_coordinate("other")}, ValueError, "contradicts"),
    )
    for changes, error, message in coordinate_cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(valid, **changes)
    comparison = sessions.LiveOccurrenceProgress(
        kind=sessions.LiveOccurrenceKind.COMPARISON,
        occurrence_id="comparison-1",
        state=sessions.LiveOccurrenceState.FINISHED,
        finished_at_nanoseconds=1,
        comparison=_comparison_coordinate(),
    )
    with pytest.raises(ValueError, match="cannot retain"):
        msgspec.structs.replace(comparison, execution=_execution_coordinate())
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(comparison, comparison=_comparison_coordinate("other"))

    assert valid.elapsed_nanoseconds is None
    finished = msgspec.structs.replace(
        valid,
        state=sessions.LiveOccurrenceState.FINISHED,
        started_at_nanoseconds=2,
        finished_at_nanoseconds=5,
    )
    assert finished.elapsed_nanoseconds == 3


def test_live_event_and_final_summary_contracts_reject_bad_values() -> None:
    valid = sessions.LiveProgressEvent(
        sequence=0,
        kind=RuntimeEventKind.RUN_STARTED,
        observed_at_nanoseconds=1,
    )
    cases: tuple[tuple[dict[str, Any], type[Exception], str], ...] = (
        ({"sequence": -1}, ValueError, "sequence"),
        ({"kind": "run-started"}, TypeError, "kind must be typed"),
        ({"observed_at_nanoseconds": -1}, ValueError, "timestamp"),
        ({"occurrence_id": ""}, ValueError, "occurrence ID"),
        ({"outcome": "observed"}, TypeError, "outcome must be typed"),
    )
    for changes, error, message in cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(valid, **changes)

    summary = sessions.ExecutionFinalSummary(
        completion="complete",
        gate="passed",
        assertions=1,
        diffs=1,
        adverse_facts=0,
    )
    with pytest.raises(ValueError, match="labels"):
        msgspec.structs.replace(summary, gate="")
    with pytest.raises(ValueError, match="counts"):
        msgspec.structs.replace(summary, adverse_facts=-1)


def test_progress_snapshot_rejects_invalid_collections_and_terminal_summary() -> None:
    valid = _snapshot()
    occurrence = sessions.LiveOccurrenceProgress(
        kind=sessions.LiveOccurrenceKind.EXECUTION,
        occurrence_id="execution-1",
        state=sessions.LiveOccurrenceState.QUEUED,
        queued_at_nanoseconds=1,
    )
    event = sessions.LiveProgressEvent(
        sequence=0,
        kind=RuntimeEventKind.RUN_STARTED,
        observed_at_nanoseconds=1,
    )
    with pytest.raises(TypeError, match="occurrences"):
        msgspec.structs.replace(valid, occurrences=cast("tuple[Any, ...]", [occurrence]))
    with pytest.raises(ValueError, match="identities must be unique"):
        msgspec.structs.replace(valid, occurrences=(occurrence, occurrence))
    with pytest.raises(TypeError, match="recent progress events"):
        msgspec.structs.replace(valid, recent_events=cast("tuple[Any, ...]", [event]))
    with pytest.raises(ValueError, match="omitted"):
        msgspec.structs.replace(valid, omitted_occurrences=-1)
    with pytest.raises(TypeError, match="final execution summary"):
        msgspec.structs.replace(
            valid,
            final_summary=cast("sessions.ExecutionFinalSummary", object()),
        )


def test_mailbox_rejects_invalid_limits_clock_and_finalization(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="typed limits"):
        sessions._ProgressMailbox(
            _snapshot(),
            limits=cast("sessions.ExecutionSessionLimits", object()),
        )
    with pytest.raises(TypeError, match="callable clock"):
        sessions._ProgressMailbox(_snapshot(), clock=cast("Any", 1))
    with pytest.raises(TypeError, match="project must be prepared"):
        sessions._ProgressMailbox(
            _snapshot(),
            project=cast("Any", object()),
        )

    mailbox = sessions._ProgressMailbox(_snapshot())
    request = _request(tmp_path)
    assert (
        mailbox._comparison_coordinate(
            0,
            _event(request, 1, RuntimeEventKind.COMPARISON_FINISHED),
        )
        is None
    )
    with pytest.raises(TypeError, match="ExecutionFinalSummary"):
        mailbox.finalize(cast("sessions.ExecutionFinalSummary", object()))


def test_mailbox_rejects_an_invalid_clock_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        mailbox = sessions._ProgressMailbox(_snapshot(), clock=lambda: -1)
        with pytest.raises(ValueError, match="clock"):
            await mailbox.emit(_event(request, 0, RuntimeEventKind.RUN_STARTED))

    asyncio.run(scenario())


def test_mailbox_counts_orphan_and_overflow_events_without_unbounded_growth(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        orphan = sessions._ProgressMailbox(_snapshot())
        with pytest.raises(ValueError, match="exceeds queued"):
            await orphan.emit(_event(request, 1, RuntimeEventKind.EXECUTION_STARTED))

        bounded = sessions._ProgressMailbox(
            _snapshot(total_executions=3),
            limits=sessions.ExecutionSessionLimits(
                maximum_occurrences=1,
                maximum_recent_events=2,
            ),
            clock=iter((1, 2, 3)).__next__,
        )
        await bounded.emit(_event(request, 1, RuntimeEventKind.EXECUTION_QUEUED))
        await bounded.emit(_event(request, 1, RuntimeEventKind.EXECUTION_QUEUED))
        await bounded.emit(_event(request, 4, RuntimeEventKind.EXECUTION_QUEUED))
        assert len(bounded.snapshot.occurrences) == 1
        assert bounded.snapshot.omitted_occurrences == 1
        assert len(bounded.snapshot.recent_events) == 2

    asyncio.run(scenario())


def test_session_requires_typed_limits(tmp_path: Path) -> None:
    async def scenario() -> None:
        with pytest.raises(TypeError, match="ExecutionSessionLimits"):
            sessions.ExecutionSession(
                _request(tmp_path),
                limits=cast("sessions.ExecutionSessionLimits", object()),
            )

    asyncio.run(scenario())


def test_session_projects_terminal_artifact_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def execute(_owned: ProductionExecutionRequest) -> ProductionExecutionResult:
            return ProductionExecutionResult(artifact=_artifact())

        monkeypatch.setattr(sessions, "execute_production", execute)
        session = sessions.start_execution_session(_request(tmp_path))
        result = await session.result()
        snapshots = tuple([snapshot async for snapshot in session.updates()])

        assert result.artifact is not None
        summary = snapshots[-1].final_summary
        assert summary is not None
        assert summary.completion == result.artifact.completion.kind.value
        assert summary.assertions == 1
        assert summary.diffs == 1
        assert summary.adverse_facts == 0

    asyncio.run(scenario())
