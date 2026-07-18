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
    #: For a streaming read, end the stream gracefully after this long with no new
    #: data (so an *idle* Server-Sent Events feed terminates instead of hanging).
    stream_idle: float | None = None
    #: A total cap on a streaming read: stop and diff what arrived after this long,
    #: no matter how busy the stream (so a *steady* public SSE feed still ends).
    stream_max: float | None = None

    @classmethod
    def resolve(cls, request: Duration | None, environment: Duration | None) -> "TimeoutBudget":
        """Merge request-level over environment-level durations, request winning per field.

        Args:
            request: The request's own timeout, if any.
            environment: The environment's default timeout, if any.

        Returns:
            The effective, parsed timeout budget.
        """
        return cls(
            connect=_seconds(_first(request, environment, "connect")),
            read=_seconds(_first(request, environment, "read")),
            stream_idle=_seconds(_first(request, environment, "stream_idle")),
            stream_max=_seconds(_first(request, environment, "stream_max")),
        )


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
