"""Tests for portable timing facts and storage-backed decoded-view outcomes."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.http.timings import TimingSource
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.primitives import Duration
from tests.core.domain.http.observation_factories import measured
from tests.core.domain.http.observation_factories import (
    uniform_exchange_timings as exchange_timings,
)


def test_timing_metrics_never_conflate_unperformed_unavailable_and_zero() -> None:
    zero = measured(0)
    reused = NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED)
    unavailable = TimingUnavailable(reason=TimingUnavailableReason.MILESTONE_MISSING)

    assert zero.duration.nanoseconds == 0
    assert reused.reason is NotPerformedReason.CONNECTION_REUSED
    assert unavailable.reason is TimingUnavailableReason.MILESTONE_MISSING
    assert len({type(zero), type(reused), type(unavailable)}) == 3


def test_timing_sources_and_components_validate_direct_construction() -> None:
    with pytest.raises(ValueError, match="namespace/name"):
        TimingSource(value="clock")
    with pytest.raises(TypeError, match="precision"):
        Measured(
            duration=Duration(nanoseconds=1),
            precision=cast("Duration", 1),
            source=TimingSource(value="comparo/clock@1"),
        )
    with pytest.raises(TypeError, match="measured duration"):
        Measured(
            duration=cast("Duration", 1),
            precision=Duration(nanoseconds=1),
            source=TimingSource(value="comparo/clock@1"),
        )
    with pytest.raises(TypeError, match="timing source"):
        Measured(
            duration=Duration(nanoseconds=1),
            precision=Duration(nanoseconds=1),
            source=cast("TimingSource", "clock"),
        )
    with pytest.raises(TypeError, match="not-performed"):
        NotPerformed(reason=cast("NotPerformedReason", "reused"))
    with pytest.raises(TypeError, match="unavailable timing"):
        TimingUnavailable(reason=cast("TimingUnavailableReason", "missing"))
    with pytest.raises(TypeError, match="TimingMetric"):
        exchange_timings(cast("TimingMetric", "implicit"))


def test_exchange_execution_and_stream_timings_are_independent() -> None:
    metric = measured()
    exchange = exchange_timings(metric)
    execution = EnvironmentExecutionTimings(queue=metric, elapsed=metric)
    stream = StreamTimings(time_to_first_record=metric, observation_window_elapsed=metric)
    assert exchange.dns is metric
    assert execution.queue is metric
    assert stream.time_to_first_record is metric

    with pytest.raises(TypeError, match="execution queue"):
        EnvironmentExecutionTimings(queue=cast("TimingMetric", 0), elapsed=metric)
    with pytest.raises(TypeError, match="stream timing"):
        StreamTimings(
            time_to_first_record=metric, observation_window_elapsed=cast("TimingMetric", 0)
        )


def test_not_performed_reasons_are_scoped_to_their_exact_timing_phase() -> None:
    metric = measured()
    no_response_body = NotPerformed(reason=NotPerformedReason.NO_RESPONSE_BODY)
    valid = HttpExchangeTimings(
        connection_acquisition=metric,
        dns=NotPerformed(reason=NotPerformedReason.DNS_CACHE_HIT),
        connect=NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED),
        proxy_tunnel=NotPerformed(reason=NotPerformedReason.NO_PROXY),
        tls=NotPerformed(reason=NotPerformedReason.CLEAR_TEXT_CONNECTION),
        request_write=metric,
        response_headers=metric,
        server_wait=metric,
        first_body_byte=no_response_body,
        body_read=no_response_body,
        elapsed=metric,
    )
    assert valid.request_write is metric

    with pytest.raises(ValueError, match="not valid for DNS"):
        HttpExchangeTimings(
            connection_acquisition=metric,
            dns=NotPerformed(reason=NotPerformedReason.NO_PROXY),
            connect=metric,
            proxy_tunnel=metric,
            tls=metric,
            request_write=metric,
            response_headers=metric,
            server_wait=metric,
            first_body_byte=metric,
            body_read=metric,
            elapsed=metric,
        )
    with pytest.raises(ValueError, match="attempt elapsed"):
        HttpAttemptTimings(elapsed=NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED))
    with pytest.raises(ValueError, match="always applicable"):
        EnvironmentExecutionTimings(queue=no_response_body, elapsed=metric)
    with pytest.raises(ValueError, match="not valid for request write"):
        msgspec.structs.replace(valid, request_write=no_response_body)

    empty_stream = NotPerformed(reason=NotPerformedReason.EMPTY_STREAM)
    assert (
        StreamTimings(
            time_to_first_record=empty_stream,
            observation_window_elapsed=metric,
        ).time_to_first_record
        is empty_stream
    )
    with pytest.raises(ValueError, match="only an empty stream"):
        StreamTimings(
            time_to_first_record=NotPerformed(reason=NotPerformedReason.NO_RESPONSE_BODY),
            observation_window_elapsed=metric,
        )
    with pytest.raises(ValueError, match="always applicable"):
        StreamTimings(
            time_to_first_record=empty_stream,
            observation_window_elapsed=empty_stream,
        )
