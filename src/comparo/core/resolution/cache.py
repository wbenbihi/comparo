"""Async single-flight caching for exact source and scope coordinates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any


class SingleFlightCache[KeyT, ValueT]:
    """Run each exact async factory once while allowing concurrent waiters."""

    __slots__ = ("_closed", "_lock", "_tasks")

    def __init__(self) -> None:
        """Create an empty cache bound lazily to its first running event loop."""
        self._closed = False
        self._lock = asyncio.Lock()
        self._tasks: dict[KeyT, asyncio.Task[ValueT]] = {}

    async def get(
        self,
        key: KeyT,
        factory: Callable[[], Coroutine[Any, Any, ValueT]],
    ) -> ValueT:
        """Return a shared task result without caller cancellation cancelling the source read."""
        async with self._lock:
            if self._closed:
                raise RuntimeError("single-flight cache is closed")
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(factory())
                self._tasks[key] = task
        return await asyncio.shield(task)

    async def close(self) -> None:
        """Cancel unfinished source work, await cleanup, and discard cached values."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
