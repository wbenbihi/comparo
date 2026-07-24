"""Terminal stream extent validation against exact exchange and typed stop evidence."""

from __future__ import annotations

from typing import Protocol

from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_attempts import StreamNonSegmentOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.stream_termination import AcceptedManualWindowEvidence
from comparo.core.domain.http.stream_termination import ConditionWindowEvidence
from comparo.core.domain.http.stream_termination import DurationWindowEvidence
from comparo.core.domain.http.stream_termination import EventCountWindowEvidence
from comparo.core.domain.http.stream_termination import FramingDecodeFailureEvidence
from comparo.core.domain.http.stream_termination import IdleWindowEvidence
from comparo.core.domain.http.stream_termination import SafetyByteLimitEvidence
from comparo.core.domain.http.stream_termination import SafetyCountLimitEvidence
from comparo.core.domain.http.stream_termination import SafetyDurationLimitEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationReason
from comparo.core.domain.http.stream_termination import non_segment_termination
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import StreamTimings
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count


class ValueLike(Protocol):
    """Stable string-valued enum boundary for cycle-free validation."""

    @property
    def value(self) -> str:
        """Return the stable enum value."""
        ...


class TerminalSegmentLike(Protocol):
    """Terminal physical segment facts used by extent validation."""

    @property
    def end_reason(self) -> ValueLike:
        """Return the physical segment end classification."""
        ...

    @property
    def view(self) -> DecodedViewHandle:
        """Return the exact decoded stream view."""
        ...

    @property
    def error_count(self) -> Count:
        """Return the terminal segment's framing-error count."""
        ...


class StreamExtentLike(Protocol):
    """Declared completeness and termination values to verify."""

    @property
    def completeness(self) -> ValueLike:
        """Return the stable completeness value."""
        ...

    @property
    def termination(self) -> StreamTerminationReason:
        """Return the declared termination reason."""
        ...


class StreamRecordsLike(Protocol):
    """Aggregate record/error counts relevant to typed stop evidence."""

    @property
    def record_count(self) -> Count:
        """Return the retained application-record count."""
        ...

    @property
    def error_count(self) -> Count:
        """Return the retained framing-error count."""
        ...


def validate_terminal_stream_extent(
    *,
    terminal_segment: TerminalSegmentLike,
    terminal_exchange: HttpExchange,
    terminal_occurrence: StreamNonSegmentOccurrence | None,
    extent: StreamExtentLike,
    evidence: StreamTerminationEvidence | None,
    records: StreamRecordsLike,
    captured_bytes: ByteCount,
    timings: StreamTimings,
    terminal_has_trailing: bool,
) -> None:
    """Bind terminal segment and logical extent to immutable observed truth."""
    if terminal_occurrence is not None:
        _validate_terminal_occurrence(
            terminal_segment=terminal_segment,
            terminal_exchange=terminal_exchange,
            terminal_occurrence=terminal_occurrence,
            extent=extent,
            evidence=evidence,
        )
        return
    if terminal_exchange.failure is not None:
        if terminal_exchange.failure.phase is FailurePhase.RESOURCE_BUDGET:
            _validate_resource_budget(terminal_exchange, evidence)
        elif evidence is not None:
            raise ValueError("terminal exchange failures cannot be replaced by stop evidence")
        termination, end_reason = _failure_extent(terminal_exchange.failure.phase)
        _require_extent(
            terminal_segment,
            extent,
            completeness="incomplete",
            termination=termination,
            end_reason=end_reason,
        )
        return
    if evidence is None:
        if terminal_segment.view.completeness is not ViewCompleteness.FULL:
            raise ValueError("a partial terminal stream requires exact typed stop evidence")
        _require_extent(
            terminal_segment,
            extent,
            completeness="full",
            termination=StreamTerminationReason.REMOTE_EOF,
            end_reason="remote-eof",
        )
        return
    completeness, termination, end_reason = _evidence_extent(
        evidence,
        terminal_segment,
        records,
        captured_bytes,
        timings,
        terminal_has_trailing,
    )
    _require_extent(
        terminal_segment,
        extent,
        completeness=completeness,
        termination=termination,
        end_reason=end_reason,
    )


def _validate_terminal_occurrence(
    *,
    terminal_segment: TerminalSegmentLike,
    terminal_exchange: HttpExchange,
    terminal_occurrence: StreamNonSegmentOccurrence,
    extent: StreamExtentLike,
    evidence: StreamTerminationEvidence | None,
) -> None:
    """Bind one typed non-segment outcome to terminal extent and admissible evidence."""
    if type(terminal_occurrence) is StreamPreSendOccurrence:
        if evidence is not None:
            raise ValueError("terminal pre-send outcomes cannot carry stop evidence")
    elif terminal_exchange.failure is not None and (
        terminal_exchange.failure.phase is FailurePhase.RESOURCE_BUDGET
    ):
        _validate_resource_budget(terminal_exchange, evidence)
    elif evidence is not None:
        raise ValueError("terminal non-segment outcomes cannot carry stop evidence")
    _require_extent(
        terminal_segment,
        extent,
        completeness="incomplete",
        termination=non_segment_termination(terminal_occurrence),
        end_reason=None,
    )


def _failure_extent(phase: FailurePhase) -> tuple[StreamTerminationReason, str]:
    if phase is FailurePhase.CANCELLATION:
        return StreamTerminationReason.CANCELLED, "cancelled"
    if phase is FailurePhase.CONTENT_DECODING:
        return StreamTerminationReason.DECODE_FAILURE, "failure"
    if phase is FailurePhase.RESOURCE_BUDGET:
        return StreamTerminationReason.RESOURCE_BUDGET, "failure"
    if phase is FailurePhase.STORAGE:
        return StreamTerminationReason.STORAGE_FAILURE, "failure"
    if phase is FailurePhase.POLICY:
        return StreamTerminationReason.POLICY_BLOCKED, "failure"
    return StreamTerminationReason.TRANSPORT_FAILURE, "failure"


def _evidence_extent(  # noqa: C901 - exhaustive closed evidence dispatch is clearest inline
    evidence: StreamTerminationEvidence,
    terminal_segment: TerminalSegmentLike,
    records: StreamRecordsLike,
    captured_bytes: ByteCount,
    timings: StreamTimings,
    terminal_has_trailing: bool,
) -> tuple[str, StreamTerminationReason, str]:
    if type(evidence) is EventCountWindowEvidence:
        if records.record_count.value != evidence.limit.value:
            raise ValueError("event-count window must close at its exact authored limit")
        return "window", StreamTerminationReason.EVENT_COUNT_WINDOW, "window-closed"
    if type(evidence) is DurationWindowEvidence:
        _require_elapsed_limit(timings, evidence.limit.nanoseconds, "duration window")
        return "window", StreamTerminationReason.DURATION_WINDOW, "window-closed"
    if type(evidence) is IdleWindowEvidence:
        _require_elapsed_limit(
            timings,
            evidence.observed_idle.nanoseconds,
            "observed idle",
        )
        return "window", StreamTerminationReason.IDLE_WINDOW, "window-closed"
    if type(evidence) is ConditionWindowEvidence:
        return "window", StreamTerminationReason.CONDITION_WINDOW, "window-closed"
    if type(evidence) is AcceptedManualWindowEvidence:
        return "window", StreamTerminationReason.ACCEPTED_MANUAL_WINDOW, "window-closed"
    if type(evidence) is SafetyCountLimitEvidence:
        if records.record_count.value != evidence.limit.value:
            raise ValueError("stream record count must equal its safety limit")
        return "incomplete", StreamTerminationReason.SAFETY_COUNT_LIMIT, "failure"
    if type(evidence) is SafetyDurationLimitEvidence:
        _require_elapsed_limit(timings, evidence.limit.nanoseconds, "safety duration")
        return "incomplete", StreamTerminationReason.SAFETY_DURATION_LIMIT, "failure"
    if type(evidence) is SafetyByteLimitEvidence:
        if captured_bytes.value != evidence.limit.value:
            raise ValueError("captured stream bytes must equal their safety limit")
        return "incomplete", StreamTerminationReason.SAFETY_BYTE_LIMIT, "failure"
    if type(evidence) is FramingDecodeFailureEvidence:
        if terminal_segment.error_count.value == 0 and not terminal_has_trailing:
            raise ValueError("framing decode failure requires an error or trailing partial item")
        return "incomplete", StreamTerminationReason.DECODE_FAILURE, "failure"
    return "incomplete", StreamTerminationReason.MANUAL_STOP, "cancelled"


def _validate_resource_budget(
    exchange: HttpExchange,
    evidence: StreamTerminationEvidence | None,
) -> None:
    if evidence is not None:
        raise ValueError("canonical capture exhaustion must not be duplicated as stop evidence")
    response = exchange.response
    if (
        response is None
        or type(response.body) is not ResponseEntityBody
        or type(response.body.capture) is not BudgetExceededCapture
        or response.body.handle is None
    ):
        raise ValueError("resource-budget failure requires exact canonical capture exhaustion")


def _require_elapsed_limit(timings: StreamTimings, limit: int, label: str) -> None:
    elapsed = timings.observation_window_elapsed
    if type(elapsed) is not Measured or elapsed.duration.nanoseconds < limit:
        raise ValueError(f"measured stream elapsed must reach its {label} limit")


def _require_extent(
    segment: TerminalSegmentLike,
    extent: StreamExtentLike,
    *,
    completeness: str,
    termination: StreamTerminationReason,
    end_reason: str | None,
) -> None:
    if extent.completeness.value != completeness or extent.termination is not termination:
        raise ValueError("terminal stream truth must determine its exact logical extent")
    if end_reason is not None and segment.end_reason.value != end_reason:
        raise ValueError("terminal stream truth must determine its exact segment end reason")
