"""Public pure directed diff evaluator over two prepared selection snapshots."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.diffs.accounting import DiffBudgets
from comparo.core.diffs.accounting import DiffControl
from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.admission import admit_selection
from comparo.core.diffs.admission import applicable
from comparo.core.diffs.admission import selection_dependency_taint
from comparo.core.diffs.admission import selection_issue
from comparo.core.diffs.aggregation import aggregate_diff
from comparo.core.diffs.aggregation import diff_coverage
from comparo.core.diffs.aggregation import empty_diff_counts
from comparo.core.diffs.aggregation import empty_diff_coverage
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.pairing import pair_values
from comparo.core.diffs.reducers import apply_reducer
from comparo.core.diffs.runtime import DerivedValue
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.transforms import apply_transforms
from comparo.core.diffs.validation import validate_diff_inputs
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.coverage import DiffCoverage
from comparo.core.domain.diffs.results import DiffEvaluationError
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffEvidence
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffIncompleteReason
from comparo.core.domain.diffs.results import DiffNotEvaluatedReason
from comparo.core.domain.diffs.results import DiffPipelineIssue
from comparo.core.domain.diffs.results import DiffRuleFact
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.results import Incomplete
from comparo.core.domain.diffs.results import NotEvaluated
from comparo.core.domain.diffs.rules import ApplicabilityExpectation
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.primitives import Count
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.evaluation import derive_fact_id
from comparo.core.selection import SelectionBudgets
from comparo.core.selection import SelectionControl
from comparo.core.selection import SelectionSnapshot
from comparo.core.selection import select


@final
class DiffEvaluation(msgspec.Struct, frozen=True, kw_only=True):
    """A terminal fact plus exact cumulative budgets for the next call."""

    fact: DiffRuleFact
    baseline_selection: SelectionBudgets
    candidate_selection: SelectionBudgets
    budgets: DiffBudgets

    def __post_init__(self) -> None:
        """Require exact fact and cumulative budget snapshots."""
        if type(self.fact) is not DiffRuleFact:
            raise TypeError("diff evaluation requires a DiffRuleFact")
        if type(self.baseline_selection) is not SelectionBudgets:
            raise TypeError("diff evaluation requires baseline SelectionBudgets")
        if type(self.candidate_selection) is not SelectionBudgets:
            raise TypeError("diff evaluation requires candidate SelectionBudgets")
        if type(self.budgets) is not DiffBudgets:
            raise TypeError("diff evaluation requires DiffBudgets")


def evaluate_diff(  # noqa: C901
    baseline: SelectionSnapshot,
    candidate: SelectionSnapshot,
    application: DiffApplication,
    rule: CompiledDiffRule,
    inputs: PreparedDiffInputs,
    control: DiffControl,
) -> DiffEvaluation:
    """Evaluate one directed binary rule without I/O, resolution, gating, or reporting."""
    validate_diff_inputs(baseline, candidate, application, rule, inputs, control)
    meter = DiffMeter(control.budgets)
    baseline_control = control.baseline_selection
    candidate_control = control.candidate_selection
    dependency_taint_origins: tuple[TaintOriginId, ...] = ()
    if control.cancellation.cancelled:
        issue = DiffPipelineIssue(
            side=DiffSide.BOTH,
            stage=DiffEvaluationStage.EXECUTION,
            cause=DiffIncompleteCause.CANCELLED,
            code=control.cancellation.code,
        )
        return _incomplete_evaluation(
            application, rule, issue, baseline_control, candidate_control, meter, 0, 0
        )
    applicability = rule.applicability
    if applicability is not None:
        left = select(baseline, applicability.baseline_selector, baseline_control)
        right = select(candidate, applicability.candidate_selector, candidate_control)
        baseline_control = msgspec.structs.replace(baseline_control, budgets=left.budgets)
        candidate_control = msgspec.structs.replace(candidate_control, budgets=right.budgets)
        found_failures: list[DiffPipelineIssue] = []
        for side, result in (
            (DiffSide.BASELINE, left.result),
            (DiffSide.CANDIDATE, right.result),
        ):
            found = selection_issue(side, result)
            if found is not None:
                found_failures.append(found)
        failures = tuple(found_failures)
        if failures:
            return _incomplete_evaluation(
                application,
                rule,
                failures[0],
                baseline_control,
                candidate_control,
                meter,
                0,
                0,
                issues=failures,
            )
        expected = applicability.expectation is ApplicabilityExpectation.TRUE
        try:
            dependency_taint_origins = selection_dependency_taint(meter, left.result, right.result)
            applies = applicable(left.result) is expected and applicable(right.result) is expected
        except DiffHaltError as halted:
            if halted.exhaustion is None:  # pragma: no cover - origin merge only meters work
                return _error_evaluation(
                    application,
                    rule,
                    halted.code or DiagnosticCode(value="diff.evaluate.internal"),
                    baseline_control,
                    candidate_control,
                    meter,
                )
            issue = DiffPipelineIssue(
                side=DiffSide.BOTH,
                stage=DiffEvaluationStage.SELECTION,
                cause=DiffIncompleteCause.BUDGET_EXCEEDED,
                exhaustion=halted.exhaustion,
            )
            return _incomplete_evaluation(
                application,
                rule,
                issue,
                baseline_control,
                candidate_control,
                meter,
                0,
                0,
            )
        except ValueError:
            return _error_evaluation(
                application,
                rule,
                DiagnosticCode(value="diff.applicability.runtime-contract"),
                baseline_control,
                candidate_control,
                meter,
                dependency_taint_origins=dependency_taint_origins,
            )
        if not applies:
            return _not_evaluated(
                application,
                rule,
                DiffNotEvaluatedReason.FALSE_APPLICABILITY,
                baseline_control,
                candidate_control,
                meter,
                dependency_taint_origins=dependency_taint_origins,
            )
    left = select(baseline, rule.pipeline.baseline.selector, baseline_control)
    right = select(candidate, rule.pipeline.candidate.selector, candidate_control)
    baseline_control = msgspec.structs.replace(baseline_control, budgets=left.budgets)
    candidate_control = msgspec.structs.replace(candidate_control, budgets=right.budgets)
    left_admission = admit_selection(DiffSide.BASELINE, left.result, rule.completeness)
    right_admission = admit_selection(DiffSide.CANDIDATE, right.result, rule.completeness)
    left_matches, left_issue = left_admission.matches, left_admission.issue
    right_matches, right_issue = right_admission.matches, right_admission.issue
    issues = tuple(issue for issue in (left_issue, right_issue) if issue is not None)
    if issues:
        return _incomplete_evaluation(
            application,
            rule,
            issues[0],
            baseline_control,
            candidate_control,
            meter,
            len(left_matches),
            len(right_matches),
            issues=issues,
            dependency_taint_origins=dependency_taint_origins,
        )
    left_selected_values = tuple(
        DerivedValue(
            value=match.value,
            selector=rule.pipeline.baseline.selector,
            origins=(match.location,),
        )
        for match in left_matches
    )
    right_selected_values = tuple(
        DerivedValue(
            value=match.value,
            selector=rule.pipeline.candidate.selector,
            origins=(match.location,),
        )
        for match in right_matches
    )
    left_values: tuple[DerivedValue, ...] = ()
    right_values: tuple[DerivedValue, ...] = ()
    stage = DiffEvaluationStage.DERIVATION
    try:
        left_values = apply_reducer(
            apply_transforms(
                left_selected_values,
                rule.pipeline.baseline.transforms,
                inputs,
                meter,
                DiffSide.BASELINE,
            ),
            rule.pipeline.baseline.reducer,
            inputs,
            meter,
            DiffSide.BASELINE,
            rule.pipeline.baseline.selector,
        )
        right_values = apply_reducer(
            apply_transforms(
                right_selected_values,
                rule.pipeline.candidate.transforms,
                inputs,
                meter,
                DiffSide.CANDIDATE,
            ),
            rule.pipeline.candidate.reducer,
            inputs,
            meter,
            DiffSide.CANDIDATE,
            rule.pipeline.candidate.selector,
        )
        if not left_values and not right_values:
            return _not_evaluated(
                application,
                rule,
                (
                    DiffNotEvaluatedReason.EMPTY_SELECTION
                    if not left_matches and not right_matches
                    else DiffNotEvaluatedReason.EMPTY_DERIVATION
                ),
                baseline_control,
                candidate_control,
                meter,
                baseline_selected=len(left_matches),
                candidate_selected=len(right_matches),
                dependency_taint_origins=dependency_taint_origins,
            )
        stage = DiffEvaluationStage.PAIRING
        pairs = pair_values(
            left_values,
            right_values,
            rule.pipeline.pairing,
            meter,
        )
        stage = DiffEvaluationStage.COMPARISON
        fact = aggregate_diff(
            application,
            rule,
            inputs,
            meter,
            len(left_matches),
            len(right_matches),
            left_values,
            right_values,
            pairs,
            partial_input=left_admission.partial or right_admission.partial,
            dependency_taint_origins=dependency_taint_origins,
        )
        return _evaluation(fact, baseline_control, candidate_control, meter)
    except DiffHaltError as halted:
        if stage is DiffEvaluationStage.DERIVATION:
            if halted.side is DiffSide.BASELINE:
                left_values = halted.partial_derived
            elif halted.side is DiffSide.CANDIDATE:  # pragma: no branch - side is bound upstream
                right_values = halted.partial_derived
        partial_coverage = None
        if stage is DiffEvaluationStage.PAIRING and halted.partial_pairs:
            partial_coverage = diff_coverage(
                len(left_matches),
                len(right_matches),
                left_values,
                right_values,
                halted.partial_pairs,
                empty_diff_counts(),
                processed=0,
            )
        if halted.exhaustion is not None:
            issue = DiffPipelineIssue(
                side=DiffSide.BOTH,
                stage=stage,
                cause=DiffIncompleteCause.BUDGET_EXCEEDED,
                exhaustion=halted.exhaustion,
            )
            return _incomplete_evaluation(
                application,
                rule,
                issue,
                baseline_control,
                candidate_control,
                meter,
                len(left_matches),
                len(right_matches),
                baseline_derived=len(left_values),
                candidate_derived=len(right_values),
                coverage=partial_coverage,
                dependency_taint_origins=dependency_taint_origins,
            )
        if halted.incomplete_cause is not None:
            issue = DiffPipelineIssue(
                side=halted.side,
                stage=stage,
                cause=halted.incomplete_cause,
                code=halted.code,
            )
            return _incomplete_evaluation(
                application,
                rule,
                issue,
                baseline_control,
                candidate_control,
                meter,
                len(left_matches),
                len(right_matches),
                baseline_derived=len(left_values),
                candidate_derived=len(right_values),
                coverage=partial_coverage,
                dependency_taint_origins=dependency_taint_origins,
            )
        return _error_evaluation(
            application,
            rule,
            halted.code or DiagnosticCode(value="diff.evaluate.internal"),
            baseline_control,
            candidate_control,
            meter,
            baseline_selected=len(left_matches),
            candidate_selected=len(right_matches),
            baseline_derived=len(left_values),
            candidate_derived=len(right_values),
            coverage=partial_coverage,
            dependency_taint_origins=dependency_taint_origins,
        )


def _not_evaluated(
    application: DiffApplication,
    rule: CompiledDiffRule,
    reason: DiffNotEvaluatedReason,
    baseline: SelectionControl,
    candidate: SelectionControl,
    meter: DiffMeter,
    *,
    baseline_selected: int = 0,
    candidate_selected: int = 0,
    dependency_taint_origins: tuple[TaintOriginId, ...] = (),
) -> DiffEvaluation:
    coverage = msgspec.structs.replace(
        empty_diff_coverage(),
        baseline_selected=Count(value=baseline_selected),
        candidate_selected=Count(value=candidate_selected),
    )
    fact = DiffRuleFact(
        fact_id=derive_fact_id(application.application_id, engine="diff", role="parent"),
        application=application,
        rule=rule,
        outcome=NotEvaluated(reason=reason),
        counts=empty_diff_counts(),
        coverage=coverage,
        evidence=DiffEvidence(retained=(), omitted=Count(value=0)),
        dependency_taint_origins=dependency_taint_origins,
    )
    return _evaluation(fact, baseline, candidate, meter)


def _incomplete_evaluation(
    application: DiffApplication,
    rule: CompiledDiffRule,
    issue: DiffPipelineIssue,
    baseline: SelectionControl,
    candidate: SelectionControl,
    meter: DiffMeter,
    baseline_selected: int,
    candidate_selected: int,
    *,
    issues: tuple[DiffPipelineIssue, ...] | None = None,
    baseline_derived: int = 0,
    candidate_derived: int = 0,
    coverage: DiffCoverage | None = None,
    dependency_taint_origins: tuple[TaintOriginId, ...] = (),
) -> DiffEvaluation:
    actual_coverage = (
        msgspec.structs.replace(
            empty_diff_coverage(),
            baseline_selected=Count(value=baseline_selected),
            candidate_selected=Count(value=candidate_selected),
            baseline_derived=Count(value=baseline_derived),
            candidate_derived=Count(value=candidate_derived),
        )
        if coverage is None
        else coverage
    )
    fact = DiffRuleFact(
        fact_id=derive_fact_id(application.application_id, engine="diff", role="parent"),
        application=application,
        rule=rule,
        outcome=Incomplete(
            reason=DiffIncompleteReason(
                side=issue.side,
                stage=issue.stage,
                cause=issue.cause,
            ),
            issues=(issue,) if issues is None else issues,
        ),
        counts=empty_diff_counts(),
        coverage=actual_coverage,
        evidence=DiffEvidence(retained=(), omitted=Count(value=0)),
        dependency_taint_origins=dependency_taint_origins,
    )
    return _evaluation(fact, baseline, candidate, meter)


def _error_evaluation(
    application: DiffApplication,
    rule: CompiledDiffRule,
    code: DiagnosticCode,
    baseline: SelectionControl,
    candidate: SelectionControl,
    meter: DiffMeter,
    *,
    baseline_selected: int = 0,
    candidate_selected: int = 0,
    baseline_derived: int = 0,
    candidate_derived: int = 0,
    coverage: DiffCoverage | None = None,
    dependency_taint_origins: tuple[TaintOriginId, ...] = (),
) -> DiffEvaluation:
    actual_coverage = (
        msgspec.structs.replace(
            empty_diff_coverage(),
            baseline_selected=Count(value=baseline_selected),
            candidate_selected=Count(value=candidate_selected),
            baseline_derived=Count(value=baseline_derived),
            candidate_derived=Count(value=candidate_derived),
        )
        if coverage is None
        else coverage
    )
    fact = DiffRuleFact(
        fact_id=derive_fact_id(application.application_id, engine="diff", role="parent"),
        application=application,
        rule=rule,
        outcome=DiffEvaluationError(code=code),
        counts=empty_diff_counts(),
        coverage=actual_coverage,
        evidence=DiffEvidence(retained=(), omitted=Count(value=0)),
        dependency_taint_origins=dependency_taint_origins,
    )
    return _evaluation(fact, baseline, candidate, meter)


def _evaluation(
    fact: DiffRuleFact,
    baseline: SelectionControl,
    candidate: SelectionControl,
    meter: DiffMeter,
) -> DiffEvaluation:
    return DiffEvaluation(
        fact=fact,
        baseline_selection=baseline.budgets,
        candidate_selection=candidate.budgets,
        budgets=meter.budgets,
    )


__all__ = ("DiffEvaluation", "evaluate_diff")
