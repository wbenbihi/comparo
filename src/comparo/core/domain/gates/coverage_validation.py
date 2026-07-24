"""Per-side conservation checks for value-free diff gate coverage."""

from __future__ import annotations


def require_coverage_bounds(
    *,
    baseline_derived: int,
    candidate_derived: int,
    baseline_consumed: int,
    candidate_consumed: int,
    baseline_resolved: int,
    candidate_resolved: int,
) -> None:
    """Require resolved values to be a subset of consumed and derived values."""
    if baseline_consumed > baseline_derived:
        raise ValueError("baseline gate consumed values cannot exceed derived values")
    if candidate_consumed > candidate_derived:
        raise ValueError("candidate gate consumed values cannot exceed derived values")
    if baseline_resolved > baseline_consumed:
        raise ValueError("baseline gate resolved values cannot exceed consumed values")
    if candidate_resolved > candidate_consumed:
        raise ValueError("candidate gate resolved values cannot exceed consumed values")


def require_unit_footprints(
    *,
    units: int,
    baseline: int,
    candidate: int,
    label: str,
) -> None:
    """Require every unit to consume one or two exact side values."""
    if units < max(baseline, candidate) or units > baseline + candidate:
        raise ValueError(f"{label} gate units contradict exact per-side value coverage")


def require_side_fact_coverage(
    *,
    baseline_resolved: int,
    candidate_resolved: int,
    same: int,
    changed: int,
    added: int,
    removed: int,
    ignored: int,
    incomparable: int,
) -> None:
    """Reconcile fact direction with exact resolved side-value counts."""
    bilateral = same + changed
    baseline_extra = baseline_resolved - bilateral - removed
    candidate_extra = candidate_resolved - bilateral - added
    extra_count = ignored + incomparable
    if (
        baseline_extra < 0
        or candidate_extra < 0
        or baseline_extra > extra_count
        or candidate_extra > extra_count
        or baseline_extra + candidate_extra < extra_count
    ):
        raise ValueError("gate facts contradict exact one-sided and bilateral coverage")


def require_complete_coverage(
    *,
    baseline_derived: int,
    candidate_derived: int,
    baseline_consumed: int,
    candidate_consumed: int,
    baseline_resolved: int,
    candidate_resolved: int,
) -> None:
    """Require a terminal outcome to consume and resolve every derived value."""
    if (
        baseline_consumed != baseline_derived
        or candidate_consumed != candidate_derived
        or baseline_resolved != baseline_derived
        or candidate_resolved != candidate_derived
    ):
        raise ValueError("complete gate outcome must consume and resolve every derived value")
