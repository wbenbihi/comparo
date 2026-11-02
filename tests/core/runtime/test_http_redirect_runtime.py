"""Redirect orchestration through the production resolver and fake transport contract."""

from __future__ import annotations

import asyncio
from uuid import UUID
from uuid import uuid5

import msgspec
import pytest

from comparo.adapters.http.redirect import StandardRedirectResolver
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.pre_send import AuthorizedResolutionRequest
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.pre_send import HttpResolutionGrant
from comparo.core.domain.http.pre_send import HttpResolutionIntent
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.retry_observation_validation import _observed_retry_trigger
from comparo.core.domain.http.transition_validation import http_pre_send_request
from comparo.core.domain.http.transition_validation import validate_redirect_pre_send_transition
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.scopes import AttemptScope
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import ExecutionWork
from comparo.core.runtime import FakeExchangeAction
from comparo.core.runtime import HttpExecutionDriver
from comparo.core.runtime import HttpTransportResult
from comparo.core.runtime import ImmediateRetryWaiter
from comparo.core.runtime import ObservationOnlySnapshotProvider
from comparo.core.runtime import ObservedExecution
from comparo.core.runtime import ProgrammableFakeTransport
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime.http import NoRedirectResolver
from comparo.core.runtime.http import _retry_trigger
from comparo.core.runtime.ports import HttpExchangePreparer
from comparo.core.runtime.ports import PreparedRedirect
from comparo.core.runtime.ports import RedirectFailure
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.runtime.conftest import AllowingExchangePreparer


def _exchange_id(execution_id: UUID, exchange_index: int, *, attempt_index: int = 0) -> ExchangeId:
    attempt_id = uuid5(execution_id, f"attempt:{attempt_index}")
    return ExchangeId(value=uuid5(attempt_id, f"exchange:{exchange_index}"))


def _redirect_response(location: str, *, duplicate: bool = False) -> ResponseObservation:
    values = (location, "/other") if duplicate else (location,)
    fields = ResponseFields(
        fields=tuple(
            ResponseField(
                name=HeaderName(value="location"),
                value=HeaderText(value=value),
            )
            for value in values
        )
    )
    return msgspec.structs.replace(
        response(
            status=302,
            body=NoResponseBody(reason=NoBodyReason.HEAD_REQUEST),
        ),
        headers=fields,
    )


class RedirectRequestProvider:
    """Build one request with an explicit automatic redirect ceiling."""

    def __init__(self, *, hops: int, attempts: int = 1) -> None:
        self._hops = hops
        self._attempts = attempts

    async def prepare(
        self,
        work: ExecutionWork,
        attempt: AttemptScope,
    ) -> FinalizedWireRequest:
        del attempt
        environment = work.environment
        effective = request(
            method="HEAD",
            environment_id=environment.identity.resource_id.value,
            attempts=self._attempts,
            redirect_hops=self._hops,
        )
        return finalized_wire_request(msgspec.structs.replace(effective, environment=environment))

    async def close(self) -> None:
        """Release no state."""


class DenyRedirectPreparer:
    """Authorize the initial exchange and deny the freshly rewritten redirect target."""

    def __init__(self) -> None:
        self._initial = AllowingExchangePreparer()
        self.requests: list[FinalizedWireRequest] = []

    async def prepare(
        self,
        request_value: FinalizedWireRequest,
        occurrence: HttpRequestOccurrence,
    ) -> AuthorizedExchangeRequest | HttpAuthorizationDenial:
        if not self.requests:
            self.requests.append(request_value)
            return await self._initial.prepare(request_value, occurrence)
        self.requests.append(request_value)
        route = DirectResolvedRoute(
            destination=destination(
                host=request_value.url.authority.host.value,
                scheme=request_value.url.scheme,
                port=effective_port(request_value.url).value,
            )
        )
        return HttpAuthorizationDenial(
            intent=HttpAuthorizationIntent(
                request=request_value,
                route=route,
                occurrence=occurrence,
                plan_fingerprint=fingerprint("f"),
                effective_policy_fingerprint=fingerprint("e"),
            ),
            authority=authority(),
            code=DiagnosticCode(value="http.redirect-target-denied"),
            timings=exchange_timings(failure_phase=FailurePhase.POLICY),
        )

    async def close(self) -> None:
        """Release the initial authorization adapter."""
        await self._initial.close()


def _driver(
    transport: ProgrammableFakeTransport,
    *,
    hops: int,
    preparer: HttpExchangePreparer | None = None,
) -> HttpExecutionDriver:
    return HttpExecutionDriver(
        request_provider=RedirectRequestProvider(hops=hops),
        exchange_preparer=preparer or AllowingExchangePreparer(),
        transport=transport,
        snapshot_provider=ObservationOnlySnapshotProvider(),
        retry_waiter=ImmediateRetryWaiter(),
        cancellation=RunCancellation(),
        maximum_attempts=PositiveCount(value=1),
        redirect_resolver=StandardRedirectResolver(),
    )


def test_driver_follows_redirect_with_a_fresh_exchange_authorization(
    prepared_comparison_run: PreparedRun,
) -> None:
    work = next(iter_execution_work(prepared_comparison_run))
    first = _exchange_id(work.scope.execution_id.value, 0)
    second = _exchange_id(work.scope.execution_id.value, 1)
    transport = ProgrammableFakeTransport(
        {
            first: FakeExchangeAction(
                result=HttpTransportResult(
                    response=_redirect_response("/next"),
                    failure=None,
                    timings=exchange_timings(),
                )
            ),
            second: FakeExchangeAction(
                result=HttpTransportResult(
                    response=response(body=NoResponseBody(reason=NoBodyReason.HEAD_REQUEST)),
                    failure=None,
                    timings=exchange_timings(),
                )
            ),
        }
    )
    preparer = AllowingExchangePreparer()
    driver = _driver(transport, hops=1, preparer=preparer)
    outcome = asyncio.run(driver.execute(work))
    asyncio.run(driver.close())

    assert type(outcome) is ObservedExecution
    attempt = outcome.observation.attempts[0]
    assert len(attempt.exchanges) == 2
    assert attempt.exchanges[0].redirect is not None
    assert attempt.exchanges[1].redirect is None
    assert attempt.redirect_pre_send is None
    assert transport.calls == (first, second)
    assert len(preparer.requests) == 2
    segment = preparer.requests[1].url.path.segments[0]
    assert type(segment) is EncodedPathSegment
    assert segment.value == "next"


def test_driver_stops_at_redirect_hop_ceiling_and_on_invalid_location(
    prepared_comparison_run: PreparedRun,
) -> None:
    work = next(iter_execution_work(prepared_comparison_run))
    first = _exchange_id(work.scope.execution_id.value, 0)
    ceiling_transport = ProgrammableFakeTransport(
        {
            first: FakeExchangeAction(
                result=HttpTransportResult(
                    response=_redirect_response("/next"),
                    failure=None,
                    timings=exchange_timings(),
                )
            )
        }
    )
    ceiling_driver = _driver(ceiling_transport, hops=0)
    ceiling = asyncio.run(ceiling_driver.execute(work))
    asyncio.run(ceiling_driver.close())
    assert type(ceiling) is ObservedExecution
    assert ceiling.observation.attempts[0].exchanges[0].redirect is None

    invalid_transport = ProgrammableFakeTransport(
        {
            first: FakeExchangeAction(
                result=HttpTransportResult(
                    response=_redirect_response("/one", duplicate=True),
                    failure=None,
                    timings=exchange_timings(),
                )
            )
        }
    )
    invalid_driver = _driver(invalid_transport, hops=1)
    invalid = asyncio.run(invalid_driver.execute(work))
    asyncio.run(invalid_driver.close())
    assert type(invalid) is ObservedExecution
    exchange = invalid.observation.attempts[0].exchanges[0]
    assert exchange.response is not None
    assert exchange.failure is not None
    assert exchange.failure.code.value == "http.redirect-invalid"


def test_driver_retains_redirect_response_and_fresh_pre_send_denial(
    prepared_comparison_run: PreparedRun,
) -> None:
    work = next(iter_execution_work(prepared_comparison_run))
    first = _exchange_id(work.scope.execution_id.value, 0)
    transport = ProgrammableFakeTransport(
        {
            first: FakeExchangeAction(
                result=HttpTransportResult(
                    response=_redirect_response("/next"),
                    failure=None,
                    timings=exchange_timings(),
                )
            )
        }
    )
    driver = _driver(
        transport,
        hops=1,
        preparer=DenyRedirectPreparer(),
    )
    outcome = asyncio.run(driver.execute(work))
    asyncio.run(driver.close())

    assert type(outcome) is ObservedExecution
    attempt = outcome.observation.attempts[0]
    assert attempt.exchanges[0].response is not None
    assert attempt.exchanges[0].redirect is not None
    assert type(attempt.redirect_pre_send) is HttpAuthorizationDenial
    assert attempt.redirect_pre_send.code.value == "http.redirect-target-denied"
    assert transport.calls == (first,)
    assert _observed_retry_trigger(attempt) is None

    intent = attempt.redirect_pre_send.intent
    resolution_failure = HttpResolutionFailure(
        request=AuthorizedResolutionRequest(
            authorization=HttpResolutionGrant(
                intent=HttpResolutionIntent(
                    request=intent.request,
                    occurrence=intent.occurrence,
                    plan_fingerprint=intent.plan_fingerprint,
                    effective_policy_fingerprint=intent.effective_policy_fingerprint,
                ),
                authority=attempt.redirect_pre_send.authority,
            )
        ),
        failure=TransportFailure(
            phase=FailurePhase.DNS,
            code=DiagnosticCode(value="http.redirect-dns-failed"),
            retryable=True,
        ),
        timings=exchange_timings(failure_phase=FailurePhase.DNS),
    )
    dns_attempt = msgspec.structs.replace(
        attempt,
        redirect_pre_send=resolution_failure,
    )
    assert _observed_retry_trigger(dns_attempt) is RetryTrigger.DNS_FAILURE
    assert _retry_trigger(dns_attempt) is None
    assert http_pre_send_request(resolution_failure) == resolution_failure.request

    with pytest.raises(TypeError, match="redirect pre-send outcome"):
        msgspec.structs.replace(attempt, redirect_pre_send=object())
    with pytest.raises(ValueError, match="initial and redirect"):
        msgspec.structs.replace(attempt, pre_send=attempt.redirect_pre_send)
    with pytest.raises(ValueError, match="requires a preceding exchange"):
        msgspec.structs.replace(attempt, exchanges=())
    with pytest.raises(TypeError, match="exact HTTP pre-send"):
        http_pre_send_request(object())

    redirect = attempt.exchanges[0].redirect
    assert redirect is not None
    with pytest.raises(ValueError, match="URL must equal"):
        validate_redirect_pre_send_transition(
            attempt.exchanges[0].request,
            HttpStatus(value=302),
            attempt.exchanges[0].request.request.url,
            http_pre_send_request(attempt.redirect_pre_send),
        )


def test_no_redirect_resolver_and_driver_resolver_boundary_are_explicit(
    prepared_comparison_run: PreparedRun,
) -> None:
    resolver = NoRedirectResolver()
    request_value = authorized_request(request())
    assert resolver.resolve(request_value, response()) is not None
    with pytest.raises(TypeError, match="AuthorizedExchangeRequest"):
        resolver.resolve(object(), response())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ResponseObservation"):
        resolver.resolve(request_value, object())  # type: ignore[arg-type]

    prepared = StandardRedirectResolver().resolve(
        request_value,
        _redirect_response("/next"),
    )
    assert type(prepared) is PreparedRedirect
    with pytest.raises(TypeError, match="FinalizedWireRequest"):
        PreparedRedirect(
            request=object(),  # type: ignore[arg-type]
            transition=prepared.transition,
        )
    with pytest.raises(TypeError, match="FollowedRedirect"):
        PreparedRedirect(
            request=prepared.request,
            transition=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="resolved Location URL"):
        PreparedRedirect(
            request=request_value.request,
            transition=prepared.transition,
        )
    with pytest.raises(TypeError, match="TransportFailure"):
        RedirectFailure(failure=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="protocol phase"):
        RedirectFailure(
            failure=TransportFailure(
                phase=FailurePhase.CONNECT,
                code=DiagnosticCode(value="http.connect-failed"),
                retryable=False,
            )
        )

    no_redirect_transport = ProgrammableFakeTransport(
        {
            _exchange_id(
                next(iter_execution_work(prepared_comparison_run)).scope.execution_id.value,
                0,
            ): FakeExchangeAction(
                result=HttpTransportResult(
                    response=response(body=NoResponseBody(reason=NoBodyReason.HEAD_REQUEST)),
                    failure=None,
                    timings=exchange_timings(),
                )
            )
        }
    )
    no_redirect_driver = _driver(no_redirect_transport, hops=1)
    no_redirect_outcome = asyncio.run(
        no_redirect_driver.execute(next(iter_execution_work(prepared_comparison_run)))
    )
    asyncio.run(no_redirect_driver.close())
    assert type(no_redirect_outcome) is ObservedExecution

    transport = ProgrammableFakeTransport({})
    with pytest.raises(TypeError, match="HttpRedirectResolver"):
        HttpExecutionDriver(
            request_provider=RedirectRequestProvider(hops=0),
            exchange_preparer=AllowingExchangePreparer(),
            transport=transport,
            snapshot_provider=ObservationOnlySnapshotProvider(),
            retry_waiter=ImmediateRetryWaiter(),
            cancellation=RunCancellation(),
            maximum_attempts=PositiveCount(value=1),
            redirect_resolver=object(),  # type: ignore[arg-type]
        )
