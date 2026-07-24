"""Behavior tests for the pure occurrence-aware observation selector."""

from __future__ import annotations

from uuid import UUID

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.media_types import MediaTypeTokenValue
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.http_fields import HttpFieldProjection
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.occurrences import ObservationOccurrenceSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.paths import SelectorPath
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.domain.selectors.results import SelectionFactUnavailable
from comparo.core.domain.selectors.results import SelectionReady
from comparo.core.domain.selectors.results import SelectionSourceUnavailable
from comparo.core.domain.selectors.results import SourceUnavailableReason
from comparo.core.domain.selectors.selector_semantics import TERMINAL_OCCURRENCE
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.selectors.values import HttpFieldItems
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.selection.accounting import SelectionCancellation
from comparo.core.selection.accounting import SelectionControl
from comparo.core.selection.engine import select
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.source_evaluator import evaluate_source
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import occurrence as request_occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_failure
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import stream_execution
from tests.core.selection.json_evaluator_factories import selection_meter


def _selector(
    source: SelectorSource,
    kind: ValueKind,
    *,
    occurrence: ObservationOccurrenceSelector | None = None,
    path: SelectorPath | None = None,
) -> CompiledSelector:
    actual_occurrence = occurrence if occurrence is not None else TERMINAL_OCCURRENCE
    actual_path = NoPath() if path is None else path
    capabilities = derive_selector_capabilities(source, actual_path, actual_occurrence)
    return CompiledSelector(
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="a" * 64),
        ),
        source=source,
        path=actual_path,
        capabilities=capabilities,
        output=ValueContract(kinds=(kind,), cardinality=capabilities.cardinality),
        occurrence=actual_occurrence,
    )


def _observation(
    *,
    effective: EffectiveRequest | None = None,
    observed_response: ResponseObservation | None = None,
) -> EnvironmentExecutionObservation:
    actual_request = request() if effective is None else effective
    observed = exchange(
        exchange_number=1,
        effective_request=actual_request,
        observed_response=(
            response(status=201) if observed_response is None else observed_response
        ),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=1)),
        environment=actual_request.environment,
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


def test_selects_terminal_request_and_response_scalars() -> None:
    snapshot = SelectionSnapshot(observation=_observation())
    method = evaluate_source(
        snapshot,
        _selector(SelectorSource.REQUEST_METHOD, ValueKind.STRING),
        selection_meter(),
    )
    status = evaluate_source(
        snapshot,
        _selector(SelectorSource.RESPONSE_STATUS, ValueKind.INTEGER),
        selection_meter(),
    )

    assert type(method) is SelectionReady
    assert method.matches[0].value == JsonString(value="GET")
    assert type(status) is SelectionReady
    assert status.matches[0].value == JsonInteger(value=201)


def test_exact_missing_exchange_is_explicitly_unavailable() -> None:
    selector = _selector(
        SelectorSource.REQUEST_METHOD,
        ValueKind.STRING,
        occurrence=ExchangeOccurrence(exchange_id=_exchange_id(999)),
    )
    result = evaluate_source(
        SelectionSnapshot(observation=_observation()), selector, selection_meter()
    )

    assert type(result) is SelectionSourceUnavailable
    assert result.reason is SourceUnavailableReason.NO_SUCH_EXCHANGE


def test_execution_and_attempt_remain_selectable_after_pre_send_failure() -> None:
    effective = request()
    failed = resolution_failure(
        effective,
        request_occurrence=request_occurrence(environment=effective.environment.identity),
    )
    observation = msgspec.structs.replace(
        _observation(effective=effective),
        attempts=(
            HttpAttempt(
                attempt_id=failed.occurrence.attempt.attempt_id,
                retry=None,
                exchanges=(),
                timings=attempt_timings(),
                pre_send=failed,
            ),
        ),
    )

    execution_result = evaluate_source(
        SelectionSnapshot(observation=observation),
        _selector(SelectorSource.EXECUTION, ValueKind.STRING),
        selection_meter(),
    )
    attempt_result = evaluate_source(
        SelectionSnapshot(observation=observation),
        _selector(SelectorSource.ATTEMPT, ValueKind.STRING),
        selection_meter(),
    )

    assert type(execution_result) is SelectionReady
    assert type(attempt_result) is SelectionReady


def test_all_exchanges_preserves_execution_order() -> None:
    observation = stream_execution(ViewKind.SSE)
    selector = _selector(
        SelectorSource.REQUEST_METHOD,
        ValueKind.STRING,
        occurrence=AllExchangesOccurrence(),
    )

    result = evaluate_source(
        SelectionSnapshot(observation=observation), selector, selection_meter()
    )

    assert type(result) is SelectionReady
    assert tuple(match.value for match in result.matches) == (
        JsonString(value="GET"),
        JsonString(value="GET"),
    )


def test_match_budget_exhaustion_never_returns_a_prefix() -> None:
    selector = _selector(
        SelectorSource.REQUEST_METHOD,
        ValueKind.STRING,
        occurrence=AllExchangesOccurrence(),
    )
    result = evaluate_source(
        SelectionSnapshot(observation=stream_execution(ViewKind.SSE)),
        selector,
        selection_meter(matches=1),
    )

    assert type(result) is SelectionBudgetExceeded


def test_request_url_is_precharged_before_rendering_or_retaining_a_match() -> None:
    effective = request()
    effective = msgspec.structs.replace(
        effective,
        url=msgspec.structs.replace(
            effective.url,
            path=UrlPath(segments=(DecodedPathSegment(value="x" * 10_000),)),
        ),
    )
    meter = selection_meter(work=100)

    result = evaluate_source(
        SelectionSnapshot(observation=_observation(effective=effective)),
        _selector(SelectorSource.REQUEST_URL, ValueKind.STRING),
        meter,
    )

    assert type(result) is SelectionBudgetExceeded
    assert meter.budgets.matches.usage.consumed == Count(value=0)
    assert meter.budgets.result_bytes.usage.consumed == ByteCount(value=0)


def test_header_item_parsing_is_precharged_before_split_or_match_append() -> None:
    observed = msgspec.structs.replace(
        response(status=200),
        headers=ResponseFields(
            fields=(
                ResponseField(
                    name=HeaderName(value="vary"),
                    value=HeaderText(value="token," * 2_000 + "final"),
                ),
            )
        ),
    )
    selector = _selector(
        SelectorSource.RESPONSE_HEADERS,
        ValueKind.HTTP_FIELD_ITEMS,
        path=HttpFieldPath(name="vary", projection=HttpFieldProjection.ITEMS),
    )
    meter = selection_meter(work=2)

    result = evaluate_source(
        SelectionSnapshot(observation=_observation(observed_response=observed)),
        selector,
        meter,
    )

    assert type(result) is SelectionBudgetExceeded
    assert meter.budgets.matches.usage.consumed == Count(value=0)
    assert meter.budgets.result_bytes.usage.consumed == ByteCount(value=0)


def test_header_field_path_filters_name_and_projects_token_items() -> None:
    observed = msgspec.structs.replace(
        response(status=200),
        headers=ResponseFields(
            fields=(
                ResponseField(
                    name=HeaderName(value="vary"),
                    value=HeaderText(value="accept-encoding, user-agent"),
                ),
                ResponseField(
                    name=HeaderName(value="server"),
                    value=HeaderText(value="example"),
                ),
            )
        ),
    )
    selector = _selector(
        SelectorSource.RESPONSE_HEADERS,
        ValueKind.HTTP_FIELD_ITEMS,
        path=HttpFieldPath(
            name="vary",
            projection=HttpFieldProjection.ITEMS,
        ),
    )

    result = evaluate_source(
        SelectionSnapshot(observation=_observation(observed_response=observed)),
        selector,
        selection_meter(),
    )

    assert type(result) is SelectionReady
    assert result.matches[0].value == HttpFieldItems(
        items=(
            MediaTypeTokenValue(value="accept-encoding"),
            MediaTypeTokenValue(value="user-agent"),
        )
    )


def test_public_engine_honors_cancellation_without_consuming_budget() -> None:
    meter = selection_meter()
    control = SelectionControl(
        budgets=meter.budgets,
        cancellation=SelectionCancellation(
            cancelled=True,
            code=DiagnosticCode(value="selection.cancelled"),
        ),
    )

    evaluation = select(
        SelectionSnapshot(observation=_observation()),
        _selector(SelectorSource.RESPONSE_STATUS, ValueKind.INTEGER),
        control,
    )

    assert type(evaluation.result) is SelectionCancelled
    assert evaluation.budgets == control.budgets


def test_execution_timing_leaf_preserves_unavailable_fact() -> None:
    selector = _selector(
        SelectorSource.EXECUTION,
        ValueKind.DURATION,
        path=StaticFieldPath(segments=("execution", "timings", "elapsed")),
    )

    result = evaluate_source(
        SelectionSnapshot(observation=_observation()),
        selector,
        selection_meter(),
    )

    assert type(result) is SelectionFactUnavailable
    assert result.code.value.startswith("selection.timing.unavailable.")


def _exchange_id(number: int) -> ExchangeId:
    return ExchangeId(value=UUID(int=number))
