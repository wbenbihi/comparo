"""Pure HTTP occurrence dispatch for the shared selector engine."""

from __future__ import annotations

from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.selectors.locations import AttemptLocation
from comparo.core.domain.selectors.locations import ExchangeLocation
from comparo.core.domain.selectors.locations import ExecutionLocation
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.sources import REQUEST_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.values import JsonString
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.match_accounting import account_match_before_append
from comparo.core.selection.request_evaluator import (
    evaluate_pre_send_request_source as evaluate_pre_send_request_source,
)
from comparo.core.selection.request_evaluator import evaluate_request_source
from comparo.core.selection.response_evaluator import evaluate_response_source
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.timing_values import select_timing_value


def evaluate_http_source(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    attempt: HttpAttempt,
    exchange: HttpExchange | None,
    meter: SelectionMeter,
) -> SelectionResult:
    """Evaluate one non-stream occurrence after authoritative scope resolution."""
    source = selector.source
    if source is SelectorSource.EXECUTION:
        return _execution(snapshot, selector, meter)
    if source is SelectorSource.ATTEMPT:
        return _attempt(snapshot, selector, attempt, meter)
    if exchange is None:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_TERMINAL_EXCHANGE)
    if source is SelectorSource.EXCHANGE:
        return _exchange(snapshot, selector, exchange, meter)
    if source in REQUEST_SELECTOR_SOURCES:
        return evaluate_request_source(
            snapshot,
            selector,
            exchange.request.request,
            exchange.exchange_id,
            meter,
        )
    if exchange.response is None:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_TERMINAL_RESPONSE)
    return evaluate_response_source(snapshot, selector, exchange, exchange.response, meter)


def _execution(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    meter: SelectionMeter,
) -> SelectionResult:
    field = _field(selector, ("execution",))
    if field.segments in {("execution",), ("execution", "id")}:
        value: SelectedValue | SelectionFactUnavailable = JsonString(
            value=str(snapshot.observation.execution_id.value)
        )
    else:
        metrics: dict[tuple[str, ...], TimingMetric] = {
            ("execution", "timings", "queue"): snapshot.observation.timings.queue,
            ("execution", "timings", "elapsed"): snapshot.observation.timings.elapsed,
        }
        metric = metrics[field.segments]
        value = select_timing_value(metric)
    if type(value) is SelectionFactUnavailable:
        return value
    return _accounted_ready(
        snapshot,
        selector,
        SelectionMatch(
            value=value,
            location=ExecutionLocation(
                environment_execution_id=snapshot.observation.execution_id,
                source=selector.source,
                field=field,
            ),
        ),
        meter,
    )


def _attempt(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    attempt: HttpAttempt,
    meter: SelectionMeter,
) -> SelectionResult:
    field = _field(selector, ("attempt",))
    value: SelectedValue | SelectionFactUnavailable
    if field.segments in {("attempt",), ("attempt", "id")}:
        value = JsonString(value=str(attempt.attempt_id.value))
    else:
        value = select_timing_value(attempt.timings.elapsed)
    if type(value) is SelectionFactUnavailable:
        return value
    return _accounted_ready(
        snapshot,
        selector,
        SelectionMatch(
            value=value,
            location=AttemptLocation(
                environment_execution_id=snapshot.observation.execution_id,
                source=selector.source,
                attempt_id=attempt.attempt_id,
                field=field,
            ),
        ),
        meter,
    )


def _exchange(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    exchange: HttpExchange,
    meter: SelectionMeter,
) -> SelectionResult:
    field = _field(selector, ("exchange",))
    match = SelectionMatch(
        value=JsonString(value=str(exchange.exchange_id.value)),
        location=ExchangeLocation(
            environment_execution_id=snapshot.observation.execution_id,
            source=selector.source,
            exchange_id=exchange.exchange_id,
            field=field,
        ),
    )
    return _accounted_ready(snapshot, selector, match, meter)


def _field(
    selector: CompiledSelector,
    default: tuple[str, ...],
) -> StaticFieldPath:
    if type(selector.path) is not StaticFieldPath:
        return StaticFieldPath(segments=default)
    return selector.path


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


def _accounted_ready(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    match: SelectionMatch,
    meter: SelectionMeter,
) -> SelectionResult:
    exhausted = account_match_before_append(match, meter)
    return exhausted if exhausted is not None else _ready(snapshot, selector, match)
