"""Cycle-free ownership and storage-lineage validation for HTTP observations."""

from __future__ import annotations

from typing import Protocol

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import ViewId


class ExchangeLineageLike(Protocol):
    """Exchange facts needed by occurrence and storage-lineage validation."""

    @property
    def exchange_id(self) -> ExchangeId:
        """Return the exchange occurrence identity."""
        ...

    @property
    def request(self) -> AuthorizedExchangeRequest:
        """Return the authorized request occurrence."""
        ...

    @property
    def response(self) -> ResponseObservation | None:
        """Return the observed response when one exists."""
        ...


class AttemptLineageLike(Protocol):
    """Attempt facts needed without importing the aggregate module."""

    @property
    def attempt_id(self) -> AttemptId:
        """Return the attempt occurrence identity."""
        ...

    @property
    def exchanges(self) -> tuple[ExchangeLineageLike, ...]:
        """Return the attempt's ordered exchange occurrences."""
        ...

    @property
    def pre_send(self) -> HttpPreSendOutcome | None:
        """Return the exact pre-send terminal outcome when no exchange occurred."""
        ...


def validate_execution_occurrences(
    execution_id: EnvironmentExecutionId,
    environment: DefinitionRef,
    step_owner: StepExecutionId | None,
    attempts: tuple[AttemptLineageLike, ...],
) -> None:
    """Bind unique attempt/exchange IDs to one exact execution scope."""
    exchanges = tuple(exchange for attempt in attempts for exchange in attempt.exchanges)
    requests = tuple(exchange.request for exchange in exchanges) + tuple(
        attempt.pre_send.request
        if type(attempt.pre_send) is HttpResolutionFailure
        else attempt.pre_send
        for attempt in attempts
        if attempt.pre_send is not None
    )
    if any(request.request.environment != environment for request in requests):
        raise ValueError("every HTTP request must bind to the observed Environment")
    if len({attempt.attempt_id for attempt in attempts}) != len(attempts):
        raise ValueError("attempt occurrence IDs must be unique within an execution")
    occurrence_ids = tuple(request.occurrence.exchange_id for request in requests)
    if len(set(occurrence_ids)) != len(occurrence_ids):
        raise ValueError("HTTP occurrence IDs must be unique within an execution")
    if any(request.occurrence.execution_id != execution_id for request in requests):
        raise ValueError("HTTP request authority does not own this execution")
    if any(request.occurrence.attempt.step_execution_id != step_owner for request in requests):
        raise ValueError("exchange request authorization does not own this Scenario step")
    if len({request.occurrence.attempt.execution for request in requests}) != 1:
        raise ValueError("one observation cannot span execution scopes")


def validate_execution_storage_lineage(
    attempts: tuple[AttemptLineageLike, ...],
    views: tuple[DecodedViewHandle, ...],
) -> None:
    """Bind unique response handles and reusable request snapshots to decoded views."""
    exchanges = tuple(exchange for attempt in attempts for exchange in attempt.exchanges)
    handles = tuple(
        exchange.response.body.handle
        for exchange in exchanges
        if exchange.response is not None
        and type(exchange.response.body) is ResponseEntityBody
        and exchange.response.body.handle is not None
    )
    body_ids: tuple[BodyId, ...] = tuple(handle.body_id for handle in handles)
    if len(set(body_ids)) != len(body_ids):
        raise ValueError("body IDs must be unique within an Environment execution")
    request_sources = tuple(
        exchange.request.request.body.source
        for exchange in exchanges
        if type(exchange.request.request.body) is MaterializedBody
    )
    request_sources_by_id = {source.source_id: source for source in request_sources}
    if set(body_ids) & set(request_sources_by_id):
        raise ValueError("request and response body identities must be disjoint")
    view_ids: tuple[ViewId, ...] = tuple(view.view_id for view in views)
    if len(set(view_ids)) != len(view_ids):
        raise ValueError("view IDs must be unique within an Environment execution")
    bodies_by_id: dict[BodyId, BodyHandle | RepeatableByteSource] = {
        handle.body_id: handle for handle in handles
    }
    bodies_by_id.update(request_sources_by_id)
    if any(
        view.body_id not in bodies_by_id or view.source != bodies_by_id[view.body_id]
        for view in views
    ):
        raise ValueError("every execution view must bind to one exact request or response body")
