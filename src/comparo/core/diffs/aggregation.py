"""Incremental diff fact aggregation with conserved partial progress."""

from __future__ import annotations

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.comparators import comparator_dependency_taint
from comparo.core.diffs.comparators import compare_unit
from comparo.core.diffs.evidence import EvidenceCandidate
from comparo.core.diffs.evidence import consider_evidence
from comparo.core.diffs.evidence import finalize_evidence
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.runtime import PairUnit
from comparo.core.diffs.value_support import merge_taint_origins
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.coverage import DiffFactCounts
from comparo.core.domain.diffs.results import DiffAggregateOutcome
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffIncompleteReason
from comparo.core.domain.diffs.results import DiffNotEvaluatedReason
from comparo.core.domain.diffs.results import DiffPipelineIssue
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import DiffValueProvenance
from comparo.core.domain.diffs.results import Drifted
from comparo.core.domain.diffs.results import Equivalent
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.primitives import Count
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.evaluation import derive_fact_id


def aggregate_diff(
    application: DiffApplication,
    rule: CompiledDiffRule,
    inputs: PreparedDiffInputs,
    meter: DiffMeter,
    baseline_selected: int,
    candidate_selected: int,
    baseline_values: tuple[DerivedValue, ...],
    candidate_values: tuple[DerivedValue, ...],
    pairs: tuple[PairUnit, ...],
    *,
    partial_input: bool = False,
    dependency_taint_origins: tuple[TaintOriginId, ...] = (),
) -> DiffRuleFact:
    """Aggregate incrementally without retaining unbounded fact payloads."""
    totals = dict.fromkeys(DiffFactOutcome, 0)
    candidates: list[EvidenceCandidate] = []
    omitted_probe_size: int | None = None
    first_incomparable: tuple[DiffEvaluationStage, DiagnosticCode] | None = None
    observed_dependency_taint = list(dependency_taint_origins)
    processed = 0
    for ordinal, unit in enumerate(pairs):
        try:
            if (failure := meter.fact_exhaustion()) is not None:
                raise DiffHaltError(exhaustion=failure)
            fact_outcome, diagnostic = compare_unit(unit, rule.pipeline.comparator, inputs, meter)
            comparator_taint: tuple[TaintOriginId, ...] = ()
            if (
                unit.baseline is not None
                and unit.candidate is not None
                and unit.incomparable is None
                and not unit.ignored
            ):
                comparator_taint = comparator_dependency_taint(
                    rule.pipeline.comparator, inputs, meter
                )
            fact_dependency_taint = merge_taint_origins(
                meter, tuple(observed_dependency_taint), comparator_taint
            )
            fact = _fact(
                application,
                rule,
                unit,
                fact_outcome,
                diagnostic,
                ordinal,
                fact_dependency_taint,
            )
            dropped = consider_evidence(candidates, fact, ordinal, meter)
            if (failure := meter.charge_fact()) is not None:  # pragma: no cover - preflight binds
                raise RuntimeError("diff fact reservation changed without intervening charge")
            observed_dependency_taint = list(fact_dependency_taint)
            totals[fact_outcome] += 1
            processed += 1
            if fact_outcome is DiffFactOutcome.INCOMPARABLE and first_incomparable is None:
                first_incomparable = (
                    (
                        DiffEvaluationStage.PAIRING
                        if unit.incomparable is not None
                        else DiffEvaluationStage.COMPARISON
                    ),
                    diagnostic or DiagnosticCode(value="diff.comparator.incomparable"),
                )
            if dropped is not None:
                omitted_probe_size = dropped
        except DiffHaltError as halted:
            return _halted_fact(
                application,
                rule,
                meter,
                baseline_selected,
                candidate_selected,
                baseline_values,
                candidate_values,
                pairs,
                totals,
                candidates,
                omitted_probe_size,
                processed,
                partial_input,
                tuple(observed_dependency_taint),
                halted,
            )
    counts = counts_from_totals(totals)
    coverage = diff_coverage(
        baseline_selected,
        candidate_selected,
        baseline_values,
        candidate_values,
        pairs,
        counts,
        processed=processed,
    )
    aggregate = _aggregate_outcome(counts, first_incomparable, partial_input)
    retained, evidence_exhaustion = finalize_evidence(
        candidates, omitted_probe_size, counts.total, meter
    )
    return _aggregate_fact(
        application,
        rule,
        aggregate,
        counts,
        coverage,
        retained,
        evidence_exhaustion,
        tuple(observed_dependency_taint),
    )


def diff_coverage(
    baseline_selected: int,
    candidate_selected: int,
    baseline: tuple[DerivedValue, ...],
    candidate: tuple[DerivedValue, ...],
    pairs: tuple[PairUnit, ...],
    counts: DiffFactCounts,
    *,
    processed: int,
) -> DiffCoverage:
    """Build exact selected, derived, paired, and resolved conservation counts."""
    baseline_consumed = sum(unit.baseline is not None for unit in pairs)
    candidate_consumed = sum(unit.candidate is not None for unit in pairs)
    resolved = pairs[:processed]
    baseline_resolved = sum(unit.baseline is not None for unit in resolved)
    candidate_resolved = sum(unit.candidate is not None for unit in resolved)
    return DiffCoverage(
        baseline_selected=Count(value=baseline_selected),
        candidate_selected=Count(value=candidate_selected),
        baseline_derived=Count(value=len(baseline)),
        candidate_derived=Count(value=len(candidate)),
        baseline_consumed=Count(value=baseline_consumed),
        candidate_consumed=Count(value=candidate_consumed),
        baseline_resolved=Count(value=baseline_resolved),
        candidate_resolved=Count(value=candidate_resolved),
        paired=Count(value=len(pairs)),
        compared=Count(value=counts.evaluated),
        ignored=counts.ignored,
        not_evaluated=Count(value=len(pairs) - processed),
    )


def counts_from_totals(totals: dict[DiffFactOutcome, int]) -> DiffFactCounts:
    """Freeze exact incremental outcome counters."""
    return DiffFactCounts(
        same=Count(value=totals[DiffFactOutcome.SAME]),
        changed=Count(value=totals[DiffFactOutcome.CHANGED]),
        added=Count(value=totals[DiffFactOutcome.ADDED]),
        removed=Count(value=totals[DiffFactOutcome.REMOVED]),
        ignored=Count(value=totals[DiffFactOutcome.IGNORED]),
        incomparable=Count(value=totals[DiffFactOutcome.INCOMPARABLE]),
    )


def empty_diff_counts() -> DiffFactCounts:
    """Return exact zero counts for terminal paths before comparison."""
    zero = Count(value=0)
    return DiffFactCounts(
        same=zero, changed=zero, added=zero, removed=zero, ignored=zero, incomparable=zero
    )


def empty_diff_coverage() -> DiffCoverage:
    """Return exact zero coverage for terminal paths before selection progress."""
    zero = Count(value=0)
    return DiffCoverage(
        baseline_selected=zero,
        candidate_selected=zero,
        baseline_derived=zero,
        candidate_derived=zero,
        baseline_consumed=zero,
        candidate_consumed=zero,
        baseline_resolved=zero,
        candidate_resolved=zero,
        paired=zero,
        compared=zero,
        ignored=zero,
        not_evaluated=zero,
    )


def _halted_fact(
    application: DiffApplication,
    rule: CompiledDiffRule,
    meter: DiffMeter,
    baseline_selected: int,
    candidate_selected: int,
    baseline_values: tuple[DerivedValue, ...],
    candidate_values: tuple[DerivedValue, ...],
    pairs: tuple[PairUnit, ...],
    totals: dict[DiffFactOutcome, int],
    candidates: list[EvidenceCandidate],
    omitted_probe_size: int | None,
    processed: int,
    partial_input: bool,
    dependency_taint_origins: tuple[TaintOriginId, ...],
    halted: DiffHaltError,
) -> DiffRuleFact:
    """Freeze exact progress at one payload-free comparison-stage interruption."""
    counts = counts_from_totals(totals)
    coverage = diff_coverage(
        baseline_selected,
        candidate_selected,
        baseline_values,
        candidate_values,
        pairs,
        counts,
        processed=processed,
    )
    retained, evidence_exhaustion = finalize_evidence(
        candidates, omitted_probe_size, counts.total, meter
    )
    aggregate: DiffAggregateOutcome
    if halted.incomplete_cause is not None:
        issue = DiffPipelineIssue(
            side=halted.side,
            stage=DiffEvaluationStage.COMPARISON,
            cause=halted.incomplete_cause,
            code=halted.code,
        )
        aggregate = _incomplete(issue, partial_input)
    elif halted.exhaustion is None:
        aggregate = DiffEvaluationError(
            code=halted.code or DiagnosticCode(value="diff.evaluate.internal")
        )
    else:
        issue = DiffPipelineIssue(
            side=DiffSide.BOTH,
            stage=DiffEvaluationStage.COMPARISON,
            cause=DiffIncompleteCause.BUDGET_EXCEEDED,
            exhaustion=halted.exhaustion,
        )
        aggregate = _incomplete(issue, partial_input)
    return _aggregate_fact(
        application,
        rule,
        aggregate,
        counts,
        coverage,
        retained,
        evidence_exhaustion,
        dependency_taint_origins,
    )


def _incomplete(issue: DiffPipelineIssue, partial_input: bool) -> Incomplete:
    return Incomplete(
        reason=DiffIncompleteReason(
            side=issue.side,
            stage=issue.stage,
            cause=issue.cause,
        ),
        issues=(issue, *(() if not partial_input else (_partial_issue(),))),
    )


def _aggregate_outcome(
    counts: DiffFactCounts,
    first_incomparable: tuple[DiffEvaluationStage, DiagnosticCode] | None,
    partial_input: bool,
) -> DiffAggregateOutcome:
    if counts.incomparable.value:
        stage, diagnostic = first_incomparable or (
            DiffEvaluationStage.COMPARISON,
            DiagnosticCode(value="diff.comparator.incomparable"),
        )
        issue = DiffPipelineIssue(
            side=DiffSide.BOTH,
            stage=stage,
            cause=DiffIncompleteCause.PARTIAL,
            code=diagnostic,
        )
        return Incomplete(
            reason=DiffIncompleteReason(
                side=DiffSide.BOTH,
                stage=stage,
                cause=DiffIncompleteCause.PARTIAL,
            ),
            issues=(issue, *(() if not partial_input else (_partial_issue(),))),
        )
    if partial_input:
        issue = _partial_issue()
        return Incomplete(
            reason=DiffIncompleteReason(
                side=issue.side,
                stage=issue.stage,
                cause=issue.cause,
            ),
            issues=(issue,),
        )
    if counts.changed.value or counts.added.value or counts.removed.value:
        return Drifted()
    if counts.ignored.value and counts.total == counts.ignored.value:
        return NotEvaluated(reason=DiffNotEvaluatedReason.ALL_IGNORED)
    return Equivalent()


def _partial_issue() -> DiffPipelineIssue:
    return DiffPipelineIssue(
        side=DiffSide.BOTH,
        stage=DiffEvaluationStage.CAPTURE,
        cause=DiffIncompleteCause.PARTIAL,
        code=DiagnosticCode(value="diff.selection.partial-prefix"),
    )


def _aggregate_fact(
    application: DiffApplication,
    rule: CompiledDiffRule,
    aggregate: DiffAggregateOutcome,
    counts: DiffFactCounts,
    coverage: DiffCoverage,
    retained: tuple[DiffFact, ...],
    evidence_exhaustion: BudgetExceeded | None,
    dependency_taint_origins: tuple[TaintOriginId, ...],
) -> DiffRuleFact:
    return DiffRuleFact(
        fact_id=derive_fact_id(application.application_id, engine="diff", role="parent"),
        application=application,
        rule=rule,
        outcome=aggregate,
        counts=counts,
        coverage=coverage,
        evidence=DiffEvidence(
            retained=retained,
            omitted=Count(value=counts.total - len(retained)),
            exhaustion=evidence_exhaustion,
        ),
        dependency_taint_origins=dependency_taint_origins,
    )


def _fact(
    application: DiffApplication,
    rule: CompiledDiffRule,
    unit: PairUnit,
    outcome: DiffFactOutcome,
    diagnostic: DiagnosticCode | None,
    ordinal: int,
    dependency_taint_origins: tuple[TaintOriginId, ...],
) -> DiffFact:
    return DiffFact(
        fact_id=derive_fact_id(
            application.application_id,
            engine="diff",
            role="comparison",
            ordinal=ordinal,
        ),
        outcome=outcome,
        comparator=rule.pipeline.comparator.identity,
        baseline=_provenance(unit.baseline),
        candidate=_provenance(unit.candidate),
        diagnostic=diagnostic,
        dependency_taint_origins=dependency_taint_origins,
    )


def _provenance(value: DerivedValue | None) -> DiffValueProvenance | None:
    if value is None:
        return None
    return DiffValueProvenance(
        selector=value.selector,
        locations=value.origins,
        operations=value.operations,
        taint_origins=value.taint_origins,
        empty_reducer_identity=value.empty_reducer_identity,
    )


__all__ = (
    "aggregate_diff",
    "counts_from_totals",
    "diff_coverage",
    "empty_diff_counts",
    "empty_diff_coverage",
)
