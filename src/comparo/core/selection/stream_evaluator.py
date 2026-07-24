"""Pure native-stream selection over a complete pre-resolved item snapshot."""

from __future__ import annotations

from typing import cast

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.stream_items import JsonStreamRecord
from comparo.core.domain.http.stream_items import JsonStreamRecordError
from comparo.core.domain.http.stream_items import SseComment
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseIdStateTransition
from comparo.core.domain.http.stream_items import SseJsonBudgetExceeded
from comparo.core.domain.http.stream_items import SseJsonDecodeError
from comparo.core.domain.http.stream_items import SseJsonReady
from comparo.core.domain.http.stream_items import SseRetryControl
from comparo.core.domain.http.stream_items import SseUnknownField
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.primitives import Duration
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.paths import JsonPointerPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionItemBudgetIssue
from comparo.core.domain.selectors.results import SelectionItemDecodeIssue
from comparo.core.domain.selectors.results import SelectionItemIssue
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionPartialReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SelectionViewProvenance
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.json_evaluator import JsonEvaluationReady
from comparo.core.selection.json_evaluator import evaluate_json
from comparo.core.selection.match_accounting import account_match_before_append
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.timing_values import select_timing_value

_ACCESS = SensitiveValueAccess(purpose="selector.stream")


def evaluate_stream_source(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    meter: SelectionMeter,
) -> SelectionResult:
    """Evaluate one stream source without reading the decoded-view store."""
    stream = snapshot.stream
    if stream is None:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.OBSERVATION_UNAVAILABLE)
    metadata = _stream_metadata(snapshot, selector, stream, meter)
    if metadata is not None:
        return metadata
    exhaustion = meter.charge_work(len(snapshot.stream_items)) if snapshot.stream_items else None
    if exhaustion is not None:
        return SelectionBudgetExceeded(exhaustion=exhaustion)
    selected = _select_matches(snapshot, selector, meter)
    if not isinstance(selected, tuple):
        return selected
    return _stream_result(snapshot, selector, stream, selected)


def _select_matches(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    meter: SelectionMeter,
) -> tuple[SelectionMatch, ...] | SelectionBudgetExceeded | SelectionUnsupported:
    if type(selector.path) in {JsonPointerPath, JsonPathExpression}:
        return _json_matches(snapshot, selector, meter)
    retained: list[SelectionMatch] = []
    for envelope in snapshot.stream_items:
        match = _match(snapshot, selector, envelope)
        if match is None:
            continue
        if (exhausted := account_match_before_append(match, meter)) is not None:
            return exhausted
        retained.append(match)
    return tuple(retained)


def _stream_result(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    stream: StreamObservation,
    matches: tuple[SelectionMatch, ...],
) -> SelectionResult:
    issues = tuple(
        issue
        for envelope in snapshot.stream_items
        if (issue := _issue(snapshot, selector, envelope)) is not None
    )
    provenance = _view_provenance(selector, stream)
    if issues:
        return SelectionPartial(
            selector=selector,
            environment_execution_id=snapshot.observation.execution_id,
            matches=matches,
            reason=SelectionPartialReason.ITEM_FAILURE,
            issues=issues,
            views=provenance,
            stream_extent=stream.extent,
        )
    if matches and stream.extent.completeness is StreamCompleteness.INCOMPLETE:
        reason = SelectionPartialReason.STREAM_WINDOW_INCOMPLETE
        return SelectionPartial(
            selector=selector,
            environment_execution_id=snapshot.observation.execution_id,
            matches=matches,
            reason=reason,
            views=provenance,
            stream_extent=stream.extent,
        )
    if stream.extent.completeness is StreamCompleteness.INCOMPLETE:
        return SelectionIncomplete(reason=SelectionIncompleteReason.STREAM_WINDOW_INCOMPLETE)
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=matches,
        views=provenance,
    )


def _stream_metadata(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    stream: StreamObservation,
    meter: SelectionMeter,
) -> SelectionResult | None:
    if selector.source is SelectorSource.STREAM_TIMINGS:
        return _stream_timing(snapshot, selector, stream, meter)
    if selector.source is SelectorSource.STREAM_TERMINATION:
        return _termination(snapshot, selector, stream, meter)
    return None


def _issue(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    envelope: StreamItemEnvelope,
) -> SelectionItemIssue | None:
    item = envelope.item
    if selector.source is SelectorSource.STREAM_RECORDS and type(item) is JsonStreamRecordError:
        return SelectionItemDecodeIssue(
            location=_location(snapshot, selector, envelope),
            decoder=envelope.view.decoder,
            code=item.code,
        )
    if selector.source is SelectorSource.STREAM_EVENT_DATA_JSON and type(item) is SseEvent:
        nested = item.nested_data
        if type(nested) is SseJsonDecodeError:
            return SelectionItemDecodeIssue(
                location=_location(snapshot, selector, envelope),
                decoder=nested.decoder,
                code=nested.code,
            )
        if type(nested) is SseJsonBudgetExceeded:
            return SelectionItemBudgetIssue(
                location=_location(snapshot, selector, envelope),
                decoder=nested.decoder,
                exhaustion=nested.exhaustion,
            )
    return None


def _match(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    envelope: StreamItemEnvelope,
) -> SelectionMatch | None:
    item = envelope.item
    value: JsonValue | SensitiveValue[JsonValue]
    if selector.source is SelectorSource.STREAM_RECORDS:
        if type(item) is not JsonStreamRecord:
            return None
        value = item.value
    elif selector.source is SelectorSource.STREAM_EVENTS:
        if type(item) is not SseEvent:
            return None
        value = _event_value(item)
    elif selector.source is SelectorSource.STREAM_EVENT_DATA_JSON:
        if type(item) is not SseEvent or type(item.nested_data) is not SseJsonReady:
            return None
        value = item.nested_data.value
    else:
        if type(item) not in {SseComment, SseRetryControl, SseIdStateTransition, SseUnknownField}:
            return None
        value = _control_value(
            cast(
                "SseComment | SseRetryControl | SseIdStateTransition | SseUnknownField",
                item,
            )
        )
    return SelectionMatch(value=value, location=_location(snapshot, selector, envelope))


def _json_matches(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    meter: SelectionMeter,
) -> tuple[SelectionMatch, ...] | SelectionBudgetExceeded | SelectionUnsupported:
    path = selector.path
    if type(path) not in {JsonPointerPath, JsonPathExpression}:
        raise TypeError("stream JSON evaluation requires a compiled JSON path")
    json_path = cast("JsonPointerPath | JsonPathExpression", path)
    matches: list[SelectionMatch] = []
    for envelope in snapshot.stream_items:
        value = _json_input(selector, envelope)
        if value is None:
            continue
        evaluated = evaluate_json(value, json_path, meter)
        if type(evaluated) is not JsonEvaluationReady:
            return evaluated
        matches.extend(
            SelectionMatch(
                value=match.value,
                location=_location(
                    snapshot,
                    selector,
                    envelope,
                    nested_pointer=match.pointer,
                ),
            )
            for match in evaluated.matches
        )
    return tuple(matches)


def _json_input(
    selector: CompiledSelector,
    envelope: StreamItemEnvelope,
) -> JsonValue | SensitiveValue[JsonValue] | None:
    item = envelope.item
    if selector.source is SelectorSource.STREAM_RECORDS:
        return item.value if type(item) is JsonStreamRecord else None
    if selector.source is SelectorSource.STREAM_EVENTS:
        return _event_value(item) if type(item) is SseEvent else None
    if selector.source is SelectorSource.STREAM_EVENT_DATA_JSON:
        if type(item) is SseEvent and type(item.nested_data) is SseJsonReady:
            return item.nested_data.value
        return None
    raise ValueError("compiled JSON stream path has an incompatible source")


def _location(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    envelope: StreamItemEnvelope,
    *,
    nested_pointer: JsonPointer | None = None,
) -> StreamLocation:
    location = envelope.item.location
    return StreamLocation(
        environment_execution_id=snapshot.observation.execution_id,
        source=selector.source,
        view_id=envelope.view.view_id,
        connection_segment=location.segment_index,
        item_index=location.item_index,
        category_index=location.category_index,
        nested_pointer=nested_pointer,
    )


def _event_value(event: SseEvent) -> JsonValue | SensitiveValue[JsonValue]:
    members = (
        JsonObjectMember(name="kind", value=JsonString(value="event")),
        JsonObjectMember(name="data", value=JsonString(value=_text(event.data))),
        JsonObjectMember(name="event", value=JsonString(value=_text(event.event_type))),
        JsonObjectMember(name="last-event-id", value=JsonString(value=_text(event.last_event_id))),
    )
    return _contain(
        JsonObject(members=members),
        (event.data, event.event_type, event.last_event_id),
    )


def _control_value(
    item: SseComment | SseRetryControl | SseIdStateTransition | SseUnknownField,
) -> JsonValue | SensitiveValue[JsonValue]:
    values: tuple[object, ...]
    if type(item) is SseComment:
        values = (item.value,)
        members = _members("comment", ("value", JsonString(value=_text(item.value))))
    elif type(item) is SseRetryControl:
        values = (item.retry_after,)
        duration = _duration(item.retry_after)
        members = _members("retry", ("nanoseconds", JsonInteger(value=duration.nanoseconds)))
    elif type(item) is SseIdStateTransition:
        values = (item.previous, item.current)
        members = _members(
            "id-transition",
            ("previous", JsonString(value=_text(item.previous))),
            ("current", JsonString(value=_text(item.current))),
        )
    else:
        values = (item.name, item.value)
        members = _members(
            "unknown-field",
            ("name", JsonString(value=_text(item.name))),
            ("value", JsonString(value=_text(item.value))),
        )
    return _contain(JsonObject(members=members), values)


def _members(kind: str, *members: tuple[str, JsonValue]) -> tuple[JsonObjectMember, ...]:
    return (
        JsonObjectMember(name="kind", value=JsonString(value=kind)),
        *(JsonObjectMember(name=name, value=value) for name, value in members),
    )


def _text(value: BodyText | SensitiveValue[BodyText]) -> str:
    if type(value) is BodyText:
        return value.value
    return value.reveal(access=_ACCESS).value


def _duration(value: Duration | SensitiveValue[Duration]) -> Duration:
    if type(value) is Duration:
        return value
    return value.reveal(access=_ACCESS)


def _contain(
    value: JsonValue,
    components: tuple[object, ...],
) -> JsonValue | SensitiveValue[JsonValue]:
    origins: list[TaintOriginId] = []
    for component in components:
        if is_sensitive_value(component):
            origins.extend(component.origins)
    return SensitiveValue(value, origins=tuple(dict.fromkeys(origins))) if origins else value


def _view_provenance(
    selector: CompiledSelector,
    stream: StreamObservation,
) -> tuple[SelectionViewProvenance, ...]:
    handles = tuple(dict.fromkeys(segment.view for segment in stream.segments))
    return tuple(
        SelectionViewProvenance(
            view_id=handle.view_id,
            source=selector.source,
            requested=(
                BodyViewKind.AUTO
                if handle.auto_selection is not None
                else _stream_body_view_from_kind(handle.decoder.view)
            ),
            decoder=handle.decoder,
            auto_selection=handle.auto_selection,
            media=handle.source.media if type(handle.source) is BodyHandle else None,
            completeness=handle.completeness,
        )
        for handle in handles
    )


def _stream_body_view_from_kind(kind: ViewKind) -> BodyViewKind:
    try:
        return {
            ViewKind.SSE: BodyViewKind.SSE_EVENTS,
            ViewKind.NDJSON: BodyViewKind.NDJSON_RECORDS,
            ViewKind.JSON_SEQUENCE: BodyViewKind.JSON_SEQUENCE_RECORDS,
        }[kind]
    except KeyError as error:
        raise ValueError("native stream item uses a non-stream decoded view") from error


def _termination(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    stream: StreamObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    if type(selector.path) is StaticFieldPath:
        field = selector.path
    else:
        field = StaticFieldPath(segments=("stream", "termination"))
    values = {
        ("stream", "termination"): JsonString(value=stream.extent.termination.value),
        ("stream", "termination", "reason"): JsonString(value=stream.extent.termination.value),
        ("stream", "termination", "completeness"): JsonString(
            value=stream.extent.completeness.value
        ),
    }
    value = values.get(field.segments)
    if value is None:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.field.unsupported"))
    terminal = stream.segments[-1]
    match = SelectionMatch(
        value=value,
        location=ExchangeLocation(
            environment_execution_id=snapshot.observation.execution_id,
            source=selector.source,
            exchange_id=terminal.exchange_id,
            field=field,
        ),
    )
    if (exhausted := account_match_before_append(match, meter)) is not None:
        return exhausted
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=(match,),
    )


def _stream_timing(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    stream: StreamObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    if type(selector.path) is not StaticFieldPath:  # pragma: no cover - compiled invariant
        raise ValueError("stream timing selector requires a static field")
    metrics: dict[tuple[str, ...], TimingMetric] = {
        ("stream", "timings", "time-to-first-record"): stream.timings.time_to_first_record,
        (
            "stream",
            "timings",
            "observation-window-elapsed",
        ): stream.timings.observation_window_elapsed,
    }
    selected = select_timing_value(metrics[selector.path.segments])
    if type(selected) is SelectionFactUnavailable:
        return selected
    terminal = stream.segments[-1]
    match = SelectionMatch(
        value=selected,
        location=ExchangeLocation(
            environment_execution_id=snapshot.observation.execution_id,
            source=selector.source,
            exchange_id=terminal.exchange_id,
            field=selector.path,
        ),
    )
    if (exhausted := account_match_before_append(match, meter)) is not None:
        return exhausted
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=(match,),
    )
