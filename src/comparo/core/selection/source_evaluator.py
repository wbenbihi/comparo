"""Pure occurrence resolution and selection dispatch over an immutable snapshot."""

from __future__ import annotations

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.occurrences import AllAttemptsOccurrence
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import AttemptOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionIncomplete
from comparo.core.domain.selectors.results import SelectionIncompleteReason
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.sources import REQUEST_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import RESPONSE_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import STREAM_SELECTOR_SOURCES
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.http_evaluator import evaluate_http_source
from comparo.core.selection.http_evaluator import evaluate_pre_send_request_source
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.stream_evaluator import evaluate_stream_source


def evaluate_source(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    meter: SelectionMeter,
) -> SelectionResult:
    """Evaluate one compiled selector using only its authoritative occurrence scope."""
    if type(snapshot) is not SelectionSnapshot:
        raise TypeError("source evaluation requires a SelectionSnapshot")
    if type(selector) is not CompiledSelector:
        raise TypeError("source evaluation requires a CompiledSelector")
    if type(meter) is not SelectionMeter:
        raise TypeError("source evaluation requires a SelectionMeter")
    if selector.source is SelectorSource.SCENARIO_STEP:
        return SelectionUnsupported(code=DiagnosticCode(value="selection.scenario.unsupported"))
    if selector.source in STREAM_SELECTOR_SOURCES:
        exhausted = meter.charge_work()
        if exhausted is not None:
            return SelectionBudgetExceeded(exhaustion=exhausted)
        return evaluate_stream_source(snapshot, selector, meter)
    occurrences = _resolve_occurrences(snapshot, selector)
    if type(occurrences) is SelectionSourceUnavailable:
        return occurrences
    return _evaluate_occurrences(snapshot, selector, meter, occurrences)


def _evaluate_occurrences(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    meter: SelectionMeter,
    occurrences: tuple[tuple[HttpAttempt, HttpExchange | None], ...],
) -> SelectionResult:
    results: list[SelectionResult] = []
    for item in occurrences:
        exhausted = meter.charge_work()
        if exhausted is not None:
            return SelectionBudgetExceeded(exhaustion=exhausted)
        result = _evaluate_occurrence(snapshot, selector, meter, *item)
        if type(result) is SelectionBudgetExceeded:
            return result
        results.append(result)
    return _combine(snapshot, selector, tuple(results))


def _resolve_occurrences(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
) -> tuple[tuple[HttpAttempt, HttpExchange | None], ...] | SelectionSourceUnavailable:
    attempts = snapshot.observation.attempts
    occurrence = selector.occurrence
    if type(occurrence) is AllAttemptsOccurrence:
        return tuple((attempt, None) for attempt in attempts)
    if type(occurrence) is AllExchangesOccurrence:
        return tuple((attempt, exchange) for attempt in attempts for exchange in attempt.exchanges)
    if type(occurrence) is AttemptOccurrence:
        attempt = next(
            (item for item in attempts if item.attempt_id == occurrence.attempt_id),
            None,
        )
        if attempt is None:
            return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_SUCH_ATTEMPT)
        return ((attempt, attempt.exchanges[-1] if attempt.exchanges else None),)
    if type(occurrence) is ExchangeOccurrence:
        found = next(
            (
                (attempt, exchange)
                for attempt in attempts
                for exchange in attempt.exchanges
                if exchange.exchange_id == occurrence.exchange_id
            ),
            None,
        )
        if found is None:
            return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_SUCH_EXCHANGE)
        return (found,)
    terminal = attempts[-1]
    return ((terminal, terminal.exchanges[-1] if terminal.exchanges else None),)


def _evaluate_occurrence(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    meter: SelectionMeter,
    attempt: HttpAttempt,
    exchange: HttpExchange | None,
) -> SelectionResult:
    if selector.source in {SelectorSource.EXECUTION, SelectorSource.ATTEMPT}:
        return evaluate_http_source(snapshot, selector, attempt, exchange, meter)
    if exchange is None and selector.source in REQUEST_SELECTOR_SOURCES:
        if attempt.pre_send is None:
            return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_TERMINAL_EXCHANGE)
        return evaluate_pre_send_request_source(snapshot, selector, attempt.pre_send, meter)
    if exchange is None:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_TERMINAL_EXCHANGE)
    if selector.source in RESPONSE_SELECTOR_SOURCES and exchange.response is None:
        return SelectionSourceUnavailable(reason=SourceUnavailableReason.NO_TERMINAL_RESPONSE)
    return evaluate_http_source(snapshot, selector, attempt, exchange, meter)


def _combine(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    results: tuple[SelectionResult, ...],
) -> SelectionResult:
    ready = tuple(result for result in results if type(result) is SelectionReady)
    if len(ready) != len(results):
        if type(selector.occurrence) is AllExchangesOccurrence:
            return SelectionIncomplete(reason=SelectionIncompleteReason.UPSTREAM_FAILURE)
        return next(result for result in results if type(result) is not SelectionReady)
    return SelectionReady(
        selector=selector,
        environment_execution_id=snapshot.observation.execution_id,
        matches=tuple(match for result in ready for match in result.matches),
        views=tuple(view for result in ready for view in result.views),
    )
