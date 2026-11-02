"""Exact logical-stream termination taxonomy and non-segment outcome classification."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expressions import SymbolPath
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpProxyDnsApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.stream_attempts import StreamConnectionFailureOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentResponseOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount


class StreamTerminationReason(DomainStrEnum):
    """Exact remote, authored-window, safety, operational, and manual stop reasons."""

    REMOTE_EOF = "remote-eof"
    EVENT_COUNT_WINDOW = "event-count-window"
    DURATION_WINDOW = "duration-window"
    IDLE_WINDOW = "idle-window"
    CONDITION_WINDOW = "condition-window"
    ACCEPTED_MANUAL_WINDOW = "accepted-manual-window"
    SAFETY_COUNT_LIMIT = "safety-count-limit"
    SAFETY_DURATION_LIMIT = "safety-duration-limit"
    SAFETY_BYTE_LIMIT = "safety-byte-limit"
    RESOURCE_BUDGET = "resource-budget"
    TRANSPORT_FAILURE = "transport-failure"
    DECODE_FAILURE = "decode-failure"
    STORAGE_FAILURE = "storage-failure"
    RECONNECT_EXHAUSTED = "reconnect-exhausted"
    MANUAL_STOP = "manual-stop"
    CANCELLED = "cancelled"
    POLICY_BLOCKED = "policy-blocked"


WINDOW_TERMINATIONS = frozenset(
    {
        StreamTerminationReason.EVENT_COUNT_WINDOW,
        StreamTerminationReason.DURATION_WINDOW,
        StreamTerminationReason.IDLE_WINDOW,
        StreamTerminationReason.CONDITION_WINDOW,
        StreamTerminationReason.ACCEPTED_MANUAL_WINDOW,
    }
)


@final
class EventCountWindowEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An authored event-count window closed at its exact record limit."""

    limit: PositiveCount

    def __post_init__(self) -> None:
        """Require a positive authored record limit."""
        if type(self.limit) is not PositiveCount:
            raise TypeError("event-count window evidence requires PositiveCount")


@final
class DurationWindowEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An authored observation-duration window reached its exact limit."""

    limit: Duration

    def __post_init__(self) -> None:
        """Require a typed authored duration limit."""
        if type(self.limit) is not Duration:
            raise TypeError("duration window evidence requires Duration")


@final
class IdleWindowEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An authored idle window closed after an observed idle interval."""

    timeout: Duration
    observed_idle: Duration

    def __post_init__(self) -> None:
        """Require typed idle threshold and observation facts."""
        if type(self.timeout) is not Duration or type(self.observed_idle) is not Duration:
            raise TypeError("idle window evidence requires typed durations")
        if self.observed_idle.nanoseconds < self.timeout.nanoseconds:
            raise ValueError("observed stream idle duration must reach its authored timeout")


@final
class ConditionWindowEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An authored condition expression matched and closed the window."""

    condition: SymbolPath

    def __post_init__(self) -> None:
        """Require the exact authored condition identity."""
        if type(self.condition) is not SymbolPath:
            raise TypeError("condition window evidence requires SymbolPath")


@final
class AcceptedManualWindowEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """A manual stop was explicitly accepted as a successful authored window."""

    def __post_init__(self) -> None:
        """Retain a closed typed acceptance variant."""


@final
class SafetyCountLimitEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An external safety count cap stopped stream capture."""

    limit: PositiveCount

    def __post_init__(self) -> None:
        """Require a positive safety count cap."""
        if type(self.limit) is not PositiveCount:
            raise TypeError("safety count evidence requires PositiveCount")


@final
class SafetyDurationLimitEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An external safety duration cap stopped stream capture."""

    limit: Duration

    def __post_init__(self) -> None:
        """Require a typed safety duration cap."""
        if type(self.limit) is not Duration:
            raise TypeError("safety duration evidence requires Duration")


@final
class SafetyByteLimitEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An external safety byte cap stopped stream capture."""

    limit: ByteCount

    def __post_init__(self) -> None:
        """Require a typed safety byte cap."""
        if type(self.limit) is not ByteCount:
            raise TypeError("stream safety byte evidence requires ByteCount")


@final
class FramingDecodeFailureEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Stored framing errors or a trailing partial item stopped stream decoding."""

    def __post_init__(self) -> None:
        """Retain a closed framing-failure variant."""


@final
class ManualStopEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """An unaccepted manual stop ended an incomplete stream."""

    def __post_init__(self) -> None:
        """Retain a closed manual-stop variant."""


type StreamTerminationEvidence = (
    EventCountWindowEvidence
    | DurationWindowEvidence
    | IdleWindowEvidence
    | ConditionWindowEvidence
    | AcceptedManualWindowEvidence
    | SafetyCountLimitEvidence
    | SafetyDurationLimitEvidence
    | SafetyByteLimitEvidence
    | FramingDecodeFailureEvidence
    | ManualStopEvidence
)


def is_stream_termination_evidence(value: object) -> bool:
    """Return whether a value is one exact authored or operational stop variant."""
    return type(value) in {
        EventCountWindowEvidence,
        DurationWindowEvidence,
        IdleWindowEvidence,
        ConditionWindowEvidence,
        AcceptedManualWindowEvidence,
        SafetyCountLimitEvidence,
        SafetyDurationLimitEvidence,
        SafetyByteLimitEvidence,
        FramingDecodeFailureEvidence,
        ManualStopEvidence,
    }


def non_segment_termination(
    occurrence: StreamNonSegmentOccurrence,
) -> StreamTerminationReason:
    """Classify one exact terminal outcome without collapsing response-bearing failures."""
    if type(occurrence) is StreamPreSendOccurrence:
        return _pre_send_termination(occurrence)
    failure = occurrence.failure
    if failure is None:
        response_occurrence = cast(StreamNonSegmentResponseOccurrence, occurrence)
        status = response_occurrence.response.status.value
        if 200 <= status < 300:
            return StreamTerminationReason.DECODE_FAILURE
        return StreamTerminationReason.RECONNECT_EXHAUSTED
    if failure.phase is FailurePhase.CANCELLATION:
        return StreamTerminationReason.CANCELLED
    if failure.phase is FailurePhase.POLICY:
        return StreamTerminationReason.POLICY_BLOCKED
    if failure.phase is FailurePhase.CONTENT_DECODING:
        return StreamTerminationReason.DECODE_FAILURE
    if failure.phase is FailurePhase.RESOURCE_BUDGET:
        return StreamTerminationReason.RESOURCE_BUDGET
    if failure.phase is FailurePhase.STORAGE:
        return StreamTerminationReason.STORAGE_FAILURE
    if type(occurrence) is StreamConnectionFailureOccurrence:
        return StreamTerminationReason.RECONNECT_EXHAUSTED
    return StreamTerminationReason.TRANSPORT_FAILURE


def _pre_send_termination(occurrence: StreamPreSendOccurrence) -> StreamTerminationReason:
    """Classify route-free resolution failure separately from denied send authority."""
    if type(occurrence.outcome) in {
        HttpResolutionDenial,
        HttpAuthorizationDenial,
        HttpMutationApprovalDenial,
        HttpProxyDnsApprovalDenial,
    }:
        return StreamTerminationReason.POLICY_BLOCKED
    return StreamTerminationReason.RECONNECT_EXHAUSTED
