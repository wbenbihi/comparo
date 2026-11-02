"""Assertion result invariants for partial and terminal outcomes."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from typing import cast

import pytest

from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionErrorScope
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.selection import select
from tests.core.assertions.factories import control
from tests.core.assertions.factories import rule
from tests.core.assertions.factories import snapshot
from tests.core.domain.assertions._invariant_support import _assertion_fact


def _counts(*, selected: int, satisfied: int, violated: int, errors: int) -> AssertionCounts:
    evaluated = satisfied + violated + errors
    return AssertionCounts(
        selected=Count(value=selected),
        evaluated=Count(value=evaluated),
        satisfied=Count(value=satisfied),
        violated=Count(value=violated),
        errors=Count(value=errors),
    )


def test_assertion_result_new_terminal_invariant_branches() -> None:
    selected = select(snapshot(), rule().selector, control().selection)
    assert type(selected.result) is SelectionReady
    exhausted = select(snapshot(), rule().selector, control(selection_limit=0).selection)
    assert type(exhausted.result) is SelectionBudgetExceeded
    with pytest.raises(TypeError, match="truncation must be BudgetExceeded"):
        AssertionEvidence(
            retained=(),
            omitted=Count(value=0),
            truncation=cast("object", object()),
        )
    with pytest.raises(ValueError, match="evidence-stage"):
        AssertionEvidence(
            retained=(), omitted=Count(value=0), truncation=exhausted.result.exhaustion
        )
    code = DiagnosticCode(value="assertion.unavailable")
    with pytest.raises(TypeError, match="reason must be"):
        AssertionUnavailable(code=code, reason=cast("object", object()))
    with pytest.raises(TypeError, match="exhaustion must be"):
        AssertionUnavailable(
            code=code,
            reason=AssertionUnavailableReason.BUDGET_EXCEEDED,
            exhaustion=cast("object", object()),
        )
    with pytest.raises(ValueError, match="exactly budget-exceeded"):
        AssertionUnavailable(code=code, reason=AssertionUnavailableReason.BUDGET_EXCEEDED)
    with pytest.raises(TypeError, match="scope must be"):
        AssertionError(code=DiagnosticCode(value="assertion.error"), scope=cast("object", object()))

    zero = _counts(selected=0, satisfied=0, violated=0, errors=0)
    fact = _assertion_fact(
        rule=rule(),
        outcome=AssertionError(
            code=DiagnosticCode(value="assertion.evaluation.error"),
            scope=AssertionErrorScope.EVALUATION,
        ),
        counts=zero,
    )
    assert fact.counts.evaluated.value == 0
    fully_evaluated = _counts(selected=1, satisfied=0, violated=0, errors=1)
    with pytest.raises(ValueError, match="unevaluated selected matches"):
        _assertion_fact(
            rule=rule(),
            outcome=AssertionUnavailable(
                code=code,
                reason=AssertionUnavailableReason.OPERAND,
            ),
            counts=fully_evaluated,
        )
    partial_counts = AssertionCounts(
        selected=Count(value=2),
        evaluated=Count(value=1),
        satisfied=Count(value=1),
        violated=Count(value=0),
        errors=Count(value=0),
    )
    with pytest.raises(ValueError, match="reason cannot contain partial"):
        _assertion_fact(
            rule=rule(),
            outcome=AssertionUnavailable(
                code=code,
                reason=AssertionUnavailableReason.SELECTION,
            ),
            counts=partial_counts,
        )
    incomplete = _assertion_fact(
        rule=rule(),
        outcome=AssertionUnavailable(
            code=code,
            reason=AssertionUnavailableReason.INCOMPLETE,
        ),
        counts=partial_counts,
    )
    assert incomplete.counts == partial_counts
