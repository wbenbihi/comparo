"""Assertion outcomes, exact counts, and bounded concrete evidence."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.domain.assertions.extended_operators import CardinalityOperator
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.assertions.rules import Severity
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import FactId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import require_match_contract


class MatchOutcome(DomainStrEnum):
    """Concrete predicate outcomes for one selected value."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    ERROR = "error"


@final
class MatchFact(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded child fact at a concrete selector location."""

    fact_id: FactId
    match: SelectionMatch
    outcome: MatchOutcome
    diagnostic: DiagnosticCode | None = None

    def __post_init__(self) -> None:
        """Require diagnostics exactly for evaluator errors."""
        if type(self.fact_id) is not FactId or type(self.match) is not SelectionMatch:
            raise TypeError("match fact requires a FactId and SelectionMatch")
        if type(self.outcome) is not MatchOutcome:
            raise TypeError("match outcome must be a MatchOutcome")
        if self.diagnostic is not None and type(self.diagnostic) is not DiagnosticCode:
            raise TypeError("match diagnostic must be a DiagnosticCode")
        if (self.outcome is MatchOutcome.ERROR) != (self.diagnostic is not None):
            raise ValueError("exactly evaluator-error match facts require diagnostics")


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
class AssertionEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Bounded evidence whose omission never changes logical counts."""

    retained: tuple[MatchFact, ...]
    omitted: Count

    def __post_init__(self) -> None:
        """Require immutable typed evidence and exact omission count."""
        if type(self.retained) is not tuple:
            raise TypeError("assertion evidence must be an immutable tuple")
        if any(type(fact) is not MatchFact for fact in self.retained):
            raise TypeError("assertion evidence contains an unsupported fact")
        if type(self.omitted) is not Count:
            raise TypeError("omitted evidence must be a Count")
        fact_ids = tuple(fact.fact_id for fact in self.retained)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("assertion evidence fact IDs must be unique")


@final
class Satisfied(msgspec.Struct, frozen=True, kw_only=True):
    """The quantified predicate and cardinality were satisfied."""


class ViolationReason(DomainStrEnum):
    """Parent-level assertion violation categories."""

    PREDICATE = "predicate"
    CARDINALITY = "cardinality"


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


@final
class AssertionUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """Required observation, view, or operand data was unavailable."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("unavailability requires a DiagnosticCode")


@final
class AssertionError(msgspec.Struct, frozen=True, kw_only=True):
    """A runtime operand failure or evaluator defect."""

    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a safe stable diagnostic code."""
        if type(self.code) is not DiagnosticCode:
            raise TypeError("assertion error requires a DiagnosticCode")


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
    if type(outcome) in {Skipped, AssertionUnavailable}:
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
            raise ValueError("skipped or unavailable assertions cannot contain match counts")
        return
    if type(rule.operator) is CardinalityOperator:
        _require_cardinality_outcome(rule.operator, outcome, counts)
        return
    _require_quantified_outcome(cast("Quantifier", rule.quantifier), outcome, counts)


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
    if errors:
        raise ValueError("non-error assertion outcomes cannot contain match errors")
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
