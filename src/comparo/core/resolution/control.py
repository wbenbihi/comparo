"""Explicit first-wins cancellation owned by one run context."""

from __future__ import annotations

import asyncio

from comparo.core.domain.lifecycle import CancellationSource


class RunCancellation:
    """A mutable run-local cancellation signal with a stable value-free reason."""

    __slots__ = ("_event", "_source")

    def __init__(self) -> None:
        """Create an uncancelled signal."""
        self._event = asyncio.Event()
        self._source: CancellationSource | None = None

    @property
    def source(self) -> CancellationSource | None:
        """Return the first cancellation source, if cancellation was requested."""
        return self._source

    @property
    def cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def cancel(self, source: CancellationSource) -> None:
        """Record the first typed cancellation source and wake waiters."""
        if type(source) is not CancellationSource:
            raise TypeError("run cancellation source must be CancellationSource")
        if self._source is None:
            self._source = source
            self._event.set()

    async def wait(self) -> CancellationSource:
        """Wait until cancellation and return its stable source."""
        await self._event.wait()
        source = self._source
        if source is None:  # pragma: no cover - event/source transition is atomic in one loop
            raise RuntimeError("cancelled run has no cancellation source")
        return source
