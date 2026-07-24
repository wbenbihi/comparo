"""Exercise every diff selection-admission and applicability terminal state."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.diffs.admission import SelectionAdmission
from comparo.core.diffs.admission import _selection_code
from comparo.core.diffs.admission import admit_selection
from comparo.core.diffs.admission import applicable
from comparo.core.diffs.admission import selection_dependency_taint
from comparo.core.diffs.admission import selection_issue
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffPipelineIssue
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.diffs.rules import DiffCompletenessRequirement
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionPartialReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonString
from tests.core.diffs.factories import meter
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.selectors.test_result_coverage import _body_partial
from tests.core.domain.selectors.test_result_coverage import _failure
from tests.core.domain.selectors.test_result_coverage import _selection_exhaustion
from tests.core.domain.selectors.test_result_coverage import _stream_partial

_SIDE = DiffSide.BASELINE
_CODE = DiagnosticCode(value="diff.test.selection")


def _window_partial() -> SelectionPartial:
    base = _stream_partial()
    issue = base.issues[0]
    assert base.stream_extent is not None
    return SelectionPartial(
        selector=base.selector,
        environment_execution_id=base.environment_execution_id,
        matches=(SelectionMatch(value=JsonString(value="record"), location=issue.location),),
        reason=SelectionPartialReason.STREAM_WINDOW_INCOMPLETE,
        views=base.views,
        stream_extent=msgspec.structs.replace(
            base.stream_extent,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.SAFETY_COUNT_LIMIT,
        ),
    )


def _stream_ready(completeness: StreamCompleteness) -> SelectionReady:
    partial = _window_partial()
    assert partial.stream_extent is not None
    return SelectionReady(
        selector=partial.selector,
        environment_execution_id=partial.environment_execution_id,
        matches=partial.matches,
        views=partial.views,
        stream_extent=msgspec.structs.replace(
            partial.stream_extent,
            completeness=completeness,
            termination=(
                StreamTerminationReason.REMOTE_EOF
                if completeness is StreamCompleteness.FULL
                else StreamTerminationReason.EVENT_COUNT_WINDOW
            ),
        ),
    )


def test_ready_and_partial_admission_respects_authored_completeness() -> None:
    body = _body_partial()
    body_ready = SelectionReady(
        selector=body.selector,
        environment_execution_id=body.environment_execution_id,
        matches=body.matches,
        views=body.views,
    )
    assert admit_selection(_SIDE, body_ready, DiffCompletenessRequirement.COMPLETE).issue is None

    full = _stream_ready(StreamCompleteness.FULL)
    window = _stream_ready(StreamCompleteness.WINDOW)
    assert admit_selection(_SIDE, full, DiffCompletenessRequirement.COMPLETE).issue is None
    assert admit_selection(_SIDE, window, DiffCompletenessRequirement.CLOSED_WINDOW).issue is None
    assert admit_selection(_SIDE, window, DiffCompletenessRequirement.PARTIAL_PREFIX).issue is None
    rejected = admit_selection(_SIDE, window, DiffCompletenessRequirement.COMPLETE)
    assert rejected.issue is not None
    assert rejected.partial

    admitted = admit_selection(
        _SIDE,
        _window_partial(),
        DiffCompletenessRequirement.PARTIAL_PREFIX,
    )
    assert admitted.issue is None
    assert admitted.partial
    assert admitted.matches
    body_rejected = admit_selection(
        _SIDE,
        body,
        DiffCompletenessRequirement.PARTIAL_PREFIX,
    )
    assert body_rejected.issue is not None
    assert body_rejected.partial


def test_every_selection_terminal_maps_to_a_value_free_issue() -> None:
    failure = _failure()
    terminals: tuple[SelectionResult, ...] = (
        SelectionBudgetExceeded(exhaustion=_selection_exhaustion()),
        SelectionCancelled(code=_CODE),
        SelectionUnsupported(code=_CODE),
        SelectionObservedInvalid(code=_CODE),
        SelectionFactUnavailable(code=_CODE),
        SelectionDecodeFailure(code=_CODE, provenance=failure),
        SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_BODY),
        SelectionIncomplete(reason=SelectionIncompleteReason.PARTIAL_CAPTURE),
    )
    expected_causes = (
        DiffIncompleteCause.BUDGET_EXCEEDED,
        DiffIncompleteCause.CANCELLED,
        DiffIncompleteCause.UNSUPPORTED,
        DiffIncompleteCause.OBSERVED_INVALID,
        DiffIncompleteCause.UNAVAILABLE,
        DiffIncompleteCause.UNAVAILABLE,
        DiffIncompleteCause.UNAVAILABLE,
        DiffIncompleteCause.UNAVAILABLE,
    )
    for terminal, cause in zip(terminals, expected_causes, strict=True):
        issue = selection_issue(_SIDE, terminal)
        assert issue is not None
        assert issue.cause is cause
        assert admit_selection(_SIDE, terminal, DiffCompletenessRequirement.COMPLETE).matches == ()

    decode_budget = SelectionBudgetExceeded(
        exhaustion=view_exhaustion(ViewBudgetKind.JSON_NODES),
        view_failure=failure,
    )
    issue = selection_issue(_SIDE, decode_budget)
    assert issue is not None
    assert issue.view_failure == failure

    assert selection_issue(_SIDE, _stream_ready(StreamCompleteness.FULL)) is None
    partial_issue = selection_issue(_SIDE, _window_partial())
    assert partial_issue is not None
    assert partial_issue.cause is DiffIncompleteCause.PARTIAL


def test_applicability_and_admission_models_reject_ambiguous_states() -> None:
    body = _body_partial()
    ready = SelectionReady(
        selector=body.selector,
        environment_execution_id=body.environment_execution_id,
        matches=body.matches,
        views=body.views,
    )
    with pytest.raises(ValueError, match="JSON boolean"):
        applicable(ready)
    with pytest.raises(ValueError, match="exactly one boolean"):
        applicable(SelectionFactUnavailable(code=_CODE))
    boolean_match = msgspec.structs.replace(body.matches[0], value=JsonBoolean(value=True))
    boolean_ready = msgspec.structs.replace(ready, matches=(boolean_match,))
    assert applicable(boolean_ready)
    with pytest.raises(ValueError, match="requires ready"):
        selection_dependency_taint(meter(), SelectionFactUnavailable(code=_CODE))

    admission = SelectionAdmission(matches=(), issue=None, partial=False)
    with pytest.raises(TypeError, match="immutable typed tuple"):
        msgspec.structs.replace(admission, matches=cast("tuple[SelectionMatch, ...]", []))
    with pytest.raises(TypeError, match="DiffPipelineIssue"):
        msgspec.structs.replace(admission, issue=cast("DiffPipelineIssue", "bad"))
    with pytest.raises(TypeError, match="bool"):
        msgspec.structs.replace(admission, partial=cast("bool", 0))

    assert _selection_code(SelectionObservedInvalid(code=_CODE)) == _CODE
    assert _selection_code(SelectionCancelled(code=_CODE)).value == "diff.selection.unavailable"
