"""Cycle-free projection of ordered HTTP attempts into one exact logical stream path."""

from __future__ import annotations

from typing import Protocol
from typing import cast

from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.stream_attempts import StreamConnectionFailureOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentOccurrence
from comparo.core.domain.http.stream_attempts import StreamNonSegmentResponseOccurrence
from comparo.core.domain.http.stream_attempts import StreamPreSendOccurrence
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId


class StreamKindLike(Protocol):
    """Structural kind boundary that avoids importing the stream aggregate module."""

    @property
    def value(self) -> str:
        """Return the stable stream-kind value."""
        ...


class StreamEndReasonLike(Protocol):
    """Structural segment-end boundary that avoids importing the stream aggregate module."""

    @property
    def value(self) -> str:
        """Return the stable segment-end value."""
        ...


class StreamRecordsLike(Protocol):
    """Minimal record-index ownership facts needed for attempt-path validation."""

    @property
    def owner(self) -> EnvironmentExecutionObservation:
        """Return the exact owning execution."""
        ...

    @property
    def kind(self) -> StreamKindLike:
        """Return the stored stream framing kind."""
        ...


class ConnectionSegmentLike(Protocol):
    """Minimal captured connection facts needed for execution binding."""

    @property
    def attempt_id(self) -> AttemptId:
        """Return the physical connection attempt ID."""
        ...

    @property
    def exchange_id(self) -> ExchangeId:
        """Return the terminal streaming exchange ID."""
        ...

    @property
    def view(self) -> DecodedViewHandle:
        """Return the exact per-exchange decoded view."""
        ...

    @property
    def end_reason(self) -> StreamEndReasonLike:
        """Return why this captured physical connection ended."""
        ...


def validate_stream_owner(
    records: StreamRecordsLike,
    segments: tuple[ConnectionSegmentLike, ...],
    occurrences: tuple[StreamNonSegmentOccurrence, ...],
) -> tuple[HttpExchange, StreamNonSegmentOccurrence | None]:
    """Project every owner attempt to a stream segment or exact non-segment outcome."""
    if type(occurrences) is not tuple or any(
        type(occurrence)
        not in {
            StreamConnectionFailureOccurrence,
            StreamNonSegmentResponseOccurrence,
            StreamPreSendOccurrence,
        }
        for occurrence in occurrences
    ):
        raise TypeError("non-segment stream attempts must be an immutable typed tuple")
    attempts = records.owner.attempts
    attempts_by_id = {attempt.attempt_id: attempt for attempt in attempts}
    exchanges = {
        (attempt.attempt_id, exchange.exchange_id): exchange
        for attempt in attempts
        for exchange in attempt.exchanges
    }
    views = {view.view_id: view for view in records.owner.views}
    segments_by_attempt = {segment.attempt_id: segment for segment in segments}
    occurrences_by_attempt = {occurrence.attempt_id: occurrence for occurrence in occurrences}
    _validate_unique_lineage(segments)
    _validate_occurrence_identities(occurrences, attempts_by_id)
    if set(segments_by_attempt) & set(occurrences_by_attempt):
        raise ValueError("one stream attempt cannot be both captured and non-segment")

    ordered_segment_ids: list[AttemptId] = []
    ordered_occurrence_ids: list[AttemptId] = []
    expected_view_kind = ViewKind(records.kind.value)
    for index, attempt in enumerate(attempts):
        segment = segments_by_attempt.get(attempt.attempt_id)
        occurrence = occurrences_by_attempt.get(attempt.attempt_id)
        if segment is not None:
            exchange = _resolve_segment_exchange(attempts_by_id, exchanges, segment)
            _validate_terminal_stream_exchange(attempt, exchange)
            _validate_capture_binding(segment, exchange, views, expected_view_kind)
            ordered_segment_ids.append(attempt.attempt_id)
            if index + 1 < len(attempts):
                _validate_outgoing_segment_end(segment, attempt, attempts[index + 1])
        elif occurrence is not None:
            _validate_non_segment_occurrence(
                attempt,
                occurrence,
                views,
                expected_view_kind,
            )
            ordered_occurrence_ids.append(attempt.attempt_id)
            if index + 1 < len(attempts):
                _validate_non_segment_outgoing(attempts[index + 1])
        else:
            raise ValueError(
                "every stream owner attempt must be an exact segment or non-segment outcome"
            )

    _validate_projected_order(
        tuple(ordered_segment_ids),
        segments,
        tuple(ordered_occurrence_ids),
        occurrences,
    )
    terminal_exchange = next(
        attempt.exchanges[-1] for attempt in reversed(attempts) if attempt.exchanges
    )
    return (
        terminal_exchange,
        occurrences_by_attempt.get(attempts[-1].attempt_id),
    )


def _validate_projected_order(
    ordered_segment_ids: tuple[AttemptId, ...],
    segments: tuple[ConnectionSegmentLike, ...],
    ordered_occurrence_ids: tuple[AttemptId, ...],
    occurrences: tuple[StreamNonSegmentOccurrence, ...],
) -> None:
    if ordered_segment_ids != tuple(segment.attempt_id for segment in segments):
        raise ValueError("stream segments must follow the owner's ordered attempt path")
    if ordered_occurrence_ids != tuple(occurrence.attempt_id for occurrence in occurrences):
        raise ValueError("non-segment attempts must follow the owner's ordered attempt path")


def _validate_unique_lineage(segments: tuple[ConnectionSegmentLike, ...]) -> None:
    if len({segment.view.view_id for segment in segments}) != len(segments):
        raise ValueError("each stream segment must use a unique decoded view")
    if len({segment.view.body_id for segment in segments}) != len(segments):
        raise ValueError("each stream segment must use a unique response body")


def _validate_occurrence_identities(
    occurrences: tuple[StreamNonSegmentOccurrence, ...],
    attempts: dict[AttemptId, HttpAttempt],
) -> None:
    if len({occurrence.attempt_id for occurrence in occurrences}) != len(occurrences):
        raise ValueError("non-segment stream attempt IDs must be unique")
    if len({occurrence.exchange_id for occurrence in occurrences}) != len(occurrences):
        raise ValueError("non-segment stream exchange IDs must be unique")
    if any(occurrence.attempt_id not in attempts for occurrence in occurrences):
        raise ValueError("non-segment attempt does not belong to its exact execution owner")


def _resolve_segment_exchange(
    attempts: dict[AttemptId, HttpAttempt],
    exchanges: dict[tuple[AttemptId, ExchangeId], HttpExchange],
    segment: ConnectionSegmentLike,
) -> HttpExchange:
    if attempts.get(segment.attempt_id) is None:
        raise ValueError("stream segment does not belong to its exact execution owner")
    exchange = exchanges.get((segment.attempt_id, segment.exchange_id))
    if exchange is None:
        raise ValueError("stream segment does not belong to its exact execution owner")
    return exchange


def _validate_terminal_stream_exchange(attempt: HttpAttempt, exchange: HttpExchange) -> None:
    if exchange.redirect is not None or exchange.exchange_id != attempt.exchanges[-1].exchange_id:
        raise ValueError("stream segment must bind the terminal exchange of its connection attempt")


def _validate_outgoing_segment_end(
    segment: ConnectionSegmentLike,
    attempt: HttpAttempt,
    following: HttpAttempt,
) -> None:
    if following.stream_reconnect is not None:
        if following.stream_reconnect.previous_stream_view_id != segment.view.view_id:
            raise ValueError("stream reconnect must bind the predecessor segment's exact view")
        if segment.end_reason.value != "reconnect":
            raise ValueError("every segment followed by reconnect must end for reconnection")
        return
    terminal = attempt.exchanges[-1]
    expected = "failure" if terminal.failure is not None else "remote-eof"
    if segment.end_reason.value != expected:
        raise ValueError("stream segment end reason must match its outgoing retry outcome")


def _validate_non_segment_outgoing(following: HttpAttempt) -> None:
    if following.stream_reconnect is not None:
        raise ValueError("a stream reconnect predecessor must be a captured stream segment")


def _validate_non_segment_occurrence(
    attempt: HttpAttempt,
    occurrence: StreamNonSegmentOccurrence,
    views: dict[ViewId, DecodedViewHandle],
    expected_view_kind: ViewKind,
) -> None:
    if _validate_pre_send_occurrence(attempt, occurrence):
        return
    if not attempt.exchanges:
        raise ValueError("exchange-backed stream occurrence requires a terminal exchange")
    terminal = attempt.exchanges[-1]
    if occurrence.exchange_id != terminal.exchange_id:
        raise ValueError("non-segment attempt must bind its terminal exchange")
    if type(occurrence) is StreamConnectionFailureOccurrence:
        if terminal.response is not None or terminal.failure is None:
            raise ValueError("a stream failure occurrence must have no HTTP response")
        if occurrence.failure != terminal.failure:
            raise ValueError("stream failure occurrence must retain the exact transport failure")
        return
    response_occurrence = cast(StreamNonSegmentResponseOccurrence, occurrence)
    if (
        terminal.response != response_occurrence.response
        or terminal.failure != response_occurrence.failure
    ):
        raise ValueError("non-stream response occurrence must retain the exact terminal outcome")
    response = response_occurrence.response
    if type(response.body) is ResponseEntityBody and response.body.handle is not None:
        matching_stream_view = any(
            view.source == response.body.handle and view.decoder.view is expected_view_kind
            for view in views.values()
        )
        if matching_stream_view:
            raise ValueError("a captured stream response must be represented by a segment")


def _validate_pre_send_occurrence(
    attempt: HttpAttempt,
    occurrence: StreamNonSegmentOccurrence,
) -> bool:
    """Validate a pre-send occurrence and report whether it handled the union member."""
    if type(occurrence) is not StreamPreSendOccurrence:
        return False
    if attempt.pre_send != occurrence.outcome or attempt.exchanges:
        raise ValueError("stream pre-send occurrence must retain the exact attempt outcome")
    return True


def _validate_capture_binding(
    segment: ConnectionSegmentLike,
    exchange: HttpExchange,
    views: dict[ViewId, DecodedViewHandle],
    expected_view_kind: ViewKind,
) -> None:
    response = exchange.response
    if (
        response is None
        or type(response.body) is not ResponseEntityBody
        or response.body.handle is None
    ):
        raise ValueError("stream segment exchange must own a captured response body")
    if views.get(segment.view.view_id) != segment.view:
        raise ValueError("stream segment view must be an exact view of its execution owner")
    if segment.view.source != response.body.handle:
        raise ValueError("stream segment view must source its exact exchange response body")
    if segment.view.decoder.view is not expected_view_kind:
        raise ValueError("stream kind must agree with every segment decoded view")
