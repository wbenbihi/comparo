"""Programmable, concurrency-observable fake HTTP transport for offline execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import final

import msgspec

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Count
from comparo.core.runtime.ports import HttpTransportResult


@final
class FakeExchangeAction(msgspec.Struct, frozen=True, kw_only=True):
    """One exact fake result with cooperative yields used to permute completion order."""

    result: HttpTransportResult
    yields: Count = Count(value=0)

    def __post_init__(self) -> None:
        """Require typed transport output and a finite non-negative yield count."""
        if type(self.result) is not HttpTransportResult:
            raise TypeError("fake exchange action requires HttpTransportResult")
        if type(self.yields) is not Count:
            raise TypeError("fake exchange yields must be a Count")


class ProgrammableFakeTransport:
    """Consume one scripted result per authorized exchange and expose concurrency evidence."""

    __slots__ = (
        "_actions",
        "_active",
        "_calls",
        "_closed",
        "_maximum_active",
    )

    def __init__(self, actions: Mapping[ExchangeId, FakeExchangeAction]) -> None:
        """Copy and validate a unique script without retaining a caller-owned mutable mapping."""
        if not isinstance(actions, Mapping):
            raise TypeError("fake transport actions must be a Mapping")
        if any(
            type(exchange_id) is not ExchangeId or type(action) is not FakeExchangeAction
            for exchange_id, action in actions.items()
        ):
            raise TypeError("fake transport script contains an unsupported entry")
        self._actions = dict(actions)
        self._active = 0
        self._calls: list[ExchangeId] = []
        self._closed = False
        self._maximum_active = 0

    @property
    def calls(self) -> tuple[ExchangeId, ...]:
        """Return exchange identities in actual transport-entry order."""
        return tuple(self._calls)

    @property
    def maximum_active(self) -> Count:
        """Return the observed simultaneous exchange high-water mark."""
        return Count(value=self._maximum_active)

    @property
    def active(self) -> Count:
        """Return the current active exchange count, useful for cleanup assertions."""
        return Count(value=self._active)

    @property
    def closed(self) -> bool:
        """Return whether the run released this transport."""
        return self._closed

    @property
    def remaining_exchange_ids(self) -> tuple[ExchangeId, ...]:
        """Return unconsumed scripted exchange identities in insertion order."""
        return tuple(self._actions)

    async def exchange(self, request: AuthorizedExchangeRequest) -> HttpTransportResult:
        """Return the exact scripted result while remaining cooperatively cancellable."""
        if type(request) is not AuthorizedExchangeRequest:
            raise TypeError("fake transport accepts only AuthorizedExchangeRequest")
        if self._closed:
            raise RuntimeError("fake transport is closed")
        exchange_id = request.occurrence.exchange_id
        action = self._actions.pop(exchange_id, None)
        if action is None:
            raise RuntimeError("fake transport has no action for authorized exchange")
        self._calls.append(exchange_id)
        self._active += 1
        self._maximum_active = max(self._maximum_active, self._active)
        try:
            for _index in range(action.yields.value):
                await asyncio.sleep(0)
            return action.result
        finally:
            self._active -= 1

    async def close(self) -> None:
        """Mark the fake closed after all active calls have unwound."""
        if self._active:
            raise RuntimeError("cannot close fake transport with active exchanges")
        self._closed = True
