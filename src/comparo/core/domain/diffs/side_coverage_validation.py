"""Per-side conservation policies for directed diff aggregate facts."""

from __future__ import annotations

from comparo.core.domain.diffs.coverage import DiffCoverage


def require_extra_side_shapes(
    *,
    baseline_extra: int,
    candidate_extra: int,
    extra_count: int,
    label: str,
) -> None:
    """Require extras to be an exact mix of baseline-only, candidate-only, or bilateral."""
    if (
        baseline_extra < 0
        or candidate_extra < 0
        or baseline_extra > extra_count
        or candidate_extra > extra_count
        or baseline_extra + candidate_extra < extra_count
    ):
        raise ValueError(f"{label} contradict exact one-sided and bilateral coverage")


def require_complete_side_coverage(coverage: DiffCoverage) -> None:
    """Require a complete outcome to consume and resolve every derived value."""
    if (
        coverage.baseline_consumed != coverage.baseline_derived
        or coverage.candidate_consumed != coverage.candidate_derived
        or coverage.baseline_resolved != coverage.baseline_derived
        or coverage.candidate_resolved != coverage.candidate_derived
    ):
        raise ValueError("complete diff outcome must consume and resolve every derived value")
