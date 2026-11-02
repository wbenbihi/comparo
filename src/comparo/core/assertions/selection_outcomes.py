"""Translate selector completion and failure states into assertion outcomes."""

from __future__ import annotations

from comparo.core.domain.assertions.results import AssertionOutcome
from comparo.core.domain.assertions.results import AssertionUnavailable
from comparo.core.domain.assertions.results import AssertionUnavailableReason
from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.assertions.results import Violated
from comparo.core.domain.assertions.results import ViolationReason
from comparo.core.domain.assertions.rules import CompletenessRequirement
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SourceUnavailableReason


def decode_state(result: SelectionResult) -> AssertionOutcome | None:
    """Map terminal body-view state to the predicate-free decode outcome."""
    if type(result) is SelectionReady:
        return Satisfied()
    if type(result) in {SelectionDecodeFailure, SelectionObservedInvalid}:
        return Violated(reason=ViolationReason.STATE)
    if (
        type(result) is SelectionSourceUnavailable
        and result.reason is SourceUnavailableReason.NO_BODY
    ):
        return Violated(reason=ViolationReason.STATE)
    return None


def meets_completeness(
    result: SelectionReady | SelectionPartial,
    requirement: CompletenessRequirement,
) -> bool:
    """Apply explicit stream-extent and partial-prefix acceptance laws."""
    if type(result) is SelectionPartial:
        return requirement is CompletenessRequirement.PARTIAL_PREFIX
    if result.stream_extent is None:
        return True
    if requirement is CompletenessRequirement.COMPLETE:
        return result.stream_extent.completeness is StreamCompleteness.FULL
    return result.stream_extent.completeness in {
        StreamCompleteness.FULL,
        StreamCompleteness.WINDOW,
    }


def selection_unavailable(result: SelectionResult) -> AssertionUnavailable:
    """Preserve exact selector failure categories at the assertion boundary."""
    if type(result) is SelectionBudgetExceeded:
        return AssertionUnavailable(
            code=_code("assertion.selection.budget-exceeded"),
            reason=AssertionUnavailableReason.BUDGET_EXCEEDED,
            exhaustion=result.exhaustion,
        )
    if type(result) is SelectionCancelled:
        return AssertionUnavailable(code=result.code, reason=AssertionUnavailableReason.CANCELLED)
    if type(result) in {SelectionPartial, SelectionIncomplete}:
        return AssertionUnavailable(
            code=_code("assertion.selection.incomplete"),
            reason=AssertionUnavailableReason.INCOMPLETE,
        )
    if type(result) is SelectionUnsupported:
        return AssertionUnavailable(code=result.code, reason=AssertionUnavailableReason.CAPABILITY)
    if type(result) is SelectionSourceUnavailable:
        return AssertionUnavailable(
            code=_code(f"assertion.selection.{result.reason.value}"),
            reason=AssertionUnavailableReason.SELECTION,
        )
    if type(result) is SelectionFactUnavailable:
        return AssertionUnavailable(
            code=result.code,
            reason=AssertionUnavailableReason.SELECTION,
        )
    if type(result) is SelectionObservedInvalid:
        return AssertionUnavailable(
            code=result.code,
            reason=AssertionUnavailableReason.SELECTION,
        )
    if type(result) is SelectionDecodeFailure:
        return AssertionUnavailable(
            code=result.code,
            reason=AssertionUnavailableReason.SELECTION,
        )
    raise TypeError("ready selection cannot map to unavailable")


def _code(value: str) -> DiagnosticCode:
    return DiagnosticCode(value=value)
