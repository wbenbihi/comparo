"""Bounded prioritized evidence construction for assertion matches."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.assertions.evidence_size import evidence_fact_size
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.assertions.predicates import PredicateError
from comparo.core.assertions.predicates import PredicateOperands
from comparo.core.assertions.predicates import PredicateResult
from comparo.core.assertions.predicates import PredicateSatisfied
from comparo.core.assertions.predicates import PredicateUnavailable
from comparo.core.assertions.predicates import PredicateViolated
from comparo.core.assertions.predicates import evaluate_predicate
from comparo.core.assertions.predicates import predicate_operands
from comparo.core.assertions.work import charge_evidence_measurement_work
from comparo.core.assertions.work import charge_predicate_work
from comparo.core.domain.assertions.evidence import AssertionOperatorEvidenceValue
from comparo.core.domain.assertions.results import AssertionCounts
from comparo.core.domain.assertions.results import AssertionEvidence
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import MatchFact
from comparo.core.domain.assertions.results import MatchOutcome
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.assertions.rules import Quantifier
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.evaluation import derive_fact_id
from comparo.core.selection import SelectionSnapshot


@final
class PreparedUnavailability(msgspec.Struct, frozen=True, kw_only=True):
    """One unavailable prepared operand or capability."""

    code: DiagnosticCode
    reason: AssertionUnavailableReason


@final
class EvaluatedMatches(msgspec.Struct, frozen=True, kw_only=True):
    """Exact bounded-space logical counts and independently bounded evidence."""

    counts: AssertionCounts
    evidence: AssertionEvidence
    terminal: PreparedUnavailability | BudgetExceeded | None = None


@final
class _EvidenceCandidate(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded evidence candidate ranked without retaining every fact."""

    size: int
    ordinal: int
    fact: MatchFact


@final
class _PreparedFact(msgspec.Struct, frozen=True, kw_only=True):
    """One fully evaluated and optionally measured fact ready for accounting."""

    fact: MatchFact
    size: int | None


def evaluate_matches(
    matches: tuple[SelectionMatch, ...],
    rule: CompiledAssertionRule,
    application: AssertionApplication,
    inputs: PreparedAssertionInputs,
    snapshot: SelectionSnapshot,
    meter: AssertionMeter,
) -> EvaluatedMatches:
    """Evaluate until terminal, preserving bounded priority evidence and partial counts."""
    satisfied = 0
    violated = 0
    error_count = 0
    evaluated = 0
    capacity = _candidate_capacity(meter)
    errors: list[_EvidenceCandidate] = []
    violations: list[_EvidenceCandidate] = []
    passes: list[_EvidenceCandidate] = []
    terminal: PreparedUnavailability | BudgetExceeded | None = None
    for ordinal, match in enumerate(matches):
        prepared = _prepare_fact(
            match,
            ordinal,
            rule,
            application,
            inputs,
            snapshot,
            meter,
            measure=capacity > 0,
        )
        if type(prepared) is PreparedUnavailability:
            terminal = prepared
            break
        if type(prepared) is BudgetExceeded:
            terminal = prepared
            break
        fact = prepared.fact
        outcome = fact.outcome
        bucket = {
            MatchOutcome.ERROR: errors,
            MatchOutcome.VIOLATED: violations,
            MatchOutcome.SATISFIED: passes,
        }[outcome]
        evaluated += 1
        if outcome is MatchOutcome.SATISFIED:
            satisfied += 1
        elif outcome is MatchOutcome.VIOLATED:
            violated += 1
        else:
            error_count += 1
        _retain_candidate(bucket, fact, ordinal, capacity, size=prepared.size)
        if _decisive_match(rule.quantifier, outcome):
            break
    evidence = _finalize_evidence(
        (*errors, *violations, *passes),
        evaluated=evaluated,
        meter=meter,
    )
    counts = AssertionCounts(
        selected=Count(value=len(matches)),
        evaluated=Count(value=evaluated),
        satisfied=Count(value=satisfied),
        violated=Count(value=violated),
        errors=Count(value=error_count),
    )
    return EvaluatedMatches(counts=counts, evidence=evidence, terminal=terminal)


def _prepare_fact(
    match: SelectionMatch,
    ordinal: int,
    rule: CompiledAssertionRule,
    application: AssertionApplication,
    inputs: PreparedAssertionInputs,
    snapshot: SelectionSnapshot,
    meter: AssertionMeter,
    *,
    measure: bool,
) -> _PreparedFact | PreparedUnavailability | BudgetExceeded:
    if (exhaustion := meter.preflight_fact()) is not None:
        return exhaustion
    resolved = predicate_operands(rule.operator, inputs)
    if (
        type(resolved) is PredicateOperands
        and (
            exhaustion := charge_predicate_work(
                meter,
                match.value,
                resolved.values,
                rule.operator,
                operand_origin_groups=resolved.origin_groups,
            )
        )
        is not None
    ):
        return exhaustion
    predicate = evaluate_predicate(match, rule.operator, inputs, snapshot)
    if type(predicate) is PredicateUnavailable:
        return PreparedUnavailability(code=predicate.code, reason=predicate.reason)
    outcome, diagnostic, operator_evidence = _match_outcome(predicate)
    fact = MatchFact(
        fact_id=derive_fact_id(
            application.application_id,
            engine="assertion",
            role="match",
            ordinal=ordinal,
        ),
        match=match,
        outcome=outcome,
        diagnostic=diagnostic,
        operator_evidence=operator_evidence,
    )
    size = _measure_fact(fact, meter) if measure else None
    if type(size) is BudgetExceeded:
        return size
    exhaustion = meter.charge_fact()
    if exhaustion is not None:  # pragma: no cover - preflight guard
        return exhaustion
    return _PreparedFact(fact=fact, size=size)


def _measure_fact(fact: MatchFact, meter: AssertionMeter) -> int | BudgetExceeded:
    exhaustion = charge_evidence_measurement_work(
        meter,
        fact.match.value,
        fact.operator_evidence,
    )
    return exhaustion if exhaustion is not None else evidence_fact_size(fact)


def _candidate_capacity(meter: AssertionMeter) -> int:
    budget = meter.budgets.evidence_examples
    effective = cast("Count", budget.effective)
    consumed = cast("Count", budget.usage.consumed)
    return effective.value - consumed.value


def _retain_candidate(
    bucket: list[_EvidenceCandidate],
    fact: MatchFact,
    ordinal: int,
    capacity: int,
    *,
    size: int | None,
) -> None:
    if capacity == 0:
        return
    if size is None:  # pragma: no cover - guarded by capacity
        raise ValueError("retained evidence candidates require a measured size")
    bucket.append(
        _EvidenceCandidate(
            size=size,
            ordinal=ordinal,
            fact=fact,
        )
    )
    bucket.sort(key=lambda item: (item.size, item.ordinal))
    if len(bucket) > capacity:
        bucket.pop()


def _finalize_evidence(
    candidates: tuple[_EvidenceCandidate, ...],
    *,
    evaluated: int,
    meter: AssertionMeter,
) -> AssertionEvidence:
    retained: list[MatchFact] = []
    truncation: BudgetExceeded | None = None
    for candidate in candidates:
        evidence_exhaustion = meter.charge_evidence(candidate.size)
        if evidence_exhaustion is None:
            retained.append(candidate.fact)
        elif truncation is None:
            truncation = evidence_exhaustion
    omitted = evaluated - len(retained)
    if omitted and truncation is None:
        truncation = meter.charge_evidence(0)
    return AssertionEvidence(
        retained=tuple(retained),
        omitted=Count(value=omitted),
        truncation=truncation,
    )


def _match_outcome(
    predicate: PredicateResult,
) -> tuple[MatchOutcome, DiagnosticCode | None, AssertionOperatorEvidenceValue | None]:
    if type(predicate) is PredicateSatisfied:
        return MatchOutcome.SATISFIED, None, predicate.evidence
    if type(predicate) is PredicateViolated:
        return MatchOutcome.VIOLATED, None, predicate.evidence
    if type(predicate) is PredicateError:
        return MatchOutcome.ERROR, predicate.code, None
    raise TypeError("unavailable predicates are parent outcomes")


def _decisive_match(quantifier: Quantifier | None, outcome: MatchOutcome) -> bool:
    """Stop only when remaining selected matches cannot change the parent truth."""
    if quantifier is Quantifier.ALL:
        return outcome is MatchOutcome.VIOLATED
    if quantifier in {Quantifier.ANY, Quantifier.NONE}:
        return outcome is MatchOutcome.SATISFIED
    return False
