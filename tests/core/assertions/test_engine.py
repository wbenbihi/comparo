"""Exercise the public pure assertion boundary and aggregation laws."""

from __future__ import annotations

import msgspec

from comparo.core.assertions import evaluate_assertion
from comparo.core.domain.assertions.operators import EqualityOperator
from comparo.core.domain.assertions.operators import EqualityRelation
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import AllConditions
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.values import JsonInteger
from tests.core.assertions.factories import application
from tests.core.assertions.factories import control
from tests.core.assertions.factories import inputs
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot


def test_status_assertion_is_satisfied_with_deterministic_facts() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    bound = application(compiled, prepared_snapshot)

    first = evaluate_assertion(
        prepared_snapshot,
        bound,
        compiled,
        inputs(compiled, prepared_snapshot),
        control(),
    )
    second = evaluate_assertion(
        prepared_snapshot,
        bound,
        compiled,
        inputs(compiled, prepared_snapshot),
        control(),
    )

    assert type(first.fact.outcome) is Satisfied
    assert first.fact.counts.selected.value == 1
    assert first.fact.counts.satisfied.value == 1
    assert len(first.fact.evidence.retained) == 1
    assert first.fact.fact_id == second.fact.fact_id
    assert first.fact.evidence.retained[0].fact_id == second.fact.evidence.retained[0].fact_id


def test_false_predicate_is_a_violation_not_an_error() -> None:
    prepared_snapshot = snapshot()
    original = rule()
    assert type(original.operator) is EqualityOperator
    expression = original.operator.expected
    assert type(expression) is LiteralExpr
    changed = msgspec.structs.replace(
        original,
        operator=EqualityOperator(
            relation=EqualityRelation.EQUALS,
            expected=msgspec.structs.replace(expression, value=JsonInteger(value=201)),
        ),
    )

    evaluated = evaluate_assertion(
        prepared_snapshot,
        application(changed, prepared_snapshot),
        changed,
        inputs(changed, prepared_snapshot),
        control(),
    )

    assert type(evaluated.fact.outcome) is Violated
    assert evaluated.fact.outcome.reason is ViolationReason.PREDICATE


def test_false_applicability_is_the_only_skipped_path() -> None:
    prepared_snapshot = snapshot()
    original = rule()
    assert type(original.operator) is EqualityOperator
    condition_operator = EqualityOperator(
        relation=EqualityRelation.EQUALS,
        expected=msgspec.structs.replace(
            original.operator.expected,
            value=JsonInteger(value=999),
        ),
    )
    condition = PredicateCondition(
        selector=original.selector,
        operator_id=original.operator_id,
        operator=condition_operator,
        quantifier=Quantifier.ONE,
    )
    conditional = msgspec.structs.replace(
        original,
        applicability=AllConditions(conditions=(condition,)),
    )

    evaluated = evaluate_assertion(
        prepared_snapshot,
        application(conditional, prepared_snapshot),
        conditional,
        inputs(conditional, prepared_snapshot),
        control(),
    )

    assert type(evaluated.fact.outcome) is Skipped
    assert evaluated.fact.counts.selected.value == 0


def test_cancellation_is_unavailable_without_selection_work() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    evaluated = evaluate_assertion(
        prepared_snapshot,
        application(compiled, prepared_snapshot),
        compiled,
        inputs(compiled, prepared_snapshot),
        control(cancelled=True),
    )

    assert type(evaluated.fact.outcome) is AssertionUnavailable
    assert evaluated.selection_budgets == control(cancelled=True).selection.budgets


def test_evidence_exhaustion_preserves_satisfied_outcome_and_counts() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    evaluated = evaluate_assertion(
        prepared_snapshot,
        application(compiled, prepared_snapshot),
        compiled,
        inputs(compiled, prepared_snapshot),
        control(evidence=0),
    )

    assert type(evaluated.fact.outcome) is Satisfied
    assert evaluated.fact.counts.evaluated.value == 1
    assert evaluated.fact.evidence.retained == ()
    assert evaluated.fact.evidence.omitted.value == 1
    assert evaluated.fact.evidence.truncation is not None


def test_evidence_byte_budget_charges_the_encoded_fact_not_a_constant() -> None:
    prepared_snapshot = snapshot()
    compiled = rule()
    evaluated = evaluate_assertion(
        prepared_snapshot,
        application(compiled, prepared_snapshot),
        compiled,
        inputs(compiled, prepared_snapshot),
        control(evidence=1, evidence_bytes=1),
    )

    assert type(evaluated.fact.outcome) is Satisfied
    assert evaluated.fact.evidence.retained == ()
    assert evaluated.fact.evidence.omitted.value == 1
    assert evaluated.fact.evidence.truncation is not None
