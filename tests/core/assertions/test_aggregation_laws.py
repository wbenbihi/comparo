"""Quantifier, cardinality, and partial-proof aggregation laws."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

import pytest

from comparo.core.assertions.aggregation import assertion_counts
from comparo.core.assertions.aggregation import cardinality_outcome
from comparo.core.assertions.aggregation import partial_outcome
from comparo.core.assertions.aggregation import quantified_outcome
from comparo.core.assertions.aggregation import quantifier_cardinality
from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.selection import select
from tests.core.assertions.factories import control
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot


def _counts(*, selected: int, satisfied: int, violated: int, errors: int) -> AssertionCounts:
    evaluated = satisfied + violated + errors
    return AssertionCounts(
        selected=Count(value=selected),
        evaluated=Count(value=evaluated),
        satisfied=Count(value=satisfied),
        violated=Count(value=violated),
        errors=Count(value=errors),
    )


@pytest.mark.parametrize(
    ("quantifier", "selected", "expected"),
    [
        (Quantifier.ONE, 0, Violated),
        (Quantifier.ONE, 2, Violated),
        (Quantifier.ALL, 0, Violated),
        (Quantifier.ANY, 0, Violated),
        (Quantifier.NONE, 0, Satisfied),
        (Quantifier.ALL, 1, type(None)),
    ],
)
def test_quantifier_cardinality_branches(
    quantifier: Quantifier, selected: int, expected: type[object]
) -> None:
    assert type(quantifier_cardinality(quantifier, selected)) is expected


@pytest.mark.parametrize(
    ("quantifier", "counts", "expected"),
    [
        (Quantifier.ONE, _counts(selected=1, satisfied=0, violated=0, errors=1), AssertionError),
        (Quantifier.ONE, _counts(selected=1, satisfied=0, violated=1, errors=0), Violated),
        (Quantifier.ALL, _counts(selected=2, satisfied=1, violated=1, errors=0), Violated),
        (Quantifier.ALL, _counts(selected=1, satisfied=0, violated=0, errors=1), AssertionError),
        (Quantifier.ALL, _counts(selected=1, satisfied=1, violated=0, errors=0), Satisfied),
        (Quantifier.ANY, _counts(selected=2, satisfied=1, violated=0, errors=1), Satisfied),
        (Quantifier.ANY, _counts(selected=1, satisfied=0, violated=0, errors=1), AssertionError),
        (Quantifier.ANY, _counts(selected=1, satisfied=0, violated=1, errors=0), Violated),
        (Quantifier.NONE, _counts(selected=1, satisfied=1, violated=0, errors=0), Violated),
        (Quantifier.NONE, _counts(selected=1, satisfied=0, violated=0, errors=1), AssertionError),
        (Quantifier.NONE, _counts(selected=1, satisfied=0, violated=1, errors=0), Satisfied),
    ],
)
def test_quantified_outcome_branches(
    quantifier: Quantifier, counts: AssertionCounts, expected: type[object]
) -> None:
    assert type(quantified_outcome(quantifier, counts)) is expected


def test_partial_and_cardinality_proof_branches() -> None:
    error = AssertionError(code=DiagnosticCode(value="assertion.error"))
    assert (
        partial_outcome(
            Quantifier.ALL, error, _counts(selected=1, satisfied=0, violated=0, errors=1)
        )
        is None
    )
    assert (
        type(
            partial_outcome(
                Quantifier.ALL,
                Violated(reason=ViolationReason.PREDICATE),
                _counts(selected=1, satisfied=0, violated=1, errors=0),
            )
        )
        is Violated
    )
    assert (
        type(
            partial_outcome(
                Quantifier.ANY, Satisfied(), _counts(selected=1, satisfied=1, violated=0, errors=0)
            )
        )
        is Satisfied
    )
    assert (
        type(
            partial_outcome(
                Quantifier.NONE,
                Violated(reason=ViolationReason.PREDICATE),
                _counts(selected=1, satisfied=1, violated=0, errors=0),
            )
        )
        is Violated
    )
    assert (
        type(
            partial_outcome(
                Quantifier.ONE,
                Violated(reason=ViolationReason.PREDICATE),
                _counts(selected=2, satisfied=1, violated=1, errors=0),
            )
        )
        is Violated
    )
    assert (
        partial_outcome(
            Quantifier.ALL, Satisfied(), _counts(selected=1, satisfied=1, violated=0, errors=0)
        )
        is None
    )

    exact = CardinalityOperator(exact=Count(value=2))
    maximum = CardinalityOperator(maximum=Count(value=1))
    minimum = CardinalityOperator(minimum=Count(value=1))
    assert type(cardinality_outcome(exact, 2, partial=False)) is Satisfied
    assert type(cardinality_outcome(exact, 1, partial=False)) is Violated
    assert type(cardinality_outcome(maximum, 2, partial=True)) is Violated
    assert type(cardinality_outcome(exact, 3, partial=True)) is Violated
    assert type(cardinality_outcome(minimum, 1, partial=True)) is Satisfied
    assert cardinality_outcome(exact, 1, partial=True) is None


def test_assertion_counts_buckets_exact_matches() -> None:
    prepared = snapshot()
    selected = select(prepared, rule().selector, control().selection)
    assert type(selected.result) is SelectionReady
    matches = (selected.result.matches[0],) * 3
    counts = assertion_counts(
        matches,
        (MatchOutcome.SATISFIED, MatchOutcome.VIOLATED, MatchOutcome.ERROR),
    )
    assert counts == _counts(selected=3, satisfied=1, violated=1, errors=1)
