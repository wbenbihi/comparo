"""Explicit occurrence scopes for retry and redirect aware selection."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId


@final
class TerminalOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """Select the terminal attempt or its terminal exchange.

    For exchange-owned sources, an execution whose terminal attempt stopped before
    creating an exchange produces ``NO_TERMINAL_EXCHANGE`` rather than falling back
    to an earlier attempt.
    """


@final
class AttemptOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """Select one exact attempt or its terminal exchange."""

    attempt_id: AttemptId

    def __post_init__(self) -> None:
        """Require an exact runtime attempt identity."""
        if type(self.attempt_id) is not AttemptId:
            raise TypeError("attempt occurrence selector requires an AttemptId")


@final
class ExchangeOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """Select one exact exchange by runtime identity."""

    exchange_id: ExchangeId

    def __post_init__(self) -> None:
        """Require an exact runtime exchange identity."""
        if type(self.exchange_id) is not ExchangeId:
            raise TypeError("exchange occurrence selector requires an ExchangeId")


@final
class AllAttemptsOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """Select every attempt in execution order for the attempt source."""


@final
class AllExchangesOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """Select every exchange in attempt and redirect order."""


type ObservationOccurrenceSelector = (
    TerminalOccurrence
    | AttemptOccurrence
    | ExchangeOccurrence
    | AllAttemptsOccurrence
    | AllExchangesOccurrence
)


def is_observation_occurrence_selector(
    value: object,
) -> TypeGuard[ObservationOccurrenceSelector]:
    """Narrow an arbitrary value to the closed occurrence selector union."""
    return type(value) in {
        TerminalOccurrence,
        AttemptOccurrence,
        ExchangeOccurrence,
        AllAttemptsOccurrence,
        AllExchangesOccurrence,
    }
