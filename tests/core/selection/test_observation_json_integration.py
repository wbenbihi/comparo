"""Integration tests for JSON paths over storage-resolved observation views."""

from __future__ import annotations

import msgspec

from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_items import SseEvent
from comparo.core.domain.http.stream_items import SseJsonDecodeError
from comparo.core.domain.http.stream_items import StreamItemEnvelope
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.locations import JsonLocation
from comparo.core.domain.selectors.locations import StreamLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonString
from comparo.core.selection import compile_json_pointer
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.source_evaluator import evaluate_source
from tests.core.domain.http.observation_factories import measured
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import location
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import single_stream_execution
from tests.core.domain.http.stream_factories import stream_execution
from tests.core.domain.http.stream_factories import stream_timings
from tests.core.domain.http.stream_factories import view_handle
from tests.core.selection.json_evaluator_factories import json_object
from tests.core.selection.json_evaluator_factories import selection_meter


def test_body_json_pointer_maps_location_and_charges_result_once() -> None:
    stream_observation = stream_execution(ViewKind.SSE)
    terminal_response = stream_observation.attempts[-1].exchanges[-1].response
    assert terminal_response is not None
    assert type(terminal_response.body) is ResponseEntityBody
    assert terminal_response.body.handle is not None
    handle = view_handle(
        ViewKind.JSON,
        source=terminal_response.body.handle,
        view_number=103,
    )
    observation = msgspec.structs.replace(
        stream_observation,
        views=(*stream_observation.views, handle),
    )
    resolved = ResolvedBodyView(
        handle=handle,
        requested_view=BodyViewKind.JSON,
        value=SensitiveValue(
            json_object(answer=JsonString(value="yes")),
            origins=(TaintOriginId(value="response.body"),),
        ),
        payload_fingerprint=handle.fingerprint,
    )
    path = compile_json_pointer("/answer")
    capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_BODY, path)
    selector = CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="e" * 64),
        ),
        source=SelectorSource.RESPONSE_BODY,
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=capabilities.cardinality,
        ),
        body_view=BodyViewKind.JSON,
    )
    meter = selection_meter()

    result = evaluate_source(
        SelectionSnapshot(observation=observation, body_views=(resolved,)),
        selector,
        meter,
    )

    assert type(result) is SelectionReady
    assert len(result.matches) == 1
    assert type(result.matches[0].location) is JsonLocation
    assert result.matches[0].location.pointer.value == "/answer"
    assert is_sensitive_value(result.matches[0].value)
    assert result.matches[0].value.payload_type is JsonString
    assert meter.budgets.matches.usage.consumed == Count(value=1)


def test_body_decode_failure_retains_exact_safe_view_provenance() -> None:
    observation = single_stream_execution(ViewKind.SSE)
    terminal_response = observation.attempts[-1].exchanges[-1].response
    assert terminal_response is not None
    assert type(terminal_response.body) is ResponseEntityBody
    assert terminal_response.body.handle is not None
    source = terminal_response.body.handle
    failed = FailedBodyView(
        source=source,
        requested_view=BodyViewKind.JSON,
        outcome=ViewDecodeError(
            decoder=decoder(ViewKind.JSON),
            code=DecodeErrorCode.INVALID_SYNTAX,
        ),
    )
    selector = _body_selector(NoPath(), ValueKind.UNKNOWN)

    result = evaluate_source(
        SelectionSnapshot(observation=observation, body_views=(failed,)),
        selector,
        selection_meter(),
    )

    assert type(result) is SelectionDecodeFailure
    assert result.view is BodyViewKind.JSON
    assert result.provenance.body_id == source.body_id
    assert result.provenance.source is SelectorSource.RESPONSE_BODY
    assert result.provenance.decoder == failed.outcome.decoder
    assert result.provenance.auto_selection is failed.outcome.auto_selection
    assert result.provenance.media == source.media


def test_outer_sse_json_path_ignores_nested_data_decode_failure() -> None:
    stream = StreamObservation(
        records=records_handle(StreamKind.SSE, records=1),
        segments=(segment(index=0, first=0, count=1),),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=stream_timings(),
    )
    sealed_text = SensitiveValue(
        BodyText(value="message"),
        origins=(TaintOriginId(value="stream.data"),),
    )
    envelope = StreamItemEnvelope(
        view=stream.segments[0].view,
        item=SseEvent(
            data=sealed_text,
            event_type=sealed_text,
            last_event_id=sealed_text,
            fields=(),
            location=location(),
            nested_data=SseJsonDecodeError(
                decoder=decoder(ViewKind.JSON),
                code=DecodeErrorCode.INVALID_SYNTAX,
            ),
        ),
    )
    path = compile_json_pointer("/event")
    capabilities = derive_selector_capabilities(SelectorSource.STREAM_EVENTS, path)
    selector = CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="f" * 64),
        ),
        source=SelectorSource.STREAM_EVENTS,
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(ValueKind.UNKNOWN,),
            cardinality=capabilities.cardinality,
        ),
    )

    snapshot = SelectionSnapshot(
        observation=stream.records.owner,
        stream=stream,
        stream_items=(envelope,),
    )
    result = evaluate_source(
        snapshot,
        selector,
        selection_meter(),
    )

    assert type(result) is SelectionReady
    assert result.stream_extent is stream.extent
    assert len(result.matches) == 1
    assert type(result.matches[0].location) is StreamLocation
    assert result.matches[0].location.nested_pointer is not None
    assert result.matches[0].location.nested_pointer.value == "/event"

    nested = evaluate_source(
        snapshot,
        _stream_selector(
            SelectorSource.STREAM_EVENT_DATA_JSON,
            NoPath(),
            ValueKind.UNKNOWN,
        ),
        selection_meter(),
    )
    assert type(nested) is SelectionPartial
    assert nested.matches == ()
    assert len(nested.issues) == 1
    assert nested.stream_extent == stream.extent


def test_empty_stream_retains_view_provenance_and_exposes_timing_leaf() -> None:
    stream = StreamObservation(
        records=records_handle(StreamKind.SSE, records=0),
        segments=(segment(index=0, first=0, count=0),),
        captured_bytes=ByteCount(value=4),
        extent=StreamExtent(
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ),
        trailing_incomplete=(),
        timings=StreamTimings(
            time_to_first_record=NotPerformed(reason=NotPerformedReason.EMPTY_STREAM),
            observation_window_elapsed=measured(),
        ),
    )
    snapshot = SelectionSnapshot(
        observation=stream.records.owner,
        stream=stream,
        stream_items=(),
    )
    events = _stream_selector(SelectorSource.STREAM_EVENTS, NoPath(), ValueKind.OBJECT)
    timing = _stream_selector(
        SelectorSource.STREAM_TIMINGS,
        StaticFieldPath(segments=("stream", "timings", "observation-window-elapsed")),
        ValueKind.DURATION,
    )

    event_result = evaluate_source(snapshot, events, selection_meter())
    timing_result = evaluate_source(snapshot, timing, selection_meter())

    assert type(event_result) is SelectionReady
    assert event_result.matches == ()
    assert event_result.stream_extent is stream.extent
    assert len(event_result.views) == 1
    assert type(timing_result) is SelectionReady
    assert timing_result.matches[0].value == measured().duration


def _stream_selector(
    source: SelectorSource,
    path: NoPath | StaticFieldPath,
    kind: ValueKind,
) -> CompiledSelector:
    capabilities = derive_selector_capabilities(source, path)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="d" * 64),
        ),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(kinds=(kind,), cardinality=capabilities.cardinality),
    )


def _body_selector(path: NoPath, kind: ValueKind) -> CompiledSelector:
    capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_BODY, path)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="c" * 64),
        ),
        source=SelectorSource.RESPONSE_BODY,
        path=path,
        capabilities=capabilities,
        output=ValueContract(kinds=(kind,), cardinality=capabilities.cardinality),
        body_view=BodyViewKind.JSON,
    )
