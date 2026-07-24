"""End-to-end contracts for the public pure directed diff evaluator."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs import PreparedExpression
from comparo.core.diffs import PreparedOperandError
from comparo.core.diffs import PreparedOperandReady
from comparo.core.diffs import PreparedOperandUnavailable
from comparo.core.diffs import evaluate_diff
from comparo.core.diffs.inputs import PreparedOperandResult
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.operations import DiffOperationFamily
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import DiffPipeline
from comparo.core.domain.diffs.strategies import IgnoreComparator
from comparo.core.domain.diffs.strategies import NumericToleranceComparator
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Count
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonInteger
from tests.core.diffs.factories import application
from tests.core.diffs.factories import control
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import rule
from tests.core.diffs.factories import snapshots
from tests.core.domain.diffs._invariant_support import _literal
from tests.core.domain.diffs._invariant_support import _operation


def test_exact_engine_is_directed_deterministic_and_preserves_provenance() -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    bound = application(compiled, baseline, candidate)

    first = evaluate_diff(baseline, candidate, bound, compiled, inputs(compiled), control())
    second = evaluate_diff(baseline, candidate, bound, compiled, inputs(compiled), control())

    assert type(first.fact.outcome) is Equivalent
    assert first.fact.counts.same.value == 1
    assert first.fact.coverage.baseline_selected.value == 1
    assert first.fact.fact_id == second.fact.fact_id
    assert first.fact.evidence.retained[0].fact_id == second.fact.evidence.retained[0].fact_id
    detail = first.fact.evidence.retained[0]
    assert detail.outcome is DiffFactOutcome.SAME
    assert detail.baseline is not None
    assert detail.candidate is not None
    assert detail.baseline.selector == compiled.pipeline.baseline.selector
    assert (
        detail.baseline.locations[0].environment_execution_id == baseline.observation.execution_id
    )
    assert (
        detail.candidate.locations[0].environment_execution_id == candidate.observation.execution_id
    )


def test_exact_engine_reports_directed_changed_fact() -> None:
    baseline, candidate = snapshots(candidate_status=201)
    compiled = rule()
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        control(),
    )

    assert type(evaluated.fact.outcome) is Drifted
    assert evaluated.fact.counts.changed.value == 1
    assert evaluated.fact.evidence.retained[0].outcome is DiffFactOutcome.CHANGED


def test_ignore_comparator_produces_all_ignored_not_equivalent() -> None:
    baseline, candidate = snapshots(candidate_status=201)
    base = rule()
    compiled = msgspec.structs.replace(
        base,
        pipeline=DiffPipeline(
            baseline=base.pipeline.baseline,
            candidate=base.pipeline.candidate,
            pairing=base.pipeline.pairing,
            comparator=IgnoreComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "ignore")
            ),
        ),
    )
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        control(),
    )

    assert type(evaluated.fact.outcome) is NotEvaluated
    assert evaluated.fact.counts.ignored.value == 1


def test_numeric_tolerance_uses_shared_prepared_operand() -> None:
    baseline, candidate = snapshots(baseline_status=200, candidate_status=202)
    expression = LiteralExpr(
        value=JsonInteger(value=2),
        provenance=_literal().provenance,
    )
    base = rule()
    comparator = NumericToleranceComparator(
        identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
        absolute_tolerance=expression,
    )
    compiled = msgspec.structs.replace(
        base,
        pipeline=msgspec.structs.replace(base.pipeline, comparator=comparator),
    )
    prepared = PreparedDiffInputs(
        rule_fingerprint=compiled.fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        shared_expressions=(
            PreparedExpression(
                expression=expression,
                result=PreparedOperandReady(value=JsonInteger(value=2)),
            ),
        ),
    )

    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        prepared,
        control(),
    )
    assert type(evaluated.fact.outcome) is Equivalent


@pytest.mark.parametrize(
    ("terminal", "outcome_type"),
    [
        (
            PreparedOperandUnavailable(code=DiagnosticCode(value="diff.tolerance.unavailable")),
            Incomplete,
        ),
        (
            PreparedOperandError(code=DiagnosticCode(value="diff.tolerance.error")),
            DiffEvaluationError,
        ),
    ],
)
def test_tolerance_terminal_states_do_not_consume_fact_capacity(
    terminal: PreparedOperandResult,
    outcome_type: type[object],
) -> None:
    baseline, candidate = snapshots()
    expression = LiteralExpr(value=JsonInteger(value=1), provenance=_literal().provenance)
    base = rule()
    compiled = msgspec.structs.replace(
        base,
        pipeline=msgspec.structs.replace(
            base.pipeline,
            comparator=NumericToleranceComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
                absolute_tolerance=expression,
            ),
        ),
    )
    prepared = PreparedDiffInputs(
        rule_fingerprint=compiled.fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        shared_expressions=(PreparedExpression(expression=expression, result=terminal),),
    )
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        prepared,
        control(),
    )
    assert type(evaluated.fact.outcome) is outcome_type
    consumed = evaluated.budgets.facts.usage.consumed
    assert isinstance(consumed, Count)
    assert consumed.value == 0
    if type(evaluated.fact.outcome) is Incomplete:
        assert evaluated.fact.outcome.reason.cause is DiffIncompleteCause.UNAVAILABLE


def test_sensitive_tolerance_is_recorded_as_value_free_dependency_taint() -> None:
    baseline, candidate = snapshots()
    expression = LiteralExpr(value=JsonInteger(value=1), provenance=_literal().provenance)
    origin = TaintOriginId(value="diff-tolerance-secret")
    base = rule()
    compiled = msgspec.structs.replace(
        base,
        pipeline=msgspec.structs.replace(
            base.pipeline,
            comparator=NumericToleranceComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
                absolute_tolerance=expression,
            ),
        ),
    )
    prepared = PreparedDiffInputs(
        rule_fingerprint=compiled.fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        shared_expressions=(
            PreparedExpression(
                expression=expression,
                result=PreparedOperandReady(
                    value=SensitiveValue(JsonInteger(value=1), origins=(origin,))
                ),
            ),
        ),
    )
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        prepared,
        control(),
    )
    assert evaluated.fact.dependency_taint_origins == (origin,)
    assert evaluated.fact.evidence.retained[0].dependency_taint_origins == (origin,)


def test_runtime_invalid_tolerance_is_an_evaluation_error() -> None:
    baseline, candidate = snapshots()
    expression = LiteralExpr(
        value=JsonInteger(value=2),
        provenance=_literal().provenance,
    )
    base = rule()
    compiled = msgspec.structs.replace(
        base,
        pipeline=msgspec.structs.replace(
            base.pipeline,
            comparator=NumericToleranceComparator(
                identity=_operation(DiffOperationFamily.COMPARATOR, "numeric-tolerance"),
                absolute_tolerance=expression,
            ),
        ),
    )
    prepared = PreparedDiffInputs(
        rule_fingerprint=compiled.fingerprint,
        comparison_id=ComparisonId(value=UUID(int=61)),
        baseline_execution_id=EnvironmentExecutionId(value=UUID(int=62)),
        candidate_execution_id=EnvironmentExecutionId(value=UUID(int=63)),
        shared_expressions=(
            PreparedExpression(
                expression=expression,
                result=PreparedOperandReady(value=JsonInteger(value=-1)),
            ),
        ),
    )

    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        prepared,
        control(),
    )
    assert type(evaluated.fact.outcome) is DiffEvaluationError
    assert evaluated.fact.outcome.code.value == "diff.comparator.tolerance-negative"
    assert evaluated.fact.coverage.baseline_derived.value == 1


def test_evidence_budget_truncates_without_changing_exact_counts() -> None:
    baseline, candidate = snapshots(candidate_status=201)
    compiled = rule()
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        control(evidence=0),
    )

    assert evaluated.fact.counts.changed.value == 1
    assert evaluated.fact.evidence.retained == ()
    assert evaluated.fact.evidence.omitted.value == 1
    assert evaluated.fact.evidence.exhaustion is not None
    assert isinstance(evaluated.fact.evidence.exhaustion.attempted, Count)
    assert evaluated.fact.evidence.exhaustion.attempted.value == 1


@pytest.mark.parametrize(
    ("resource", "stage"),
    [("derived", "derivation"), ("pairs", "pairing"), ("facts", "comparison")],
)
def test_evaluation_budget_exhaustion_is_exact_and_incomplete(resource: str, stage: str) -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        {
            "derived": control(derived=0),
            "pairs": control(pairs=0),
            "facts": control(facts=0),
        }[resource],
    )

    assert type(evaluated.fact.outcome) is Incomplete
    assert evaluated.fact.outcome.reason.stage is DiffEvaluationStage(stage)
    assert evaluated.fact.outcome.reason.cause is DiffIncompleteCause.BUDGET_EXCEEDED
    assert evaluated.fact.outcome.issues[0].exhaustion is not None
    assert evaluated.fact.coverage.baseline_selected.value == 1


def test_call_boundary_cancellation_prevents_selection() -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        control(cancelled=True),
    )

    assert type(evaluated.fact.outcome) is Incomplete
    assert evaluated.fact.outcome.reason.stage is DiffEvaluationStage.EXECUTION
    assert evaluated.fact.outcome.reason.cause is DiffIncompleteCause.CANCELLED
    consumed = evaluated.baseline_selection.work.usage.consumed
    assert isinstance(consumed, Count)
    assert consumed.value == 0


def test_public_boundary_rejects_mismatched_snapshots_applications_and_inputs() -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    bound = application(compiled, baseline, candidate)
    with pytest.raises(ValueError, match="candidate snapshot"):
        evaluate_diff(baseline, baseline, bound, compiled, inputs(compiled), control())
    with pytest.raises(ValueError, match="compiled rule"):
        evaluate_diff(
            baseline,
            candidate,
            msgspec.structs.replace(
                bound,
                effective_id=msgspec.structs.replace(bound.effective_id, value="other"),
            ),
            compiled,
            inputs(compiled),
            control(),
        )
    with pytest.raises(ValueError, match="different compiled rule"):
        evaluate_diff(
            baseline,
            candidate,
            bound,
            compiled,
            msgspec.structs.replace(
                inputs(compiled),
                rule_fingerprint=msgspec.structs.replace(
                    compiled.fingerprint,
                    digest=msgspec.structs.replace(
                        compiled.fingerprint.digest,
                        hex_value="7" * 64,
                    ),
                ),
            ),
            control(),
        )
    with pytest.raises(ValueError, match="directed pair frames"):
        evaluate_diff(
            baseline,
            candidate,
            bound,
            compiled,
            msgspec.structs.replace(
                inputs(compiled),
                baseline_execution_id=candidate.observation.execution_id,
                candidate_execution_id=baseline.observation.execution_id,
            ),
            control(),
        )
    with pytest.raises(ValueError, match="directed pair frames"):
        evaluate_diff(
            baseline,
            candidate,
            bound,
            compiled,
            msgspec.structs.replace(
                inputs(compiled),
                comparison_id=ComparisonId(value=UUID(int=999)),
            ),
            control(),
        )


def test_snapshot_execution_ids_are_distinct_in_factory() -> None:
    baseline, candidate = snapshots()
    assert baseline.observation.execution_id == EnvironmentExecutionId(value=UUID(int=62))
    assert candidate.observation.execution_id == EnvironmentExecutionId(value=UUID(int=63))
