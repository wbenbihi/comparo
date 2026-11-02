"""Adversarial validation tests for cross-object HTTP observation invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import TlsVersion as PolicyTlsVersion
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.transport import TlsVersion as ObservedTlsVersion
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Duration
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import proxy_request_route_endpoint
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.observation_factories import tls


def execution_timings() -> EnvironmentExecutionTimings:
    """Build explicit unavailable execution intervals."""
    unavailable = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionTimings(queue=unavailable, elapsed=unavailable)


def test_tls_observation_binds_applied_policy_and_negotiated_minimum() -> None:
    effective = request()
    observed = response()
    assert observed.tls.origin is None
    mismatched_policy = msgspec.structs.replace(
        effective.policies.tls,
        minimum_version=PolicyTlsVersion.TLS_1_3,
    )
    with pytest.raises(ValueError, match="policy evidence"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=800)),
            request=authorized_request(effective, request_occurrence=occurrence(exchange=800)),
            response=msgspec.structs.replace(
                observed,
                tls=TlsLayers(
                    connect=msgspec.structs.replace(
                        tls(),
                        applied_policy=mismatched_policy,
                    )
                ),
            ),
            failure=None,
            redirect=None,
            timings=exchange_timings(),
        )

    strict = msgspec.structs.replace(
        effective,
        policies=msgspec.structs.replace(effective.policies, tls=mismatched_policy),
    )
    with pytest.raises(ValueError, match="below the authorized minimum"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=801)),
            request=authorized_request(strict, request_occurrence=occurrence(exchange=801)),
            response=msgspec.structs.replace(
                observed,
                tls=TlsLayers(
                    connect=msgspec.structs.replace(
                        tls(),
                        version=ObservedTlsVersion.TLS_1_2,
                        applied_policy=mismatched_policy,
                    )
                ),
            ),
            failure=None,
            redirect=None,
            timings=exchange_timings(),
        )


@pytest.mark.parametrize(
    ("method", "status", "reason"),
    [
        ("HEAD", 200, NoBodyReason.HEAD_REQUEST),
        ("CONNECT", 200, NoBodyReason.CONNECT_TUNNEL),
        ("GET", 205, NoBodyReason.STATUS_205),
        ("GET", 304, NoBodyReason.STATUS_304),
    ],
)
def test_no_body_reason_is_bound_to_method_and_status(
    method: str,
    status: int,
    reason: NoBodyReason,
) -> None:
    effective_request = request(method=method)
    observed_response = response(status=status, body=NoResponseBody(reason=reason))
    observed = exchange(
        exchange_number=status,
        effective_request=effective_request,
        observed_response=observed_response,
    )
    assert observed.response is observed_response


def test_exchange_rejects_impossible_tls_layer_topologies_and_protocol_drift() -> None:
    effective_request = request()
    direct = response()
    with pytest.raises(ValueError, match="direct route"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=1)),
            request=authorized_request(
                effective_request,
                request_occurrence=occurrence(exchange=1),
            ),
            response=msgspec.structs.replace(
                direct,
                tls=TlsLayers(connect=tls(), origin=tls()),
            ),
            failure=None,
            redirect=None,
            timings=exchange_timings(),
        )
    with pytest.raises(ValueError, match="ALPN"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=2)),
            request=authorized_request(
                effective_request,
                request_occurrence=occurrence(exchange=2),
            ),
            response=msgspec.structs.replace(
                direct,
                tls=TlsLayers(connect=tls(HttpProtocol.HTTP_1_1)),
            ),
            failure=None,
            redirect=None,
            timings=exchange_timings(),
        )

    proxied, route, proxy_endpoint = proxy_request_route_endpoint(effective_request)
    with pytest.raises(ValueError, match="separate origin TLS"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=3)),
            request=authorized_request(
                proxied,
                route,
                request_occurrence=occurrence(exchange=3),
            ),
            response=msgspec.structs.replace(
                direct,
                endpoint=proxy_endpoint,
                tls=TlsLayers(connect=TlsNotPerformed()),
            ),
            failure=None,
            redirect=None,
            timings=exchange_timings(proxy=True),
        )

    clear_request = request(scheme=HttpScheme.HTTP)
    clear_proxied, clear_route, clear_proxy_endpoint = proxy_request_route_endpoint(clear_request)
    clear_response = msgspec.structs.replace(
        response(scheme=HttpScheme.HTTP),
        endpoint=clear_proxy_endpoint,
        tls=TlsLayers(connect=TlsNotPerformed()),
    )
    assert (
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=4)),
            request=authorized_request(
                clear_proxied,
                clear_route,
                request_occurrence=occurrence(exchange=4),
            ),
            response=clear_response,
            failure=None,
            redirect=None,
            timings=exchange_timings(proxy=True, clear_text=True),
        ).response
        is clear_response
    )
    with pytest.raises(ValueError, match="clear-text proxy target"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=5)),
            request=authorized_request(
                clear_proxied,
                clear_route,
                request_occurrence=occurrence(exchange=5),
            ),
            response=msgspec.structs.replace(
                clear_response,
                tls=TlsLayers(connect=TlsNotPerformed(), origin=tls()),
            ),
            failure=None,
            redirect=None,
            timings=exchange_timings(proxy=True, clear_text=True),
        )


def test_exchange_and_attempt_reject_untyped_nested_components() -> None:
    effective_request = request(attempts=2)
    authorized = authorized_request(effective_request)
    observed_response = response()
    timings = exchange_timings()
    exchange_id = ExchangeId(value=UUID(int=1))
    with pytest.raises(TypeError, match="exchange response"):
        HttpExchange(
            exchange_id=exchange_id,
            request=authorized,
            response=cast("ResponseObservation", "response"),
            failure=None,
            redirect=None,
            timings=timings,
        )
    with pytest.raises(TypeError, match="exchange failure"):
        HttpExchange(
            exchange_id=exchange_id,
            request=authorized,
            response=observed_response,
            failure=cast("TransportFailure", "failure"),
            redirect=None,
            timings=timings,
        )
    with pytest.raises(TypeError, match="exchange redirect"):
        HttpExchange(
            exchange_id=exchange_id,
            request=authorized,
            response=observed_response,
            failure=None,
            redirect=cast("FollowedRedirect", "redirect"),
            timings=timings,
        )
    with pytest.raises(TypeError, match="exchange timings"):
        HttpExchange(
            exchange_id=exchange_id,
            request=authorized,
            response=observed_response,
            failure=None,
            redirect=None,
            timings=cast("HttpExchangeTimings", "timings"),
        )

    valid_exchange = exchange(
        exchange_number=1,
        effective_request=effective_request,
        observed_response=observed_response,
    )
    with pytest.raises(TypeError, match="retry metadata"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=1)),
            retry=cast("RetryMetadata", "retry"),
            exchanges=(valid_exchange,),
            timings=attempt_timings(),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=1)),
            retry=None,
            exchanges=cast("tuple[HttpExchange, ...]", []),
            timings=attempt_timings(),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=1)),
            retry=None,
            exchanges=cast("tuple[HttpExchange, ...]", ("exchange",)),
            timings=attempt_timings(),
        )
    with pytest.raises(TypeError, match="attempt timings"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=1)),
            retry=None,
            exchanges=(valid_exchange,),
            timings=cast("HttpAttemptTimings", "timings"),
        )


def test_retry_status_429_is_accepted_only_when_enabled() -> None:
    effective_request = request(attempts=2)
    effective_request = msgspec.structs.replace(
        effective_request,
        policies=msgspec.structs.replace(
            effective_request.policies,
            retries=msgspec.structs.replace(
                effective_request.policies.retries,
                conditions=(RetryCondition.STATUS_429,),
            ),
        ),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(
            exchange(
                exchange_number=1,
                effective_request=effective_request,
                observed_response=response(status=429),
            ),
        ),
        timings=attempt_timings(),
    )
    second = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=RetryMetadata(
            trigger=RetryTrigger.STATUS_429,
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        ),
        exchanges=(
            exchange(
                exchange_number=2,
                effective_request=effective_request,
                observed_response=response(),
            ),
        ),
        timings=attempt_timings(),
    )
    observed = EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=1)),
        environment=effective_request.environment,
        step_owner=None,
        attempts=(first, second),
        timings=execution_timings(),
    )
    assert observed.attempts[-1].retry is not None


def test_execution_rejects_untyped_collections_and_duplicate_occurrence_ids() -> None:
    effective_request = request(attempts=2)
    failed_exchange = exchange(
        exchange_number=1,
        effective_request=effective_request,
        observed_response=None,
        failure=failure(FailurePhase.CONNECT),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(failed_exchange,),
        timings=attempt_timings(),
    )
    successful_exchange = exchange(
        exchange_number=2,
        effective_request=effective_request,
        observed_response=response(),
    )
    second = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=RetryMetadata(
            trigger=RetryTrigger.CONNECT_FAILURE,
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        ),
        exchanges=(successful_exchange,),
        timings=attempt_timings(),
    )

    def observe(*attempts: HttpAttempt) -> EnvironmentExecutionObservation:
        return EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=attempts,
            timings=execution_timings(),
        )

    with pytest.raises(TypeError, match="execution Environment"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=cast("DefinitionRef", "staging"),
            step_owner=None,
            attempts=(first,),
            timings=execution_timings(),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=cast("tuple[HttpAttempt, ...]", ("attempt",)),
            timings=execution_timings(),
        )
    with pytest.raises(TypeError, match="execution timings"):
        EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(first,),
            timings=cast("EnvironmentExecutionTimings", "timings"),
        )
    duplicate_attempt_exchange = msgspec.structs.replace(
        successful_exchange,
        request=authorized_request(
            effective_request,
            request_occurrence=occurrence(attempt=1, exchange=2),
        ),
    )
    duplicate_attempt = msgspec.structs.replace(
        second,
        attempt_id=first.attempt_id,
        exchanges=(duplicate_attempt_exchange,),
    )
    with pytest.raises(ValueError, match="attempt occurrence IDs"):
        observe(first, duplicate_attempt)
    duplicated_exchange = msgspec.structs.replace(
        successful_exchange,
        exchange_id=failed_exchange.exchange_id,
        request=authorized_request(
            effective_request,
            request_occurrence=occurrence(attempt=2, exchange=1),
        ),
    )
    with pytest.raises(ValueError, match="HTTP occurrence IDs"):
        observe(
            first,
            msgspec.structs.replace(second, exchanges=(duplicated_exchange,)),
        )


def test_execution_requires_one_retry_and_redirect_policy_identity() -> None:
    effective_request = request(attempts=2)
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(
            exchange(
                exchange_number=1,
                effective_request=effective_request,
                observed_response=None,
                failure=failure(FailurePhase.CONNECT),
            ),
        ),
        timings=attempt_timings(),
    )

    def retry_attempt(changed_request: EffectiveRequest) -> HttpAttempt:
        return HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=2)),
            retry=RetryMetadata(
                trigger=RetryTrigger.CONNECT_FAILURE,
                backoff=Duration(nanoseconds=0),
                jitter=Duration(nanoseconds=0),
            ),
            exchanges=(
                exchange(
                    exchange_number=2,
                    effective_request=changed_request,
                    observed_response=response(),
                ),
            ),
            timings=attempt_timings(),
        )

    def observe(second: HttpAttempt) -> EnvironmentExecutionObservation:
        return EnvironmentExecutionObservation(
            execution_id=EnvironmentExecutionId(value=UUID(int=1)),
            environment=effective_request.environment,
            step_owner=None,
            attempts=(first, second),
            timings=execution_timings(),
        )

    with pytest.raises(ValueError, match="retry policy"):
        observe(retry_attempt(request(attempts=3)))
    with pytest.raises(ValueError, match="redirect policy"):
        observe(retry_attempt(request(attempts=2, redirect_hops=1)))
