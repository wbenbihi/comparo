"""Pure finalized-request source evaluation."""

from __future__ import annotations

from typing import cast
from urllib.parse import quote

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import PathSegment
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import HeaderLocation
from comparo.core.domain.selectors.locations import HttpMessageSection
from comparo.core.domain.selectors.locations import NamedFieldLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionObservedInvalid
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import BareQueryFieldValue
from comparo.core.domain.selectors.values import HttpFieldTextValue
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonString
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.body_evaluator import evaluate_request_body
from comparo.core.selection.http_field_evaluator import field_selected
from comparo.core.selection.http_field_evaluator import project_header_value
from comparo.core.selection.match_accounting import account_match_before_append
from comparo.core.selection.match_accounting import charge_work
from comparo.core.selection.snapshot import SelectionSnapshot

_ACCESS = SensitiveValueAccess(purpose="selector.evaluation")
type _SelectedOutput = SelectedValue | SensitiveValue[SelectedValue]


def evaluate_request_source(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    request: FinalizedWireRequest,
    exchange_id: ExchangeId,
    meter: SelectionMeter,
) -> SelectionResult:
    """Evaluate one finalized request occurrence, including a pre-send request."""
    source = selector.source
    if source is SelectorSource.REQUEST_METHOD:
        return _one(snapshot, selector, JsonString(value=request.method.value), exchange_id, meter)
    if source is SelectorSource.REQUEST_URL:
        exhausted = charge_work(meter, _url_render_work(request.url))
        if exhausted is not None:
            return exhausted
        rendered = JsonString(value=_render_url(request.url))
        selected = (
            SensitiveValue(rendered, origins=request.url.taint_origins)
            if request.url.taint_origins
            else rendered
        )
        return _one(snapshot, selector, selected, exchange_id, meter)
    if source is SelectorSource.REQUEST_QUERY:
        return _query(snapshot, selector, request, exchange_id, meter)
    if source is SelectorSource.REQUEST_HEADERS:
        return _headers(snapshot, selector, request, exchange_id, meter)
    if source is SelectorSource.REQUEST_COOKIES:
        return _cookies(snapshot, selector, request, exchange_id, meter)
    if source is SelectorSource.REQUEST_BODY:
        return evaluate_request_body(snapshot, selector, request, meter)
    return SelectionUnsupported(code=DiagnosticCode(value="selection.source.unsupported"))


def evaluate_pre_send_request_source(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    outcome: HttpPreSendOutcome,
    meter: SelectionMeter,
) -> SelectionResult:
    """Select an observable request from a terminal pre-send outcome."""
    request = outcome.request.request if type(outcome) is HttpResolutionFailure else outcome.request
    return evaluate_request_source(
        snapshot,
        selector,
        request,
        outcome.occurrence.exchange_id,
        meter,
    )


def _query(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    request: FinalizedWireRequest,
    exchange_id: ExchangeId,
    meter: SelectionMeter,
) -> SelectionResult:
    matches: list[SelectionMatch] = []
    occurrences: dict[object, int] = {}
    for parameter in request.url.query.parameters:
        if (exhausted := charge_work(meter)) is not None:
            return exhausted
        occurrence = occurrences.get(parameter.name, 0)
        occurrences[parameter.name] = occurrence + 1
        if not field_selected(
            selector.path,
            name=parameter.name.value,
            occurrence=occurrence,
        ):
            continue
        selected: _SelectedOutput
        if type(parameter.value) is BareQueryValue:
            selected = BareQueryFieldValue()
        elif is_sensitive_value(parameter.value):
            value = cast(QueryText, parameter.value.reveal(access=_ACCESS))
            selected = SensitiveValue(
                HttpFieldTextValue(value=value.value),
                origins=parameter.value.origins,
            )
        else:
            value = cast(QueryText, parameter.value)
            selected = HttpFieldTextValue(value=value.value)
        match = SelectionMatch(
            value=selected,
            location=NamedFieldLocation(
                environment_execution_id=snapshot.observation.execution_id,
                source=selector.source,
                exchange_id=exchange_id,
                name=parameter.name,
                occurrence=Count(value=occurrence),
            ),
        )
        if (exhausted := account_match_before_append(match, meter)) is not None:
            return exhausted
        matches.append(match)
    return _ready(snapshot, selector, tuple(matches))


def _headers(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    request: FinalizedWireRequest,
    exchange_id: ExchangeId,
    meter: SelectionMeter,
) -> SelectionResult:
    matches = _header_matches(
        snapshot,
        selector,
        exchange_id,
        request.headers.fields,
        meter,
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
    fields: tuple[HeaderField, ...],
    meter: SelectionMeter,
) -> tuple[SelectionMatch, ...] | SelectionObservedInvalid | SelectionBudgetExceeded:
    occurrences: dict[object, int] = {}
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
                section=HttpMessageSection.REQUEST_HEADER,
                occurrence=Count(value=occurrence),
                name=name,
            ),
        )
        if (exhausted := account_match_before_append(match, meter)) is not None:
            return exhausted
        matches.append(match)
    return tuple(matches)


def _cookies(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    request: FinalizedWireRequest,
    exchange_id: ExchangeId,
    meter: SelectionMeter,
) -> SelectionResult:
    matches: list[SelectionMatch] = []
    for cookie in request.cookies.cookies:
        if (exhausted := charge_work(meter)) is not None:
            return exhausted
        if not field_selected(selector.path, name=cookie.name.value, occurrence=0):
            continue
        match = SelectionMatch(
            value=SensitiveValue(
                HttpFieldTextValue(value=cookie.value.reveal(access=_ACCESS).value),
                origins=cookie.value.origins,
            ),
            location=NamedFieldLocation(
                environment_execution_id=snapshot.observation.execution_id,
                source=selector.source,
                exchange_id=exchange_id,
                name=cookie.name,
                occurrence=Count(value=0),
            ),
        )
        if (exhausted := account_match_before_append(match, meter)) is not None:
            return exhausted
        matches.append(match)
    return _ready(snapshot, selector, tuple(matches))


def _one(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    value: _SelectedOutput,
    exchange_id: ExchangeId,
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
            exchange_id=exchange_id,
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


def _render_url(url: AbsoluteHttpUrl) -> str:
    host = url.authority.host.value
    if type(url.authority.host) is Ipv6Host:
        host = f"[{host}]"
    authority = host if url.authority.port is None else f"{host}:{url.authority.port.value}"
    path = "/" + "/".join(_render_path_segment(segment) for segment in url.path.segments)
    if url.path.trailing_slash:
        path += "/"
    query: list[str] = []
    for parameter in url.query.parameters:
        name = quote(parameter.name.value, safe="")
        if type(parameter.value) is BareQueryValue:
            query.append(name)
        else:
            value = (
                cast(QueryText, parameter.value.reveal(access=_ACCESS))
                if is_sensitive_value(parameter.value)
                else cast(QueryText, parameter.value)
            )
            query.append(f"{name}={quote(value.value, safe='')}")
    return f"{url.scheme.value}://{authority}{path}" + ("" if not query else "?" + "&".join(query))


def _render_path_segment(segment: PathSegment) -> str:
    value = (
        cast("DecodedPathSegment | EncodedPathSegment", segment.reveal(access=_ACCESS))
        if is_sensitive_value(segment)
        else cast("DecodedPathSegment | EncodedPathSegment", segment)
    )
    return quote(value.value, safe="") if type(value) is DecodedPathSegment else value.value


def _url_render_work(url: AbsoluteHttpUrl) -> int:
    """Measure URL input before any percent-encoding or output-list allocation."""
    amount = len(url.scheme.value) + len(url.authority.host.value) + 1
    if url.authority.port is not None:
        amount += len(str(url.authority.port.value))
    for segment in url.path.segments:
        value = segment.reveal(access=_ACCESS) if is_sensitive_value(segment) else segment
        amount += len(cast("DecodedPathSegment | EncodedPathSegment", value).value) + 1
    for parameter in url.query.parameters:
        amount += len(parameter.name.value) + 1
        if type(parameter.value) is not BareQueryValue:
            value = (
                parameter.value.reveal(access=_ACCESS)
                if is_sensitive_value(parameter.value)
                else parameter.value
            )
            amount += len(cast(QueryText, value).value)
    return amount
