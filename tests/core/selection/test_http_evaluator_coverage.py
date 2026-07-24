"""Branch-complete behavior coverage for the pure HTTP selector evaluators."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_metadata import derive_content_encodings
from comparo.core.domain.http.response_metadata import derive_media_type
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.responses import InformationalResponse
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.http_fields import HttpFieldProjection
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.occurrences import AllAttemptsOccurrence
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import AttemptOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.occurrences import ObservationOccurrenceSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.selector_semantics import TERMINAL_OCCURRENCE
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import BodyViewKind
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.http_evaluator import evaluate_http_source
from comparo.core.selection.http_field_evaluator import field_selected
from comparo.core.selection.http_field_evaluator import project_header_value
from comparo.core.selection.request_evaluator import evaluate_pre_send_request_source
from comparo.core.selection.request_evaluator import evaluate_request_source
from comparo.core.selection.response_evaluator import evaluate_response_source
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.source_evaluator import evaluate_source
from comparo.core.selection.timing_values import select_timing_value
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import measured
from tests.core.domain.http.observation_factories import occurrence as request_occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_denial
from tests.core.domain.http.observation_factories import resolution_failure
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.observation_factories import tls
from tests.core.selection.json_evaluator_factories import selection_meter

_NO_PATH = NoPath()
_NO_RESPONSE_FIELDS = ResponseFields()
_ACCESS = SensitiveValueAccess(purpose="selector.coverage")


def _selector(
    source: SelectorSource,
    kind: ValueKind,
    *,
    occurrence: ObservationOccurrenceSelector = TERMINAL_OCCURRENCE,
    path: SelectorPath = _NO_PATH,
    body_view: BodyViewKind | None = None,
) -> CompiledSelector:
    capabilities = derive_selector_capabilities(source, path, occurrence)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="c" * 64),
        ),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(kinds=(kind,), cardinality=capabilities.cardinality),
        body_view=body_view,
        occurrence=occurrence,
    )


def _observation(
    *,
    effective_request: EffectiveRequest | None = None,
    observed_response: ResponseObservation | None = None,
) -> EnvironmentExecutionObservation:
    effective = request() if effective_request is None else effective_request
    observed = exchange(
        exchange_number=1,
        effective_request=effective,
        observed_response=response(status=201) if observed_response is None else observed_response,
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=1)),
        environment=effective.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=1)),
                retry=None,
                exchanges=(observed,),
                timings=attempt_timings(),
            ),
        ),
        timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
    )


def _response_fields(
    *values: tuple[str, HeaderText | SensitiveValue[HeaderText]],
) -> ResponseFields:
    return ResponseFields(
        fields=tuple(
            ResponseField(name=HeaderName(value=name), value=value) for name, value in values
        )
    )


def _response_with_metadata(
    headers: ResponseFields,
    *,
    trailers: ResponseFields = _NO_RESPONSE_FIELDS,
    informational: tuple[InformationalResponse, ...] = (),
) -> ResponseObservation:
    base = response()
    codings, coding_error = derive_content_encodings(headers)
    return msgspec.structs.replace(
        base,
        informational=informational,
        headers=headers,
        trailers=trailers,
        media=derive_media_type(headers),
        content_encodings=codings,
        content_encoding_error=coding_error,
        body=ResponseEntityBody(
            capture=PolicyOmittedCapture(),
            handle=None,
            encoded_byte_count=None,
        ),
    )


def _snapshot_parts(
    *,
    effective_request: EffectiveRequest | None = None,
    observed_response: ResponseObservation | None = None,
) -> tuple[SelectionSnapshot, HttpAttempt, HttpExchange, FinalizedWireRequest]:
    snapshot = SelectionSnapshot(
        observation=_observation(
            effective_request=effective_request,
            observed_response=observed_response,
        )
    )
    attempt = snapshot.observation.attempts[0]
    return snapshot, attempt, attempt.exchanges[0], attempt.exchanges[0].request.request


def _sensitive[ValueT](value: ValueT, origin: str = "coverage") -> SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=(TaintOriginId(value=origin),))


def _pre_send_observation(*, include_outcome: bool) -> EnvironmentExecutionObservation:
    effective = request()
    outcome = resolution_failure(
        effective,
        request_occurrence=request_occurrence(environment=effective.environment.identity),
    )
    base = _observation(effective_request=effective)
    observation = msgspec.structs.replace(
        base,
        attempts=(
            HttpAttempt(
                attempt_id=outcome.occurrence.attempt.attempt_id,
                retry=None,
                exchanges=(),
                timings=attempt_timings(),
                pre_send=outcome,
            ),
        ),
    )
    if not include_outcome:
        # Exercise the evaluator's defensive branch against a corrupted adapter snapshot.
        msgspec.structs.force_setattr(observation.attempts[0], "pre_send", None)
    return observation


def _request_header(name: str, value: HeaderText | SensitiveValue[HeaderText]) -> HeaderField:
    return HeaderField(
        name=HeaderName(value=name),
        value=value,
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )


def test_http_field_selection_and_projection_cover_exact_occurrences_and_sensitivity() -> None:
    raw = HttpFieldPath(name="x", occurrence=Count(value=1))
    assert field_selected(NoPath(), name="anything", occurrence=0)
    assert not field_selected(StaticFieldPath(segments=("execution",)), name="x", occurrence=0)
    assert not field_selected(raw, name="other", occurrence=1)
    assert not field_selected(raw, name="x", occurrence=0)
    assert field_selected(raw, name="x", occurrence=1)

    meter = selection_meter()
    assert project_header_value(HeaderText(value="raw"), NoPath(), meter) == HttpFieldTextValue(
        value="raw"
    )
    sealed = _sensitive(HeaderText(value="secret"))
    projected_raw = project_header_value(sealed, raw, meter)
    assert isinstance(projected_raw, SensitiveValue)
    assert projected_raw.reveal(access=_ACCESS) == HttpFieldTextValue(value="secret")

    items = project_header_value(
        HeaderText(value="one,\ttwo"),
        HttpFieldPath(name="x", projection=HttpFieldProjection.ITEMS),
        meter,
    )
    assert type(items) is HttpFieldItems
    sealed_items = project_header_value(
        _sensitive(HeaderText(value="one,two")),
        HttpFieldPath(name="x", projection=HttpFieldProjection.ITEMS),
        meter,
    )
    assert isinstance(sealed_items, SensitiveValue)
    assert type(sealed_items.reveal(access=_ACCESS)) is HttpFieldItems


@pytest.mark.parametrize("value", ["", "one,,two", "not a token"])
def test_invalid_header_item_lists_are_observed_invalid(value: str) -> None:
    result = project_header_value(
        HeaderText(value=value),
        HttpFieldPath(name="x", projection=HttpFieldProjection.ITEMS),
        selection_meter(),
    )
    assert type(result) is SelectionObservedInvalid


def test_header_item_projection_is_precharged() -> None:
    result = project_header_value(
        HeaderText(value="one"),
        HttpFieldPath(name="x", projection=HttpFieldProjection.ITEMS),
        selection_meter(work=0),
    )
    assert type(result) is SelectionBudgetExceeded


def test_request_url_renders_exact_shape_and_retains_taint() -> None:
    effective = request()
    effective = msgspec.structs.replace(
        effective,
        url=AbsoluteHttpUrl(
            scheme=effective.url.scheme,
            authority=HttpAuthority(host=Ipv6Host(value="2001:db8::1"), port=HttpPort(value=8443)),
            path=UrlPath(
                segments=(
                    DecodedPathSegment(value="decoded value"),
                    EncodedPathSegment(value="encoded%2Fvalue"),
                    _sensitive(DecodedPathSegment(value="secret value"), "path"),
                ),
                trailing_slash=True,
            ),
            query=QueryParameters(
                parameters=(
                    QueryParameter(
                        name=QueryName(value="bare name"),
                        value=BareQueryValue(),
                        source=QuerySource.AUTHORED,
                        provenance=provenance(),
                    ),
                    QueryParameter(
                        name=QueryName(value="plain"),
                        value=QueryText(value="plain value"),
                        source=QuerySource.AUTHORED,
                        provenance=provenance(),
                    ),
                    QueryParameter(
                        name=QueryName(value="sealed"),
                        value=_sensitive(QueryText(value="secret value"), "query"),
                        source=QuerySource.AUTHORED,
                        provenance=provenance(),
                    ),
                )
            ),
        ),
    )
    snapshot, _, observed_exchange, _ = _snapshot_parts()
    wire = finalized_wire_request(effective)

    result = evaluate_request_source(
        snapshot,
        _selector(SelectorSource.REQUEST_URL, ValueKind.STRING),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )

    assert type(result) is SelectionReady
    selected = result.matches[0].value
    assert isinstance(selected, SensitiveValue)
    assert selected.reveal(access=_ACCESS) == JsonString(
        value=(
            "https://[2001:db8::1]:8443/decoded%20value/encoded%2Fvalue/"
            "secret%20value/?bare%20name&plain=plain%20value&sealed=secret%20value"
        )
    )


def test_request_url_without_query_or_taint_and_scalar_match_budget() -> None:
    snapshot, _, observed_exchange, wire = _snapshot_parts()
    ready = evaluate_request_source(
        snapshot,
        _selector(SelectorSource.REQUEST_URL, ValueKind.STRING),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    exhausted = evaluate_request_source(
        snapshot,
        _selector(SelectorSource.REQUEST_METHOD, ValueKind.STRING),
        wire,
        observed_exchange.exchange_id,
        selection_meter(matches=0),
    )
    assert type(ready) is SelectionReady
    assert ready.matches[0].value == JsonString(value="https://api.example.test/")
    assert type(exhausted) is SelectionBudgetExceeded


def test_request_query_preserves_bare_plain_sensitive_duplicates_and_filtering() -> None:
    effective = request()
    parameters = (
        QueryParameter(
            name=QueryName(value="bare"),
            value=BareQueryValue(),
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        ),
        QueryParameter(
            name=QueryName(value="plain"),
            value=QueryText(value="first"),
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        ),
        QueryParameter(
            name=QueryName(value="plain"),
            value=QueryText(value="second"),
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        ),
        QueryParameter(
            name=QueryName(value="sealed"),
            value=_sensitive(QueryText(value="secret")),
            source=QuerySource.AUTHORED,
            provenance=provenance(),
        ),
    )
    effective = msgspec.structs.replace(
        effective,
        url=msgspec.structs.replace(
            effective.url,
            query=QueryParameters(parameters=parameters),
        ),
    )
    snapshot, _, observed_exchange, wire = _snapshot_parts(effective_request=effective)
    all_result = evaluate_request_source(
        snapshot,
        _selector(SelectorSource.REQUEST_QUERY, ValueKind.HTTP_FIELD_VALUE),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    exact_result = evaluate_request_source(
        snapshot,
        _selector(
            SelectorSource.REQUEST_QUERY,
            ValueKind.HTTP_FIELD_VALUE,
            path=HttpFieldPath(name="plain", occurrence=Count(value=1)),
        ),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    assert type(all_result) is SelectionReady
    assert type(all_result.matches[0].value) is BareQueryFieldValue
    assert all_result.matches[1].value == HttpFieldTextValue(value="first")
    assert isinstance(all_result.matches[3].value, SensitiveValue)
    assert type(exact_result) is SelectionReady
    assert exact_result.matches[0].value == HttpFieldTextValue(value="second")


@pytest.mark.parametrize(
    ("work", "matches"),
    [(0, 100), (100, 0)],
)
def test_request_query_budget_failures_return_no_prefix(work: int, matches: int) -> None:
    effective = request()
    parameter = QueryParameter(
        name=QueryName(value="plain"),
        value=QueryText(value="value"),
        source=QuerySource.AUTHORED,
        provenance=provenance(),
    )
    effective = msgspec.structs.replace(
        effective,
        url=msgspec.structs.replace(
            effective.url,
            query=QueryParameters(parameters=(parameter,)),
        ),
    )
    snapshot, _, observed_exchange, wire = _snapshot_parts(effective_request=effective)
    result = evaluate_request_source(
        snapshot,
        _selector(SelectorSource.REQUEST_QUERY, ValueKind.HTTP_FIELD_VALUE),
        wire,
        observed_exchange.exchange_id,
        selection_meter(work=work, matches=matches),
    )
    assert type(result) is SelectionBudgetExceeded


def test_request_headers_cover_filter_projection_invalidity_and_budgets() -> None:
    fields = (
        _request_header("x-skip", HeaderText(value="skip")),
        _request_header("x-items", HeaderText(value="one,two")),
        _request_header("x-sealed", _sensitive(HeaderText(value="secret"))),
    )
    effective = msgspec.structs.replace(request(), headers=RequestHeaders(fields=fields))
    snapshot, _, observed_exchange, wire = _snapshot_parts(effective_request=effective)

    items = evaluate_request_source(
        snapshot,
        _selector(
            SelectorSource.REQUEST_HEADERS,
            ValueKind.HTTP_FIELD_ITEMS,
            path=HttpFieldPath(name="x-items", projection=HttpFieldProjection.ITEMS),
        ),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    raw = evaluate_request_source(
        snapshot,
        _selector(
            SelectorSource.REQUEST_HEADERS,
            ValueKind.HTTP_FIELD_VALUE,
            path=HttpFieldPath(name="x-sealed"),
        ),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    assert type(items) is SelectionReady
    assert type(items.matches[0].value) is HttpFieldItems
    assert type(raw) is SelectionReady
    assert isinstance(raw.matches[0].value, SensitiveValue)

    invalid_effective = msgspec.structs.replace(
        request(),
        headers=RequestHeaders(fields=(_request_header("x-items", HeaderText(value="bad item")),)),
    )
    invalid_snapshot, _, invalid_exchange, invalid_wire = _snapshot_parts(
        effective_request=invalid_effective
    )
    invalid = evaluate_request_source(
        invalid_snapshot,
        _selector(
            SelectorSource.REQUEST_HEADERS,
            ValueKind.HTTP_FIELD_ITEMS,
            path=HttpFieldPath(name="x-items", projection=HttpFieldProjection.ITEMS),
        ),
        invalid_wire,
        invalid_exchange.exchange_id,
        selection_meter(),
    )
    assert type(invalid) is SelectionObservedInvalid

    for work, matches in ((0, 100), (1, 100), (100, 0)):
        exhausted = evaluate_request_source(
            snapshot,
            _selector(
                SelectorSource.REQUEST_HEADERS,
                ValueKind.HTTP_FIELD_ITEMS,
                path=HttpFieldPath(name="x-items", projection=HttpFieldProjection.ITEMS),
            ),
            wire,
            observed_exchange.exchange_id,
            selection_meter(work=work, matches=matches),
        )
        assert type(exhausted) is SelectionBudgetExceeded

    one_field = msgspec.structs.replace(
        request(),
        headers=RequestHeaders(fields=(_request_header("x-items", HeaderText(value="one,two")),)),
    )
    one_snapshot, _, one_exchange, one_wire = _snapshot_parts(effective_request=one_field)
    projection_budget = evaluate_request_source(
        one_snapshot,
        _selector(
            SelectorSource.REQUEST_HEADERS,
            ValueKind.HTTP_FIELD_ITEMS,
            path=HttpFieldPath(name="x-items", projection=HttpFieldProjection.ITEMS),
        ),
        one_wire,
        one_exchange.exchange_id,
        selection_meter(work=1),
    )
    assert type(projection_budget) is SelectionBudgetExceeded


def test_request_cookies_cover_filtering_and_budget_failures() -> None:
    cookies = RequestCookies(
        cookies=(
            RequestCookie(
                name=CookieName(value="first"),
                value=_sensitive(CookieText(value="one")),
                source=CookieSource.EXPLICIT,
                provenance=provenance(),
            ),
            RequestCookie(
                name=CookieName(value="second"),
                value=_sensitive(CookieText(value="two")),
                source=CookieSource.EXPLICIT,
                provenance=provenance(),
            ),
        )
    )
    effective = msgspec.structs.replace(request(), cookies=cookies)
    snapshot, _, observed_exchange, wire = _snapshot_parts(effective_request=effective)
    selected = evaluate_request_source(
        snapshot,
        _selector(
            SelectorSource.REQUEST_COOKIES,
            ValueKind.HTTP_FIELD_VALUE,
            path=HttpFieldPath(name="second"),
        ),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    assert type(selected) is SelectionReady
    assert isinstance(selected.matches[0].value, SensitiveValue)

    for work, matches in ((0, 100), (100, 0)):
        exhausted = evaluate_request_source(
            snapshot,
            _selector(SelectorSource.REQUEST_COOKIES, ValueKind.HTTP_FIELD_VALUE),
            wire,
            observed_exchange.exchange_id,
            selection_meter(work=work, matches=matches),
        )
        assert type(exhausted) is SelectionBudgetExceeded


def test_request_body_delegate_unsupported_source_and_both_pre_send_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, _, observed_exchange, wire = _snapshot_parts()
    delegated = SelectionUnsupported(code=DiagnosticCode(value="selection.test.delegate"))
    monkeypatch.setattr(
        "comparo.core.selection.request_evaluator.evaluate_request_body",
        lambda *_: delegated,
    )
    body = evaluate_request_source(
        snapshot,
        _selector(
            SelectorSource.REQUEST_BODY,
            ValueKind.BODY_HANDLE,
            body_view=BodyViewKind.HANDLE,
        ),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    unsupported = evaluate_request_source(
        snapshot,
        _selector(SelectorSource.RESPONSE_STATUS, ValueKind.INTEGER),
        wire,
        observed_exchange.exchange_id,
        selection_meter(),
    )
    assert body is delegated
    assert type(unsupported) is SelectionUnsupported

    effective = request()
    occurrence = request_occurrence(environment=effective.environment.identity)
    for outcome in (
        resolution_failure(effective, request_occurrence=occurrence),
        resolution_denial(effective, request_occurrence=occurrence),
    ):
        result = evaluate_pre_send_request_source(
            snapshot,
            _selector(SelectorSource.REQUEST_METHOD, ValueKind.STRING),
            outcome,
            selection_meter(),
        )
        assert type(result) is SelectionReady


def test_response_scalars_media_and_detail_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _response_fields(("content-type", HeaderText(value="application/json")))
    observed = _response_with_metadata(headers)
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=observed)
    assert observed_exchange.response is not None
    for source, kind, value_type in (
        (SelectorSource.RESPONSE_PROTOCOL, ValueKind.STRING, JsonString),
        (SelectorSource.RESPONSE_STATUS, ValueKind.INTEGER, JsonInteger),
        (SelectorSource.RESPONSE_MEDIA, ValueKind.MEDIA_TYPE, MediaType),
    ):
        selected = evaluate_response_source(
            snapshot,
            _selector(source, kind),
            observed_exchange,
            observed,
            selection_meter(),
        )
        assert type(selected) is SelectionReady
        assert isinstance(selected.matches[0].value, value_type)

    missing = evaluate_response_source(
        snapshot,
        _selector(SelectorSource.RESPONSE_MEDIA, ValueKind.MEDIA_TYPE),
        observed_exchange,
        response(),
        selection_meter(),
    )
    invalid_response = _response_with_metadata(
        _response_fields(("content-type", HeaderText(value="invalid")))
    )
    invalid = evaluate_response_source(
        snapshot,
        _selector(SelectorSource.RESPONSE_MEDIA, ValueKind.MEDIA_TYPE),
        observed_exchange,
        invalid_response,
        selection_meter(),
    )
    assert type(missing) is SelectionFactUnavailable
    assert type(invalid) is SelectionObservedInvalid

    delegated = SelectionUnsupported(code=DiagnosticCode(value="selection.test.body"))
    monkeypatch.setattr(
        "comparo.core.selection.response_evaluator.evaluate_response_body",
        lambda *_: delegated,
    )
    body = evaluate_response_source(
        snapshot,
        _selector(
            SelectorSource.RESPONSE_BODY,
            ValueKind.BODY_HANDLE,
            body_view=BodyViewKind.HANDLE,
        ),
        observed_exchange,
        observed,
        selection_meter(),
    )
    unsupported = evaluate_response_source(
        snapshot,
        _selector(SelectorSource.REQUEST_METHOD, ValueKind.STRING),
        observed_exchange,
        observed,
        selection_meter(),
    )
    assert body is delegated
    assert type(unsupported) is SelectionUnsupported

    match_budget = evaluate_response_source(
        snapshot,
        _selector(SelectorSource.RESPONSE_PROTOCOL, ValueKind.STRING),
        observed_exchange,
        observed,
        selection_meter(matches=0),
    )
    assert type(match_budget) is SelectionBudgetExceeded


def test_response_headers_trailers_and_informational_preserve_locations() -> None:
    ordinary = HeaderText(value="one,two")
    fields = _response_fields(("x-value", ordinary), ("x-skip", HeaderText(value="skip")))
    trailers = _response_fields(("x-value", _sensitive(HeaderText(value="secret"))))
    informational = (
        InformationalResponse(
            protocol=response().protocol,
            status=HttpStatus(value=103),
            headers=_response_fields(("x-value", ordinary)),
        ),
        InformationalResponse(
            protocol=response().protocol,
            status=HttpStatus(value=102),
            headers=_response_fields(("x-value", ordinary)),
        ),
    )
    observed = _response_with_metadata(fields, trailers=trailers, informational=informational)
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=observed)
    for source in (
        SelectorSource.RESPONSE_INFORMATIONAL,
        SelectorSource.RESPONSE_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
    ):
        selected = evaluate_response_source(
            snapshot,
            _selector(
                source,
                ValueKind.HTTP_FIELD_ITEMS,
                path=HttpFieldPath(name="x-value", projection=HttpFieldProjection.ITEMS),
            ),
            observed_exchange,
            observed,
            selection_meter(),
        )
        assert type(selected) is SelectionReady
        assert selected.matches


@pytest.mark.parametrize(
    "source",
    [
        SelectorSource.RESPONSE_INFORMATIONAL,
        SelectorSource.RESPONSE_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
    ],
)
def test_response_field_sources_propagate_invalidity_and_budgets(source: SelectorSource) -> None:
    fields = _response_fields(("x-value", HeaderText(value="bad item")))
    informational = (
        InformationalResponse(
            protocol=response().protocol,
            status=HttpStatus(value=103),
            headers=fields,
        ),
    )
    observed = _response_with_metadata(
        fields,
        trailers=fields,
        informational=informational,
    )
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=observed)
    selector = _selector(
        source,
        ValueKind.HTTP_FIELD_ITEMS,
        path=HttpFieldPath(name="x-value", projection=HttpFieldProjection.ITEMS),
    )
    invalid = evaluate_response_source(
        snapshot, selector, observed_exchange, observed, selection_meter()
    )
    assert type(invalid) is SelectionObservedInvalid
    for work in (0, 1):
        exhausted = evaluate_response_source(
            snapshot,
            selector,
            observed_exchange,
            observed,
            selection_meter(work=work),
        )
        assert type(exhausted) is SelectionBudgetExceeded


def test_response_field_match_budget_is_propagated() -> None:
    fields = _response_fields(("x-value", HeaderText(value="value")))
    observed = _response_with_metadata(fields)
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=observed)
    result = evaluate_response_source(
        snapshot,
        _selector(SelectorSource.RESPONSE_HEADERS, ValueKind.HTTP_FIELD_VALUE),
        observed_exchange,
        observed,
        selection_meter(matches=0),
    )
    assert type(result) is SelectionBudgetExceeded


def test_response_encodings_cover_invalid_ready_work_and_match_limits() -> None:
    valid = _response_with_metadata(
        _response_fields(("content-encoding", HeaderText(value="gzip, br")))
    )
    invalid = _response_with_metadata(
        _response_fields(("content-encoding", HeaderText(value="gzip,")))
    )
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=valid)
    selector = _selector(SelectorSource.RESPONSE_ENCODINGS, ValueKind.HTTP_FIELD_VALUE)
    selected = evaluate_response_source(
        snapshot, selector, observed_exchange, valid, selection_meter()
    )
    malformed = evaluate_response_source(
        snapshot, selector, observed_exchange, invalid, selection_meter()
    )
    assert type(selected) is SelectionReady
    assert len(selected.matches) == 2
    assert type(malformed) is SelectionObservedInvalid
    for meter in (selection_meter(work=0), selection_meter(matches=0)):
        exhausted = evaluate_response_source(snapshot, selector, observed_exchange, valid, meter)
        assert type(exhausted) is SelectionBudgetExceeded


def test_response_timing_endpoint_and_tls_static_fields() -> None:
    observed = response()
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=observed)
    unavailable_timing = evaluate_response_source(
        snapshot,
        _selector(
            SelectorSource.RESPONSE_TIMINGS,
            ValueKind.DURATION,
            path=StaticFieldPath(segments=("response", "timings", "elapsed")),
        ),
        observed_exchange,
        observed,
        selection_meter(),
    )
    measured_exchange = msgspec.structs.replace(
        observed_exchange,
        timings=msgspec.structs.replace(observed_exchange.timings, elapsed=measured(55)),
    )
    measured_timing = evaluate_response_source(
        snapshot,
        _selector(
            SelectorSource.RESPONSE_TIMINGS,
            ValueKind.DURATION,
            path=StaticFieldPath(segments=("response", "timings", "elapsed")),
        ),
        measured_exchange,
        observed,
        selection_meter(),
    )
    assert type(unavailable_timing) is SelectionFactUnavailable
    assert type(measured_timing) is SelectionReady

    for path, expected in (
        (("response", "endpoint", "remote-address"), JsonString),
        (("response", "endpoint", "remote-port"), JsonInteger),
        (("response", "endpoint", "connection-id"), JsonString),
        (("response", "endpoint", "connection-reused"), JsonBoolean),
    ):
        selected = evaluate_response_source(
            snapshot,
            _selector(
                SelectorSource.RESPONSE_ENDPOINT,
                {
                    JsonString: ValueKind.STRING,
                    JsonInteger: ValueKind.INTEGER,
                    JsonBoolean: ValueKind.BOOLEAN,
                }[expected],
                path=StaticFieldPath(segments=path),
            ),
            observed_exchange,
            observed,
            selection_meter(),
        )
        assert type(selected) is SelectionReady
        assert type(selected.matches[0].value) is expected

    origin_tls = msgspec.structs.replace(observed, tls=TlsLayers(connect=tls(), origin=tls()))
    for segments, kind in (
        (("response", "tls", "connect", "version"), ValueKind.STRING),
        (("response", "tls", "origin", "cipher-suite"), ValueKind.STRING),
        (("response", "tls", "connect", "alpn"), ValueKind.STRING),
        (("response", "tls", "origin", "session-resumed"), ValueKind.BOOLEAN),
    ):
        selected = evaluate_response_source(
            snapshot,
            _selector(
                SelectorSource.RESPONSE_TLS,
                kind,
                path=StaticFieldPath(segments=segments),
            ),
            observed_exchange,
            origin_tls,
            selection_meter(),
        )
        assert type(selected) is SelectionReady

    no_origin = evaluate_response_source(
        snapshot,
        _selector(
            SelectorSource.RESPONSE_TLS,
            ValueKind.STRING,
            path=StaticFieldPath(segments=("response", "tls", "origin", "version")),
        ),
        observed_exchange,
        observed,
        selection_meter(),
    )
    assert type(no_origin) is SelectionFactUnavailable


@pytest.mark.parametrize(
    "source",
    [
        SelectorSource.RESPONSE_TIMINGS,
        SelectorSource.RESPONSE_ENDPOINT,
        SelectorSource.RESPONSE_TLS,
    ],
)
def test_response_static_detail_rejects_non_static_path(source: SelectorSource) -> None:
    observed = response()
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=observed)
    # Runtime guard coverage intentionally bypasses compile-time validation.
    selector = _selector(
        source,
        ValueKind.UNKNOWN,
        path={
            SelectorSource.RESPONSE_TIMINGS: StaticFieldPath(
                segments=("response", "timings", "elapsed")
            ),
            SelectorSource.RESPONSE_ENDPOINT: StaticFieldPath(
                segments=("response", "endpoint", "remote-address")
            ),
            SelectorSource.RESPONSE_TLS: StaticFieldPath(
                segments=("response", "tls", "connect", "version")
            ),
        }[source],
    )
    msgspec.structs.force_setattr(selector, "path", cast("SelectorPath", NoPath()))
    result = evaluate_response_source(
        snapshot, selector, observed_exchange, observed, selection_meter()
    )
    assert type(result) is SelectionUnsupported


@pytest.mark.parametrize(
    "source",
    [
        SelectorSource.RESPONSE_TIMINGS,
        SelectorSource.RESPONSE_ENDPOINT,
        SelectorSource.RESPONSE_TLS,
    ],
)
def test_response_static_detail_rejects_unknown_static_leaf(source: SelectorSource) -> None:
    observed = response()
    snapshot, _, observed_exchange, _ = _snapshot_parts(observed_response=observed)
    valid_path = {
        SelectorSource.RESPONSE_TIMINGS: StaticFieldPath(
            segments=("response", "timings", "elapsed")
        ),
        SelectorSource.RESPONSE_ENDPOINT: StaticFieldPath(
            segments=("response", "endpoint", "remote-address")
        ),
        SelectorSource.RESPONSE_TLS: StaticFieldPath(
            segments=("response", "tls", "connect", "version")
        ),
    }[source]
    selector = _selector(source, ValueKind.UNKNOWN, path=valid_path)
    msgspec.structs.force_setattr(
        selector,
        "path",
        StaticFieldPath(segments=(source.value.split(".")[0], "unknown")),
    )
    result = evaluate_response_source(
        snapshot, selector, observed_exchange, observed, selection_meter()
    )
    assert type(result) is SelectionUnsupported


def test_timing_projection_covers_every_outcome_family() -> None:
    assert select_timing_value(measured(7)) == measured(7).duration
    not_performed = select_timing_value(NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED))
    unavailable = select_timing_value(
        TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    )
    assert type(not_performed) is SelectionFactUnavailable
    assert "not-performed.connection.reused" in not_performed.code.value
    assert type(unavailable) is SelectionFactUnavailable
    assert "unavailable.instrumentation.unsupported" in unavailable.code.value


def test_execution_attempt_exchange_and_source_resolution_edges() -> None:
    base = _observation()
    measured_attempt = msgspec.structs.replace(
        base.attempts[0],
        timings=HttpAttemptTimings(elapsed=measured(10)),
    )
    measured_observation = msgspec.structs.replace(
        base,
        attempts=(measured_attempt,),
        timings=EnvironmentExecutionTimings(queue=measured(1), elapsed=measured(20)),
    )
    snapshot = SelectionSnapshot(observation=measured_observation)
    cases = (
        _selector(SelectorSource.EXECUTION, ValueKind.STRING),
        _selector(
            SelectorSource.EXECUTION,
            ValueKind.DURATION,
            path=StaticFieldPath(segments=("execution", "timings", "queue")),
        ),
        _selector(SelectorSource.ATTEMPT, ValueKind.STRING),
        _selector(
            SelectorSource.ATTEMPT,
            ValueKind.DURATION,
            path=StaticFieldPath(segments=("attempt", "timings", "elapsed")),
        ),
        _selector(SelectorSource.EXCHANGE, ValueKind.STRING),
    )
    for selector in cases:
        result = evaluate_source(snapshot, selector, selection_meter())
        assert type(result) is SelectionReady

    all_attempts = evaluate_source(
        snapshot,
        _selector(
            SelectorSource.ATTEMPT,
            ValueKind.STRING,
            occurrence=AllAttemptsOccurrence(),
        ),
        selection_meter(),
    )
    exact_attempt = evaluate_source(
        snapshot,
        _selector(
            SelectorSource.ATTEMPT,
            ValueKind.STRING,
            occurrence=AttemptOccurrence(attempt_id=measured_attempt.attempt_id),
        ),
        selection_meter(),
    )
    missing_attempt = evaluate_source(
        snapshot,
        _selector(
            SelectorSource.ATTEMPT,
            ValueKind.STRING,
            occurrence=AttemptOccurrence(attempt_id=AttemptId(value=UUID(int=999))),
        ),
        selection_meter(),
    )
    assert type(all_attempts) is SelectionReady
    assert type(exact_attempt) is SelectionReady
    assert type(missing_attempt) is SelectionSourceUnavailable

    exhausted = evaluate_source(snapshot, cases[0], selection_meter(matches=0))
    assert type(exhausted) is SelectionBudgetExceeded

    unavailable_attempt_timing = evaluate_source(
        SelectionSnapshot(observation=base),
        _selector(
            SelectorSource.ATTEMPT,
            ValueKind.DURATION,
            path=StaticFieldPath(segments=("attempt", "timings", "elapsed")),
        ),
        selection_meter(),
    )
    assert type(unavailable_attempt_timing) is SelectionFactUnavailable

    exact_exchange = evaluate_source(
        snapshot,
        _selector(
            SelectorSource.EXCHANGE,
            ValueKind.STRING,
            occurrence=ExchangeOccurrence(exchange_id=measured_attempt.exchanges[0].exchange_id),
        ),
        selection_meter(),
    )
    assert type(exact_exchange) is SelectionReady


def test_source_dispatch_covers_scenario_stream_work_and_pre_send_states() -> None:
    snapshot = SelectionSnapshot(observation=_observation())
    scenario = evaluate_source(
        snapshot,
        _selector(SelectorSource.SCENARIO_STEP, ValueKind.UNKNOWN),
        selection_meter(),
    )
    assert type(scenario) is SelectionUnsupported

    stream_selector = _selector(
        SelectorSource.STREAM_TIMINGS,
        ValueKind.DURATION,
        path=StaticFieldPath(segments=("stream", "timings", "time-to-first-record")),
    )
    stream_exhausted = evaluate_source(snapshot, stream_selector, selection_meter(work=0))
    stream_unavailable = evaluate_source(snapshot, stream_selector, selection_meter())
    assert type(stream_exhausted) is SelectionBudgetExceeded
    assert type(stream_unavailable) is SelectionSourceUnavailable

    no_outcome_snapshot = SelectionSnapshot(
        observation=_pre_send_observation(include_outcome=False)
    )
    with_outcome_snapshot = SelectionSnapshot(
        observation=_pre_send_observation(include_outcome=True)
    )
    request_selector = _selector(SelectorSource.REQUEST_METHOD, ValueKind.STRING)
    no_exchange = evaluate_source(
        no_outcome_snapshot,
        request_selector,
        selection_meter(),
    )
    pre_send = evaluate_source(
        with_outcome_snapshot,
        request_selector,
        selection_meter(),
    )
    exchange_source = evaluate_source(
        no_outcome_snapshot,
        _selector(SelectorSource.EXCHANGE, ValueKind.STRING),
        selection_meter(),
    )
    assert type(no_exchange) is SelectionSourceUnavailable
    assert type(pre_send) is SelectionReady
    assert type(exchange_source) is SelectionSourceUnavailable


def test_source_occurrence_budget_missing_response_and_all_exchange_failure() -> None:
    effective = request()
    failed_exchange = exchange(
        exchange_number=3,
        effective_request=effective,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    base = _observation(effective_request=effective)
    failed_observation = msgspec.structs.replace(
        base,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=3)),
                retry=None,
                exchanges=(failed_exchange,),
                timings=attempt_timings(),
            ),
        ),
    )
    snapshot = SelectionSnapshot(observation=failed_observation)
    status = _selector(SelectorSource.RESPONSE_STATUS, ValueKind.INTEGER)
    no_response = evaluate_source(snapshot, status, selection_meter())
    all_failed = evaluate_source(
        snapshot,
        _selector(
            SelectorSource.RESPONSE_STATUS,
            ValueKind.INTEGER,
            occurrence=AllExchangesOccurrence(),
        ),
        selection_meter(),
    )
    occurrence_budget = evaluate_source(
        snapshot,
        _selector(SelectorSource.REQUEST_METHOD, ValueKind.STRING),
        selection_meter(work=0),
    )
    assert type(no_response) is SelectionSourceUnavailable
    assert type(all_failed) is SelectionIncomplete
    assert type(occurrence_budget) is SelectionBudgetExceeded

    attempt = snapshot.observation.attempts[0]
    direct_no_exchange = evaluate_http_source(
        snapshot,
        _selector(SelectorSource.REQUEST_METHOD, ValueKind.STRING),
        attempt,
        None,
        selection_meter(),
    )
    direct_no_response = evaluate_http_source(
        snapshot,
        status,
        attempt,
        failed_exchange,
        selection_meter(),
    )
    assert type(direct_no_exchange) is SelectionSourceUnavailable
    assert type(direct_no_response) is SelectionSourceUnavailable


@pytest.mark.parametrize("position", ["snapshot", "selector", "meter"])
def test_source_evaluator_rejects_untyped_inputs(position: str) -> None:
    snapshot = SelectionSnapshot(observation=_observation())
    selector = _selector(SelectorSource.EXECUTION, ValueKind.STRING)
    meter = selection_meter()
    values = {"snapshot": snapshot, "selector": selector, "meter": meter}
    values[position] = object()
    with pytest.raises(TypeError):
        evaluate_source(
            cast("SelectionSnapshot", values["snapshot"]),
            cast("CompiledSelector", values["selector"]),
            cast("SelectionMeter", values["meter"]),
        )
