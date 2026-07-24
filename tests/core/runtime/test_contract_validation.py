"""Defensive validation tests for public runtime contracts."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import Count
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import AssertionCall
from comparo.core.runtime import BlockedExecution
from comparo.core.runtime import CancelledExecution
from comparo.core.runtime import ComparisonRecord
from comparo.core.runtime import DiffCall
from comparo.core.runtime import EvaluatedComparison
from comparo.core.runtime import ExecutionRecord
from comparo.core.runtime import HttpTransportResult
from comparo.core.runtime import IncompleteComparison
from comparo.core.runtime import ObservedExecution
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime import RequestPreparationFailure
from comparo.core.runtime import RunResult
from comparo.core.runtime import RuntimeEvent
from comparo.core.runtime import RuntimeEventKind
from comparo.core.runtime import UnavailableExecution
from comparo.core.runtime import run_prepared
from comparo.core.runtime.models import is_execution_outcome
from tests.core.assertions import factories as assertion_factories
from tests.core.diffs import factories as diff_factories
from tests.core.domain.http.factories import definition
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import response
from tests.core.runtime.conftest import FixedClock
from tests.core.runtime.test_kernel_integration import KernelPreparation
from tests.core.runtime.test_vertical_runtime import _driver


@pytest.fixture
def runtime_result(prepared_comparison_run: PreparedRun) -> RunResult:
    """Return a complete result carrying real assertion and diff evaluations."""
    cancellation = RunCancellation()
    driver, _transport = _driver(prepared_comparison_run, cancellation)
    return asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=KernelPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=RecordingEventSink(),
        )
    )


def test_execution_work_and_outcome_contracts_reject_contradictions(
    runtime_result: RunResult,
) -> None:
    work = runtime_result.executions[0].work
    other = runtime_result.executions[1]
    observed = cast(ObservedExecution, runtime_result.executions[0].outcome)

    with pytest.raises(TypeError, match="ordinal"):
        msgspec.structs.replace(work, ordinal=0)
    with pytest.raises(ValueError, match="Request or Scenario"):
        msgspec.structs.replace(work, subject=work.environment)
    with pytest.raises(TypeError, match="PlannedTrial"):
        msgspec.structs.replace(work, trial=object())
    with pytest.raises(ValueError, match="planned cell"):
        msgspec.structs.replace(work, subject=definition(ResourceKind.REQUEST, "other"))
    with pytest.raises(ValueError, match="Environment"):
        msgspec.structs.replace(work, environment=work.subject)
    with pytest.raises(TypeError, match="ExecutionScope"):
        msgspec.structs.replace(work, scope=object())
    contradictory_scope = msgspec.structs.replace(
        work.scope,
        environment=other.work.scope.environment,
    )
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(work, scope=contradictory_scope)

    with pytest.raises(TypeError, match="EnvironmentExecutionObservation"):
        ObservedExecution(
            observation=object(),  # type: ignore[arg-type]
            snapshot=observed.snapshot,
        )
    with pytest.raises(TypeError, match="SelectionSnapshot"):
        ObservedExecution(
            observation=observed.observation,
            snapshot=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="observed execution"):
        ObservedExecution(
            observation=observed.observation,
            snapshot=cast(ObservedExecution, other.outcome).snapshot,
        )

    code = DiagnosticCode(value="runtime.test.contract")
    with pytest.raises(TypeError, match="typed stage"):
        UnavailableExecution(stage="transport", code=code)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="typed stage"):
        UnavailableExecution(
            stage=DiagnosticStage.TRANSPORT,
            code="bad",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="typed stage"):
        BlockedExecution(stage="resolution", code=code)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="typed stage"):
        BlockedExecution(
            stage=DiagnosticStage.RESOLUTION,
            code="bad",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="resolution or authorization"):
        BlockedExecution(stage=DiagnosticStage.TRANSPORT, code=code)
    with pytest.raises(TypeError, match="CancellationSource"):
        CancelledExecution(source="user")  # type: ignore[arg-type]
    assert not is_execution_outcome(object())


def test_execution_and_comparison_records_validate_owned_facts(runtime_result: RunResult) -> None:
    first, second = runtime_result.executions[:2]
    comparison = runtime_result.comparisons[0]

    with pytest.raises(TypeError, match="typed work"):
        ExecutionRecord(work=object(), outcome=first.outcome)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="typed work"):
        ExecutionRecord(work=first.work, outcome=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable typed tuple"):
        ExecutionRecord(
            work=first.work,
            outcome=first.outcome,
            assertions=[*first.assertions],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="unobserved"):
        ExecutionRecord(
            work=first.work,
            outcome=UnavailableExecution(
                stage=DiagnosticStage.TRANSPORT,
                code=DiagnosticCode(value="runtime.test.unavailable"),
            ),
            assertions=first.assertions,
        )
    with pytest.raises(ValueError, match="contradicts"):
        ExecutionRecord(work=first.work, outcome=second.outcome)
    with pytest.raises(ValueError, match="another execution"):
        msgspec.structs.replace(first, assertions=second.assertions)

    with pytest.raises(TypeError, match="ComparisonWork"):
        ComparisonRecord(work=object(), outcome=comparison.outcome)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="unsupported outcome"):
        ComparisonRecord(work=comparison.work, outcome=object())  # type: ignore[arg-type]
    foreign_work = msgspec.structs.replace(
        comparison.work,
        comparison_id=ComparisonId(value=UUID(int=991)),
    )
    with pytest.raises(ValueError, match="another comparison"):
        ComparisonRecord(work=foreign_work, outcome=comparison.outcome)


def test_comparison_work_and_outcome_contracts_are_closed(runtime_result: RunResult) -> None:
    work = runtime_result.comparisons[0].work

    with pytest.raises(TypeError, match="Count ordinal"):
        msgspec.structs.replace(work, ordinal=0)
    with pytest.raises(TypeError, match="Count ordinal"):
        msgspec.structs.replace(work, comparison_id=object())
    with pytest.raises(TypeError, match="definition and PlannedTrial"):
        msgspec.structs.replace(work, subject=object())
    with pytest.raises(TypeError, match="definition and PlannedTrial"):
        msgspec.structs.replace(work, trial=object())
    with pytest.raises(ValueError, match="planned cell"):
        msgspec.structs.replace(work, subject=definition(ResourceKind.REQUEST, "other"))
    with pytest.raises(TypeError, match="EnvironmentPair"):
        msgspec.structs.replace(work, pair=object())
    with pytest.raises(TypeError, match="EnvironmentExecutionId"):
        msgspec.structs.replace(work, baseline_execution_id=object())
    with pytest.raises(TypeError, match="EnvironmentExecutionId"):
        msgspec.structs.replace(work, candidate_execution_id=object())
    with pytest.raises(ValueError, match="distinct executions"):
        msgspec.structs.replace(
            work,
            candidate_execution_id=work.baseline_execution_id,
        )

    with pytest.raises(ValueError, match="requires unavailable"):
        IncompleteComparison(unavailable_executions=())
    with pytest.raises(TypeError, match="EnvironmentExecutionId"):
        IncompleteComparison(unavailable_executions=(object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unique"):
        IncompleteComparison(
            unavailable_executions=(
                work.baseline_execution_id,
                work.baseline_execution_id,
            )
        )
    with pytest.raises(TypeError, match="immutable typed tuple"):
        EvaluatedComparison(diffs=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable typed tuple"):
        EvaluatedComparison(diffs=(object(),))  # type: ignore[arg-type]


def test_events_and_run_result_reject_ambiguous_coordinates(runtime_result: RunResult) -> None:
    run_id = runtime_result.run_id
    execution_id = runtime_result.executions[0].work.scope.execution_id
    comparison_id = runtime_result.comparisons[0].work.comparison_id

    with pytest.raises(TypeError, match="Count sequence"):
        RuntimeEvent(sequence=0, run_id=run_id, kind=RuntimeEventKind.RUN_STARTED)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Count sequence"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=object(),  # type: ignore[arg-type]
            kind=RuntimeEventKind.RUN_STARTED,
        )
    with pytest.raises(TypeError, match="kind"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=run_id,
            kind="run-started",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="execution coordinate"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=run_id,
            kind=RuntimeEventKind.EXECUTION_STARTED,
        )
    with pytest.raises(ValueError, match="execution coordinate"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=run_id,
            kind=RuntimeEventKind.RUN_STARTED,
            execution_id=execution_id,
        )
    with pytest.raises(TypeError, match="execution ID"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=run_id,
            kind=RuntimeEventKind.EXECUTION_STARTED,
            execution_id=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="comparison coordinate"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=run_id,
            kind=RuntimeEventKind.COMPARISON_FINISHED,
        )
    with pytest.raises(ValueError, match="comparison coordinate"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=run_id,
            kind=RuntimeEventKind.RUN_FINISHED,
            comparison_id=comparison_id,
        )
    with pytest.raises(TypeError, match="comparison ID"):
        RuntimeEvent(
            sequence=Count(value=0),
            run_id=run_id,
            kind=RuntimeEventKind.COMPARISON_FINISHED,
            comparison_id=object(),  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError, match="RunId"):
        msgspec.structs.replace(runtime_result, run_id=object())
    with pytest.raises(TypeError, match="lifecycle outcome"):
        msgspec.structs.replace(runtime_result, completion=object())
    with pytest.raises(TypeError, match="run executions"):
        msgspec.structs.replace(runtime_result, executions=[*runtime_result.executions])
    with pytest.raises(TypeError, match="run executions"):
        msgspec.structs.replace(runtime_result, executions=(object(),))
    with pytest.raises(TypeError, match="run comparisons"):
        msgspec.structs.replace(runtime_result, comparisons=[*runtime_result.comparisons])
    with pytest.raises(TypeError, match="run comparisons"):
        msgspec.structs.replace(runtime_result, comparisons=(object(),))
    with pytest.raises(ValueError, match=r"execution.*order"):
        msgspec.structs.replace(
            runtime_result,
            executions=tuple(reversed(runtime_result.executions)),
        )
    with pytest.raises(ValueError, match=r"comparison.*order"):
        msgspec.structs.replace(
            runtime_result,
            comparisons=tuple(reversed(runtime_result.comparisons)),
        )
    with pytest.raises(ValueError, match="another run"):
        msgspec.structs.replace(runtime_result, run_id=RunId(value=UUID(int=992)))
    with pytest.raises(TypeError, match="GateEvaluation"):
        msgspec.structs.replace(runtime_result, gate=object())
    with pytest.raises(TypeError, match="run ledgers"):
        msgspec.structs.replace(runtime_result, ledgers=[*runtime_result.ledgers])
    with pytest.raises(TypeError, match="run ledgers"):
        msgspec.structs.replace(runtime_result, ledgers=(object(),))


def test_port_value_objects_validate_closed_types(runtime_result: RunResult) -> None:
    with pytest.raises(TypeError, match="response"):
        HttpTransportResult(
            response=object(),  # type: ignore[arg-type]
            failure=None,
            timings=exchange_timings(),
        )
    with pytest.raises(TypeError, match="failure"):
        HttpTransportResult(
            response=None,
            failure=object(),  # type: ignore[arg-type]
            timings=exchange_timings(),
        )
    with pytest.raises(ValueError, match="response or failure"):
        HttpTransportResult(response=None, failure=None, timings=exchange_timings())
    with pytest.raises(TypeError, match="timings"):
        HttpTransportResult(
            response=response(),
            failure=None,
            timings=object(),  # type: ignore[arg-type]
        )
    assert (
        HttpTransportResult(
            response=None,
            failure=failure(),
            timings=exchange_timings(),
        ).failure
        is not None
    )

    with pytest.raises(TypeError, match="ExecutionOutcome"):
        RequestPreparationFailure(outcome=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot contain"):
        RequestPreparationFailure(
            outcome=cast(ObservedExecution, runtime_result.executions[0].outcome)
        )


def test_assertion_and_diff_calls_validate_every_kernel_input(runtime_result: RunResult) -> None:
    observed = cast(ObservedExecution, runtime_result.executions[0].outcome)
    assertion_rule = assertion_factories.rule()
    assertion = AssertionCall(
        application=assertion_factories.application(assertion_rule, observed.snapshot),
        rule=assertion_rule,
        inputs=assertion_factories.inputs(assertion_rule, observed.snapshot),
        control=assertion_factories.control(),
    )
    for field in ("application", "rule", "inputs", "control"):
        with pytest.raises(TypeError, match="assertion call"):
            msgspec.structs.replace(assertion, **{field: object()})

    baseline = cast(ObservedExecution, runtime_result.executions[0].outcome)
    candidate = cast(ObservedExecution, runtime_result.executions[1].outcome)
    diff_rule = diff_factories.rule()
    diff = DiffCall(
        application=diff_factories.application(diff_rule, baseline.snapshot, candidate.snapshot),
        rule=diff_rule,
        inputs=diff_factories.inputs(diff_rule),
        control=diff_factories.control(),
    )
    for field in ("application", "rule", "inputs", "control"):
        with pytest.raises(TypeError, match="diff call"):
            msgspec.structs.replace(diff, **{field: object()})
