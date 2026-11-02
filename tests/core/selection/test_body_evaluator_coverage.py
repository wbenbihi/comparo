"""Close branch coverage for pure request and response body selection."""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.views import DecodeErrorCode
from comparo.core.domain.http.views import IncompleteViewReason
from comparo.core.domain.http.views import UnsupportedViewReason
from comparo.core.domain.http.views import ViewBudgetExceeded
from comparo.core.domain.http.views import ViewBudgetKind
from comparo.core.domain.http.views import ViewCancelled
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewDecodeError
from comparo.core.domain.http.views import ViewIncomplete
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.http.views import ViewNoBody
from comparo.core.domain.http.views import ViewUnsupported
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionDecodeFailure
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionPartial
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from comparo.core.selection import body_evaluator
from comparo.core.selection.body_evaluator import evaluate_request_body
from comparo.core.selection.body_evaluator import evaluate_response_body
from comparo.core.selection.snapshot import FailedBodyView
from comparo.core.selection.snapshot import FailedViewOutcome
from comparo.core.selection.snapshot import ResolvedBodyBytes
from comparo.core.selection.snapshot import ResolvedBodyView
from comparo.core.selection.snapshot import SelectionSnapshot
from tests.core.domain.http.budget_factories import view_exhaustion
from tests.core.domain.http.factories import finalized_request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import decoder
from tests.core.domain.http.stream_factories import single_stream_execution
from tests.core.domain.http.stream_factories import view_handle
from tests.core.selection.json_evaluator_factories import selection_meter

_BODY_ORIGIN = TaintOriginId(value="body-content")
_VIEW_ORIGIN = TaintOriginId(value="view-content")


def _selector(
    source: SelectorSource,
    view: BodyViewKind,
    *,
    kind: ValueKind,
) -> CompiledSelector:
    path = NoPath()
    capabilities = derive_selector_capabilities(source, path)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="a" * 64),
        ),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(kinds=(kind,), cardinality=capabilities.cardinality),
        body_view=view,
    )


def _response_payload_snapshot(
    data: bytes = b"data",
) -> tuple[SelectionSnapshot, BodyId]:
    observation = single_stream_execution(ViewKind.SSE)
    exchange = observation.attempts[0].exchanges[0]
    assert exchange.response is not None
    assert type(exchange.response.body) is ResponseEntityBody
    source = exchange.response.body.handle
    assert source is not None
    source = msgspec.structs.replace(
        source,
        byte_count=ByteCount(value=len(data)),
        digest=SensitiveValue(
            Sha256Digest(hex_value=sha256(data).hexdigest()),
            origins=(_BODY_ORIGIN,),
        ),
    )
    changed_body = msgspec.structs.replace(
        exchange.response.body,
        handle=source,
        encoded_byte_count=ByteCount(value=len(data)),
    )
    changed_response = msgspec.structs.replace(exchange.response, body=changed_body)
    changed_exchange = msgspec.structs.replace(exchange, response=changed_response)
    changed_attempt = msgspec.structs.replace(
        observation.attempts[0],
        exchanges=(changed_exchange,),
    )
    changed_observation = msgspec.structs.replace(
        observation,
        attempts=(changed_attempt,),
        views=(),
    )
    payload = ResolvedBodyBytes(
        source=source,
        value=SensitiveValue(BytesValue(value=data), origins=(_BODY_ORIGIN,)),
    )
    return (
        SelectionSnapshot(observation=changed_observation, body_bytes=(payload,)),
        source.body_id,
    )


def _view_snapshot(
    kind: ViewKind,
    requested: BodyViewKind,
    value: JsonString,
    *,
    completeness: ViewCompleteness = ViewCompleteness.FULL,
) -> tuple[SelectionSnapshot, ResolvedBodyView]:
    observation = single_stream_execution(ViewKind.SSE)
    exchange = observation.attempts[0].exchanges[0]
    assert exchange.response is not None
    assert type(exchange.response.body) is ResponseEntityBody
    source = exchange.response.body.handle
    assert source is not None
    handle = view_handle(
        kind,
        completeness=completeness,
        source=source,
        view_number=900,
    )
    observation = msgspec.structs.replace(observation, views=(handle,))
    resolved = ResolvedBodyView(
        handle=handle,
        requested_view=requested,
        value=SensitiveValue(value, origins=(_VIEW_ORIGIN,)),
        payload_fingerprint=handle.fingerprint,
    )
    return SelectionSnapshot(observation=observation, body_views=(resolved,)), resolved


def test_request_body_selects_materialized_handle_and_reports_absence() -> None:
    snapshot = SelectionSnapshot(observation=single_stream_execution(ViewKind.SSE))
    selector = _selector(
        SelectorSource.REQUEST_BODY,
        BodyViewKind.HANDLE,
        kind=ValueKind.BODY_HANDLE,
    )
    source = RepeatableByteSource(
        source_id=BodyId(value=UUID(int=901)),
        byte_count=ByteCount(value=0),
        digest=Sha256Digest(hex_value=sha256(b"").hexdigest()),
        provenance=finalized_request().provenance,
    )
    request_with_body = finalized_request(body=MaterializedBody(source=source))

    ready = evaluate_request_body(snapshot, selector, request_with_body, selection_meter())
    missing = evaluate_request_body(snapshot, selector, finalized_request(), selection_meter())
    assert type(ready) is SelectionReady
    assert ready.matches[0].value == source.source_id
    assert type(missing) is SelectionSourceUnavailable
    assert missing.reason is SourceUnavailableReason.NO_BODY


def test_response_body_reports_no_body_and_preserves_partial_extent() -> None:
    snapshot = SelectionSnapshot(observation=single_stream_execution(ViewKind.SSE))
    selector = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.HANDLE,
        kind=ValueKind.BODY_HANDLE,
    )
    no_body = response(
        status=204,
        body=NoResponseBody(reason=NoBodyReason.STATUS_204),
    )
    assert type(evaluate_response_body(snapshot, selector, no_body, selection_meter())) is (
        SelectionSourceUnavailable
    )

    entity = cast("ResponseEntityBody", response().body)
    assert entity.handle is not None
    source = msgspec.structs.replace(
        entity.handle,
        extent=BodyCaptureExtent.PARTIAL,
    )
    assert source is not None
    partial_response = response(
        body=ResponseEntityBody(
            capture=RemoteTruncatedCapture(),
            handle=source,
            encoded_byte_count=source.byte_count,
        )
    )
    partial = evaluate_response_body(snapshot, selector, partial_response, selection_meter())
    assert type(partial) is SelectionReady


def test_body_value_requires_view_and_dispatches_all_view_families() -> None:
    snapshot, body_id = _response_payload_snapshot()
    with pytest.raises(ValueError, match="declare a body view"):
        body_evaluator._body_value(
            snapshot,
            cast("CompiledSelector", SimpleNamespace(body_view=None)),
            body_id,
            selection_meter(),
            partial=False,
        )

    handle = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.HANDLE,
        kind=ValueKind.BODY_HANDLE,
    )
    entity = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.ENTITY_BYTES,
        kind=ValueKind.BYTES,
    )
    decoded = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.TEXT,
        kind=ValueKind.STRING,
    )
    assert (
        type(
            body_evaluator._body_value(
                snapshot,
                handle,
                body_id,
                selection_meter(),
                partial=False,
            )
        )
        is SelectionReady
    )
    assert (
        type(
            body_evaluator._body_value(
                snapshot,
                entity,
                body_id,
                selection_meter(),
                partial=False,
            )
        )
        is SelectionReady
    )
    assert (
        type(
            body_evaluator._body_value(
                snapshot,
                decoded,
                body_id,
                selection_meter(),
                partial=False,
            )
        )
        is SelectionSourceUnavailable
    )


def test_handle_and_entity_bytes_are_precharged_and_partial_is_explicit() -> None:
    snapshot, body_id = _response_payload_snapshot()
    handle_selector = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.HANDLE,
        kind=ValueKind.BODY_HANDLE,
    )
    bytes_selector = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.ENTITY_BYTES,
        kind=ValueKind.BYTES,
    )
    assert (
        type(
            body_evaluator._body_handle(
                snapshot,
                handle_selector,
                body_id,
                BodyViewKind.HANDLE,
                selection_meter(matches=0),
            )
        )
        is SelectionBudgetExceeded
    )
    assert (
        type(
            body_evaluator._body_bytes(
                snapshot,
                bytes_selector,
                BodyId(value=UUID(int=999)),
                BodyViewKind.ENTITY_BYTES,
                selection_meter(),
                partial=False,
            )
        )
        is SelectionSourceUnavailable
    )
    assert (
        type(
            body_evaluator._body_bytes(
                snapshot,
                bytes_selector,
                body_id,
                BodyViewKind.ENTITY_BYTES,
                selection_meter(matches=0),
                partial=False,
            )
        )
        is SelectionBudgetExceeded
    )
    assert (
        type(
            body_evaluator._body_bytes(
                snapshot,
                bytes_selector,
                body_id,
                BodyViewKind.ENTITY_BYTES,
                selection_meter(),
                partial=True,
            )
        )
        is SelectionPartial
    )


def test_decoded_body_handles_missing_full_prefix_and_budget_states() -> None:
    selector = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.TEXT,
        kind=ValueKind.STRING,
    )
    empty = SelectionSnapshot(observation=single_stream_execution(ViewKind.SSE))
    response_observation = empty.observation.attempts[0].exchanges[0].response
    assert response_observation is not None
    response_body = cast("ResponseEntityBody", response_observation.body)
    assert response_body.handle is not None
    body_id = response_body.handle.body_id
    assert (
        type(
            body_evaluator._decoded_body(
                empty,
                selector,
                body_id,
                BodyViewKind.TEXT,
                selection_meter(),
                capture_partial=False,
            )
        )
        is SelectionSourceUnavailable
    )
    incomplete = body_evaluator._decoded_body(
        empty,
        selector,
        body_id,
        BodyViewKind.TEXT,
        selection_meter(),
        capture_partial=True,
    )
    assert type(incomplete) is SelectionIncomplete
    assert incomplete.reason is SelectionIncompleteReason.PARTIAL_CAPTURE

    full_snapshot, full = _view_snapshot(
        ViewKind.TEXT,
        BodyViewKind.TEXT,
        JsonString(value="complete"),
    )
    assert (
        type(
            body_evaluator._decoded_body(
                full_snapshot,
                selector,
                full.handle.body_id,
                BodyViewKind.TEXT,
                selection_meter(),
                capture_partial=False,
            )
        )
        is SelectionReady
    )
    assert (
        type(
            body_evaluator._decoded_body(
                full_snapshot,
                selector,
                full.handle.body_id,
                BodyViewKind.TEXT,
                selection_meter(matches=0),
                capture_partial=False,
            )
        )
        is SelectionBudgetExceeded
    )

    prefix_snapshot, prefix = _view_snapshot(
        ViewKind.TEXT,
        BodyViewKind.TEXT,
        JsonString(value="prefix"),
        completeness=ViewCompleteness.PREFIX,
    )
    assert (
        type(
            body_evaluator._decoded_body(
                prefix_snapshot,
                selector,
                prefix.handle.body_id,
                BodyViewKind.TEXT,
                selection_meter(),
                capture_partial=True,
            )
        )
        is SelectionPartial
    )
    assert (
        type(
            body_evaluator._decoded_body(
                prefix_snapshot,
                selector,
                prefix.handle.body_id,
                BodyViewKind.TEXT,
                selection_meter(matches=0),
                capture_partial=True,
            )
        )
        is SelectionBudgetExceeded
    )
    fabricated = cast(
        "CompiledSelector",
        SimpleNamespace(path=object(), source=SelectorSource.RESPONSE_BODY),
    )
    assert (
        type(
            body_evaluator._decoded_body(
                prefix_snapshot,
                fabricated,
                prefix.handle.body_id,
                BodyViewKind.TEXT,
                selection_meter(),
                capture_partial=True,
            )
        )
        is SelectionIncomplete
    )


def test_json_evaluation_failure_is_forwarded_without_collapsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from comparo.core.selection import compile_json_pointer

    snapshot, resolved = _view_snapshot(
        ViewKind.JSON,
        BodyViewKind.JSON,
        JsonString(value="json"),
    )
    path = compile_json_pointer("")
    capabilities = derive_selector_capabilities(SelectorSource.RESPONSE_BODY, path)
    selector = CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="b" * 64),
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
    expected = SelectionUnsupported(
        code=body_evaluator._code("selection.test.unsupported"),
    )
    monkeypatch.setattr(body_evaluator, "evaluate_json", lambda *_args: expected)
    assert (
        body_evaluator._decoded_body(
            snapshot,
            selector,
            resolved.handle.body_id,
            BodyViewKind.JSON,
            selection_meter(),
            capture_partial=False,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("outcome", "expected_type"),
    [
        (ViewNoBody(decoder=decoder(ViewKind.JSON)), SelectionSourceUnavailable),
        (
            ViewDecodeError(
                decoder=decoder(ViewKind.JSON),
                code=DecodeErrorCode.INVALID_SYNTAX,
            ),
            SelectionDecodeFailure,
        ),
        (
            ViewIncomplete(
                decoder=decoder(ViewKind.JSON),
                reason=IncompleteViewReason.ENTITY_PARTIAL,
            ),
            SelectionIncomplete,
        ),
        (
            ViewBudgetExceeded(
                decoder=decoder(ViewKind.JSON),
                exhaustion=view_exhaustion(ViewBudgetKind.JSON_NODES),
            ),
            SelectionBudgetExceeded,
        ),
        (
            ViewUnsupported(
                decoder=decoder(ViewKind.JSON),
                reason=UnsupportedViewReason.DECODER_UNAVAILABLE,
            ),
            SelectionUnsupported,
        ),
        (ViewCancelled(decoder=decoder(ViewKind.JSON)), SelectionCancelled),
    ],
)
def test_view_failure_outcomes_remain_exhaustive(
    outcome: FailedViewOutcome, expected_type: type[object]
) -> None:
    observation = single_stream_execution(ViewKind.SSE)
    response_observation = observation.attempts[0].exchanges[0].response
    assert response_observation is not None
    source = cast("ResponseEntityBody", response_observation.body).handle
    assert source is not None
    failed = FailedBodyView(
        source=source,
        requested_view=BodyViewKind.JSON,
        outcome=outcome,
    )
    selector = _selector(
        SelectorSource.RESPONSE_BODY,
        BodyViewKind.JSON,
        kind=ValueKind.UNKNOWN,
    )
    result = body_evaluator._view_failure(selector, failed)
    assert type(result) is expected_type
    provenance = (
        result.provenance
        if type(result) is SelectionDecodeFailure
        else cast("SelectionSourceUnavailable", result).view_failure
    )
    assert provenance is not None
    assert provenance.body_id == source.body_id


def test_failure_provenance_handles_repeatable_request_sources() -> None:
    request_body = finalized_request(body=MaterializedBody(source=_request_source())).body
    assert type(request_body) is MaterializedBody
    source = request_body.source
    selector = _selector(
        SelectorSource.REQUEST_BODY,
        BodyViewKind.JSON,
        kind=ValueKind.UNKNOWN,
    )
    provenance = body_evaluator._failure_provenance(
        selector,
        source,
        BodyViewKind.JSON,
        decoder(ViewKind.JSON),
        None,
    )
    assert provenance.body_id == source.source_id
    assert provenance.media is None


def _request_source() -> RepeatableByteSource:
    request = finalized_request()
    return RepeatableByteSource(
        source_id=BodyId(value=UUID(int=902)),
        byte_count=ByteCount(value=0),
        digest=Sha256Digest(hex_value=sha256(b"").hexdigest()),
        provenance=request.provenance,
    )
