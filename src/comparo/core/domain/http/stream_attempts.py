"""Typed non-segment occurrences in one logical stream connection-attempt path."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import is_http_pre_send_outcome
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId


@final
class StreamConnectionFailureOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """One exact no-response connection attempt retained outside captured segments."""

    attempt_id: AttemptId
    exchange_id: ExchangeId
    failure: TransportFailure

    def __post_init__(self) -> None:
        """Require typed occurrence identities and the exact structured failure fact."""
        if type(self.attempt_id) is not AttemptId or type(self.exchange_id) is not ExchangeId:
            raise TypeError("stream failure occurrence requires typed HTTP occurrence IDs")
        if type(self.failure) is not TransportFailure:
            raise TypeError("stream failure occurrence requires TransportFailure")


@final
class StreamNonSegmentResponseOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """One exact terminal HTTP response that did not establish the requested stream."""

    attempt_id: AttemptId
    exchange_id: ExchangeId
    response: ResponseObservation
    failure: TransportFailure | None

    def __post_init__(self) -> None:
        """Require typed identities and an exact response-bearing terminal outcome."""
        if type(self.attempt_id) is not AttemptId or type(self.exchange_id) is not ExchangeId:
            raise TypeError("non-stream response occurrence requires typed HTTP occurrence IDs")
        if type(self.response) is not ResponseObservation:
            raise TypeError("non-stream response occurrence requires ResponseObservation")
        if self.failure is not None and type(self.failure) is not TransportFailure:
            raise TypeError("non-stream response failure must be TransportFailure when present")


@final
class StreamPreSendOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """One exact resolution failure or authorization denial after an earlier stream segment."""

    attempt_id: AttemptId
    outcome: HttpPreSendOutcome

    def __post_init__(self) -> None:
        """Bind an exact typed pre-send outcome to its owning attempt occurrence."""
        if type(self.attempt_id) is not AttemptId:
            raise TypeError("stream pre-send occurrence requires AttemptId")
        if not is_http_pre_send_outcome(self.outcome):
            raise TypeError("stream pre-send occurrence requires an exact pre-send outcome")
        if self.outcome.occurrence.attempt_id != self.attempt_id:
            raise ValueError("stream pre-send outcome does not own its attempt")

    @property
    def exchange_id(self) -> ExchangeId:
        """Return the planned outbound occurrence ID that stopped before exchange creation."""
        return self.outcome.occurrence.exchange_id


type StreamNonSegmentOccurrence = (
    StreamConnectionFailureOccurrence | StreamNonSegmentResponseOccurrence | StreamPreSendOccurrence
)
