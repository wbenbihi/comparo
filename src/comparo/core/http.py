"""The HTTP client port, the materialized response, and the timeout budget.

The core defines this port; an adapter implements it with a concrete HTTP
library. Core code never imports that library — the boundary is enforced in CI.
"""

import dataclasses
from typing import Protocol

from comparo.core.models import Duration
from comparo.core.resolve import ResolvedRequest

_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


class HttpError(Exception):
    """A transport-level failure, raised by adapters and caught by the engine."""


@dataclasses.dataclass(frozen=True, slots=True)
class HttpResponse:
    """A response materialized from the wire.

    For a streamed response (``streaming: true``), ``events`` holds the ordered
    records — parsed SSE events or the JSON objects of a chunked stream — so a
    front-end can diff the sequence, not just the assembled body.
    """

    status: int
    headers: list[tuple[str, str]]
    body: bytes
    elapsed_ms: float
    events: list[object] | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class TimeoutBudget:
    """Per-phase timeouts, in seconds; ``None`` means no limit for that phase."""

    connect: float | None = None
    read: float | None = None

    @classmethod
    def resolve(cls, request: Duration | None, environment: Duration | None) -> "TimeoutBudget":
        """Merge request-level over environment-level durations, request winning per field.

        Args:
            request: The request's own timeout, if any.
            environment: The environment's default timeout, if any.

        Returns:
            The effective, parsed timeout budget.
        """
        chosen_connect = _first(request, environment, "connect")
        chosen_read = _first(request, environment, "read")
        return cls(connect=_seconds(chosen_connect), read=_seconds(chosen_read))


class HttpClient(Protocol):
    """The transport the engine sends resolved requests through."""

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        """Send *request* and return the materialized response."""
        ...

    async def aclose(self) -> None:
        """Release any underlying connections."""
        ...


def _first(request: Duration | None, environment: Duration | None, field: str) -> str | None:
    value = getattr(request, field, None) if request is not None else None
    if value is not None:
        return str(value)
    fallback = getattr(environment, field, None) if environment is not None else None
    return str(fallback) if fallback is not None else None


def _seconds(text: str | None) -> float | None:
    if text is None:
        return None
    for suffix, factor in _UNITS.items():
        if text.endswith(suffix):
            return int(text[: -len(suffix)]) * factor
    message = f"invalid duration '{text}'"
    raise ValueError(message)
