"""Cross-object timing truth tests for routes, TLS, reuse, and body semantics."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.lifecycle_validation import validate_attempt_timing_envelope
from comparo.core.domain.http.lifecycle_validation import validate_execution_timing_envelope
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.timing_validation import _validate_entity_timing_metric
from comparo.core.domain.http.timing_validation import validate_exchange_timing_truth
from comparo.core.domain.http.timing_validation import validate_pre_send_timing_truth
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Duration
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import measured
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import proxy_request_route_endpoint
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.observation_factories import tls


def test_parent_timing_envelopes_cover_children_and_retry_delays() -> None:
    exchange_timing = msgspec.structs.replace(exchange_timings(), elapsed=measured(10))
    attempt = HttpAttemptTimings(elapsed=measured(10))
    validate_attempt_timing_envelope(attempt, (exchange_timing,))
    with pytest.raises(ValueError, match="shorter than its exchanges"):
        validate_attempt_timing_envelope(
            HttpAttemptTimings(elapsed=measured(9)),
            (exchange_timing,),
        )
    unavailable = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    validate_attempt_timing_envelope(
        attempt,
        (msgspec.structs.replace(exchange_timing, elapsed=unavailable),),
    )

    execution = EnvironmentExecutionTimings(queue=measured(1), elapsed=measured(25))
    validate_execution_timing_envelope(
        execution,
        (HttpAttemptTimings(elapsed=measured(10)),) * 2,
        (Duration(nanoseconds=5),),
    )
    with pytest.raises(ValueError, match="shorter than attempts and backoff"):
        validate_execution_timing_envelope(
            msgspec.structs.replace(execution, elapsed=measured(24)),
            (HttpAttemptTimings(elapsed=measured(10)),) * 2,
            (Duration(nanoseconds=5),),
        )
    validate_execution_timing_envelope(
        execution,
        (HttpAttemptTimings(elapsed=unavailable),),
        (),
    )


def test_exchange_elapsed_envelope_rejects_impossible_phase_spans() -> None:
    observed = exchange(
        exchange_number=50,
        effective_request=request(),
        observed_response=response(),
    )
    bounded = msgspec.structs.replace(observed.timings, elapsed=measured(1))
    validate_exchange_timing_truth(
        observed.request,
        observed.response,
        observed.failure,
        bounded,
    )
    impossible = msgspec.structs.replace(
        observed.timings,
        dns=measured(3_600_000_000_000),
        elapsed=measured(1),
    )
    with pytest.raises(ValueError, match="dns timing exceeds exchange elapsed"):
        validate_exchange_timing_truth(
            observed.request,
            observed.response,
            observed.failure,
            impossible,
        )
    with pytest.raises(ValueError, match="dns timing exceeds exchange elapsed"):
        msgspec.structs.replace(observed, timings=impossible)


def test_pre_send_timing_elapsed_envelope_rejects_impossible_phase_spans() -> None:
    impossible = msgspec.structs.replace(
        exchange_timings(failure_phase=FailurePhase.DNS),
        dns=measured(2),
        elapsed=measured(1),
    )
    with pytest.raises(ValueError, match="dns timing exceeds exchange elapsed"):
        validate_pre_send_timing_truth(FailurePhase.DNS, impossible)


def test_entity_timing_reasons_match_captured_byte_extent() -> None:
    empty = NotPerformed(reason=NotPerformedReason.EMPTY_RESPONSE_BODY)
    no_body = NotPerformed(reason=NotPerformedReason.NO_RESPONSE_BODY)
    with pytest.raises(ValueError, match="present response entity"):
        _validate_entity_timing_metric(no_body, phase="body", byte_count=1)
    with pytest.raises(ValueError, match="empty non-empty"):
        _validate_entity_timing_metric(empty, phase="body", byte_count=1)
    with pytest.raises(ValueError, match="zero-byte"):
        _validate_entity_timing_metric(measured(), phase="body", byte_count=0)
    with pytest.raises(ValueError, match="must use empty-response-body"):
        _validate_entity_timing_metric(
            NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED),
            phase="body",
            byte_count=0,
        )


def test_pre_send_timing_validation_rejects_post_resolution_phases() -> None:
    with pytest.raises(ValueError, match="requires DNS or policy"):
        validate_pre_send_timing_truth(FailurePhase.CONNECT, exchange_timings())


def test_unknown_proxy_connection_tls_truth_follows_both_route_layers() -> None:
    secure_target = request()
    proxied_secure, secure_route, _ = proxy_request_route_endpoint(secure_target)
    secure_timings = exchange_timings(proxy=True, failure_phase=FailurePhase.REQUEST_WRITE)
    with pytest.raises(ValueError, match="TLS timing cannot claim clear-text-connection"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=1)),
            request=authorized_request(
                proxied_secure,
                secure_route,
                request_occurrence=occurrence(exchange=1),
            ),
            response=None,
            failure=failure(FailurePhase.REQUEST_WRITE),
            redirect=None,
            timings=msgspec.structs.replace(
                secure_timings,
                tls=NotPerformed(reason=NotPerformedReason.CLEAR_TEXT_CONNECTION),
            ),
        )

    clear_target = request(scheme=HttpScheme.HTTP)
    tls_proxy_request, tls_proxy_route, _ = proxy_request_route_endpoint(
        clear_target,
        proxy_scheme=HttpScheme.HTTPS,
    )
    with pytest.raises(ValueError, match="TLS timing cannot claim clear-text-connection"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=2)),
            request=authorized_request(
                tls_proxy_request,
                tls_proxy_route,
                request_occurrence=occurrence(exchange=2),
            ),
            response=None,
            failure=failure(FailurePhase.REQUEST_WRITE),
            redirect=None,
            timings=msgspec.structs.replace(
                secure_timings,
                tls=NotPerformed(reason=NotPerformedReason.CLEAR_TEXT_CONNECTION),
            ),
        )

    clear_proxy_request, clear_route, _ = proxy_request_route_endpoint(clear_target)
    clear_timings = exchange_timings(
        proxy=True,
        clear_text=True,
        failure_phase=FailurePhase.REQUEST_WRITE,
    )
    assert (
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=3)),
            request=authorized_request(
                clear_proxy_request,
                clear_route,
                request_occurrence=occurrence(exchange=3),
            ),
            response=None,
            failure=failure(FailurePhase.REQUEST_WRITE),
            redirect=None,
            timings=clear_timings,
        ).failure
        is not None
    )
    with pytest.raises(ValueError, match="entirely clear-text route"):
        HttpExchange(
            exchange_id=ExchangeId(value=UUID(int=4)),
            request=authorized_request(
                clear_proxy_request,
                clear_route,
                request_occurrence=occurrence(exchange=4),
            ),
            response=None,
            failure=failure(FailurePhase.REQUEST_WRITE),
            redirect=None,
            timings=msgspec.structs.replace(clear_timings, tls=measured()),
        )


def test_reused_proxy_connection_requires_every_network_phase_to_claim_reuse() -> None:
    effective_request = request()
    proxied, route, proxy_endpoint = proxy_request_route_endpoint(effective_request)
    reused_endpoint = msgspec.structs.replace(proxy_endpoint, connection_reused=True)
    observed_response = msgspec.structs.replace(
        response(),
        endpoint=reused_endpoint,
        tls=TlsLayers(connect=TlsNotPerformed(), origin=tls()),
    )
    timings = exchange_timings(proxy=True, reused=True)
    valid = HttpExchange(
        exchange_id=ExchangeId(value=UUID(int=5)),
        request=authorized_request(
            proxied,
            route,
            request_occurrence=occurrence(exchange=5),
        ),
        response=observed_response,
        failure=None,
        redirect=None,
        timings=timings,
    )
    assert valid.response is observed_response
    with pytest.raises(ValueError, match="proxy tunnel timing must be not performed"):
        msgspec.structs.replace(
            valid,
            timings=msgspec.structs.replace(
                timings,
                proxy_tunnel=exchange_timings(proxy=True).proxy_tunnel,
            ),
        )


def test_body_timing_omission_reason_matches_semantic_body_presence() -> None:
    no_body = exchange(
        exchange_number=6,
        effective_request=request(),
        observed_response=response(
            status=204,
            body=NoResponseBody(reason=NoBodyReason.STATUS_204),
        ),
    )
    with pytest.raises(ValueError, match="no-response-body reason"):
        msgspec.structs.replace(
            no_body,
            timings=msgspec.structs.replace(
                no_body.timings,
                first_body_byte=NotPerformed(reason=NotPerformedReason.EMPTY_RESPONSE_BODY),
            ),
        )

    entity = exchange(
        exchange_number=7,
        effective_request=request(),
        observed_response=response(),
    )
    with pytest.raises(ValueError, match="cannot omit a present response entity"):
        msgspec.structs.replace(
            entity,
            timings=msgspec.structs.replace(
                entity.timings,
                body_read=NotPerformed(reason=NotPerformedReason.NO_RESPONSE_BODY),
            ),
        )
