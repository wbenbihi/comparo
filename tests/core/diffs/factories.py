"""Shared factories for pure diff engine tests."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec

from comparo.core.diffs import DiffBudgets
from comparo.core.diffs import DiffCancellation
from comparo.core.diffs import DiffControl
from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs import PreparedExpression
from comparo.core.diffs import PreparedOperandReady
from comparo.core.diffs import diff_budget_operations
from comparo.core.diffs import required_expressions
from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.rules import DirectedEnvironmentPair
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.values import ResolvedValue
from comparo.core.selection import SelectionBudgets
from comparo.core.selection import SelectionCancellation
from comparo.core.selection import SelectionControl
from comparo.core.selection import SelectionSnapshot
from comparo.core.selection import selection_budget_operations
from tests.core.assertions.factories import _budget
from tests.core.domain.diffs._invariant_support import _application
from tests.core.domain.diffs._invariant_support import _compiled_rule
from tests.core.domain.diffs._invariant_support import _location
from tests.core.domain.diffs._invariant_support import _selector
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response


def snapshot(*, execution: int, environment: str, status: int = 200) -> SelectionSnapshot:
    """Build one internally consistent terminal response observation."""
    effective = request(environment_id=environment)
    observed = exchange(
        exchange_number=execution,
        execution_number=execution,
        attempt_number=execution,
        effective_request=effective,
        observed_response=response(status=status),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    observation = EnvironmentExecutionObservation(
        execution_id=observed.request.occurrence.attempt.execution.execution_id,
        environment=effective.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=execution)),
                retry=None,
                exchanges=(observed,),
                timings=attempt_timings(),
            ),
        ),
        timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
    )
    return SelectionSnapshot(observation=observation)


def snapshots(
    *, baseline_status: int = 200, candidate_status: int = 200
) -> tuple[SelectionSnapshot, SelectionSnapshot]:
    """Build a canonical prod-to-staging directed pair."""
    return (
        snapshot(execution=62, environment="prod", status=baseline_status),
        snapshot(execution=63, environment="staging", status=candidate_status),
    )


def rule() -> CompiledDiffRule:
    """Build the canonical status exact-comparison rule."""
    return _compiled_rule()


def application(
    compiled: CompiledDiffRule,
    baseline: SelectionSnapshot,
    candidate: SelectionSnapshot,
) -> DiffApplication:
    """Bind a rule to the exact test snapshots."""
    return msgspec.structs.replace(
        _application(),
        definition=compiled.definition,
        effective_id=compiled.effective_id,
        rule_fingerprint=compiled.fingerprint,
        pair=DirectedEnvironmentPair(
            comparison_id=ComparisonId(value=UUID(int=61)),
            baseline_environment=baseline.observation.environment,
            baseline_execution_id=baseline.observation.execution_id,
            candidate_environment=candidate.observation.environment,
            candidate_execution_id=candidate.observation.execution_id,
        ),
    )


def inputs(
    compiled: CompiledDiffRule,
    *,
    baseline_values: dict[object, ResolvedValue] | None = None,
    candidate_values: dict[object, ResolvedValue] | None = None,
    shared_values: dict[object, ResolvedValue] | None = None,
) -> PreparedDiffInputs:
    """Prepare exact expression coverage, with optional per-frame overrides."""
    required = required_expressions(compiled)
    return PreparedDiffInputs(
        rule_fingerprint=compiled.fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        baseline_expressions=_prepared(required.baseline, baseline_values),
        candidate_expressions=_prepared(required.candidate, candidate_values),
        shared_expressions=_prepared(required.shared, shared_values),
    )


def control(
    *,
    selection: int = 100_000,
    derived: int = 100_000,
    derived_bytes: int = 100_000,
    work: int = 100_000,
    pairs: int = 100_000,
    facts: int = 100_000,
    evidence: int = 100_000,
    evidence_bytes: int = 100_000,
    cancelled: bool = False,
) -> DiffControl:
    """Build independent selection and exact diff budget controls."""
    operations = diff_budget_operations()
    return DiffControl(
        baseline_selection=_selection_control(selection),
        candidate_selection=_selection_control(selection),
        budgets=DiffBudgets(
            derived_values=_budget(
                BudgetResource.DERIVED_VALUES,
                BudgetStage.EVALUATION,
                operations[0],
                Count(value=derived),
            ),
            derived_bytes=_budget(
                BudgetResource.DERIVED_BYTES,
                BudgetStage.EVALUATION,
                operations[1],
                ByteCount(value=derived_bytes),
            ),
            transform_work=_budget(
                BudgetResource.TRANSFORM_WORK,
                BudgetStage.EVALUATION,
                operations[2],
                Count(value=work),
            ),
            pairs=_budget(
                BudgetResource.PAIRS,
                BudgetStage.EVALUATION,
                operations[3],
                Count(value=pairs),
            ),
            facts=_budget(
                BudgetResource.FACTS,
                BudgetStage.EVALUATION,
                operations[4],
                Count(value=facts),
            ),
            evidence_examples=_budget(
                BudgetResource.EVIDENCE_EXAMPLES,
                BudgetStage.EVIDENCE,
                operations[5],
                Count(value=evidence),
            ),
            evidence_bytes=_budget(
                BudgetResource.EVIDENCE_BYTES,
                BudgetStage.EVIDENCE,
                operations[6],
                ByteCount(value=evidence_bytes),
            ),
        ),
        cancellation=DiffCancellation(
            cancelled=cancelled,
            code=DiagnosticCode(value="diff.test.cancelled"),
        ),
    )


def meter(
    *,
    derived_limit: int = 100_000,
    derived_bytes: int = 100_000,
    work: int = 100_000,
    pairs: int = 100_000,
    facts: int = 100_000,
    evidence: int = 100_000,
    evidence_bytes: int = 100_000,
) -> DiffMeter:
    """Build a fresh internal meter for focused pure-stage tests."""
    return DiffMeter(
        control(
            derived=derived_limit,
            derived_bytes=derived_bytes,
            work=work,
            pairs=pairs,
            facts=facts,
            evidence=evidence,
            evidence_bytes=evidence_bytes,
        ).budgets
    )


def derived(
    value: SelectedValue,
    *,
    candidate: bool = False,
    selector: CompiledSelector | None = None,
    index: int = 100,
) -> DerivedValue:
    """Build one public untainted stage value with exact root provenance."""
    return DerivedValue(
        value=value,
        selector=_selector() if selector is None else selector,
        origins=(_location(index, candidate=candidate),),
    )


def _selection_control(limit: int) -> SelectionControl:
    operations = selection_budget_operations()
    return SelectionControl(
        budgets=SelectionBudgets(
            work=_budget(
                BudgetResource.SELECTION_WORK,
                BudgetStage.SELECTION,
                operations[0],
                Count(value=limit),
            ),
            matches=_budget(
                BudgetResource.SELECTION_MATCHES,
                BudgetStage.SELECTION,
                operations[1],
                Count(value=limit),
            ),
            result_bytes=_budget(
                BudgetResource.SELECTED_RESULT_BYTES,
                BudgetStage.SELECTION,
                operations[2],
                ByteCount(value=limit),
            ),
        ),
        cancellation=SelectionCancellation(
            cancelled=False,
            code=DiagnosticCode(value="diff.test.selection-cancelled"),
        ),
    )


def _prepared(
    expressions: tuple[object, ...],
    overrides: dict[object, ResolvedValue] | None,
) -> tuple[PreparedExpression, ...]:
    return tuple(
        PreparedExpression(
            expression=cast("LiteralExpr", expression),
            result=PreparedOperandReady(
                value=(
                    cast("LiteralExpr", expression).value
                    if overrides is None
                    else overrides[expression]
                )
            ),
        )
        for expression in expressions
    )
