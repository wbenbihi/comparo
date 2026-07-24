"""Live sensitive-value retention for the later sanitization boundary."""

from __future__ import annotations

import asyncio

from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import run_scope_of
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolvedValue
from comparo.core.resolution.models import RegisteredSensitiveValue
from comparo.core.resolution.models import SanitizationSnapshot


class SensitiveValueRegistry:
    """Retain every distinct sealed value from resolution until the run closes."""

    __slots__ = ("_closed", "_entries", "_identities", "_lock", "_run")

    def __init__(self, run: RunScope) -> None:
        """Create an empty registry for one exact run."""
        if type(run) is not RunScope:
            raise TypeError("sensitive registry requires a RunScope")
        self._run = run
        self._lock = asyncio.Lock()
        self._closed = False
        self._entries: list[RegisteredSensitiveValue] = []
        self._identities: set[int] = set()

    async def register(
        self,
        result: Present[ResolvedValue],
        *,
        symbol: SymbolPath,
        scope: RuntimeScope,
    ) -> None:
        """Retain a sensitive result once by sealed-container identity."""
        value = result.value
        if type(value) is not SensitiveValue:
            return
        if type(symbol) is not SymbolPath:
            raise TypeError("sensitive registration requires a SymbolPath")
        if run_scope_of(scope) != self._run:
            raise ValueError("sensitive registration must belong to the registry run")
        entry = RegisteredSensitiveValue(
            value=value,
            symbol=symbol,
            scope=scope,
            provenance=result.provenance,
        )
        identity = id(value)
        async with self._lock:
            if self._closed:
                raise RuntimeError("sensitive registry is closed")
            if identity not in self._identities:
                self._identities.add(identity)
                self._entries.append(entry)

    async def snapshot(self) -> SanitizationSnapshot:
        """Return an immutable same-run snapshot without revealing any value."""
        async with self._lock:
            if self._closed:
                raise RuntimeError("sensitive registry is closed")
            entries = tuple(self._entries)
        return SanitizationSnapshot(
            run=self._run,
            revision=Count(value=len(entries)),
            entries=entries,
        )

    async def close(self) -> None:
        """Drop all live sensitive references and reject later snapshots or registrations."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._entries.clear()
            self._identities.clear()
