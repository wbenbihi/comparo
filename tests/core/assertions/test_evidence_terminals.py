"""Bounded assertion evidence and terminal evaluation behavior."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

import msgspec

from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.evidence import EvaluatedMatches
from comparo.core.assertions.evidence import PreparedUnavailability
from comparo.core.assertions.evidence import evaluate_matches
from comparo.core.assertions.inputs import PreparedExpression
from comparo.core.assertions.inputs import PreparedOperandUnavailable
from comparo.core.domain.assertions.operators import AssertionOperatorIdentity
from comparo.core.domain.assertions.operators import AssertionOperatorKind
from comparo.core.domain.assertions.operators import BetweenOperator
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.values import JsonInteger
from comparo.core.selection import select
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.domain.assertions.test_contracts import _literal


def test_evidence_evaluation_work_fact_and_operand_terminals() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    selected = select(prepared_snapshot, compiled.selector, control().selection)
    assert type(selected.result) is SelectionReady
    match = selected.result.matches[0]
    for meter_control in (control(work=0), control(facts=0)):
        result = evaluate_matches(
            (match,),
            compiled,
            application(compiled, prepared_snapshot),
            inputs(compiled, prepared_snapshot),
            prepared_snapshot,
            AssertionMeter(meter_control.budgets),
        )
        assert type(result) is EvaluatedMatches
        assert result.terminal is not None
    prepared = inputs(compiled, prepared_snapshot)
    missing = msgspec.structs.replace(
        prepared,
        expressions=(
            PreparedExpression(
                expression=prepared.expressions[0].expression,
                result=PreparedOperandUnavailable(code=DiagnosticCode(value="operand.missing")),
            ),
        ),
    )
    result = evaluate_matches(
        (match,),
        compiled,
        application(compiled, prepared_snapshot),
        missing,
        prepared_snapshot,
        AssertionMeter(control().budgets),
    )
    assert type(result) is EvaluatedMatches
    assert type(result.terminal) is PreparedUnavailability


def test_evidence_error_bucket_capacity_and_repeated_truncation() -> None:
    prepared_snapshot = snapshot()
    original = rule()
    inverted = BetweenOperator(
        lower=_literal(JsonInteger(value=3)),
        upper=_literal(JsonInteger(value=2)),
        include_lower=True,
        include_upper=True,
    )
    compiled = msgspec.structs.replace(
        original,
        operator_id=AssertionOperatorIdentity(
            namespace="comparo.core",
            kind=AssertionOperatorKind.BETWEEN,
            version="1",
        ),
        operator=inverted,
    )
    selected = select(prepared_snapshot, compiled.selector, control().selection)
    assert type(selected.result) is SelectionReady
    match = selected.result.matches[0]
    errors = evaluate_matches(
        (match, match),
        compiled,
        application(compiled, prepared_snapshot),
        inputs(compiled, prepared_snapshot),
        prepared_snapshot,
        AssertionMeter(control(evidence=1).budgets),
    )
    assert errors.counts.errors.value == 2
    assert len(errors.evidence.retained) == 1

    passes = evaluate_matches(
        (match, match),
        original,
        application(original, prepared_snapshot),
        inputs(original, prepared_snapshot),
        prepared_snapshot,
        AssertionMeter(control(evidence=2, evidence_bytes=1).budgets),
    )
    assert passes.evidence.retained == ()
    assert passes.evidence.truncation is not None
