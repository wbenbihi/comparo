"""Production UTC wall clock for run-scoped resolution."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from comparo.core.domain.primitives import UtcInstant


class SystemRunClock:
    """Read the system wall clock only through the injected RunClock boundary."""

    __slots__ = ()

    def now(self) -> UtcInstant:
        """Return the current aware UTC instant."""
        return UtcInstant.from_datetime(datetime.now(UTC))


__all__ = ("SystemRunClock",)
