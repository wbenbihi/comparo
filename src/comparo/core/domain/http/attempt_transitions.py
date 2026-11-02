"""Typed HTTP attempt transitions independent from ordinary retry policy."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId


@final
class StreamReconnectTransition(msgspec.Struct, frozen=True, kw_only=True):
    """A claimed clean reconnect whose truth is proven by StreamObservation ownership."""

    previous_attempt_id: AttemptId
    previous_exchange_id: ExchangeId
    previous_stream_view_id: ViewId

    def __post_init__(self) -> None:
        """Require typed identities for the preceding physical connection."""
        if type(self.previous_attempt_id) is not AttemptId:
            raise TypeError("stream reconnect predecessor must use AttemptId")
        if type(self.previous_exchange_id) is not ExchangeId:
            raise TypeError("stream reconnect predecessor must use ExchangeId")
        if type(self.previous_stream_view_id) is not ViewId:
            raise TypeError("stream reconnect predecessor must use a streaming ViewId")
