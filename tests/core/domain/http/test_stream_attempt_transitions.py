"""Tests for clean stream reconnect transitions outside ordinary HTTP retry policy."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.attempt_transitions import StreamReconnectTransition
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import Duration
from tests.core.domain.http.stream_factories import stream_execution


def test_stream_reconnect_transition_requires_exact_typed_predecessor_ids() -> None:
    with pytest.raises(TypeError, match="AttemptId"):
        StreamReconnectTransition(
            previous_attempt_id=cast("AttemptId", "attempt"),
            previous_exchange_id=ExchangeId(value=UUID(int=1)),
            previous_stream_view_id=ViewId(value=UUID(int=1)),
        )
    with pytest.raises(TypeError, match="ExchangeId"):
        StreamReconnectTransition(
            previous_attempt_id=AttemptId(value=UUID(int=1)),
            previous_exchange_id=cast("ExchangeId", "exchange"),
            previous_stream_view_id=ViewId(value=UUID(int=1)),
        )
    with pytest.raises(TypeError, match="ViewId"):
        StreamReconnectTransition(
            previous_attempt_id=AttemptId(value=UUID(int=1)),
            previous_exchange_id=ExchangeId(value=UUID(int=1)),
            previous_stream_view_id=cast("ViewId", "view"),
        )


def test_stream_reconnect_is_neither_untyped_nor_an_ordinary_retry() -> None:
    owner = stream_execution(ViewKind.SSE)
    reconnect = owner.attempts[1]
    with pytest.raises(TypeError, match="StreamReconnectTransition"):
        msgspec.structs.replace(
            reconnect,
            stream_reconnect=cast("StreamReconnectTransition", "reconnect"),
        )
    with pytest.raises(ValueError, match="both an HTTP retry and a stream reconnect"):
        msgspec.structs.replace(
            reconnect,
            retry=RetryMetadata(
                trigger=RetryTrigger.CONNECT_FAILURE,
                backoff=Duration(nanoseconds=0),
                jitter=Duration(nanoseconds=0),
            ),
        )


def test_execution_rejects_missing_replayed_or_initial_reconnect_transitions() -> None:
    owner = stream_execution(ViewKind.SSE)
    first, reconnect = owner.attempts
    assert reconnect.stream_reconnect is not None

    wrong_predecessor = msgspec.structs.replace(
        reconnect.stream_reconnect,
        previous_attempt_id=AttemptId(value=UUID(int=99)),
    )
    with pytest.raises(ValueError, match="immediately preceding attempt"):
        msgspec.structs.replace(
            owner,
            attempts=(
                first,
                msgspec.structs.replace(
                    reconnect,
                    stream_reconnect=wrong_predecessor,
                ),
            ),
        )

    with pytest.raises(ValueError, match="only attempts after"):
        msgspec.structs.replace(
            owner,
            attempts=(
                msgspec.structs.replace(
                    first,
                    stream_reconnect=reconnect.stream_reconnect,
                ),
                reconnect,
            ),
        )
    with pytest.raises(ValueError, match="only attempts after"):
        msgspec.structs.replace(
            owner,
            attempts=(first, msgspec.structs.replace(reconnect, stream_reconnect=None)),
        )
