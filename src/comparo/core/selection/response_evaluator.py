"""Pure response metadata, field, timing, endpoint, and TLS selection."""

from __future__ import annotations

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import InvalidMediaType
from comparo.core.domain.http.response_bodies import ParsedMediaType
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.http.transport import TlsMetadata
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import HttpMessageSection
from comparo.core.domain.selectors.locations import IndexedLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.body_evaluator import evaluate_response_body
from comparo.core.selection.http_field_evaluator import field_selected
from comparo.core.selection.http_field_evaluator import project_header_value
from comparo.core.selection.match_accounting import account_match_before_append
from comparo.core.selection.match_accounting import charge_work
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.timing_values import select_timing_value

_ACCESS = SensitiveValueAccess(purpose="selector.evaluation")
type _SelectedOutput = SelectedValue | SensitiveValue[SelectedValue]


def evaluate_response_source(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    """Evaluate one response-owned source with explicit unavailable facts."""
    source = selector.source
    if source is SelectorSource.RESPONSE_PROTOCOL:
        return _one(snapshot, selector, JsonString(value=response.protocol.value), exchange, meter)
    if source is SelectorSource.RESPONSE_STATUS:
        return _one(snapshot, selector, JsonInteger(value=response.status.value), exchange, meter)
    if source is SelectorSource.RESPONSE_INFORMATIONAL:
        return _informational(snapshot, selector, exchange, response, meter)
    if source is SelectorSource.RESPONSE_HEADERS:
        return _fields(snapshot, selector, exchange, response.headers, meter)
    if source is SelectorSource.RESPONSE_TRAILERS:
        return _fields(snapshot, selector, exchange, response.trailers, meter)
    if source is SelectorSource.RESPONSE_MEDIA:
        return _media(snapshot, selector, exchange, response, meter)
    return _response_detail(snapshot, selector, exchange, response, meter)


def _response_detail(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    source = selector.source
    if source is SelectorSource.RESPONSE_ENCODINGS:
        return _encodings(snapshot, selector, exchange, response, meter)
    if source is SelectorSource.RESPONSE_BODY:
        return evaluate_response_body(snapshot, selector, response, meter)
    if source is SelectorSource.RESPONSE_TIMINGS:
        return _timing(snapshot, selector, exchange, meter)
    if source is SelectorSource.RESPONSE_ENDPOINT:
        return _endpoint(snapshot, selector, exchange, response, meter)
    if source is SelectorSource.RESPONSE_TLS:
        return _tls(snapshot, selector, exchange, response, meter)
    return SelectionUnsupported(code=DiagnosticCode(value="selection.source.unsupported"))


def _media(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    if type(response.media) is ParsedMediaType:
        return _one(snapshot, selector, response.media.value, exchange, meter)
    if type(response.media) is InvalidMediaType:
        return SelectionObservedInvalid(code=DiagnosticCode(value="selection.media.invalid"))
    return SelectionFactUnavailable(code=DiagnosticCode(value="selection.media.missing"))


def _informational(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    matches: list[SelectionMatch] = []
    occurrences: dict[HeaderName, int] = {}
    for info_index, info in enumerate(response.informational):
        selected = _header_matches(
            snapshot,
            selector,
            exchange.exchange_id,
            info.headers.fields,
            HttpMessageSection.RESPONSE_INFORMATIONAL_HEADER,
            meter,
            occurrences=occurrences,
            informational=(info_index, info.status.value),
        )
        if type(selected) is SelectionObservedInvalid:
            return selected
        if type(selected) is SelectionBudgetExceeded:
            return selected
        matches.extend(selected)
    return _ready(snapshot, selector, tuple(matches))


def _fields(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    fields: ResponseFields,
    meter: SelectionMeter,
) -> SelectionResult:
    section = (
        HttpMessageSection.RESPONSE_HEADER
        if selector.source is SelectorSource.RESPONSE_HEADERS
        else HttpMessageSection.RESPONSE_TRAILER
    )
    matches = _header_matches(
        snapshot,
        selector,
        exchange.exchange_id,
        fields.fields,
        section,
        meter,
        occurrences={},
    )
    if type(matches) is SelectionObservedInvalid:
        return matches
    if type(matches) is SelectionBudgetExceeded:
        return matches
    return _ready(snapshot, selector, matches)


def _header_matches(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange_id: ExchangeId,
    fields: tuple[ResponseField, ...],
    section: HttpMessageSection,
    meter: SelectionMeter,
    occurrences: dict[HeaderName, int],
    informational: tuple[int, int] | None = None,
) -> tuple[SelectionMatch, ...] | SelectionObservedInvalid | SelectionBudgetExceeded:
    matches: list[SelectionMatch] = []
    for field in fields:
        if (exhausted := charge_work(meter)) is not None:
            return exhausted
        name, value = field.name, field.value
        occurrence = occurrences.get(name, 0)
        occurrences[name] = occurrence + 1
        if not field_selected(
            selector.path,
            name=name.value,
            occurrence=occurrence,
        ):
            continue
        projected = project_header_value(value, selector.path, meter)
        if type(projected) is SelectionObservedInvalid:
            return projected
        if type(projected) is SelectionBudgetExceeded:
            return projected
        match = SelectionMatch(
            value=projected,
            location=HeaderLocation(
                environment_execution_id=snapshot.observation.execution_id,
                source=selector.source,
                exchange_id=exchange_id,
                section=section,
                occurrence=Count(value=occurrence),
                name=name,
                informational_response=(Count(value=informational[0]) if informational else None),
                informational_status=informational[1] if informational else None,
            ),
        )
        if (exhausted := account_match_before_append(match, meter)) is not None:
            return exhausted
        matches.append(match)
    return tuple(matches)


def _encodings(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    if response.content_encoding_error is not None:
        return SelectionObservedInvalid(
            code=DiagnosticCode(value="selection.content.encoding.invalid")
        )
    matches: list[SelectionMatch] = []
    for index, coding in enumerate(response.content_encodings):
        if (exhausted := charge_work(meter)) is not None:
            return exhausted
        match = SelectionMatch(
            value=HttpFieldTextValue(value=coding.value),
            location=IndexedLocation(
                environment_execution_id=snapshot.observation.execution_id,
                source=selector.source,
                exchange_id=exchange.exchange_id,
                index=Count(value=index),
            ),
        )
        if (exhausted := account_match_before_append(match, meter)) is not None:
            return exhausted
        matches.append(match)
    return _ready(snapshot, selector, tuple(matches))


def _timing(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    meter: SelectionMeter,
) -> SelectionResult:
    if type(selector.path) is not StaticFieldPath:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.field.unsupported"))
    timings = exchange.timings
    metrics: dict[tuple[str, ...], TimingMetric] = {
        ("response", "timings", "connection-acquisition"): timings.connection_acquisition,
        ("response", "timings", "dns"): timings.dns,
        ("response", "timings", "connect"): timings.connect,
        ("response", "timings", "proxy-tunnel"): timings.proxy_tunnel,
        ("response", "timings", "tls"): timings.tls,
        ("response", "timings", "request-write"): timings.request_write,
        ("response", "timings", "response-headers"): timings.response_headers,
        ("response", "timings", "server-wait"): timings.server_wait,
        ("response", "timings", "first-body-byte"): timings.first_body_byte,
        ("response", "timings", "body-read"): timings.body_read,
        ("response", "timings", "elapsed"): timings.elapsed,
    }
    metric = metrics.get(selector.path.segments)
    if metric is None:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.field.unsupported"))
    selected = select_timing_value(metric)
    return (
        selected
        if type(selected) is SelectionFactUnavailable
        else _one(snapshot, selector, selected, exchange, meter)
    )


def _endpoint(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    if type(selector.path) is not StaticFieldPath:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.field.unsupported"))
    endpoint = response.endpoint
    values: dict[tuple[str, ...], _SelectedOutput] = {
        ("response", "endpoint", "remote-address"): JsonString(value=endpoint.remote_address.value),
        ("response", "endpoint", "remote-port"): JsonInteger(value=endpoint.remote_port.value),
        ("response", "endpoint", "connection-id"): JsonString(value=endpoint.connection_id),
        ("response", "endpoint", "connection-reused"): JsonBoolean(
            value=endpoint.connection_reused
        ),
    }
    value = values.get(selector.path.segments)
    if value is None:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.field.unsupported"))
    return _one(snapshot, selector, value, exchange, meter)


def _tls(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    response: ResponseObservation,
    meter: SelectionMeter,
) -> SelectionResult:
    if type(selector.path) is not StaticFieldPath:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.field.unsupported"))
    segments = selector.path.segments
    layer = response.tls.origin if "origin" in segments else response.tls.connect
    if type(layer) is not TlsMetadata:
        return SelectionFactUnavailable(code=DiagnosticCode(value="selection.tls.unavailable"))
    prefix = ("response", "tls", "origin" if "origin" in segments else "connect")
    values: dict[tuple[str, ...], _SelectedOutput] = {
        (*prefix, "version"): JsonString(value=layer.version.value),
        (*prefix, "cipher-suite"): JsonString(value=layer.cipher_suite),
        (*prefix, "alpn"): JsonString(value=layer.alpn.value),
        (*prefix, "session-resumed"): JsonBoolean(value=layer.session_resumed),
    }
    value = values.get(segments)
    if value is None:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.field.unsupported"))
    return _one(snapshot, selector, value, exchange, meter)


def _one(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    value: _SelectedOutput,
    exchange: HttpExchange,
    meter: SelectionMeter,
) -> SelectionResult:
    field = (
        selector.path
        if type(selector.path) is StaticFieldPath
        else StaticFieldPath(segments=tuple(selector.source.value.split(".")))
    )
    match = SelectionMatch(
        value=value,
        location=ExchangeLocation(
            environment_execution_id=snapshot.observation.execution_id,
            source=selector.source,
            exchange_id=exchange.exchange_id,
            field=field,
        ),
    )
    if (exhausted := account_match_before_append(match, meter)) is not None:
        return exhausted
    return _ready(snapshot, selector, (match,))


def _ready(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    matches: tuple[SelectionMatch, ...],
) -> SelectionReady:
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=matches,
    )
