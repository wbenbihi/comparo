"""Pure request and response body selection from validated snapshot payloads."""

from __future__ import annotations

from typing import cast

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecoderSpec
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import BodyId
from comparo.core.domain.selectors.locations import BodyLocation
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import ViewLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionPartialReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SelectionViewFailureProvenance
from comparo.core.domain.selectors.results import SelectionViewProvenance
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.json_evaluator import JsonEvaluationReady
from comparo.core.selection.json_evaluator import SelectableJson
from comparo.core.selection.json_evaluator import evaluate_json
from comparo.core.selection.match_accounting import account_match_before_append
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.snapshot import body_bytes_for
from comparo.core.selection.snapshot import body_view_for


def evaluate_request_body(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    request: FinalizedWireRequest,
    meter: SelectionMeter,
) -> SelectionResult:
    """Select one finalized request body without reading its byte-source port."""
    body = request.body
    if type(body) is not MaterializedBody:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_BODY)
    return _body_value(snapshot, selector, body.source.source_id, meter, partial=False)


def evaluate_response_body(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    """Select one response body while preserving capture completeness."""
    if type(response.body) is NoResponseBody or response.body.handle is None:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_BODY)
    return _body_value(
        snapshot,
        selector,
        response.body.handle.body_id,
        meter,
        partial=response.body.handle.extent is BodyCaptureExtent.PARTIAL,
    )


def _body_value(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    body_id: BodyId,
    meter: SelectionMeter,
    *,
    partial: bool,
) -> SelectionResult:
    view = selector.body_view
    if view is None:
        raise ValueError("compiled body selector must declare a body view")
    if view is BodyViewKind.HANDLE:
        return _body_handle(snapshot, selector, body_id, view, meter)
    if view is BodyViewKind.ENTITY_BYTES:
        return _body_bytes(snapshot, selector, body_id, view, meter, partial=partial)
    return _decoded_body(snapshot, selector, body_id, view, meter, capture_partial=partial)


def _body_handle(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    body_id: BodyId,
    view: BodyViewKind,
    meter: SelectionMeter,
) -> SelectionResult:
    match = SelectionMatch(
        value=body_id,
        location=BodyLocation(
            environment_execution_id=snapshot.observation.execution_id,
            source=selector.source,
            body_id=body_id,
            body_view=view,
        ),
    )
    exhausted = account_match_before_append(match, meter)
    return exhausted if exhausted is not None else _ready(snapshot, selector, match)


def _body_bytes(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    body_id: BodyId,
    view: BodyViewKind,
    meter: SelectionMeter,
    *,
    partial: bool,
) -> SelectionResult:
    payload = body_bytes_for(snapshot, body_id)
    if payload is None:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.OBSERVATION_UNAVAILABLE)
    match = SelectionMatch(
        value=payload.value,
        location=BodyLocation(
            environment_execution_id=snapshot.observation.execution_id,
            source=selector.source,
            body_id=body_id,
            body_view=view,
        ),
    )
    if (exhausted := account_match_before_append(match, meter)) is not None:
        return exhausted
    if partial:
        return SelectionPartial(
            selector=selector,
            environment_execution_id=snapshot.observation.execution_id,
            matches=(match,),
            reason=SelectionPartialReason.VIEW_PREFIX,
        )
    return _ready(
        snapshot,
        selector,
        match,
    )


def _decoded_body(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    body_id: BodyId,
    view: BodyViewKind,
    meter: SelectionMeter,
    *,
    capture_partial: bool,
) -> SelectionResult:
    resolved = body_view_for(snapshot, body_id, view)
    if resolved is None:
        return (
            SelectionIncomplete(reason=SelectionIncompleteReason.PARTIAL_CAPTURE)
            if capture_partial
            else SelectionSourceUnavailable(reason=SourceUnavailableReason.OBSERVATION_UNAVAILABLE)
        )
    if type(resolved) is FailedBodyView:
        return _view_failure(selector, resolved)
    if resolved.handle.completeness.value != "full":
        if type(selector.path) is NoPath and resolved.handle.decoder.view is not ViewKind.JSON:
            ready = _decoded_ready(snapshot, selector, resolved, view, meter)
            if type(ready) is not SelectionReady:
                return ready
            return SelectionPartial(
                selector=selector,
                environment_execution_id=snapshot.observation.execution_id,
                matches=ready.matches,
                reason=SelectionPartialReason.VIEW_PREFIX,
                views=ready.views,
            )
        return SelectionIncomplete(
            reason=SelectionIncompleteReason.PARTIAL_VIEW,
            view_failure=_failure_provenance(
                selector,
                resolved.handle.source,
                view,
                resolved.handle.decoder,
                resolved.handle.auto_selection,
            ),
        )
    if type(selector.path) in {JsonPointerPath, JsonPathExpression}:
        path = cast("JsonPointerPath | JsonPathExpression", selector.path)
        evaluated = evaluate_json(cast(SelectableJson, resolved.value), path, meter)
        if type(evaluated) is not JsonEvaluationReady:
            return evaluated
        return _json_ready(snapshot, selector, resolved, view, evaluated)
    return _decoded_ready(snapshot, selector, resolved, view, meter)


def _json_ready(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    resolved: ResolvedBodyView,
    view: BodyViewKind,
    evaluated: JsonEvaluationReady,
) -> SelectionReady:
    matches = tuple(
        SelectionMatch(
            value=match.value,
            location=JsonLocation(
                environment_execution_id=snapshot.observation.execution_id,
                source=selector.source,
                view_id=resolved.handle.view_id,
                pointer=match.pointer,
                body_view=view,
            ),
        )
        for match in evaluated.matches
    )
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=matches,
        views=(_provenance(selector, resolved, view),),
    )


def _decoded_ready(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    resolved: ResolvedBodyView,
    view: BodyViewKind,
    meter: SelectionMeter,
) -> SelectionResult:
    match = SelectionMatch(
        value=resolved.value,
        location=ViewLocation(
            environment_execution_id=snapshot.observation.execution_id,
            source=selector.source,
            view_id=resolved.handle.view_id,
            body_view=view,
        ),
    )
    if (exhausted := account_match_before_append(match, meter)) is not None:
        return exhausted
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=(match,),
        views=(_provenance(selector, resolved, view),),
    )


def _provenance(
    selector: CompiledSelector,
    resolved: ResolvedBodyView,
    view: BodyViewKind,
) -> SelectionViewProvenance:
    source = resolved.handle.source
    return SelectionViewProvenance(
        view_id=resolved.handle.view_id,
        source=selector.source,
        requested=view,
        decoder=resolved.handle.decoder,
        auto_selection=resolved.handle.auto_selection,
        media=source.media if type(source) is BodyHandle else None,
        completeness=resolved.handle.completeness,
    )


def _view_failure(selector: CompiledSelector, view: FailedBodyView) -> SelectionResult:
    from comparo.core.domain.http.views import ViewBudgetExceeded
    from comparo.core.domain.http.views import ViewDecodeError
    from comparo.core.domain.http.views import ViewIncomplete
    from comparo.core.domain.http.views import ViewNoBody
    from comparo.core.domain.http.views import ViewUnsupported

    outcome = view.outcome
    provenance = _failure_provenance(
        selector,
        view.source,
        view.requested_view,
        outcome.decoder,
        outcome.auto_selection,
    )
    if type(outcome) is ViewNoBody:
        return SelectionSourceUnavailable(
            reason=SourceUnavailableReason.NO_BODY,
            view_failure=provenance,
        )
    if type(outcome) is ViewDecodeError:
        return SelectionDecodeFailure(
            code=_code(f"selection.decode.{outcome.code.value.replace('-', '.')}"),
            provenance=provenance,
        )
    if type(outcome) is ViewIncomplete:
        return SelectionIncomplete(
            reason=SelectionIncompleteReason.PARTIAL_VIEW,
            view_failure=provenance,
            view_reason=outcome.reason,
        )
    if type(outcome) is ViewBudgetExceeded:
        return SelectionBudgetExceeded(
            exhaustion=outcome.exhaustion,
            view_failure=provenance,
        )
    if type(outcome) is ViewUnsupported:
        return SelectionUnsupported(
            code=_code("selection.view.unsupported"),
            view_failure=provenance,
        )
    return SelectionCancelled(
        code=_code("selection.view.cancelled"),
        view_failure=provenance,
    )


def _failure_provenance(
    selector: CompiledSelector,
    source: BodyHandle | RepeatableByteSource,
    requested: BodyViewKind,
    decoder: DecoderSpec,
    auto_selection: AutoViewSelection | None,
) -> SelectionViewFailureProvenance:
    return SelectionViewFailureProvenance(
        body_id=source.body_id if type(source) is BodyHandle else source.source_id,
        source=selector.source,
        requested=requested,
        decoder=decoder,
        auto_selection=auto_selection,
        media=source.media if type(source) is BodyHandle else None,
    )


def _ready(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    match: SelectionMatch,
) -> SelectionReady:
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=(match,),
    )


def _code(value: str) -> DiagnosticCode:
    return DiagnosticCode(value=value)
