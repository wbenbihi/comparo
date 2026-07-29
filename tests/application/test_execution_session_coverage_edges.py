"""Contract and state-machine edges for interactive execution sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application import execution_session as sessions
from comparo.application.inspection import ResourceCoordinate
from comparo.application.plan_occurrences import PlanComparisonInspection
from comparo.application.plan_occurrences import PlanOccurrenceInspection
from comparo.application.production_execution import ProductionExecutionRequest
from comparo.application.production_execution import ProductionExecutionResult
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.primitives import Count
from comparo.core.runtime.models import RuntimeEventKind
from comparo.core.runtime.models import RuntimeFactEngine
from comparo.core.runtime.models import RuntimeFactMetric
from comparo.core.runtime.models import RuntimeFactOutcome
from comparo.core.runtime.models import RuntimeFactSummary
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


def _fact(
    engine: RuntimeFactEngine = RuntimeFactEngine.ASSERTION,
    *,
    ordinal: int = 1,
) -> RuntimeFactSummary:
    return RuntimeFactSummary(
        engine=engine,
        fact_id=FactId(value=UUID(int=ordinal)),
        rule_id=RuleId(value=f"rule-{ordinal}"),
        effective_rule_id=EffectiveRuleId(value=f"effective-{ordinal}"),
        severity="error",
        outcome=(
            RuntimeFactOutcome.SATISFIED
            if engine is RuntimeFactEngine.ASSERTION
            else RuntimeFactOutcome.EQUIVALENT
        ),
        counts=(RuntimeFactMetric(name="evaluated", value=Count(value=1)),),
    )


def _live_fact() -> sessions.LiveRuleFactProgress:
    return sessions.LiveRuleFactProgress(
        owner_kind=sessions.LiveOccurrenceKind.EXECUTION,
        owner_id="execution-1",
        engine=RuntimeFactEngine.ASSERTION,
        fact_id="fact-1",
        rule_id="rule-1",
        effective_rule_id="effective-1",
        severity="error",
        outcome=RuntimeFactOutcome.SATISFIED,
        counts=(sessions.LiveFactMetric(name="evaluated", value=1),),
    )


def test_session_limits_require_exact_positive_integers() -> None:
    for changes in (
        {"maximum_occurrences": 0, "maximum_recent_events": 1},
        {"maximum_occurrences": 1, "maximum_recent_events": -1},
        {"maximum_occurrences": 1, "maximum_recent_events": 1, "maximum_facts": 0},
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

    metric = sessions.LiveFactMetric(name="evaluated", value=1)
    for changes in ({"name": ""}, {"name": "x" * 65}, {"value": -1}):
        with pytest.raises(ValueError, match="live fact metric"):
            msgspec.structs.replace(metric, **changes)

    fact = _live_fact()
    fact_cases: tuple[tuple[dict[str, Any], type[Exception], str], ...] = (
        ({"owner_kind": "execution"}, TypeError, "owner kind"),
        ({"engine": "assertion"}, TypeError, "engine"),
        ({"owner_kind": sessions.LiveOccurrenceKind.COMPARISON}, ValueError, "contradicts"),
        ({"owner_id": ""}, ValueError, "non-empty"),
        ({"fact_id": ""}, ValueError, "non-empty"),
        ({"rule_id": ""}, ValueError, "non-empty"),
        ({"effective_rule_id": ""}, ValueError, "non-empty"),
        ({"severity": ""}, ValueError, "non-empty"),
        ({"outcome": "satisfied"}, TypeError, "outcome"),
        ({"counts": []}, TypeError, "metrics"),
        ({"counts": (object(),)}, TypeError, "metrics"),
        ({"counts": (metric, metric)}, ValueError, "canonical"),
        (
            {"counts": (metric, msgspec.structs.replace(metric, name="before"))},
            ValueError,
            "canonical",
        ),
    )
    for changes, error, message in fact_cases:
        with pytest.raises(error, match=message):
            msgspec.structs.replace(fact, **changes)
    diff = msgspec.structs.replace(
        fact,
        owner_kind=sessions.LiveOccurrenceKind.COMPARISON,
        engine=RuntimeFactEngine.DIFF,
        outcome=RuntimeFactOutcome.EQUIVALENT,
    )
    assert diff.owner_kind is sessions.LiveOccurrenceKind.COMPARISON

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
        ({"fact_count": -1}, ValueError, "fact count"),
        ({"fact_evaluation_complete": True}, ValueError, "completion marker"),
        (
            {
                "kind": RuntimeEventKind.ASSERTIONS_FINISHED,
                "fact_evaluation_complete": cast("bool", 1),
            },
            TypeError,
            "completion marker",
        ),
        ({"fact_count": 1}, ValueError, "fact count requires"),
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
    fact = _live_fact()
    with pytest.raises(TypeError, match="progress facts"):
        msgspec.structs.replace(valid, facts=cast("tuple[Any, ...]", [fact]))
    with pytest.raises(ValueError, match="fact identities"):
        msgspec.structs.replace(valid, facts=(fact, fact))
    with pytest.raises(ValueError, match="omitted"):
        msgspec.structs.replace(valid, omitted_occurrences=-1)
    with pytest.raises(ValueError, match="fact omission"):
        msgspec.structs.replace(valid, omitted_facts=-1)
    with pytest.raises(ValueError, match="fact omission"):
        msgspec.structs.replace(valid, incomplete_fact_scopes=-1)
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


def test_mailbox_projects_and_bounds_live_fact_summaries(tmp_path: Path) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        mailbox = sessions._ProgressMailbox(
            _snapshot(),
            limits=sessions.ExecutionSessionLimits(
                maximum_occurrences=2,
                maximum_recent_events=4,
                maximum_facts=1,
            ),
        )
        event = msgspec.structs.replace(
            _event(request, 1, RuntimeEventKind.ASSERTIONS_FINISHED),
            facts=(_fact(), _fact(ordinal=2)),
            fact_evaluation_complete=False,
        )

        await mailbox.emit(event)
        await mailbox.emit(
            msgspec.structs.replace(
                event,
                sequence=Count(value=2),
                facts=(_fact(),),
                fact_evaluation_complete=True,
            )
        )

        snapshot = mailbox.snapshot
        assert len(snapshot.facts) == 1
        assert event.execution_id is not None
        assert snapshot.facts[0].owner_kind is sessions.LiveOccurrenceKind.EXECUTION
        assert snapshot.facts[0].owner_id == str(event.execution_id.value)
        assert snapshot.facts[0].counts == (sessions.LiveFactMetric(name="evaluated", value=1),)
        assert snapshot.omitted_facts == 1
        assert snapshot.incomplete_fact_scopes == 1
        assert snapshot.recent_events[-1].fact_count == 1
        assert snapshot.recent_events[-1].fact_evaluation_complete is True

        comparison = msgspec.structs.replace(
            _event(request, 3, RuntimeEventKind.COMPARISON_FINISHED),
            facts=(_fact(RuntimeFactEngine.DIFF, ordinal=3),),
        )
        await mailbox.emit(comparison)
        assert mailbox.snapshot.omitted_facts == 2

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
        assert session.task_cancelled is False
        summary = snapshots[-1].final_summary
        assert summary is not None
        assert summary.completion == result.artifact.completion.kind.value
        assert summary.assertions == 1
        assert summary.diffs == 1
        assert summary.adverse_facts == 0
        assert await session.after(snapshots[-1].revision) is None

    asyncio.run(scenario())
