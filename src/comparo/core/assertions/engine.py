"""Public pure assertion evaluation over the shared selection engine."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.assertions.accounting import AssertionBudgets
from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.aggregation import cardinality_outcome
from comparo.core.assertions.aggregation import partial_outcome
from comparo.core.assertions.aggregation import quantified_outcome
from comparo.core.assertions.aggregation import quantifier_cardinality
from comparo.core.assertions.applicability import ConditionState
from comparo.core.assertions.applicability import evaluate_condition
from comparo.core.assertions.evidence import PreparedUnavailability
from comparo.core.assertions.evidence import evaluate_matches
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.inputs import require_exact_expression_coverage
from comparo.core.assertions.inputs import require_known_predicate_bindings
from comparo.core.assertions.selection_outcomes import decode_state as _decode_state
from comparo.core.assertions.selection_outcomes import meets_completeness as _meets_completeness
from comparo.core.assertions.selection_outcomes import (
    selection_unavailable as _selection_unavailable,
)
from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionError
from comparo.core.domain.assertions.results import AssertionErrorScope
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionFact
from comparo.core.domain.assertions.results import AssertionOutcome
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import Skipped
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionPartialReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.evaluation import derive_fact_id
from comparo.core.selection import SelectionBudgets
from comparo.core.selection import SelectionControl
from comparo.core.selection import SelectionSnapshot
from comparo.core.selection import select


@final
class AssertionControl(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned cumulative selector and assertion accounting."""

    selection: SelectionControl
    budgets: AssertionBudgets

    def __post_init__(self) -> None:
        """Require exact immutable controls."""
        if type(self.selection) is not SelectionControl:
            raise TypeError("assertion control requires SelectionControl")
        if type(self.budgets) is not AssertionBudgets:
            raise TypeError("assertion control requires AssertionBudgets")


@final
class AssertionEvaluation(msgspec.Struct, frozen=True, kw_only=True):
    """One assertion fact paired with cumulative accounting for the next call."""

    fact: AssertionFact
    selection_budgets: SelectionBudgets
    budgets: AssertionBudgets

    def __post_init__(self) -> None:
        """Require the closed fact and accounting records."""
        if type(self.fact) is not AssertionFact:
            raise TypeError("assertion evaluation requires an AssertionFact")
        if type(self.selection_budgets) is not SelectionBudgets:
            raise TypeError("assertion evaluation requires SelectionBudgets")
        if type(self.budgets) is not AssertionBudgets:
            raise TypeError("assertion evaluation requires AssertionBudgets")


def evaluate_assertion(
    snapshot: SelectionSnapshot,
    application: AssertionApplication,
    rule: CompiledAssertionRule,
    inputs: PreparedAssertionInputs,
    control: AssertionControl,
) -> AssertionEvaluation:
    """Evaluate one compiled rule without I/O, ambient state, or nondeterminism."""
    _require_boundary(snapshot, application, rule, inputs, control)
    return _evaluate_assertion(snapshot, application, rule, inputs, control)


def _evaluate_assertion(  # noqa: C901
    snapshot: SelectionSnapshot,
    application: AssertionApplication,
    rule: CompiledAssertionRule,
    inputs: PreparedAssertionInputs,
    control: AssertionControl,
) -> AssertionEvaluation:
    """Run the evaluation path while recording exact prepared capability consumption."""
    meter = AssertionMeter(control.budgets)
    selection_budgets = control.selection.budgets
    if control.selection.cancellation.cancelled:
        return _terminal(
            application,
            rule,
            AssertionUnavailable(
                code=control.selection.cancellation.code,
                reason=AssertionUnavailableReason.CANCELLED,
            ),
            _zero_counts(),
            AssertionEvidence(retained=(), omitted=Count(value=0)),
            selection_budgets,
            meter.budgets,
        )
    applicability, selection_budgets = evaluate_condition(
        rule.applicability,
        snapshot,
        inputs,
        control.selection,
        selection_budgets,
        meter,
    )
    if applicability.state is not ConditionState.APPLICABLE:
        if applicability.state is ConditionState.FALSE:
            outcome: AssertionOutcome = Skipped()
        elif applicability.state is ConditionState.ERROR:
            outcome = AssertionError(
                code=cast("DiagnosticCode", applicability.code),
                scope=AssertionErrorScope.EVALUATION,
            )
        else:
            outcome = AssertionUnavailable(
                code=cast("DiagnosticCode", applicability.code),
                reason=cast("AssertionUnavailableReason", applicability.reason),
                exhaustion=applicability.exhaustion,
            )
        return _terminal(
            application,
            rule,
            outcome,
            _zero_counts(),
            AssertionEvidence(retained=(), omitted=Count(value=0)),
            selection_budgets,
            meter.budgets,
        )
    selected = select(
        snapshot,
        rule.selector,
        msgspec.structs.replace(control.selection, budgets=selection_budgets),
    )
    selection_budgets = selected.budgets
    result = selected.result
    if type(rule.operator) is DecodeOperator:
        state_outcome = _decode_state(result)
        if state_outcome is not None:
            return _terminal(
                application,
                rule,
                state_outcome,
                _zero_counts(),
                AssertionEvidence(retained=(), omitted=Count(value=0)),
                selection_budgets,
                meter.budgets,
            )
        if type(result) is SelectionPartial:
            return _terminal(
                application,
                rule,
                _selection_unavailable(result),
                _zero_counts(),
                AssertionEvidence(retained=(), omitted=Count(value=0)),
                selection_budgets,
                meter.budgets,
            )
    if type(result) not in {SelectionReady, SelectionPartial}:
        unavailable = _selection_unavailable(result)
        return _terminal(
            application,
            rule,
            unavailable,
            _zero_counts(),
            AssertionEvidence(retained=(), omitted=Count(value=0)),
            selection_budgets,
            meter.budgets,
        )
    collection = cast("SelectionReady | SelectionPartial", result)
    open_selection = type(collection) is SelectionPartial
    if not _meets_completeness(collection, rule.completeness):
        return _terminal(
            application,
            rule,
            AssertionUnavailable(
                code=_code("assertion.selection.incomplete"),
                reason=AssertionUnavailableReason.INCOMPLETE,
            ),
            _zero_counts(),
            AssertionEvidence(retained=(), omitted=Count(value=0)),
            selection_budgets,
            meter.budgets,
        )
    if (
        type(collection) is SelectionPartial
        and collection.reason is SelectionPartialReason.VIEW_PREFIX
    ):
        return _incomplete(application, rule, selection_budgets, meter.budgets)
    if type(rule.operator) is CardinalityOperator:
        cardinality_result = cardinality_outcome(
            rule.operator,
            len(collection.matches),
            partial=open_selection,
        )
        if cardinality_result is None:
            return _terminal(
                application,
                rule,
                AssertionUnavailable(
                    code=_code("assertion.selection.incomplete"),
                    reason=AssertionUnavailableReason.INCOMPLETE,
                ),
                AssertionCounts(
                    selected=Count(value=len(collection.matches)),
                    evaluated=Count(value=0),
                    satisfied=Count(value=0),
                    violated=Count(value=0),
                    errors=Count(value=0),
                ),
                AssertionEvidence(retained=(), omitted=Count(value=0)),
                selection_budgets,
                meter.budgets,
            )
        counts = AssertionCounts(
            selected=Count(value=len(collection.matches)),
            evaluated=Count(value=0),
            satisfied=Count(value=0),
            violated=Count(value=0),
            errors=Count(value=0),
        )
        return _terminal(
            application,
            rule,
            cardinality_result,
            counts,
            AssertionEvidence(retained=(), omitted=Count(value=0)),
            selection_budgets,
            meter.budgets,
        )
    quantifier = cast("Quantifier", rule.quantifier)
    cardinality_result = quantifier_cardinality(quantifier, len(collection.matches))
    if cardinality_result is not None:
        if open_selection and not (quantifier is Quantifier.ONE and len(collection.matches) > 1):
            return _incomplete(application, rule, selection_budgets, meter.budgets)
        return _terminal(
            application,
            rule,
            cardinality_result,
            AssertionCounts(
                selected=Count(value=len(collection.matches)),
                evaluated=Count(value=0),
                satisfied=Count(value=0),
                violated=Count(value=0),
                errors=Count(value=0),
            ),
            AssertionEvidence(retained=(), omitted=Count(value=0)),
            selection_budgets,
            meter.budgets,
        )
    evaluated = evaluate_matches(
        collection.matches,
        rule,
        application,
        inputs,
        snapshot,
        meter,
    )
    buckets = evaluated.counts
    if type(evaluated.terminal) is BudgetExceeded:
        return _budget_unavailable(
            application,
            rule,
            evaluated.terminal,
            buckets,
            evaluated.evidence,
            selection_budgets,
            meter.budgets,
        )
    if type(evaluated.terminal) is PreparedUnavailability:
        return _terminal(
            application,
            rule,
            AssertionUnavailable(
                code=evaluated.terminal.code,
                reason=evaluated.terminal.reason,
            ),
            buckets,
            evaluated.evidence,
            selection_budgets,
            meter.budgets,
        )
    aggregate_outcome = quantified_outcome(quantifier, buckets)
    if open_selection:
        decisive_partial = partial_outcome(quantifier, aggregate_outcome, buckets)
        if decisive_partial is None:
            return _incomplete(
                application,
                rule,
                selection_budgets,
                meter.budgets,
                counts=buckets,
                evidence=evaluated.evidence,
            )
        aggregate_outcome = decisive_partial
    return _terminal(
        application,
        rule,
        aggregate_outcome,
        buckets,
        evaluated.evidence,
        selection_budgets,
        meter.budgets,
    )


def _require_boundary(
    snapshot: SelectionSnapshot,
    application: AssertionApplication,
    rule: CompiledAssertionRule,
    inputs: PreparedAssertionInputs,
    control: AssertionControl,
) -> None:
    if type(snapshot) is not SelectionSnapshot:
        raise TypeError("assertion evaluation requires SelectionSnapshot")
    if type(application) is not AssertionApplication or type(rule) is not CompiledAssertionRule:
        raise TypeError("assertion evaluation requires application and compiled rule")
    if type(inputs) is not PreparedAssertionInputs or type(control) is not AssertionControl:
        raise TypeError("assertion evaluation requires prepared inputs and control")
    if snapshot.observation.execution_id != application.environment_execution_id:
        raise ValueError("assertion snapshot belongs to another application execution")
    if inputs.environment_execution_id != application.environment_execution_id:
        raise ValueError("prepared assertion inputs belong to another execution")
    if inputs.rule_fingerprint != rule.fingerprint:
        raise ValueError("prepared assertion inputs belong to another rule")
    if (
        application.definition != rule.definition
        or application.effective_id != rule.effective_id
        or application.rule_fingerprint != rule.fingerprint
    ):
        raise ValueError("assertion application does not identify the compiled rule")
    require_exact_expression_coverage(rule, inputs)
    require_known_predicate_bindings(rule, inputs)


def _terminal(
    application: AssertionApplication,
    rule: CompiledAssertionRule,
    outcome: AssertionOutcome,
    counts: AssertionCounts,
    evidence: AssertionEvidence,
    selection_budgets: SelectionBudgets,
    budgets: AssertionBudgets,
) -> AssertionEvaluation:
    return AssertionEvaluation(
        fact=AssertionFact(
            fact_id=derive_fact_id(
                application.application_id,
                engine="assertion",
                role="parent",
            ),
            application=application,
            rule=rule,
            outcome=outcome,
            counts=counts,
            evidence=evidence,
        ),
        selection_budgets=selection_budgets,
        budgets=budgets,
    )


def _incomplete(
    application: AssertionApplication,
    rule: CompiledAssertionRule,
    selection_budgets: SelectionBudgets,
    budgets: AssertionBudgets,
    *,
    counts: AssertionCounts | None = None,
    evidence: AssertionEvidence | None = None,
) -> AssertionEvaluation:
    return _terminal(
        application,
        rule,
        AssertionUnavailable(
            code=_code("assertion.selection.incomplete"),
            reason=AssertionUnavailableReason.INCOMPLETE,
        ),
        _zero_counts() if counts is None else counts,
        (AssertionEvidence(retained=(), omitted=Count(value=0)) if evidence is None else evidence),
        selection_budgets,
        budgets,
    )


def _budget_unavailable(
    application: AssertionApplication,
    rule: CompiledAssertionRule,
    exhaustion: BudgetExceeded,
    counts: AssertionCounts,
    evidence: AssertionEvidence,
    selection_budgets: SelectionBudgets,
    budgets: AssertionBudgets,
) -> AssertionEvaluation:
    return _terminal(
        application,
        rule,
        AssertionUnavailable(
            code=_code("assertion.evaluation.budget-exceeded"),
            reason=AssertionUnavailableReason.BUDGET_EXCEEDED,
            exhaustion=exhaustion,
        ),
        counts,
        evidence,
        selection_budgets,
        budgets,
    )


def _zero_counts() -> AssertionCounts:
    zero = Count(value=0)
    return AssertionCounts(
        selected=zero, evaluated=zero, satisfied=zero, violated=zero, errors=zero
    )


def _code(value: str) -> DiagnosticCode:
    return DiagnosticCode(value=value)
