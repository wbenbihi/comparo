"""Assertion engine boundary validation and terminal result paths."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.assertions.engine import AssertionControl
from comparo.core.assertions.engine import AssertionEvaluation
from comparo.core.assertions.engine import _require_boundary
from comparo.core.assertions.inputs import PreparedExpression
from comparo.core.assertions.inputs import PreparedOperandError
from comparo.core.assertions.inputs import PreparedOperandUnavailable
from comparo.core.assertions.selection_outcomes import decode_state as _decode_state
from comparo.core.assertions.selection_outcomes import meets_completeness as _meets_completeness
from comparo.core.assertions.selection_outcomes import (
    selection_unavailable as _selection_unavailable,
)
from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import ExactValueType
from comparo.core.domain.assertions.operators import TypeOperator
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionErrorScope
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import AllConditions
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.values import JsonString
from comparo.core.selection import select
from comparo.core.selection.accounting import SelectionEvaluation
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.domain.selectors.test_invariants import _json_view_failure
from tests.core.domain.selectors.test_result_coverage import _body_partial
from tests.core.domain.selectors.test_result_coverage import _stream_partial


def _condition() -> PredicateCondition:
    compiled = rule()
    return PredicateCondition(
        selector=compiled.selector,
        operator_id=compiled.operator_id,
        operator=compiled.operator,
        quantifier=Quantifier.ONE,
    )


def test_engine_boundary_records_and_helper_mappings() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    bound = application(compiled, prepared_snapshot)
    prepared_inputs = inputs(compiled, prepared_snapshot)
    normal = control()
    with pytest.raises(TypeError, match="requires SelectionSnapshot"):
        _require_boundary(cast("object", object()), bound, compiled, prepared_inputs, normal)
    with pytest.raises(TypeError, match="application and compiled rule"):
        _require_boundary(
            prepared_snapshot, cast("object", object()), compiled, prepared_inputs, normal
        )
    with pytest.raises(TypeError, match="prepared inputs and control"):
        _require_boundary(prepared_snapshot, bound, compiled, cast("object", object()), normal)
    with pytest.raises(ValueError, match="another application execution"):
        _require_boundary(
            prepared_snapshot,
            msgspec.structs.replace(
                bound,
                environment_execution_id=EnvironmentExecutionId(value=UUID(int=111)),
            ),
            compiled,
            msgspec.structs.replace(
                prepared_inputs,
                environment_execution_id=EnvironmentExecutionId(value=UUID(int=111)),
            ),
            normal,
        )
    with pytest.raises(ValueError, match="another execution"):
        _require_boundary(
            prepared_snapshot,
            bound,
            compiled,
            msgspec.structs.replace(
                prepared_inputs,
                environment_execution_id=EnvironmentExecutionId(value=UUID(int=111)),
            ),
            normal,
        )
    with pytest.raises(ValueError, match="another rule"):
        _require_boundary(
            prepared_snapshot,
            bound,
            compiled,
            msgspec.structs.replace(
                prepared_inputs, rule_fingerprint=bound.environment.fingerprint
            ),
            normal,
        )
    with pytest.raises(ValueError, match="does not identify"):
        _require_boundary(
            prepared_snapshot,
            msgspec.structs.replace(
                bound, effective_id=msgspec.structs.replace(bound.effective_id, value="other")
            ),
            compiled,
            prepared_inputs,
            normal,
        )

    with pytest.raises(TypeError, match="requires SelectionControl"):
        AssertionControl(selection=cast("object", object()), budgets=normal.budgets)
    with pytest.raises(TypeError, match="requires AssertionBudgets"):
        AssertionControl(selection=normal.selection, budgets=cast("object", object()))

    selected = select(prepared_snapshot, compiled.selector, normal.selection)
    assert type(selected.result) is SelectionReady
    assert type(_decode_state(selected.result)) is Satisfied
    assert (
        type(_decode_state(SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_BODY)))
        is Violated
    )
    assert (
        _decode_state(
            SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_TERMINAL_RESPONSE)
        )
        is None
    )
    assert _meets_completeness(selected.result, compiled.completeness)

    code = DiagnosticCode(value="selection.failed")
    variants = (
        SelectionCancelled(code=code),
        SelectionIncomplete(reason=SelectionIncompleteReason.PARTIAL_CAPTURE),
        SelectionUnsupported(code=code),
        SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_BODY),
        SelectionFactUnavailable(code=code),
        SelectionObservedInvalid(code=code),
        SelectionDecodeFailure(code=code, provenance=_json_view_failure()),
    )
    assert all(_selection_unavailable(item).code for item in variants)
    assert _selection_unavailable(_body_partial()).reason is AssertionUnavailableReason.INCOMPLETE
    exhausted = select(prepared_snapshot, compiled.selector, control(selection_limit=0).selection)
    assert type(exhausted.result) is SelectionBudgetExceeded
    assert (
        _selection_unavailable(exhausted.result).reason
        is AssertionUnavailableReason.BUDGET_EXCEEDED
    )
    with pytest.raises(TypeError, match="ready selection"):
        _selection_unavailable(selected.result)

    # Runtime validation on the public immutable evaluation envelope.
    from comparo.core.assertions import evaluate_assertion

    evaluation = evaluate_assertion(prepared_snapshot, bound, compiled, prepared_inputs, normal)
    with pytest.raises(TypeError, match="requires an AssertionFact"):
        AssertionEvaluation(
            fact=cast("object", object()),
            selection_budgets=evaluation.selection_budgets,
            budgets=evaluation.budgets,
        )
    with pytest.raises(TypeError, match="requires SelectionBudgets"):
        msgspec.structs.replace(evaluation, selection_budgets=cast("object", object()))
    with pytest.raises(TypeError, match="requires AssertionBudgets"):
        msgspec.structs.replace(evaluation, budgets=cast("object", object()))


def test_public_engine_cardinality_budget_and_operand_terminals() -> None:
    from comparo.core.assertions import evaluate_assertion

    prepared_snapshot = snapshot()
    original = rule()
    cardinality = msgspec.structs.replace(
        original,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.CARDINALITY,
            version="1",
        ),
        operator=CardinalityOperator(exact=Count(value=1)),
        quantifier=None,
    )
    satisfied = evaluate_assertion(
        prepared_snapshot,
        application(cardinality, prepared_snapshot),
        cardinality,
        inputs(cardinality, prepared_snapshot),
        control(),
    )
    assert type(satisfied.fact.outcome) is Satisfied
    violated_rule = msgspec.structs.replace(
        cardinality, operator=CardinalityOperator(exact=Count(value=2))
    )
    violated = evaluate_assertion(
        prepared_snapshot,
        application(violated_rule, prepared_snapshot),
        violated_rule,
        inputs(violated_rule, prepared_snapshot),
        control(),
    )
    assert type(violated.fact.outcome) is Violated

    selection_unavailable = evaluate_assertion(
        prepared_snapshot,
        application(original, prepared_snapshot),
        original,
        inputs(original, prepared_snapshot),
        control(selection_limit=0),
    )
    assert type(selection_unavailable.fact.outcome) is AssertionUnavailable

    budgeted = evaluate_assertion(
        prepared_snapshot,
        application(original, prepared_snapshot),
        original,
        inputs(original, prepared_snapshot),
        control(facts=0),
    )
    assert type(budgeted.fact.outcome) is AssertionUnavailable

    prepared = inputs(original, prepared_snapshot)
    operand_missing = msgspec.structs.replace(
        prepared,
        expressions=(
            PreparedExpression(
                expression=prepared.expressions[0].expression,
                result=PreparedOperandUnavailable(code=DiagnosticCode(value="operand.missing")),
            ),
        ),
    )
    unavailable = evaluate_assertion(
        prepared_snapshot,
        application(original, prepared_snapshot),
        original,
        operand_missing,
        control(),
    )
    assert type(unavailable.fact.outcome) is AssertionUnavailable

    condition = _condition()
    conditional = msgspec.structs.replace(
        original,
        applicability=AllConditions(conditions=(condition,)),
    )
    conditional_inputs = inputs(conditional, prepared_snapshot)
    applicability_error_inputs = msgspec.structs.replace(
        conditional_inputs,
        expressions=(
            PreparedExpression(
                expression=conditional_inputs.expressions[0].expression,
                result=PreparedOperandError(code=DiagnosticCode(value="operand.invalid")),
            ),
        ),
    )
    applicability_error = evaluate_assertion(
        prepared_snapshot,
        application(conditional, prepared_snapshot),
        conditional,
        applicability_error_inputs,
        control(),
    )
    assert type(applicability_error.fact.outcome) is AssertionError
    assert applicability_error.fact.outcome.scope is AssertionErrorScope.EVALUATION
    applicability_unavailable = evaluate_assertion(
        prepared_snapshot,
        application(conditional, prepared_snapshot),
        conditional,
        conditional_inputs,
        control(selection_limit=0),
    )
    assert type(applicability_unavailable.fact.outcome) is AssertionUnavailable


def test_engine_partial_selection_terminal_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from comparo.core.assertions import evaluate_assertion

    prepared_snapshot = snapshot()
    owner = prepared_snapshot.observation.execution_id
    selection_budgets = control().selection.budgets

    body = _body_partial()
    body_match = msgspec.structs.replace(
        body.matches[0],
        location=msgspec.structs.replace(
            body.matches[0].location,
            environment_execution_id=owner,
        ),
    )
    body = msgspec.structs.replace(
        body,
        environment_execution_id=owner,
        matches=(body_match,),
    )
    decode_rule = msgspec.structs.replace(
        rule(),
        selector=body.selector,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.DECODES_AS,
            version="1",
        ),
        operator=DecodeOperator(view=BodyViewKind.TEXT),
        quantifier=None,
        completeness=CompletenessRequirement.PARTIAL_PREFIX,
    )
    monkeypatch.setattr(
        "comparo.core.assertions.engine.select",
        lambda *_args: SelectionEvaluation(result=body, budgets=selection_budgets),
    )
    decoded = evaluate_assertion(
        prepared_snapshot,
        application(decode_rule, prepared_snapshot),
        decode_rule,
        inputs(decode_rule, prepared_snapshot),
        control(),
    )
    assert type(decoded.fact.outcome) is AssertionUnavailable
    exhausted = select(
        prepared_snapshot,
        rule().selector,
        control(selection_limit=0).selection,
    )
    assert type(exhausted.result) is SelectionBudgetExceeded
    monkeypatch.setattr(
        "comparo.core.assertions.engine.select",
        lambda *_args: SelectionEvaluation(
            result=exhausted.result,
            budgets=selection_budgets,
        ),
    )
    decode_exhausted = evaluate_assertion(
        prepared_snapshot,
        application(decode_rule, prepared_snapshot),
        decode_rule,
        inputs(decode_rule, prepared_snapshot),
        control(),
    )
    assert type(decode_exhausted.fact.outcome) is AssertionUnavailable

    stream = _stream_partial()
    issue = msgspec.structs.replace(
        stream.issues[0],
        location=msgspec.structs.replace(
            stream.issues[0].location,
            environment_execution_id=owner,
        ),
    )
    stream = msgspec.structs.replace(
        stream,
        environment_execution_id=owner,
        issues=(issue,),
    )
    cardinality_rule = msgspec.structs.replace(
        rule(),
        selector=stream.selector,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.CARDINALITY,
            version="1",
        ),
        operator=CardinalityOperator(minimum=Count(value=1)),
        quantifier=None,
        completeness=CompletenessRequirement.PARTIAL_PREFIX,
    )
    monkeypatch.setattr(
        "comparo.core.assertions.engine.select",
        lambda *_args: SelectionEvaluation(result=stream, budgets=selection_budgets),
    )
    cardinality = evaluate_assertion(
        prepared_snapshot,
        application(cardinality_rule, prepared_snapshot),
        cardinality_rule,
        inputs(cardinality_rule, prepared_snapshot),
        control(),
    )
    assert type(cardinality.fact.outcome) is AssertionUnavailable

    type_rule = msgspec.structs.replace(
        rule(),
        selector=stream.selector,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.IS_TYPE,
            version="1",
        ),
        operator=TypeOperator(expected=ExactValueType.STRING),
        quantifier=Quantifier.ONE,
        completeness=CompletenessRequirement.PARTIAL_PREFIX,
    )
    zero_match = evaluate_assertion(
        prepared_snapshot,
        application(type_rule, prepared_snapshot),
        type_rule,
        inputs(type_rule, prepared_snapshot),
        control(),
    )
    assert type(zero_match.fact.outcome) is AssertionUnavailable

    match_location = msgspec.structs.replace(
        cast("StreamLocation", issue.location),
        item_index=Count(value=1),
        category_index=Count(value=1),
    )
    matched_stream = msgspec.structs.replace(
        stream,
        matches=(SelectionMatch(value=JsonString(value="item"), location=match_location),),
    )
    monkeypatch.setattr(
        "comparo.core.assertions.engine.select",
        lambda *_args: SelectionEvaluation(result=matched_stream, budgets=selection_budgets),
    )
    undecidable = evaluate_assertion(
        prepared_snapshot,
        application(type_rule, prepared_snapshot),
        type_rule,
        inputs(type_rule, prepared_snapshot),
        control(),
    )
    assert type(undecidable.fact.outcome) is AssertionUnavailable
    assert undecidable.fact.counts.evaluated.value == 1

    second_location = msgspec.structs.replace(
        match_location,
        item_index=Count(value=2),
        category_index=Count(value=2),
    )
    overfull = msgspec.structs.replace(
        matched_stream,
        matches=(
            matched_stream.matches[0],
            SelectionMatch(value=JsonString(value="second"), location=second_location),
        ),
    )
    monkeypatch.setattr(
        "comparo.core.assertions.engine.select",
        lambda *_args: SelectionEvaluation(result=overfull, budgets=selection_budgets),
    )
    decisive = evaluate_assertion(
        prepared_snapshot,
        application(type_rule, prepared_snapshot),
        type_rule,
        inputs(type_rule, prepared_snapshot),
        control(),
    )
    assert type(decisive.fact.outcome) is Violated
    assert decisive.fact.outcome.reason is ViolationReason.CARDINALITY
