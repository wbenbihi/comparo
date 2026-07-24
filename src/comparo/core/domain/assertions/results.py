"""Assertion outcomes, exact counts, and bounded concrete evidence."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.extended_operators import DecodeOperator
from comparo.core.domain.assertions.match_results import AssertionEvidence as AssertionEvidence
from comparo.core.domain.assertions.match_results import MatchFact as MatchFact
from comparo.core.domain.assertions.match_results import MatchOutcome as MatchOutcome
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.results import require_match_contract


@final
class AssertionCounts(msgspec.Struct, frozen=True, kw_only=True):
    """Exact logical counts independent of evidence retention."""

    selected: Count
    evaluated: Count
    satisfied: Count
    violated: Count
    errors: Count

    def __post_init__(self) -> None:
        """Require exact consistent selection and evaluation counts."""
        values = (self.selected, self.evaluated, self.satisfied, self.violated, self.errors)
        if any(type(count) is not Count for count in values):
            raise TypeError("assertion counts must use Count values")
        total = self.satisfied.value + self.violated.value + self.errors.value
        if self.evaluated.value != total:
            raise ValueError("evaluated must equal satisfied, violated, and error counts")
        if self.evaluated.value > self.selected.value:
            raise ValueError("evaluated count cannot exceed selected count")


@final
class Satisfied(msgspec.Struct, frozen=True, kw_only=True):
    """The quantified predicate and cardinality were satisfied."""


class ViolationReason(DomainStrEnum):
    """Parent-level assertion violation categories."""

    PREDICATE = "predicate"
    CARDINALITY = "cardinality"
    STATE = "state"


@final
class Violated(msgspec.Struct, frozen=True, kw_only=True):
    """A false predicate or cardinality mismatch."""

    reason: ViolationReason

    def __post_init__(self) -> None:
        """Require a typed violation category."""
        if type(self.reason) is not ViolationReason:
            raise TypeError("violation reason must be a ViolationReason")


@final
class Skipped(msgspec.Struct, frozen=True, kw_only=True):
    """An explicitly false applicability condition."""


class AssertionUnavailableReason(DomainStrEnum):
    """Typed reason required assertion data could not be evaluated."""

    SELECTION = "selection"
    OPERAND = "operand"
    CAPABILITY = "capability"
    INCOMPLETE = "incomplete"
    BUDGET_EXCEEDED = "budget-exceeded"
    CANCELLED = "cancelled"


@final
class AssertionUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """Required observation, view, or operand data was unavailable."""

    code: DiagnosticCode
    reason: AssertionUnavailableReason = AssertionUnavailableReason.SELECTION
    exhaustion: BudgetExceeded | None = None

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("unavailability requires a DiagnosticCode")
        if type(self.reason) is not AssertionUnavailableReason:
            raise TypeError("unavailability reason must be an AssertionUnavailableReason")
        if self.exhaustion is not None and type(self.exhaustion) is not BudgetExceeded:
            raise TypeError("unavailability exhaustion must be BudgetExceeded")
        if (self.reason is AssertionUnavailableReason.BUDGET_EXCEEDED) != (
            self.exhaustion is not None
        ):
            raise ValueError("exactly budget-exceeded unavailability requires exhaustion evidence")


class AssertionErrorScope(DomainStrEnum):
    """Whether an error belongs to one match or the parent evaluation."""

    MATCH = "match"
    EVALUATION = "evaluation"


@final
class AssertionError(msgspec.Struct, frozen=True, kw_only=True):
    """A runtime operand failure or evaluator defect."""

    code: DiagnosticCode
    scope: AssertionErrorScope = AssertionErrorScope.MATCH

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("assertion error requires a DiagnosticCode")
        if type(self.scope) is not AssertionErrorScope:
            raise TypeError("assertion error scope must be an AssertionErrorScope")


type AssertionOutcome = Satisfied | Violated | Skipped | AssertionUnavailable | AssertionError


@final
class AssertionFact(msgspec.Struct, frozen=True, kw_only=True):
    """The complete parent fact for one assertion application."""

    fact_id: FactId
    application: AssertionApplication
    rule: CompiledAssertionRule
    outcome: AssertionOutcome
    counts: AssertionCounts
    evidence: AssertionEvidence

    def __post_init__(self) -> None:
        """Validate identities, exact counts, and bounded evidence coverage."""
        if type(self.fact_id) is not FactId:
            raise TypeError("assertion fact ID must be a FactId")
        if type(self.application) is not AssertionApplication:
            raise TypeError("assertion fact requires an AssertionApplication")
        if type(self.rule) is not CompiledAssertionRule:
            raise TypeError("assertion fact requires a CompiledAssertionRule")
        _require_application_rule_identity(self.application, self.rule)
        if type(self.outcome) not in {
            Satisfied,
            Violated,
            Skipped,
            AssertionUnavailable,
            AssertionError,
        }:
            raise TypeError("assertion fact outcome has an unsupported type")
        if type(self.counts) is not AssertionCounts or type(self.evidence) is not AssertionEvidence:
            raise TypeError("assertion fact requires typed counts and evidence")
        if any(fact.fact_id == self.fact_id for fact in self.evidence.retained):
            raise ValueError("assertion parent fact ID must differ from retained child fact IDs")
        _require_evidence_binding(self.evidence, self.application, self.rule)
        if len(self.evidence.retained) + self.evidence.omitted.value != self.counts.evaluated.value:
            raise ValueError("evidence must cover every evaluated match")
        _require_evidence_buckets(self.evidence, self.counts)
        _require_assertion_outcome(self.rule, self.outcome, self.counts)

    @property
    def severity(self) -> Severity:
        """Return the effective rule severity without duplicating mutable coordinates."""
        return self.rule.severity

    @property
    def selector(self) -> CompiledSelector:
        """Return the bound effective selector."""
        return self.rule.selector


def _require_application_rule_identity(
    application: AssertionApplication,
    rule: CompiledAssertionRule,
) -> None:
    if (
        application.definition != rule.definition
        or application.effective_id != rule.effective_id
        or application.rule_fingerprint != rule.fingerprint
    ):
        raise ValueError("assertion application does not identify the effective compiled rule")


def _require_evidence_binding(
    evidence: AssertionEvidence,
    application: AssertionApplication,
    rule: CompiledAssertionRule,
) -> None:
    for fact in evidence.retained:
        require_match_contract(
            fact.match,
            rule.selector,
            application.environment_execution_id,
        )
    locations = tuple(fact.match.location for fact in evidence.retained)
    if not rule.selector.capabilities.may_duplicate and len(set(locations)) != len(locations):
        raise ValueError("assertion evidence cannot repeat a location for this selector")


def _require_evidence_buckets(evidence: AssertionEvidence, counts: AssertionCounts) -> None:
    retained_satisfied = sum(fact.outcome is MatchOutcome.SATISFIED for fact in evidence.retained)
    retained_violated = sum(fact.outcome is MatchOutcome.VIOLATED for fact in evidence.retained)
    retained_errors = sum(fact.outcome is MatchOutcome.ERROR for fact in evidence.retained)
    if (
        retained_satisfied > counts.satisfied.value
        or retained_violated > counts.violated.value
        or retained_errors > counts.errors.value
    ):
        raise ValueError("assertion evidence contradicts exact outcome counts")


def _require_assertion_outcome(
    rule: CompiledAssertionRule,
    outcome: AssertionOutcome,
    counts: AssertionCounts,
) -> None:
    if type(outcome) is Skipped:
        if any(
            count.value
            for count in (
                counts.selected,
                counts.evaluated,
                counts.satisfied,
                counts.violated,
                counts.errors,
            )
        ):
            raise ValueError("skipped assertions cannot contain match counts")
        return
    if type(outcome) is AssertionUnavailable:
        _require_unavailable_outcome(outcome, counts)
        return
    if type(rule.operator) is CardinalityOperator:
        _require_cardinality_outcome(rule.operator, outcome, counts)
        return
    if (
        type(rule.operator) is DecodeOperator
        and (
            type(outcome) is Satisfied
            or (type(outcome) is Violated and outcome.reason is ViolationReason.STATE)
        )
        and not any(
            count.value
            for count in (
                counts.selected,
                counts.evaluated,
                counts.satisfied,
                counts.violated,
                counts.errors,
            )
        )
    ):
        return
    if (
        type(outcome) is AssertionError
        and outcome.scope is AssertionErrorScope.EVALUATION
        and not any(
            count.value
            for count in (
                counts.selected,
                counts.evaluated,
                counts.satisfied,
                counts.violated,
                counts.errors,
            )
        )
    ):
        return
    _require_quantified_outcome(cast("Quantifier", rule.quantifier), outcome, counts)


def _require_unavailable_outcome(
    outcome: AssertionUnavailable,
    counts: AssertionCounts,
) -> None:
    """Admit exact partial coverage only for match-level terminal states."""
    if not any(
        count.value
        for count in (
            counts.selected,
            counts.evaluated,
            counts.satisfied,
            counts.violated,
            counts.errors,
        )
    ):
        return
    if outcome.reason is AssertionUnavailableReason.INCOMPLETE:
        return
    if counts.evaluated.value >= counts.selected.value:
        raise ValueError("unavailable assertions require unevaluated selected matches")
    if outcome.reason not in {
        AssertionUnavailableReason.OPERAND,
        AssertionUnavailableReason.CAPABILITY,
        AssertionUnavailableReason.BUDGET_EXCEEDED,
    }:
        raise ValueError("this unavailability reason cannot contain partial match counts")


def _require_cardinality_outcome(
    operator: CardinalityOperator,
    outcome: AssertionOutcome,
    counts: AssertionCounts,
) -> None:
    if counts.evaluated.value != 0:
        raise ValueError("predicate-free cardinality assertions cannot evaluate match predicates")
    selected = counts.selected.value
    satisfied = (
        (operator.exact is None or selected == operator.exact.value)
        and (operator.minimum is None or selected >= operator.minimum.value)
        and (operator.maximum is None or selected <= operator.maximum.value)
    )
    if satisfied and type(outcome) is not Satisfied:
        raise ValueError("satisfied cardinality requires a Satisfied outcome")
    if not satisfied and not (
        type(outcome) is Violated and outcome.reason is ViolationReason.CARDINALITY
    ):
        raise ValueError("failed cardinality requires a cardinality Violated outcome")


def _require_quantified_outcome(
    quantifier: Quantifier,
    outcome: AssertionOutcome,
    counts: AssertionCounts,
) -> None:
    selected = counts.selected.value
    evaluated = counts.evaluated.value
    satisfied = counts.satisfied.value
    violated = counts.violated.value
    errors = counts.errors.value
    if type(outcome) is AssertionError:
        _require_quantified_error(quantifier, selected, satisfied, violated, errors)
        return
    if type(outcome) is Satisfied:
        valid = (
            (quantifier is Quantifier.ONE and selected == evaluated == satisfied == 1)
            or (
                quantifier is Quantifier.ALL and selected > 0 and selected == evaluated == satisfied
            )
            or (quantifier is Quantifier.ANY and satisfied > 0)
            or (
                quantifier is Quantifier.NONE
                and evaluated == selected
                and satisfied == 0
                and violated == selected
            )
        )
        if not valid:
            raise ValueError("satisfied outcome contradicts quantifier match counts")
        return
    violation = cast("Violated", outcome)
    if violation.reason is ViolationReason.CARDINALITY:
        valid = evaluated == 0 and (
            (quantifier is Quantifier.ONE and selected != 1)
            or (quantifier in {Quantifier.ALL, Quantifier.ANY} and selected == 0)
        )
    else:
        valid = (
            (quantifier is Quantifier.ONE and selected == evaluated == violated == 1)
            or (quantifier is Quantifier.ALL and violated > 0)
            or (quantifier is Quantifier.ANY and selected > 0 and evaluated == selected == violated)
            or (quantifier is Quantifier.NONE and satisfied > 0)
        )
    if not valid:
        raise ValueError("violated outcome contradicts quantifier match counts")


def _require_quantified_error(
    quantifier: Quantifier,
    selected: int,
    satisfied: int,
    violated: int,
    errors: int,
) -> None:
    if errors == 0:
        raise ValueError("assertion error outcome requires at least one match error")
    if quantifier is Quantifier.ONE and selected != 1:
        raise ValueError("one quantifier cardinality mismatch must violate before evaluation")
    if (quantifier is Quantifier.ALL and violated) or (
        quantifier in {Quantifier.ANY, Quantifier.NONE} and satisfied
    ):
        raise ValueError("assertion error cannot override a decisive predicate result")
