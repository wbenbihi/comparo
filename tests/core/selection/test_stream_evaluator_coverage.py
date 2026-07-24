"""Branch-complete behavior coverage for native stream selection."""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.stream_items import JsonStreamRecord
from comparo.core.domain.http.stream_items import JsonStreamRecordError
from comparo.core.domain.http.stream_items import SseComment
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseIdStateTransition
from comparo.core.domain.http.stream_items import SseJsonBudgetExceeded
from comparo.core.domain.http.stream_items import SseJsonDecodeError
from comparo.core.domain.http.stream_items import SseJsonReady
from comparo.core.domain.http.stream_items import SseNestedDataView
from comparo.core.domain.http.stream_items import SseRetryControl
from comparo.core.domain.http.stream_items import SseUnknownField
from comparo.core.domain.http.stream_items import StreamItem
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.stream_termination import EventCountWindowEvidence
from comparo.core.domain.http.stream_termination import FramingDecodeFailureEvidence
from comparo.core.domain.http.stream_termination import ManualStopEvidence
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.views import AutoViewSelection
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import DecoderRegistryIdentity
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionItemBudgetIssue
from comparo.core.domain.selectors.results import SelectionItemDecodeIssue
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionPartialReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath
from comparo.core.selection import stream_evaluator as evaluator
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.snapshot import SelectionSnapshot
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import location
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_timings
from tests.core.domain.http.stream_factories import view_handle
from tests.core.selection.json_evaluator_factories import json_object
from tests.core.selection.json_evaluator_factories import selection_meter

_ORIGIN = TaintOriginId(value="stream-coverage")
_NO_PATH = NoPath()


def _selector(
    source: SelectorSource,
    *,
    path: SelectorPath = _NO_PATH,
    kind: ValueKind = ValueKind.UNKNOWN,
) -> CompiledSelector:
    capabilities = derive_selector_capabilities(source, path)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="9" * 64),
        ),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(kinds=(kind,), cardinality=capabilities.cardinality),
    )


def _stream(
    kind: StreamKind,
    *,
    records: int,
    controls: int = 0,
    errors: int = 0,
    incomplete: bool = False,
) -> StreamObservation:
    end_reason = SegmentEndReason.REMOTE_EOF
    evidence = None
    extent = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )
    if incomplete:
        end_reason = SegmentEndReason.FAILURE if errors else SegmentEndReason.CANCELLED
        evidence = FramingDecodeFailureEvidence() if errors else ManualStopEvidence()
        extent = StreamExtent(
            completeness=StreamCompleteness.INCOMPLETE,
            termination=(
                StreamTerminationReason.DECODE_FAILURE
                if errors
                else StreamTerminationReason.MANUAL_STOP
            ),
        )
    timings = stream_timings() if records else _empty_timings()
    return StreamObservation(
        records=records_handle(
            kind,
            records=records,
            controls=controls,
            errors=errors,
        ),
        segments=(
            segment(
                index=0,
                first=0,
                count=records,
                controls=controls,
                errors=errors,
                item_count=records + controls + errors,
                kind=kind,
                end_reason=end_reason,
            ),
        ),
        captured_bytes=ByteCount(value=4),
        extent=extent,
        trailing_incomplete=(),
        timings=timings,
        termination_evidence=evidence,
    )


def _empty_timings() -> StreamTimings:
    return StreamTimings(
        time_to_first_record=NotPerformed(reason=NotPerformedReason.EMPTY_STREAM),
        observation_window_elapsed=stream_timings().observation_window_elapsed,
    )


def _sealed[ValueT](value: ValueT) -> SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=(_ORIGIN,))


def _event(
    *,
    item: int = 0,
    nested: SseNestedDataView | None = None,
    sensitive: bool = True,
) -> SseEvent:
    data = BodyText(value="data")
    event_type = BodyText(value="message")
    last_event_id = BodyText(value="event-1")
    if sensitive:
        return SseEvent(
            data=_sealed(data),
            event_type=_sealed(event_type),
            last_event_id=_sealed(last_event_id),
            fields=(),
            location=location(item=item, category=0, start=0, end=0),
            nested_data=nested,
        )
    return SseEvent(
        data=data,
        event_type=event_type,
        last_event_id=last_event_id,
        fields=(),
        location=location(item=item, category=0, start=0, end=0),
        nested_data=nested,
    )


def _snapshot(
    stream: StreamObservation,
    items: tuple[StreamItem, ...],
) -> SelectionSnapshot:
    view = stream.segments[0].view
    envelopes = tuple(StreamItemEnvelope(view=view, item=item) for item in items)
    return SelectionSnapshot(
        observation=stream.records.owner,
        stream=stream,
        stream_items=envelopes,
    )


def _evaluate(
    snapshot: SelectionSnapshot,
    source: SelectorSource,
    *,
    path: SelectorPath = _NO_PATH,
    kind: ValueKind = ValueKind.UNKNOWN,
    meter: SelectionMeter | None = None,
) -> SelectionResult:
    return evaluator.evaluate_stream_source(
        snapshot,
        _selector(source, path=path, kind=kind),
        meter or selection_meter(),
    )


def test_absent_stream_and_outer_work_budget_are_explicit() -> None:
    observation = records_handle(StreamKind.SSE, records=0).owner
    missing = evaluator.evaluate_stream_source(
        SelectionSnapshot(observation=observation),
        _selector(SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT),
        selection_meter(),
    )
    assert type(missing) is SelectionSourceUnavailable

    stream = _stream(StreamKind.SSE, records=1)
    snapshot = _snapshot(stream, (_event(),))
    exhausted = _evaluate(
        snapshot,
        SelectorSource.STREAM_EVENTS,
        kind=ValueKind.OBJECT,
        meter=selection_meter(work=0),
    )
    assert type(exhausted) is SelectionBudgetExceeded


def test_sse_event_selection_contains_sensitive_text_and_location() -> None:
    stream = _stream(StreamKind.SSE, records=1)
    snapshot = _snapshot(stream, (_event(),))
    result = _evaluate(snapshot, SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT)

    assert type(result) is SelectionReady
    assert len(result.matches) == 1
    assert isinstance(result.matches[0].value, SensitiveValue)
    assert type(result.matches[0].location) is StreamLocation
    assert result.matches[0].location.item_index == Count(value=0)
    assert result.views[0].requested is BodyViewKind.SSE_EVENTS
    assert result.stream_extent is stream.extent

    public = evaluator._event_value(_event(sensitive=False))
    assert type(public) is JsonObject


def test_every_sse_control_is_selected_and_preserves_sensitive_components() -> None:
    stream = _stream(StreamKind.SSE, records=0, controls=4)
    items: tuple[StreamItem, ...] = (
        SseComment(
            value=_sealed(BodyText(value="heartbeat")),
            location=location(item=0, category=0, start=0, end=0),
        ),
        SseRetryControl(
            retry_after=_sealed(Duration(nanoseconds=1_000)),
            location=location(item=1, category=1, start=0, end=0),
        ),
        SseIdStateTransition(
            previous=_sealed(BodyText(value="previous")),
            current=_sealed(BodyText(value="current")),
            location=location(item=2, category=2, start=0, end=0),
        ),
        SseUnknownField(
            name=_sealed(BodyText(value="extension")),
            value=_sealed(BodyText(value="value")),
            location=location(item=3, category=3, start=0, end=0),
        ),
    )
    snapshot = _snapshot(stream, items)
    result = _evaluate(snapshot, SelectorSource.STREAM_CONTROLS, kind=ValueKind.OBJECT)
    events = _evaluate(snapshot, SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT)

    assert type(result) is SelectionReady
    assert len(result.matches) == 4
    assert all(isinstance(match.value, SensitiveValue) for match in result.matches)
    assert type(events) is SelectionReady
    assert events.matches == ()

    assert (
        type(
            evaluator._control_value(
                SseComment(value=BodyText(value="public"), location=location())
            )
        )
        is JsonObject
    )
    assert (
        type(
            evaluator._control_value(
                SseRetryControl(retry_after=Duration(nanoseconds=1), location=location())
            )
        )
        is JsonObject
    )
    assert (
        type(
            evaluator._control_value(
                SseIdStateTransition(
                    previous=BodyText(value="a"),
                    current=BodyText(value="b"),
                    location=location(),
                )
            )
        )
        is JsonObject
    )
    assert (
        type(
            evaluator._control_value(
                SseUnknownField(
                    name=BodyText(value="x"),
                    value=BodyText(value="y"),
                    location=location(),
                )
            )
        )
        is JsonObject
    )


def test_irrelevant_items_are_skipped_and_match_budget_returns_no_prefix() -> None:
    stream = _stream(StreamKind.SSE, records=1)
    snapshot = _snapshot(stream, (_event(),))
    controls = _evaluate(snapshot, SelectorSource.STREAM_CONTROLS, kind=ValueKind.OBJECT)
    exhausted = _evaluate(
        snapshot,
        SelectorSource.STREAM_EVENTS,
        kind=ValueKind.OBJECT,
        meter=selection_meter(matches=0),
    )
    assert type(controls) is SelectionReady
    assert controls.matches == ()
    assert type(exhausted) is SelectionBudgetExceeded


def test_ndjson_record_and_error_return_partial_with_decode_issue() -> None:
    stream = _stream(StreamKind.NDJSON, records=1, errors=1, incomplete=True)
    items: tuple[StreamItem, ...] = (
        JsonStreamRecord(
            value=_sealed(JsonInteger(value=1)),
            location=location(item=0, category=0, start=0, end=0),
        ),
        JsonStreamRecordError(
            code=DecodeErrorCode.INVALID_SYNTAX,
            location=location(item=1, category=0, start=0, end=0),
        ),
    )
    snapshot = _snapshot(stream, items)
    result = _evaluate(snapshot, SelectorSource.STREAM_RECORDS)

    assert type(result) is SelectionPartial
    assert result.reason is SelectionPartialReason.ITEM_FAILURE
    assert len(result.matches) == 1
    assert len(result.issues) == 1
    assert type(result.issues[0]) is SelectionItemDecodeIssue
    assert result.views[0].requested is BodyViewKind.NDJSON_RECORDS


def test_sse_nested_ready_decode_error_and_budget_issue_are_distinct() -> None:
    ready_stream = _stream(StreamKind.SSE, records=1)
    ready = SseJsonReady(decoder=decoder(ViewKind.JSON), value=_sealed(JsonInteger(value=7)))
    ready_snapshot = _snapshot(ready_stream, (_event(nested=ready),))
    ready_result = _evaluate(ready_snapshot, SelectorSource.STREAM_EVENT_DATA_JSON)
    assert type(ready_result) is SelectionReady
    assert len(ready_result.matches) == 1

    failed_stream = _stream(StreamKind.SSE, records=1)
    failed = SseJsonDecodeError(
        decoder=decoder(ViewKind.JSON),
        code=DecodeErrorCode.INVALID_SYNTAX,
    )
    failed_result = _evaluate(
        _snapshot(failed_stream, (_event(nested=failed),)),
        SelectorSource.STREAM_EVENT_DATA_JSON,
    )
    assert type(failed_result) is SelectionPartial
    assert type(failed_result.issues[0]) is SelectionItemDecodeIssue

    budget_stream = _stream(StreamKind.SSE, records=1)
    view = budget_stream.segments[0].view
    exhaustion = view_exhaustion(ViewBudgetKind.JSON_NODES, body_id=view.body_id)
    scope = exhaustion.budget.scope
    assert type(scope) is BodyBudgetScope
    owner_exchange = budget_stream.records.owner.attempts[0].exchanges[0]
    exhaustion = msgspec.structs.replace(
        exhaustion,
        budget=msgspec.structs.replace(
            exhaustion.budget,
            scope=msgspec.structs.replace(
                scope,
                attempt=owner_exchange.request.occurrence.attempt,
            ),
        ),
    )
    budget = SseJsonBudgetExceeded(
        decoder=decoder(ViewKind.JSON),
        exhaustion=exhaustion,
    )
    budget_result = _evaluate(
        _snapshot(budget_stream, (_event(nested=budget),)),
        SelectorSource.STREAM_EVENT_DATA_JSON,
    )
    assert type(budget_result) is SelectionPartial
    assert type(budget_result.issues[0]) is SelectionItemBudgetIssue


def test_incomplete_stream_is_partial_with_matches_and_incomplete_without_them() -> None:
    populated = _stream(StreamKind.SSE, records=1, incomplete=True)
    populated_result = _evaluate(
        _snapshot(populated, (_event(),)),
        SelectorSource.STREAM_EVENTS,
        kind=ValueKind.OBJECT,
    )
    assert type(populated_result) is SelectionPartial
    assert populated_result.reason is SelectionPartialReason.STREAM_WINDOW_INCOMPLETE

    empty = _stream(StreamKind.SSE, records=0, incomplete=True)
    empty_result = _evaluate(
        _snapshot(empty, ()),
        SelectorSource.STREAM_EVENTS,
        kind=ValueKind.OBJECT,
    )
    assert type(empty_result) is SelectionIncomplete


def test_authored_stream_window_is_ready_with_exact_extent() -> None:
    complete = _stream(StreamKind.SSE, records=1)
    window = msgspec.structs.replace(
        complete,
        segments=(
            msgspec.structs.replace(
                complete.segments[0],
                end_reason=SegmentEndReason.WINDOW_CLOSED,
            ),
        ),
        extent=StreamExtent(
            completeness=StreamCompleteness.WINDOW,
            termination=StreamTerminationReason.EVENT_COUNT_WINDOW,
        ),
        termination_evidence=EventCountWindowEvidence(limit=PositiveCount(value=1)),
    )

    result = _evaluate(
        _snapshot(window, (_event(),)),
        SelectorSource.STREAM_EVENTS,
        kind=ValueKind.OBJECT,
    )

    assert type(result) is SelectionReady
    assert result.stream_extent is window.extent
    assert result.stream_extent.completeness is StreamCompleteness.WINDOW


def test_json_pointer_selects_record_outer_event_and_nested_event_data() -> None:
    path = compile_json_pointer("/value")

    ndjson = _stream(StreamKind.NDJSON, records=1)
    ndjson_result = _evaluate(
        _snapshot(
            ndjson,
            (
                JsonStreamRecord(
                    value=_sealed(json_object(value=JsonInteger(value=1))),
                    location=location(item=0, category=0, start=0, end=0),
                ),
            ),
        ),
        SelectorSource.STREAM_RECORDS,
        path=path,
    )
    assert type(ndjson_result) is SelectionReady
    assert type(ndjson_result.matches[0].location) is StreamLocation
    assert ndjson_result.matches[0].location.nested_pointer == path.pointer

    sse = _stream(StreamKind.SSE, records=1)
    outer_result = _evaluate(
        _snapshot(sse, (_event(),)),
        SelectorSource.STREAM_EVENTS,
        path=compile_json_pointer("/event"),
    )
    assert type(outer_result) is SelectionReady
    assert outer_result.matches

    nested = _stream(StreamKind.SSE, records=1)
    nested_result = _evaluate(
        _snapshot(
            nested,
            (
                _event(
                    nested=SseJsonReady(
                        decoder=decoder(ViewKind.JSON),
                        value=_sealed(json_object(value=JsonInteger(value=2))),
                    )
                ),
            ),
        ),
        SelectorSource.STREAM_EVENT_DATA_JSON,
        path=path,
    )
    assert type(nested_result) is SelectionReady
    assert nested_result.matches


def test_json_path_skips_non_inputs_and_propagates_budget_and_unsupported() -> None:
    stream = _stream(StreamKind.SSE, records=1)
    snapshot = _snapshot(stream, (_event(),))
    no_match = _evaluate(
        snapshot,
        SelectorSource.STREAM_EVENT_DATA_JSON,
        path=compile_json_pointer("/missing"),
    )
    assert type(no_match) is SelectionReady
    assert no_match.matches == ()

    budget = _evaluate(
        snapshot,
        SelectorSource.STREAM_EVENTS,
        path=compile_json_pointer("/event"),
        meter=selection_meter(work=1),
    )
    assert type(budget) is SelectionBudgetExceeded

    unsupported = _evaluate(
        snapshot,
        SelectorSource.STREAM_EVENTS,
        path=compile_jsonpath('$[?match(@,"x")]'),
    )
    assert type(unsupported) is SelectionUnsupported


def test_json_invariant_guards_reject_non_json_path_and_incompatible_source() -> None:
    stream = _stream(StreamKind.SSE, records=1)
    snapshot = _snapshot(stream, (_event(),))
    selector = _selector(SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT)
    with pytest.raises(TypeError, match="compiled JSON path"):
        evaluator._json_matches(snapshot, selector, selection_meter())

    json_selector = _selector(
        SelectorSource.STREAM_EVENTS,
        path=compile_json_pointer("/event"),
    )
    msgspec.structs.force_setattr(json_selector, "source", SelectorSource.STREAM_CONTROLS)
    with pytest.raises(ValueError, match="incompatible source"):
        evaluator._json_input(json_selector, snapshot.stream_items[0])


def test_view_provenance_covers_auto_repeatable_sources_all_stream_kinds_and_error() -> None:
    explicit_handles = tuple(
        view_handle(kind, view_number=index)
        for index, kind in enumerate(
            (ViewKind.SSE, ViewKind.NDJSON, ViewKind.JSON_SEQUENCE),
            start=200,
        )
    )
    for handle, expected in zip(
        explicit_handles,
        (
            BodyViewKind.SSE_EVENTS,
            BodyViewKind.NDJSON_RECORDS,
            BodyViewKind.JSON_SEQUENCE_RECORDS,
        ),
        strict=True,
    ):
        fake = SimpleNamespace(segments=(SimpleNamespace(view=handle),))
        provenance_result = evaluator._view_provenance(
            _selector(SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT),
            cast("StreamObservation", fake),
        )
        assert provenance_result[0].requested is expected

    handle = explicit_handles[0]
    auto = msgspec.structs.replace(
        handle,
        auto_selection=AutoViewSelection(
            registry=DecoderRegistryIdentity(value="comparo/auto@1"),
            decoder=handle.decoder,
        ),
    )
    fake_auto = SimpleNamespace(segments=(SimpleNamespace(view=auto), SimpleNamespace(view=auto)))
    auto_result = evaluator._view_provenance(
        _selector(SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT),
        cast("StreamObservation", fake_auto),
    )
    assert len(auto_result) == 1
    assert auto_result[0].requested is BodyViewKind.AUTO

    data = b"data"
    source = RepeatableByteSource(
        source_id=BodyId(value=UUID(int=888)),
        byte_count=ByteCount(value=len(data)),
        digest=Sha256Digest(hex_value=sha256(data).hexdigest()),
        provenance=provenance(),
    )
    repeatable = msgspec.structs.replace(
        view_handle(ViewKind.SSE, source=source, view_number=889),
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="8" * 64),
        ),
        sensitivity=Sensitivity.PUBLIC,
    )
    fake_repeatable = SimpleNamespace(segments=(SimpleNamespace(view=repeatable),))
    repeatable_result = evaluator._view_provenance(
        _selector(SelectorSource.STREAM_EVENTS, kind=ValueKind.OBJECT),
        cast("StreamObservation", fake_repeatable),
    )
    assert repeatable_result[0].media is None

    with pytest.raises(ValueError, match="non-stream decoded view"):
        evaluator._stream_body_view_from_kind(ViewKind.JSON)


def test_termination_fields_default_static_unknown_and_budget() -> None:
    stream = _stream(StreamKind.SSE, records=0)
    snapshot = _snapshot(stream, ())
    for path in (
        NoPath(),
        StaticFieldPath(segments=("stream", "termination", "reason")),
        StaticFieldPath(segments=("stream", "termination", "completeness")),
    ):
        result = _evaluate(
            snapshot,
            SelectorSource.STREAM_TERMINATION,
            path=path,
            kind=ValueKind.STRING,
        )
        assert type(result) is SelectionReady

    selector = _selector(
        SelectorSource.STREAM_TERMINATION,
        path=StaticFieldPath(segments=("stream", "termination", "reason")),
        kind=ValueKind.STRING,
    )
    msgspec.structs.force_setattr(
        selector,
        "path",
        StaticFieldPath(segments=("stream", "termination", "unknown")),
    )
    unsupported = evaluator.evaluate_stream_source(snapshot, selector, selection_meter())
    exhausted = _evaluate(
        snapshot,
        SelectorSource.STREAM_TERMINATION,
        kind=ValueKind.STRING,
        meter=selection_meter(matches=0),
    )
    assert type(unsupported) is SelectionUnsupported
    assert type(exhausted) is SelectionBudgetExceeded


def test_stream_timings_return_measured_unavailable_and_match_budget() -> None:
    populated = _stream(StreamKind.SSE, records=1)
    populated_snapshot = _snapshot(populated, (_event(),))
    measured = _evaluate(
        populated_snapshot,
        SelectorSource.STREAM_TIMINGS,
        path=StaticFieldPath(segments=("stream", "timings", "time-to-first-record")),
        kind=ValueKind.DURATION,
    )
    assert type(measured) is SelectionReady

    empty = _stream(StreamKind.SSE, records=0)
    empty_snapshot = _snapshot(empty, ())
    unavailable = _evaluate(
        empty_snapshot,
        SelectorSource.STREAM_TIMINGS,
        path=StaticFieldPath(segments=("stream", "timings", "time-to-first-record")),
        kind=ValueKind.DURATION,
    )
    exhausted = _evaluate(
        populated_snapshot,
        SelectorSource.STREAM_TIMINGS,
        path=StaticFieldPath(segments=("stream", "timings", "observation-window-elapsed")),
        kind=ValueKind.DURATION,
        meter=selection_meter(matches=0),
    )
    assert type(unavailable) is SelectionFactUnavailable
    assert type(exhausted) is SelectionBudgetExceeded
