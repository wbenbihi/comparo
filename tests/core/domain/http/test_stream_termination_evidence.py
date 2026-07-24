"""Closed-constructor tests for exact stream termination evidence."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from comparo.core.domain.expressions import SymbolPath
from comparo.core.domain.http.stream_attempts import StreamConnectionFailureOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.stream_termination import ConditionWindowEvidence
from comparo.core.domain.http.stream_termination import DurationWindowEvidence
from comparo.core.domain.http.stream_termination import EventCountWindowEvidence
from comparo.core.domain.http.stream_termination import IdleWindowEvidence
from comparo.core.domain.http.stream_termination import SafetyByteLimitEvidence
from comparo.core.domain.http.stream_termination import SafetyCountLimitEvidence
from comparo.core.domain.http.stream_termination import SafetyDurationLimitEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationReason
from comparo.core.domain.http.stream_termination import non_segment_termination
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_failure


def test_evidence_limits_require_their_exact_value_types() -> None:
    """Reject structurally similar values at every evidence boundary."""
    with pytest.raises(TypeError, match="PositiveCount"):
        EventCountWindowEvidence(limit=cast("PositiveCount", Count(value=1)))
    with pytest.raises(TypeError, match="Duration"):
        DurationWindowEvidence(limit=cast("Duration", ByteCount(value=1)))
    with pytest.raises(TypeError, match="SymbolPath"):
        ConditionWindowEvidence(condition=cast("SymbolPath", "stop"))
    with pytest.raises(TypeError, match="PositiveCount"):
        SafetyCountLimitEvidence(limit=cast("PositiveCount", Count(value=1)))
    with pytest.raises(TypeError, match="Duration"):
        SafetyDurationLimitEvidence(limit=cast("Duration", ByteCount(value=1)))
    with pytest.raises(TypeError, match="ByteCount"):
        SafetyByteLimitEvidence(limit=cast("ByteCount", Count(value=1)))


def test_idle_window_evidence_requires_typed_reached_durations() -> None:
    with pytest.raises(TypeError, match="typed durations"):
        IdleWindowEvidence(
            timeout=cast("Duration", ByteCount(value=1)),
            observed_idle=Duration(nanoseconds=1),
        )
    with pytest.raises(TypeError, match="typed durations"):
        IdleWindowEvidence(
            timeout=Duration(nanoseconds=1),
            observed_idle=cast("Duration", ByteCount(value=1)),
        )
    with pytest.raises(ValueError, match="reach its authored timeout"):
        IdleWindowEvidence(
            timeout=Duration(nanoseconds=2),
            observed_idle=Duration(nanoseconds=1),
        )


def test_non_segment_policy_and_dns_failures_keep_distinct_termination_reasons() -> None:
    policy = StreamConnectionFailureOccurrence(
        attempt_id=AttemptId(value=UUID(int=1)),
        exchange_id=ExchangeId(value=UUID(int=1)),
        failure=failure(FailurePhase.POLICY),
    )
    dns = resolution_failure(
        request(),
        request_occurrence=occurrence(attempt=2, exchange=2),
    )
    dns_occurrence = StreamPreSendOccurrence(
        attempt_id=dns.occurrence.attempt_id,
        outcome=dns,
    )

    assert non_segment_termination(policy) is StreamTerminationReason.POLICY_BLOCKED
    assert non_segment_termination(dns_occurrence) is StreamTerminationReason.RECONNECT_EXHAUSTED
