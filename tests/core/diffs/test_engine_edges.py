"""Exercise public evaluator terminal branches and exact progress preservation."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import msgspec
import pytest

import comparo.core.diffs.engine as diff_engine
from comparo.core.diffs import evaluate_diff
from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import ApplicabilityExpectation
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplicability
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonString
from comparo.core.selection import SelectionControl
from comparo.core.selection import SelectionEvaluation
from comparo.core.selection import SelectionSnapshot
from comparo.core.selection import select as select_snapshot
from tests.core.diffs.factories import application
from tests.core.diffs.factories import control
from tests.core.diffs.factories import derived
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import rule
from tests.core.diffs.factories import snapshots
from tests.core.domain.diffs._invariant_support import _boolean_selector


def _applicable_rule() -> CompiledDiffRule:
    base = rule()
    selector = _boolean_selector()
    return msgspec.structs.replace(
        base,
        applicability=DiffApplicability(
            baseline_selector=selector,
            candidate_selector=selector,
            expectation=ApplicabilityExpectation.TRUE,
        ),
    )


def _ready_boolean(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    selection_control: SelectionControl,
    value: bool,
) -> SelectionEvaluation:
    status = select_snapshot(snapshot, rule().pipeline.baseline.selector, selection_control)
    assert type(status.result) is SelectionReady
    msgspec.structs.force_setattr(status.result, "selector", selector)
    msgspec.structs.force_setattr(status.result.matches[0], "value", JsonBoolean(value=value))
    return status


def _sequence_select(
    evaluations: list[SelectionEvaluation],
) -> Callable[[SelectionSnapshot, CompiledSelector, SelectionControl], SelectionEvaluation]:
    def fake_select(
        snapshot: SelectionSnapshot,
        selector: CompiledSelector,
        selection_control: SelectionControl,
    ) -> SelectionEvaluation:
        del snapshot, selector, selection_control
        return evaluations.pop(0)

    return fake_select


def test_applicability_true_continues_and_false_is_not_evaluated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = snapshots()
    compiled = _applicable_rule()
    diff_control = control()
    selector = cast("DiffApplicability", compiled.applicability).baseline_selector
    main_left = select_snapshot(
        baseline, compiled.pipeline.baseline.selector, diff_control.baseline_selection
    )
    main_right = select_snapshot(
        candidate, compiled.pipeline.candidate.selector, diff_control.candidate_selection
    )
    evaluations = [
        _ready_boolean(baseline, selector, diff_control.baseline_selection, True),
        _ready_boolean(candidate, selector, diff_control.candidate_selection, True),
        main_left,
        main_right,
    ]
    monkeypatch.setattr(diff_engine, "select", _sequence_select(evaluations))
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        diff_control,
    )
    assert type(evaluated.fact.outcome) is Equivalent

    false_evaluations = [
        _ready_boolean(baseline, selector, diff_control.baseline_selection, False),
        _ready_boolean(candidate, selector, diff_control.candidate_selection, True),
    ]
    monkeypatch.setattr(diff_engine, "select", _sequence_select(false_evaluations))
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        diff_control,
    )
    assert type(evaluated.fact.outcome) is NotEvaluated


def test_applicability_selection_failure_and_runtime_contract_are_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = snapshots()
    compiled = _applicable_rule()
    diff_control = control()
    selector = cast("DiffApplicability", compiled.applicability).baseline_selector
    unavailable = SelectionEvaluation(
        result=SelectionFactUnavailable(code=DiagnosticCode(value="diff.test.unavailable")),
        budgets=diff_control.baseline_selection.budgets,
    )
    ready = _ready_boolean(candidate, selector, diff_control.candidate_selection, True)
    monkeypatch.setattr(diff_engine, "select", _sequence_select([unavailable, ready]))
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        diff_control,
    )
    assert type(evaluated.fact.outcome) is Incomplete
    assert evaluated.fact.outcome.reason.stage is DiffEvaluationStage.SELECTION

    invalid = _ready_boolean(baseline, selector, diff_control.baseline_selection, True)
    assert type(invalid.result) is SelectionReady
    invalid_match = invalid.result.matches[0]
    msgspec.structs.force_setattr(invalid_match, "value", JsonString(value="not-boolean"))
    monkeypatch.setattr(diff_engine, "select", _sequence_select([invalid, ready]))
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        diff_control,
    )
    assert type(evaluated.fact.outcome) is DiffEvaluationError
    assert evaluated.fact.outcome.code.value == "diff.applicability.runtime-contract"


def test_applicability_dependency_taint_exhaustion_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = snapshots()
    compiled = _applicable_rule()
    diff_control = control(work=1)
    selector = cast("DiffApplicability", compiled.applicability).baseline_selector
    left = _ready_boolean(baseline, selector, diff_control.baseline_selection, True)
    right = _ready_boolean(candidate, selector, diff_control.candidate_selection, True)
    assert type(left.result) is SelectionReady
    msgspec.structs.force_setattr(
        left.result.matches[0],
        "value",
        SensitiveValue(
            JsonBoolean(value=True),
            origins=(
                TaintOriginId(value="diff-applicability-first"),
                TaintOriginId(value="diff-applicability-second"),
            ),
        ),
    )
    monkeypatch.setattr(diff_engine, "select", _sequence_select([left, right]))
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        diff_control,
    )
    assert type(evaluated.fact.outcome) is Incomplete
    assert evaluated.fact.outcome.reason.stage is DiffEvaluationStage.SELECTION
    assert evaluated.fact.outcome.reason.cause is DiffIncompleteCause.BUDGET_EXCEEDED


def test_main_selection_failure_and_empty_selection_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        control(selection=0),
    )
    assert type(evaluated.fact.outcome) is Incomplete
    assert evaluated.fact.outcome.reason.stage is DiffEvaluationStage.SELECTION

    diff_control = control()
    left = select_snapshot(
        baseline, compiled.pipeline.baseline.selector, diff_control.baseline_selection
    )
    right = select_snapshot(
        candidate, compiled.pipeline.candidate.selector, diff_control.candidate_selection
    )
    assert type(left.result) is SelectionReady
    assert type(right.result) is SelectionReady
    msgspec.structs.force_setattr(left.result, "matches", ())
    msgspec.structs.force_setattr(right.result, "matches", ())
    monkeypatch.setattr(diff_engine, "select", _sequence_select([left, right]))
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        diff_control,
    )
    assert type(evaluated.fact.outcome) is NotEvaluated
    assert evaluated.fact.outcome.reason.value == "empty-selection"


def test_empty_derivation_and_partial_pairing_preserve_exact_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    bound = application(compiled, baseline, candidate)

    def empty_transforms(
        values: tuple[DerivedValue, ...],
        *args: object,
    ) -> tuple[DerivedValue, ...]:
        del values, args
        return ()

    monkeypatch.setattr(diff_engine, "apply_transforms", empty_transforms)
    evaluated = evaluate_diff(baseline, candidate, bound, compiled, inputs(compiled), control())
    assert type(evaluated.fact.outcome) is NotEvaluated
    assert evaluated.fact.outcome.reason.value == "empty-derivation"

    monkeypatch.undo()
    partial = PairUnit(
        baseline=derived(JsonBoolean(value=True)),
        candidate=derived(JsonBoolean(value=True), candidate=True),
    )

    def halt_pairing(
        left: tuple[DerivedValue, ...],
        right: tuple[DerivedValue, ...],
        pairing: object,
        diff_meter: DiffMeter,
    ) -> tuple[PairUnit, ...]:
        del left, right, pairing
        failure = diff_meter.charge_work(200_000)
        assert failure is not None
        raise DiffHaltError(exhaustion=failure, partial_pairs=(partial,))

    monkeypatch.setattr(diff_engine, "pair_values", halt_pairing)
    evaluated = evaluate_diff(baseline, candidate, bound, compiled, inputs(compiled), control())
    assert type(evaluated.fact.outcome) is Incomplete
    assert evaluated.fact.outcome.reason.stage is DiffEvaluationStage.PAIRING
    assert evaluated.fact.coverage.paired.value == 1
    assert evaluated.fact.coverage.not_evaluated.value == 1


def test_derivation_halts_preserve_candidate_progress_and_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    bound = application(compiled, baseline, candidate)
    calls = 0

    def candidate_error(
        values: tuple[DerivedValue, ...],
        *args: object,
    ) -> tuple[DerivedValue, ...]:
        nonlocal calls
        del args
        calls += 1
        if calls == 1:
            return values
        raise DiffHaltError(
            code=DiagnosticCode(value="diff.test.candidate-derivation"),
            partial_derived=values,
            side=DiffSide.CANDIDATE,
        )

    monkeypatch.setattr(diff_engine, "apply_transforms", candidate_error)
    evaluated = evaluate_diff(baseline, candidate, bound, compiled, inputs(compiled), control())
    assert type(evaluated.fact.outcome) is DiffEvaluationError
    assert evaluated.fact.coverage.baseline_derived.value == 1
    assert evaluated.fact.coverage.candidate_derived.value == 1

    def unavailable(
        values: tuple[DerivedValue, ...],
        *args: object,
    ) -> tuple[DerivedValue, ...]:
        del values, args
        raise DiffHaltError(
            code=DiagnosticCode(value="diff.test.derived-unavailable"),
            incomplete_cause=DiffIncompleteCause.UNAVAILABLE,
            side=DiffSide.BASELINE,
        )

    monkeypatch.setattr(diff_engine, "apply_transforms", unavailable)
    evaluated = evaluate_diff(baseline, candidate, bound, compiled, inputs(compiled), control())
    assert type(evaluated.fact.outcome) is Incomplete
    assert evaluated.fact.outcome.reason.stage is DiffEvaluationStage.DERIVATION
    assert evaluated.fact.outcome.reason.cause is DiffIncompleteCause.UNAVAILABLE
    assert evaluated.fact.outcome.reason.side is DiffSide.BASELINE


def test_diff_evaluation_rejects_every_untyped_component() -> None:
    baseline, candidate = snapshots()
    compiled = rule()
    evaluated = evaluate_diff(
        baseline,
        candidate,
        application(compiled, baseline, candidate),
        compiled,
        inputs(compiled),
        control(),
    )
    invalid: tuple[tuple[str, object, str], ...] = (
        ("fact", object(), "DiffRuleFact"),
        ("baseline_selection", object(), "baseline SelectionBudgets"),
        ("candidate_selection", object(), "candidate SelectionBudgets"),
        ("budgets", object(), "DiffBudgets"),
    )
    for field, value, message in invalid:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(
                evaluated,
                **{field: cast("DiffRuleFact", value)},
            )
