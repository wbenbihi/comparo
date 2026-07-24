"""Nested three-state applicability evaluation over shared selectors."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.aggregation import quantified_outcome
from comparo.core.assertions.aggregation import quantifier_cardinality
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.predicates import PredicateError
from comparo.core.assertions.predicates import PredicateOperands
from comparo.core.assertions.predicates import PredicateSatisfied
from comparo.core.assertions.predicates import PredicateUnavailable
from comparo.core.assertions.predicates import evaluate_predicate
from comparo.core.assertions.predicates import predicate_operands
from comparo.core.assertions.work import charge_predicate_work
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.rules import AllConditions
from comparo.core.domain.assertions.rules import AnyConditions
from comparo.core.domain.assertions.rules import ApplicabilityCondition
from comparo.core.domain.assertions.rules import PredicateCondition
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.selection import SelectionBudgets
from comparo.core.selection import SelectionControl
from comparo.core.selection import SelectionSnapshot
from comparo.core.selection import select


class ConditionState(DomainStrEnum):
    """Three-state condition result plus a decisive applicable state."""

    APPLICABLE = "applicable"
    FALSE = "false"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@final
class ConditionResult(msgspec.Struct, frozen=True, kw_only=True):
    """Applicability truth with an optional value-free terminal code."""

    state: ConditionState
    code: DiagnosticCode | None = None
    reason: AssertionUnavailableReason | None = None
    exhaustion: BudgetExceeded | None = None

    def __post_init__(self) -> None:
        """Bind safe diagnostics exactly to error and unavailable states."""
        if type(self.state) is not ConditionState:
            raise TypeError("condition result state must be typed")
        terminal = self.state in {ConditionState.UNAVAILABLE, ConditionState.ERROR}
        if terminal != (self.code is not None):
            raise ValueError("exactly error and unavailable conditions require a code")
        unavailable = self.state is ConditionState.UNAVAILABLE
        if unavailable != (self.reason is not None):
            raise ValueError("exactly unavailable conditions require a reason")
        if self.code is not None and type(self.code) is not DiagnosticCode:
            raise TypeError("condition result code must be a DiagnosticCode")
        if self.reason is not None and type(self.reason) is not AssertionUnavailableReason:
            raise TypeError("condition result reason must be typed")
        if self.exhaustion is not None and type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("condition result exhaustion must be BudgetExceeded")
        budget = self.reason is AssertionUnavailableReason.BUDGET_EXCEEDED
        if budget != (self.exhaustion is not None):
            raise ValueError("exactly budget-exceeded conditions require exhaustion")


def evaluate_condition(
    condition: ApplicabilityCondition | None,
    snapshot: SelectionSnapshot,
    inputs: PreparedAssertionInputs,
    selection_control: SelectionControl,
    selection_budgets: SelectionBudgets,
    meter: AssertionMeter,
) -> tuple[ConditionResult, SelectionBudgets]:
    """Evaluate nested all/any conditions with decisive short-circuiting."""
    if condition is None:
        return ConditionResult(state=ConditionState.APPLICABLE), selection_budgets
    if type(condition) is PredicateCondition:
        return _predicate_condition(
            condition, snapshot, inputs, selection_control, selection_budgets, meter
        )
    current = selection_budgets
    first_error: ConditionResult | None = None
    first_unavailable: ConditionResult | None = None
    for child in condition.conditions:
        evaluated, current = evaluate_condition(
            child, snapshot, inputs, selection_control, current, meter
        )
        if type(condition) is AllConditions and evaluated.state is ConditionState.FALSE:
            return evaluated, current
        if type(condition) is AnyConditions and evaluated.state is ConditionState.APPLICABLE:
            return evaluated, current
        if evaluated.state is ConditionState.ERROR and first_error is None:
            first_error = evaluated
        if evaluated.state is ConditionState.UNAVAILABLE and first_unavailable is None:
            first_unavailable = evaluated
    if first_error is not None:
        return first_error, current
    if first_unavailable is not None:
        return first_unavailable, current
    state = ConditionState.APPLICABLE if type(condition) is AllConditions else ConditionState.FALSE
    return ConditionResult(state=state), current


def _predicate_condition(
    condition: PredicateCondition,
    snapshot: SelectionSnapshot,
    inputs: PreparedAssertionInputs,
    selection_control: SelectionControl,
    selection_budgets: SelectionBudgets,
    meter: AssertionMeter,
) -> tuple[ConditionResult, SelectionBudgets]:
    selected = select(
        snapshot,
        condition.selector,
        msgspec.structs.replace(selection_control, budgets=selection_budgets),
    )
    if type(selected.result) is not SelectionReady:
        exhaustion = (
            selected.result.exhaustion if type(selected.result) is SelectionBudgetExceeded else None
        )
        return (
            ConditionResult(
                state=ConditionState.UNAVAILABLE,
                code=_code("assertion.applicability.selection-unavailable"),
                reason=(
                    AssertionUnavailableReason.BUDGET_EXCEEDED
                    if exhaustion is not None
                    else AssertionUnavailableReason.SELECTION
                ),
                exhaustion=exhaustion,
            ),
            selected.budgets,
        )
    result = selected.result
    cardinality = quantifier_cardinality(condition.quantifier, len(result.matches))
    if cardinality is not None:
        return (
            ConditionResult(
                state=(
                    ConditionState.FALSE
                    if type(cardinality) is Violated
                    else ConditionState.APPLICABLE
                )
            ),
            selected.budgets,
        )
    return (
        _evaluate_ready(condition, result, inputs, snapshot, meter),
        selected.budgets,
    )


def _evaluate_ready(
    condition: PredicateCondition,
    result: SelectionReady,
    inputs: PreparedAssertionInputs,
    snapshot: SelectionSnapshot,
    meter: AssertionMeter,
) -> ConditionResult:
    """Evaluate plural matches with bounded counters and decisive short-circuiting."""
    satisfied = 0
    violated = 0
    first_error: DiagnosticCode | None = None
    for match in result.matches:
        resolved = predicate_operands(condition.operator, inputs)
        if type(resolved) is PredicateOperands:
            exhaustion = charge_predicate_work(
                meter,
                match.value,
                resolved.values,
                condition.operator,
                operand_origin_groups=resolved.origin_groups,
            )
        else:
            exhaustion = meter.charge_work()
        if exhaustion is not None:
            return ConditionResult(
                state=ConditionState.UNAVAILABLE,
                code=_code("assertion.evaluation.budget-exceeded"),
                reason=AssertionUnavailableReason.BUDGET_EXCEEDED,
                exhaustion=exhaustion,
            )
        predicate = evaluate_predicate(match, condition.operator, inputs, snapshot)
        if type(predicate) is PredicateUnavailable:
            return ConditionResult(
                state=ConditionState.UNAVAILABLE,
                code=predicate.code,
                reason=predicate.reason,
            )
        if type(predicate) is PredicateError:
            if first_error is None:
                first_error = predicate.code
            continue
        if type(predicate) is PredicateSatisfied:
            satisfied += 1
            decisive = _satisfied_condition(condition.quantifier)
        else:
            violated += 1
            decisive = _violated_condition(condition.quantifier)
        if decisive is not None:
            return decisive
    if first_error is not None:
        return ConditionResult(state=ConditionState.ERROR, code=first_error)
    outcome = quantified_outcome(
        condition.quantifier,
        AssertionCounts(
            selected=Count(value=len(result.matches)),
            evaluated=Count(value=satisfied + violated),
            satisfied=Count(value=satisfied),
            violated=Count(value=violated),
            errors=Count(value=0),
        ),
    )
    return ConditionResult(
        state=ConditionState.APPLICABLE if type(outcome) is Satisfied else ConditionState.FALSE
    )


def _satisfied_condition(quantifier: Quantifier) -> ConditionResult | None:
    if quantifier is Quantifier.ANY:
        return ConditionResult(state=ConditionState.APPLICABLE)
    if quantifier is Quantifier.NONE:
        return ConditionResult(state=ConditionState.FALSE)
    return None


def _violated_condition(quantifier: Quantifier) -> ConditionResult | None:
    if quantifier is Quantifier.ALL:
        return ConditionResult(state=ConditionState.FALSE)
    return None


def _code(value: str) -> DiagnosticCode:
    return DiagnosticCode(value=value)
