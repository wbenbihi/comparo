"""Nested applicability conditions and decisive quantifier behavior."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.applicability import ConditionResult
from comparo.core.assertions.applicability import ConditionState
from comparo.core.assertions.applicability import _evaluate_ready
from comparo.core.assertions.applicability import evaluate_condition
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.inputs import PreparedExpression
from comparo.core.assertions.inputs import PreparedOperandError
from comparo.core.assertions.inputs import PreparedOperandReady
from comparo.core.assertions.inputs import PreparedOperandUnavailable
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.rules import AllConditions
from comparo.core.domain.assertions.rules import AnyConditions
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.selection import select
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.domain.assertions.test_contracts import _literal
from tests.core.domain.selectors.test_result_coverage import _body_partial
from tests.core.selection.test_http_evaluator_coverage import _selector as _http_selector


def test_condition_result_invariants_cover_terminal_state_contract() -> None:
    code = DiagnosticCode(value="condition.failed")
    assert ConditionResult(state=ConditionState.APPLICABLE).code is None
    assert ConditionResult(state=ConditionState.FALSE).reason is None
    assert ConditionResult(state=ConditionState.ERROR, code=code).state is ConditionState.ERROR
    assert (
        ConditionResult(
            state=ConditionState.UNAVAILABLE,
            code=code,
            reason=AssertionUnavailableReason.OPERAND,
        ).reason
        is AssertionUnavailableReason.OPERAND
    )
    with pytest.raises(TypeError, match="state must be typed"):
        ConditionResult(state=cast("ConditionState", "bad"))
    with pytest.raises(ValueError, match="require a code"):
        ConditionResult(state=ConditionState.ERROR)
    with pytest.raises(ValueError, match="require a reason"):
        ConditionResult(state=ConditionState.UNAVAILABLE, code=code)
    with pytest.raises(TypeError, match="code must be"):
        ConditionResult(state=ConditionState.ERROR, code=cast("DiagnosticCode", "bad"))
    with pytest.raises(TypeError, match="reason must be typed"):
        ConditionResult(
            state=ConditionState.UNAVAILABLE,
            code=code,
            reason=cast("AssertionUnavailableReason", "bad"),
        )
    with pytest.raises(TypeError, match="exhaustion must be BudgetExceeded"):
        ConditionResult(
            state=ConditionState.UNAVAILABLE,
            code=code,
            reason=AssertionUnavailableReason.BUDGET_EXCEEDED,
            exhaustion=cast("object", object()),
        )
    with pytest.raises(ValueError, match="exactly budget-exceeded"):
        ConditionResult(
            state=ConditionState.UNAVAILABLE,
            code=code,
            reason=AssertionUnavailableReason.BUDGET_EXCEEDED,
        )


def _condition() -> PredicateCondition:
    compiled = rule()
    return PredicateCondition(
        selector=compiled.selector,
        operator_id=compiled.operator_id,
        operator=compiled.operator,
        quantifier=Quantifier.ONE,
    )


def test_applicability_none_nested_short_circuit_and_budget_paths() -> None:
    prepared_snapshot = snapshot()
    prepared_inputs = inputs()
    normal = control()
    meter = AssertionMeter(normal.budgets)
    applicable, same_budgets = evaluate_condition(
        None,
        prepared_snapshot,
        prepared_inputs,
        normal.selection,
        normal.selection.budgets,
        meter,
    )
    assert applicable.state is ConditionState.APPLICABLE
    assert same_budgets == normal.selection.budgets

    false_operator = msgspec.structs.replace(
        rule().operator,
        expected=_literal(JsonInteger(value=999)),
    )
    false_condition = msgspec.structs.replace(_condition(), operator=false_operator)
    conditional_inputs = msgspec.structs.replace(
        prepared_inputs,
        expressions=(
            *prepared_inputs.expressions,
            PreparedExpression(
                expression=false_operator.expected,
                result=PreparedOperandReady(value=JsonInteger(value=999)),
            ),
        ),
    )
    all_result, _ = evaluate_condition(
        AllConditions(conditions=(false_condition, _condition())),
        prepared_snapshot,
        conditional_inputs,
        normal.selection,
        normal.selection.budgets,
        AssertionMeter(normal.budgets),
    )
    assert all_result.state is ConditionState.FALSE
    any_result, _ = evaluate_condition(
        AnyConditions(conditions=(_condition(), false_condition)),
        prepared_snapshot,
        conditional_inputs,
        normal.selection,
        normal.selection.budgets,
        AssertionMeter(normal.budgets),
    )
    assert any_result.state is ConditionState.APPLICABLE

    no_work = control(work=0)
    exhausted, _ = evaluate_condition(
        _condition(),
        prepared_snapshot,
        prepared_inputs,
        no_work.selection,
        no_work.selection.budgets,
        AssertionMeter(no_work.budgets),
    )
    assert exhausted.state is ConditionState.UNAVAILABLE

    no_selection = control(selection_limit=0)
    unavailable, _ = evaluate_condition(
        _condition(),
        prepared_snapshot,
        prepared_inputs,
        no_selection.selection,
        no_selection.selection.budgets,
        AssertionMeter(no_selection.budgets),
    )
    assert unavailable.state is ConditionState.UNAVAILABLE


def test_applicability_propagates_prepared_operand_states() -> None:
    prepared_snapshot = snapshot()
    base = inputs()
    expression = base.expressions[0].expression
    for result, expected in (
        (
            PreparedOperandUnavailable(code=DiagnosticCode(value="operand.missing")),
            ConditionState.UNAVAILABLE,
        ),
        (
            PreparedOperandError(code=DiagnosticCode(value="operand.invalid")),
            ConditionState.ERROR,
        ),
    ):
        prepared = msgspec.structs.replace(
            base,
            expressions=(PreparedExpression(expression=expression, result=result),),
        )
        evaluated, _ = evaluate_condition(
            _condition(),
            prepared_snapshot,
            prepared,
            control().selection,
            control().selection.budgets,
            AssertionMeter(control().budgets),
        )
        assert evaluated.state is expected


def test_applicability_nested_terminal_precedence_and_fallthrough() -> None:
    prepared_snapshot = snapshot()
    base = inputs()
    expression = base.expressions[0].expression
    normal = control()
    cases = (
        (
            PreparedOperandError(code=DiagnosticCode(value="operand.invalid")),
            ConditionState.ERROR,
        ),
        (
            PreparedOperandUnavailable(code=DiagnosticCode(value="operand.missing")),
            ConditionState.UNAVAILABLE,
        ),
    )
    for operand, expected in cases:
        prepared = msgspec.structs.replace(
            base,
            expressions=(PreparedExpression(expression=expression, result=operand),),
        )
        result, _ = evaluate_condition(
            AnyConditions(conditions=(_condition(), _condition())),
            prepared_snapshot,
            prepared,
            normal.selection,
            normal.selection.budgets,
            AssertionMeter(normal.budgets),
        )
        assert result.state is expected

    false_operator = msgspec.structs.replace(
        rule().operator, expected=_literal(JsonInteger(value=999))
    )
    false_condition = msgspec.structs.replace(_condition(), operator=false_operator)
    false_inputs = msgspec.structs.replace(
        base,
        expressions=(
            PreparedExpression(
                expression=false_operator.expected,
                result=PreparedOperandReady(value=JsonInteger(value=999)),
            ),
        ),
    )
    any_false, _ = evaluate_condition(
        AnyConditions(conditions=(false_condition, false_condition)),
        prepared_snapshot,
        false_inputs,
        normal.selection,
        normal.selection.budgets,
        AssertionMeter(normal.budgets),
    )
    assert any_false.state is ConditionState.FALSE
    all_true, _ = evaluate_condition(
        AllConditions(conditions=(_condition(), _condition())),
        prepared_snapshot,
        base,
        normal.selection,
        normal.selection.budgets,
        AssertionMeter(normal.budgets),
    )
    assert all_true.state is ConditionState.APPLICABLE


def test_applicability_empty_plural_selection_is_cardinality_false() -> None:
    prepared_snapshot = snapshot()
    selector = _http_selector(SelectorSource.RESPONSE_HEADERS, ValueKind.UNKNOWN)
    operator = EqualityOperator(
        relation=EqualityRelation.EQUALS,
        expected=_literal(JsonString(value="missing")),
    )
    condition = PredicateCondition(
        selector=selector,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.EQUALITY,
            version="1",
        ),
        operator=operator,
        quantifier=Quantifier.ALL,
    )
    prepared = PreparedAssertionInputs(
        rule_fingerprint=rule().fingerprint,
        environment_execution_id=prepared_snapshot.observation.execution_id,
        expressions=(
            PreparedExpression(
                expression=operator.expected,
                result=PreparedOperandReady(value=JsonString(value="missing")),
            ),
        ),
    )
    evaluated, _ = evaluate_condition(
        condition,
        prepared_snapshot,
        prepared,
        control().selection,
        control().selection.budgets,
        AssertionMeter(control().budgets),
    )
    assert evaluated.state is ConditionState.FALSE


def test_applicability_quantifier_decisive_match_paths() -> None:
    prepared_snapshot = snapshot()
    base = _condition()
    for quantifier, expected in (
        (Quantifier.ANY, ConditionState.APPLICABLE),
        (Quantifier.NONE, ConditionState.FALSE),
    ):
        result, _ = evaluate_condition(
            msgspec.structs.replace(base, quantifier=quantifier),
            prepared_snapshot,
            inputs(),
            control().selection,
            control().selection.budgets,
            AssertionMeter(control().budgets),
        )
        assert result.state is expected

    false_operator = msgspec.structs.replace(
        rule().operator, expected=_literal(JsonInteger(value=999))
    )
    false_condition = msgspec.structs.replace(
        base,
        operator=false_operator,
        quantifier=Quantifier.ALL,
    )
    false_inputs = PreparedAssertionInputs(
        rule_fingerprint=rule().fingerprint,
        environment_execution_id=prepared_snapshot.observation.execution_id,
        expressions=(
            PreparedExpression(
                expression=false_operator.expected,
                result=PreparedOperandReady(value=JsonInteger(value=999)),
            ),
        ),
    )
    result, _ = evaluate_condition(
        false_condition,
        prepared_snapshot,
        false_inputs,
        control().selection,
        control().selection.budgets,
        AssertionMeter(control().budgets),
    )
    assert result.state is ConditionState.FALSE


def test_applicability_keeps_only_first_of_multiple_match_errors() -> None:
    prepared_snapshot = snapshot()
    selected = select(prepared_snapshot, rule().selector, control().selection)
    assert type(selected.result) is SelectionReady
    ready = msgspec.structs.replace(selected.result)
    msgspec.structs.force_setattr(
        ready,
        "matches",
        (selected.result.matches[0], selected.result.matches[0]),
    )
    inverted = BetweenOperator(
        lower=_literal(JsonInteger(value=3)),
        upper=_literal(JsonInteger(value=2)),
        include_lower=True,
        include_upper=True,
    )
    condition = PredicateCondition(
        selector=rule().selector,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.BETWEEN,
            version="1",
        ),
        operator=inverted,
        quantifier=Quantifier.ALL,
    )
    result = _evaluate_ready(
        condition,
        ready,
        inputs(
            msgspec.structs.replace(rule(), operator_id=condition.operator_id, operator=inverted)
        ),
        prepared_snapshot,
        AssertionMeter(control().budgets),
    )
    assert result.state is ConditionState.ERROR


def test_decode_operator_cannot_be_an_applicability_predicate() -> None:
    partial = _body_partial()
    with pytest.raises(ValueError, match="match predicate operator"):
        PredicateCondition(
            selector=partial.selector,
            operator_id=AssertionOperatorIdentity(
                namespace="comparo.core",
                kind=AssertionOperatorKind.DECODES_AS,
                version="1",
            ),
            operator=DecodeOperator(view=BodyViewKind.TEXT),
            quantifier=Quantifier.ONE,
        )
