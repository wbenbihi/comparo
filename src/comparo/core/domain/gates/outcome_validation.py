"""Outcome-to-count truth checks for value-free gate projections."""

from __future__ import annotations

from comparo.core.domain.gates.coverage_validation import require_complete_coverage
from comparo.core.domain.gates.types import AssertionGateOutcome
from comparo.core.domain.gates.types import DiffGateOutcome


def require_assertion_outcome_counts(
    outcome: AssertionGateOutcome,
    *,
    selected: int,
    evaluated: int,
    satisfied: int,
    violated: int,
    errors: int,
) -> None:
    """Require an assertion outcome to agree with its exact projected counts."""
    if outcome in {AssertionGateOutcome.SKIPPED, AssertionGateOutcome.UNAVAILABLE}:
        if any((selected, evaluated, satisfied, violated, errors)):
            raise ValueError("skipped or unavailable gate outcomes require zero match counts")
        return
    if outcome is AssertionGateOutcome.ERROR:
        if errors == 0:
            raise ValueError("error gate outcome requires at least one match error")
        return
    if errors:
        raise ValueError("non-error gate outcomes cannot contain match errors")
    if (
        outcome is AssertionGateOutcome.SATISFIED
        and evaluated
        and not satisfied
        and evaluated != selected
    ):
        raise ValueError(
            "satisfied gate outcome requires a satisfied match or complete all-violated coverage"
        )
    if outcome is AssertionGateOutcome.VIOLATED and evaluated and not violated:
        raise ValueError("violated gate outcome requires at least one violated match")


def require_diff_outcome_counts(
    outcome: DiffGateOutcome,
    *,
    same: int,
    changed: int,
    added: int,
    removed: int,
    ignored: int,
    incomparable: int,
    baseline_derived: int,
    candidate_derived: int,
    baseline_consumed: int,
    candidate_consumed: int,
    baseline_resolved: int,
    candidate_resolved: int,
    compared: int,
    not_evaluated: int,
) -> None:
    """Require a diff outcome to agree with exact projected facts and coverage."""
    drift = changed + added + removed
    unresolved = not_evaluated or incomparable
    if outcome in {DiffGateOutcome.EQUIVALENT, DiffGateOutcome.DRIFTED}:
        require_complete_coverage(
            baseline_derived=baseline_derived,
            candidate_derived=candidate_derived,
            baseline_consumed=baseline_consumed,
            candidate_consumed=candidate_consumed,
            baseline_resolved=baseline_resolved,
            candidate_resolved=candidate_resolved,
        )
    if outcome is DiffGateOutcome.EQUIVALENT and (
        compared == 0 or baseline_derived == 0 or candidate_derived == 0 or drift or unresolved
    ):
        raise ValueError("equivalent gate outcome requires non-empty comparable coverage")
    if outcome is DiffGateOutcome.DRIFTED and (drift == 0 or unresolved):
        raise ValueError("drifted gate outcome requires resolved concrete drift")
    if outcome is DiffGateOutcome.NOT_EVALUATED and compared:
        raise ValueError("not-evaluated gate outcome cannot contain compared coverage")
    if outcome is DiffGateOutcome.NOT_EVALUATED and ignored and not_evaluated == 0:
        require_complete_coverage(
            baseline_derived=baseline_derived,
            candidate_derived=candidate_derived,
            baseline_consumed=baseline_consumed,
            candidate_consumed=candidate_consumed,
            baseline_resolved=baseline_resolved,
            candidate_resolved=candidate_resolved,
        )
