"""Cancellation-safe helpers for application-owned asynchronous cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from typing import final

import msgspec


@final
class CleanupOutcome(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free state produced after one cleanup coroutine has fully settled."""

    failed: bool
    cancellation_received: bool


async def finish_cleanup(operation: Coroutine[Any, Any, None]) -> CleanupOutcome:
    """Finish cleanup despite repeated caller cancellation without losing its signal."""
    task = asyncio.create_task(operation)
    cancellation_received = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_received = True
        except Exception as error:  # noqa: BLE001 - collected from task.result below
            del error
    try:
        task.result()
    except asyncio.CancelledError:
        cancellation_received = True
        failed = False
    except Exception:  # noqa: BLE001 - callers receive only value-free cleanup state
        failed = True
    else:
        failed = False
    return CleanupOutcome(
        failed=failed,
        cancellation_received=cancellation_received,
    )


__all__ = ("CleanupOutcome", "finish_cleanup")
