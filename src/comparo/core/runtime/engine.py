"""Offline runtime composition from admitted work through pure evaluation and gating."""

from __future__ import annotations

from typing import cast
from uuid import uuid5

import msgspec

from comparo.core.assertions.engine import evaluate_assertion
from comparo.core.diffs.engine import evaluate_diff
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.gates.model import GateEvaluation
from comparo.core.domain.gates.normalization import normalize_assertion_fact
from comparo.core.domain.gates.normalization import normalize_diff_fact
from comparo.core.domain.gates.summaries import AssertionGateSummary
from comparo.core.domain.gates.summaries import DiffGateSummary
from comparo.core.domain.gates.summaries import GateEnvironmentExecution
from comparo.core.domain.gates.summaries import NormalizedFactSummary
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.lifecycle import PartialRunReason
from comparo.core.domain.lifecycle import RunCancelled
from comparo.core.domain.lifecycle import RunComplete
from comparo.core.domain.lifecycle import RunCompletion
from comparo.core.domain.lifecycle import RunInternalError
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.primitives import Count
from comparo.core.gates.engine import evaluate_gate
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.resolution.ports import RunClock
from comparo.core.runtime.models import ComparisonRecord
from comparo.core.runtime.models import ComparisonWork
from comparo.core.runtime.models import EvaluatedComparison
from comparo.core.runtime.models import ExecutionRecord
from comparo.core.runtime.models import IncompleteComparison
from comparo.core.runtime.models import ObservedExecution
from comparo.core.runtime.models import RunResult
from comparo.core.runtime.models import RuntimeEvent
from comparo.core.runtime.models import RuntimeEventKind
from comparo.core.runtime.ports import EvaluationPreparation
from comparo.core.runtime.ports import ExecutionDriver
from comparo.core.runtime.ports import RuntimeEventSink
from comparo.core.runtime.scheduling import schedule_executions

_CLEANUP_FAILED = DiagnosticCode(value="runtime.cleanup.failed")


async def run_prepared(
    prepared: PreparedRun,
    *,
    driver: ExecutionDriver,
    evaluation: EvaluationPreparation,
    cancellation: RunCancellation,
    clock: RunClock,
    events: RuntimeEventSink,
) -> RunResult:
    """Execute one admitted run without importing CLI, TUI, reporting, or network adapters."""
    if type(prepared) is not PreparedRun:
        raise TypeError("runtime engine requires PreparedRun")
    if type(cancellation) is not RunCancellation:
        raise TypeError("runtime engine requires RunCancellation")
    cleanup_failed = False
    try:
        scheduled = await schedule_executions(prepared, driver, cancellation, events)
        records, assertion_incomplete = await _evaluate_assertions(
            prepared,
            scheduled.records,
            evaluation,
        )
        comparisons, diff_incomplete, next_sequence = await _evaluate_comparisons(
            prepared,
            records,
            evaluation,
            events,
            scheduled.next_event_sequence.value,
        )
    finally:
        try:
            await driver.close()
        except Exception:  # noqa: BLE001 - cleanup defects become a value-free run outcome
            cleanup_failed = True
    completion = _completion(
        records,
        cancellation,
        clock,
        evaluation_incomplete=assertion_incomplete or diff_incomplete,
        cleanup_failed=cleanup_failed,
    )
    gate = _gate(evaluation, records, comparisons, completion, prepared)
    await events.emit(
        RuntimeEvent(
            sequence=Count(value=next_sequence),
            run_id=prepared.plan.run_id,
            kind=RuntimeEventKind.RUN_FINISHED,
        )
    )
    return RunResult(
        run_id=prepared.plan.run_id,
        completion=completion,
        executions=records,
        comparisons=comparisons,
        gate=gate,
        ledgers=scheduled.ledgers,
    )


async def _evaluate_assertions(
    prepared: PreparedRun,
    records: tuple[ExecutionRecord, ...],
    evaluation: EvaluationPreparation,
) -> tuple[tuple[ExecutionRecord, ...], bool]:
    evaluated: list[ExecutionRecord] = []
    incomplete = False
    for record in records:
        if type(record.outcome) is not ObservedExecution:
            evaluated.append(record)
            continue
        try:
            calls = await evaluation.assertions(prepared, record)
            if type(calls) is not tuple:
                raise TypeError("assertion preparation must return an immutable tuple")
            assertions = tuple(
                evaluate_assertion(
                    record.outcome.snapshot,
                    call.application,
                    call.rule,
                    call.inputs,
                    call.control,
                )
                for call in calls
            )
        except Exception:  # noqa: BLE001 - preparation/evaluation defects are contained per run
            assertions = ()
            incomplete = True
        evaluated.append(msgspec.structs.replace(record, assertions=assertions))
    return tuple(evaluated), incomplete


async def _evaluate_comparisons(
    prepared: PreparedRun,
    records: tuple[ExecutionRecord, ...],
    evaluation: EvaluationPreparation,
    events: RuntimeEventSink,
    sequence: int,
) -> tuple[tuple[ComparisonRecord, ...], bool, int]:
    by_execution = {record.work.scope.execution_id: record for record in records}
    comparisons: list[ComparisonRecord] = []
    incomplete = False
    for work in _comparison_work(prepared, records):
        baseline = by_execution[work.baseline_execution_id]
        candidate = by_execution[work.candidate_execution_id]
        missing = tuple(
            record.work.scope.execution_id
            for record in (baseline, candidate)
            if type(record.outcome) is not ObservedExecution
        )
        if missing:
            outcome: IncompleteComparison | EvaluatedComparison = IncompleteComparison(
                unavailable_executions=missing
            )
        else:
            try:
                calls = await evaluation.diffs(
                    prepared,
                    work,
                    baseline,
                    candidate,
                )
                if type(calls) is not tuple:
                    raise TypeError("diff preparation must return an immutable tuple")
                left = cast(ObservedExecution, baseline.outcome).snapshot
                right = cast(ObservedExecution, candidate.outcome).snapshot
                diffs = tuple(
                    evaluate_diff(
                        left,
                        right,
                        call.application,
                        call.rule,
                        call.inputs,
                        call.control,
                    )
                    for call in calls
                )
                outcome = EvaluatedComparison(diffs=diffs)
            except Exception:  # noqa: BLE001 - one comparison cannot abort unrelated work
                outcome = EvaluatedComparison(diffs=())
                incomplete = True
        comparisons.append(ComparisonRecord(work=work, outcome=outcome))
        await events.emit(
            RuntimeEvent(
                sequence=Count(value=sequence),
                run_id=prepared.plan.run_id,
                kind=RuntimeEventKind.COMPARISON_FINISHED,
                comparison_id=work.comparison_id,
            )
        )
        sequence += 1
    return tuple(comparisons), incomplete, sequence


def _comparison_work(
    prepared: PreparedRun,
    records: tuple[ExecutionRecord, ...],
) -> tuple[ComparisonWork, ...]:
    by_coordinate = {
        (
            record.work.trial.trial_id,
            record.work.environment.identity,
        ): record
        for record in records
    }
    slices = {plan_slice.cells: plan_slice for plan_slice in prepared.plan.slices}
    trials = tuple(dict.fromkeys(record.work.trial for record in records))
    result: list[ComparisonWork] = []
    for trial in trials:
        plan_slice = slices[trial.cell.recipe]
        for pair in plan_slice.comparisons:
            baseline = by_coordinate.get((trial.trial_id, pair.baseline.identity))
            candidate = by_coordinate.get((trial.trial_id, pair.candidate.identity))
            if baseline is None or candidate is None:
                continue
            comparison_id = ComparisonId(
                value=uuid5(
                    prepared.plan.run_id.value,
                    "comparison:"
                    f"{trial.trial_id.value}:"
                    f"{baseline.work.scope.execution_id.value}:"
                    f"{candidate.work.scope.execution_id.value}",
                )
            )
            result.append(
                ComparisonWork(
                    ordinal=Count(value=len(result)),
                    comparison_id=comparison_id,
                    subject=trial.cell.recipe.subject,
                    trial=trial,
                    pair=pair,
                    baseline_execution_id=baseline.work.scope.execution_id,
                    candidate_execution_id=candidate.work.scope.execution_id,
                )
            )
    return tuple(result)


def _completion(
    records: tuple[ExecutionRecord, ...],
    cancellation: RunCancellation,
    clock: RunClock,
    *,
    evaluation_incomplete: bool,
    cleanup_failed: bool,
) -> RunCompletion:
    completed_at = clock.now()
    if cleanup_failed:
        return RunInternalError(
            diagnostic=Diagnostic(
                code=_CLEANUP_FAILED,
                severity=DiagnosticSeverity.ERROR,
                stage=DiagnosticStage.TRANSPORT,
            )
        )
    if cancellation.cancelled:
        return RunCancelled(
            source=cancellation.source or CancellationSource.PARENT_SCOPE,
            stage=_cancellation_stage(cancellation.source),
            cancelled_at=completed_at,
        )
    reasons: list[PartialRunReason] = []
    if any(type(record.outcome) is not ObservedExecution for record in records):
        reasons.append(PartialRunReason.EXECUTION_INCOMPLETE)
    if evaluation_incomplete:
        reasons.append(PartialRunReason.EVALUATION_INCOMPLETE)
    if reasons:
        return RunPartial(reasons=tuple(reasons), completed_at=completed_at)
    return RunComplete(completed_at=completed_at)


def _cancellation_stage(source: CancellationSource | None) -> DiagnosticStage:
    if source is CancellationSource.EXECUTION_POLICY:
        return DiagnosticStage.TRANSPORT
    return DiagnosticStage.TRANSPORT


def _gate(
    evaluation: EvaluationPreparation,
    records: tuple[ExecutionRecord, ...],
    comparisons: tuple[ComparisonRecord, ...],
    completion: RunCompletion,
    prepared: PreparedRun,
) -> GateEvaluation:
    assertion_summaries = tuple(
        AssertionGateSummary(
            rules=tuple(
                rule
                for item in record.assertions
                for rule in normalize_assertion_fact(
                    item.fact,
                    execution=GateEnvironmentExecution(
                        environment=record.work.environment,
                        execution_id=record.work.scope.execution_id,
                        roles=_roles(prepared, record),
                    ),
                ).rules
            )
        )
        for record in records
        if record.assertions
    )
    diff_summaries = tuple(
        DiffGateSummary(
            rules=tuple(
                rule
                for item in comparison.outcome.diffs
                for rule in normalize_diff_fact(item.fact).rules
            )
        )
        for comparison in comparisons
        if type(comparison.outcome) is EvaluatedComparison and comparison.outcome.diffs
    )
    return evaluate_gate(
        evaluation.gate_profile,
        NormalizedFactSummary(
            completion=completion,
            assertions=assertion_summaries,
            diffs=diff_summaries,
        ),
    )


def _roles(prepared: PreparedRun, record: ExecutionRecord) -> tuple[EnvironmentRole, ...]:
    plan_slice = next(
        item for item in prepared.plan.slices if item.cells == record.work.trial.cell.recipe
    )
    identity = record.work.environment.identity
    baseline = any(pair.baseline.identity == identity for pair in plan_slice.comparisons)
    candidate = any(pair.candidate.identity == identity for pair in plan_slice.comparisons)
    roles: list[EnvironmentRole] = []
    if baseline:
        roles.append(EnvironmentRole.BASELINE)
    if candidate:
        roles.append(EnvironmentRole.CANDIDATE)
    if not roles:
        roles.append(EnvironmentRole.OTHER)
    return tuple(roles)
