"""Diff selection admission, completeness, and applicability contracts."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.value_support import merge_taint_origins
from comparo.core.diffs.value_support import reveal
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.results import DiffEvaluationStage
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffPipelineIssue
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionPartialReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SelectionViewFailureProvenance
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonBoolean


@final
class SelectionAdmission(msgspec.Struct, frozen=True, kw_only=True):
    """Selected complete values plus an explicit partial-input marker."""

    matches: tuple[SelectionMatch, ...]
    issue: DiffPipelineIssue | None
    partial: bool

    def __post_init__(self) -> None:
        """Require a closed immutable admission result."""
        if type(self.matches) is not tuple or any(
            type(match) is not SelectionMatch for match in self.matches
        ):
            raise TypeError("selection admission matches must be an immutable typed tuple")
        if self.issue is not None and type(self.issue) is not DiffPipelineIssue:
            raise TypeError("selection admission issue must be DiffPipelineIssue")
        if type(self.partial) is not bool:
            raise TypeError("selection admission partial marker must be bool")


def admit_selection(
    side: DiffSide,
    result: SelectionResult,
    completeness: DiffCompletenessRequirement,
) -> SelectionAdmission:
    """Admit only authored source completeness and preserve rejected prefix counts."""
    if type(result) is SelectionReady:
        extent = result.stream_extent
        if extent is None or completeness is DiffCompletenessRequirement.PARTIAL_PREFIX:
            return SelectionAdmission(matches=result.matches, issue=None, partial=False)
        if completeness is DiffCompletenessRequirement.COMPLETE and (
            extent.completeness is StreamCompleteness.FULL
        ):
            return SelectionAdmission(matches=result.matches, issue=None, partial=False)
        if completeness is DiffCompletenessRequirement.CLOSED_WINDOW and (
            extent.completeness in {StreamCompleteness.FULL, StreamCompleteness.WINDOW}
        ):
            return SelectionAdmission(matches=result.matches, issue=None, partial=False)
        return SelectionAdmission(
            matches=result.matches,
            issue=DiffPipelineIssue(
                side=side,
                stage=DiffEvaluationStage.CAPTURE,
                cause=DiffIncompleteCause.PARTIAL,
                code=DiagnosticCode(value="diff.selection.stream-extent-rejected"),
            ),
            partial=True,
        )
    if type(result) is SelectionPartial and (
        completeness is DiffCompletenessRequirement.PARTIAL_PREFIX
        and result.reason is SelectionPartialReason.STREAM_WINDOW_INCOMPLETE
    ):
        return SelectionAdmission(matches=result.matches, issue=None, partial=True)
    issue = selection_issue(side, result)
    if issue is None:  # pragma: no cover - closed union and ready branch above
        raise RuntimeError("non-ready selection terminal must produce a diff issue")
    return SelectionAdmission(
        matches=result.matches if type(result) is SelectionPartial else (),
        issue=issue,
        partial=type(result) is SelectionPartial,
    )


def selection_issue(side: DiffSide, result: SelectionResult) -> DiffPipelineIssue | None:
    """Map every terminal selection failure to a safe typed diff issue."""
    if type(result) is SelectionReady:
        return None
    if type(result) is SelectionPartial:
        return DiffPipelineIssue(
            side=side,
            stage=DiffEvaluationStage.CAPTURE,
            cause=DiffIncompleteCause.PARTIAL,
            code=DiagnosticCode(value="diff.selection.partial"),
        )
    if type(result) is SelectionBudgetExceeded:
        stage = (
            DiffEvaluationStage.DECODE
            if result.exhaustion.budget.stage is BudgetStage.DECODING
            else DiffEvaluationStage.SELECTION
        )
        return DiffPipelineIssue(
            side=side,
            stage=stage,
            cause=DiffIncompleteCause.BUDGET_EXCEEDED,
            exhaustion=result.exhaustion,
            view_failure=result.view_failure,
        )
    if type(result) is SelectionCancelled:
        return DiffPipelineIssue(
            side=side,
            stage=DiffEvaluationStage.SELECTION,
            cause=DiffIncompleteCause.CANCELLED,
            code=result.code,
            view_failure=result.view_failure,
        )
    if type(result) is SelectionUnsupported:
        return DiffPipelineIssue(
            side=side,
            stage=DiffEvaluationStage.SELECTION,
            cause=DiffIncompleteCause.UNSUPPORTED,
            code=result.code,
            view_failure=result.view_failure,
        )
    if type(result) is SelectionObservedInvalid:
        return DiffPipelineIssue(
            side=side,
            stage=DiffEvaluationStage.SELECTION,
            cause=DiffIncompleteCause.OBSERVED_INVALID,
            code=result.code,
        )
    code = _selection_code(result)
    stage = (
        DiffEvaluationStage.DECODE
        if type(result) is SelectionDecodeFailure
        else DiffEvaluationStage.SELECTION
    )
    view_failure: SelectionViewFailureProvenance | None
    if type(result) is SelectionDecodeFailure:
        view_failure = result.provenance
    elif type(result) is SelectionSourceUnavailable or type(result) is SelectionIncomplete:
        view_failure = result.view_failure
    else:
        view_failure = None
    return DiffPipelineIssue(
        side=side,
        stage=stage,
        cause=DiffIncompleteCause.UNAVAILABLE,
        code=code,
        view_failure=view_failure,
    )


def applicable(result: SelectionResult) -> bool:
    """Read one statically singular boolean applicability result."""
    if type(result) is not SelectionReady or len(result.matches) != 1:
        raise ValueError("ready applicability must contain exactly one boolean")
    raw = reveal(result.matches[0].value)
    if type(raw) is not JsonBoolean:
        raise ValueError("ready applicability must contain a JSON boolean")
    return raw.value


def selection_dependency_taint(
    meter: DiffMeter,
    *results: SelectionResult,
) -> tuple[TaintOriginId, ...]:
    """Collect safe applicability-dependency lineage from ready selection matches."""
    groups: list[tuple[TaintOriginId, ...]] = []
    for result in results:
        if type(result) is not SelectionReady:
            raise ValueError("applicability dependency requires ready selections")
        for match in result.matches:
            if is_sensitive_value(match.value):
                groups.extend((match.value.origins,))
    return merge_taint_origins(meter, *groups)


def _selection_code(result: SelectionResult) -> DiagnosticCode:
    if type(result) is SelectionFactUnavailable:
        return result.code
    if type(result) is SelectionObservedInvalid:
        return result.code
    if type(result) is SelectionDecodeFailure:
        return result.code
    if type(result) is SelectionSourceUnavailable:
        return DiagnosticCode(value=f"diff.selection.{result.reason.value}")
    if type(result) is SelectionIncomplete:
        return DiagnosticCode(value=f"diff.selection.{result.reason.value}")
    return DiagnosticCode(value="diff.selection.unavailable")


__all__ = (
    "SelectionAdmission",
    "admit_selection",
    "applicable",
    "selection_dependency_taint",
    "selection_issue",
)
