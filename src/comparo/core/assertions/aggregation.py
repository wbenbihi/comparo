"""Quantifier and cardinality proof laws for assertion evaluation."""

from __future__ import annotations

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionOutcome
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionMatch


def assertion_counts(
    matches: tuple[SelectionMatch, ...],
    outcomes: tuple[MatchOutcome, ...],
) -> AssertionCounts:
    """Count exact selected and evaluated outcome buckets."""
    return AssertionCounts(
        selected=Count(value=len(matches)),
        evaluated=Count(value=len(outcomes)),
        satisfied=Count(value=outcomes.count(MatchOutcome.SATISFIED)),
        violated=Count(value=outcomes.count(MatchOutcome.VIOLATED)),
        errors=Count(value=outcomes.count(MatchOutcome.ERROR)),
    )


def quantifier_cardinality(quantifier: Quantifier, selected: int) -> AssertionOutcome | None:
    """Return a cardinality proof before predicate evaluation when decisive."""
    if quantifier is Quantifier.ONE and selected != 1:
        return Violated(reason=ViolationReason.CARDINALITY)
    if quantifier in {Quantifier.ALL, Quantifier.ANY} and selected == 0:
        return Violated(reason=ViolationReason.CARDINALITY)
    if quantifier is Quantifier.NONE and selected == 0:
        return Satisfied()
    return None


def quantified_outcome(  # noqa: C901
    quantifier: Quantifier,
    counts: AssertionCounts,
) -> AssertionOutcome:
    """Reduce exact match buckets under decisive error precedence."""
    satisfied, violated, errors = (
        counts.satisfied.value,
        counts.violated.value,
        counts.errors.value,
    )
    if quantifier is Quantifier.ONE:
        if errors:
            return AssertionError(code=_code("assertion.operator.error"))
        return Satisfied() if satisfied else Violated(reason=ViolationReason.PREDICATE)
    if quantifier is Quantifier.ALL:
        if violated:
            return Violated(reason=ViolationReason.PREDICATE)
        if errors:
            return AssertionError(code=_code("assertion.operator.error"))
        return Satisfied()
    if quantifier is Quantifier.ANY:
        if satisfied:
            return Satisfied()
        if errors:
            return AssertionError(code=_code("assertion.operator.error"))
        return Violated(reason=ViolationReason.PREDICATE)
    if satisfied:
        return Violated(reason=ViolationReason.PREDICATE)
    if errors:
        return AssertionError(code=_code("assertion.operator.error"))
    return Satisfied()


def partial_outcome(
    quantifier: Quantifier,
    outcome: AssertionOutcome,
    counts: AssertionCounts,
) -> AssertionOutcome | None:
    """Retain only conclusions that unseen stream items cannot reverse."""
    if type(outcome) is AssertionError:
        return None
    if quantifier is Quantifier.ALL and counts.violated.value:
        return outcome
    if quantifier is Quantifier.ANY and counts.satisfied.value:
        return outcome
    if quantifier is Quantifier.NONE and counts.satisfied.value:
        return outcome
    if quantifier is Quantifier.ONE and counts.selected.value > 1:
        return Violated(reason=ViolationReason.CARDINALITY)
    return None


def cardinality_outcome(
    operator: CardinalityOperator,
    selected: int,
    *,
    partial: bool,
) -> AssertionOutcome | None:
    """Prove closed cardinality or only monotonic conclusions on a prefix."""
    satisfied = (
        (operator.exact is None or selected == operator.exact.value)
        and (operator.minimum is None or selected >= operator.minimum.value)
        and (operator.maximum is None or selected <= operator.maximum.value)
    )
    if not partial:
        return Satisfied() if satisfied else Violated(reason=ViolationReason.CARDINALITY)
    if operator.maximum is not None and selected > operator.maximum.value:
        return Violated(reason=ViolationReason.CARDINALITY)
    if operator.exact is not None and selected > operator.exact.value:
        return Violated(reason=ViolationReason.CARDINALITY)
    if (
        operator.minimum is not None
        and operator.maximum is None
        and selected >= operator.minimum.value
    ):
        return Satisfied()
    return None


def _code(value: str) -> DiagnosticCode:
    return DiagnosticCode(value=value)
