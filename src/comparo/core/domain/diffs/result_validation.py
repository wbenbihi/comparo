"""Cross-object validation for aggregate directed diff results."""

from __future__ import annotations

from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.evidence import DiffEvidence
from comparo.core.domain.diffs.evidence import DiffFactOutcome
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.diffs.side_coverage_validation import require_extra_side_shapes
from comparo.core.domain.selectors.locations import location_environment_execution_id
from comparo.core.domain.selectors.model import require_location_matches_selector


def require_evidence_binding(
    evidence: DiffEvidence,
    application: DiffApplication,
    rule: CompiledDiffRule,
) -> None:
    """Bind every retained provenance coordinate to the exact directed pair."""
    pair = application.pair
    for fact in evidence.retained:
        for side, provenance, selector, execution_id in (
            (
                "baseline",
                fact.baseline,
                rule.pipeline.baseline.selector,
                pair.baseline_execution_id,
            ),
            (
                "candidate",
                fact.candidate,
                rule.pipeline.candidate.selector,
                pair.candidate_execution_id,
            ),
        ):
            if provenance is None:
                continue
            side_pipeline = (
                rule.pipeline.baseline if side == "baseline" else rule.pipeline.candidate
            )
            if provenance.selector != selector:
                raise ValueError(f"{side} diff provenance uses a different selector")
            expected_operations = tuple(
                transform.identity for transform in side_pipeline.transforms
            ) + (() if side_pipeline.reducer is None else (side_pipeline.reducer.identity,))
            if provenance.operations != expected_operations:
                raise ValueError(f"{side} diff provenance operations contradict the pipeline")
            for location in provenance.locations:
                if location_environment_execution_id(location) != execution_id:
                    raise ValueError(f"{side} diff evidence belongs to a different execution")
                require_location_matches_selector(location, selector)


def require_side_coverage(counts: DiffFactCounts, coverage: DiffCoverage) -> None:
    """Require exact resolved-side consumption across all fact shapes."""
    bilateral = counts.same.value + counts.changed.value
    baseline_known = bilateral + counts.removed.value
    candidate_known = bilateral + counts.added.value
    extra_count = counts.ignored.value + counts.incomparable.value
    require_extra_side_shapes(
        baseline_extra=coverage.baseline_resolved.value - baseline_known,
        candidate_extra=coverage.candidate_resolved.value - candidate_known,
        extra_count=extra_count,
        label="diff facts",
    )


def require_evidence_side_shapes(
    evidence: DiffEvidence,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
) -> None:
    """Reject retained ignored/incomparable shapes impossible under exact coverage."""
    bilateral = counts.same.value + counts.changed.value
    baseline_extra = coverage.baseline_resolved.value - bilateral - counts.removed.value
    candidate_extra = coverage.candidate_resolved.value - bilateral - counts.added.value
    extra_count = counts.ignored.value + counts.incomparable.value
    expected_baseline_only = extra_count - candidate_extra
    expected_candidate_only = extra_count - baseline_extra
    expected_bilateral = baseline_extra + candidate_extra - extra_count
    retained = tuple(
        fact
        for fact in evidence.retained
        if fact.outcome in {DiffFactOutcome.IGNORED, DiffFactOutcome.INCOMPARABLE}
    )
    retained_baseline_only = sum(
        fact.baseline is not None and fact.candidate is None for fact in retained
    )
    retained_candidate_only = sum(
        fact.baseline is None and fact.candidate is not None for fact in retained
    )
    retained_bilateral = sum(
        fact.baseline is not None and fact.candidate is not None for fact in retained
    )
    if (
        retained_baseline_only > expected_baseline_only
        or retained_candidate_only > expected_candidate_only
        or retained_bilateral > expected_bilateral
    ):
        raise ValueError("retained ignored or incomparable locations contradict side coverage")


def require_evidence_buckets(evidence: DiffEvidence, counts: DiffFactCounts) -> None:
    """Ensure each retained outcome fits its exact aggregate bucket."""
    limits = {
        DiffFactOutcome.SAME: counts.same.value,
        DiffFactOutcome.CHANGED: counts.changed.value,
        DiffFactOutcome.ADDED: counts.added.value,
        DiffFactOutcome.REMOVED: counts.removed.value,
        DiffFactOutcome.IGNORED: counts.ignored.value,
        DiffFactOutcome.INCOMPARABLE: counts.incomparable.value,
    }
    for outcome, limit in limits.items():
        if sum(fact.outcome is outcome for fact in evidence.retained) > limit:
            raise ValueError("diff evidence contradicts exact outcome counts")


def require_incomplete_evidence(
    *,
    pairing_stage: bool,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
) -> None:
    """Require an observable unresolved unit for pairing-stage incompleteness."""
    if pairing_stage and not (
        coverage.not_evaluated.value
        or counts.incomparable.value
        or coverage.baseline_consumed.value < coverage.baseline_derived.value
        or coverage.candidate_consumed.value < coverage.candidate_derived.value
    ):
        raise ValueError("pairing incompleteness requires unresolved or incomparable units")


def require_not_evaluated_evidence(
    *,
    all_ignored: bool,
    false_applicability: bool,
    empty_selection: bool,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
) -> None:
    """Validate each deliberate no-comparison state without importing outcome models."""
    if coverage.compared.value:
        raise ValueError("not-evaluated cannot contain compared coverage")
    if all_ignored:
        _require_all_ignored(counts, coverage)
        return
    if counts.total != 0:
        raise ValueError("false-applicability or empty-selection cannot contain concrete facts")
    if false_applicability:
        _require_false_applicability(coverage)
        return
    if empty_selection:
        if coverage.baseline_selected.value or coverage.candidate_selected.value:
            raise ValueError("empty selection requires both selected sides to be empty")
    elif not (coverage.baseline_selected.value or coverage.candidate_selected.value):
        raise ValueError("empty derivation requires at least one selected source value")
    if any(
        count.value
        for count in (
            coverage.baseline_derived,
            coverage.candidate_derived,
            coverage.paired,
            coverage.compared,
            coverage.ignored,
            coverage.not_evaluated,
        )
    ):
        raise ValueError("empty selection requires zero derived and resolved coverage")


def _require_all_ignored(counts: DiffFactCounts, coverage: DiffCoverage) -> None:
    if (
        counts.ignored.value == 0
        or counts.total != counts.ignored.value
        or coverage.not_evaluated.value
    ):
        raise ValueError("all-ignored requires at least one and only ignored concrete fact")


def _require_false_applicability(coverage: DiffCoverage) -> None:
    if any(
        count.value
        for count in (
            coverage.baseline_selected,
            coverage.candidate_selected,
            coverage.baseline_derived,
            coverage.candidate_derived,
            coverage.paired,
            coverage.compared,
            coverage.ignored,
            coverage.not_evaluated,
        )
    ):
        raise ValueError("false applicability requires zero pipeline coverage")


__all__ = (
    "require_evidence_binding",
    "require_evidence_buckets",
    "require_evidence_side_shapes",
    "require_incomplete_evidence",
    "require_not_evaluated_evidence",
    "require_side_coverage",
)
