"""An httpx-backed implementation of the :class:`HttpClient` port.

This is the one module that imports httpx; the core engine never does. It maps a
resolved request onto an httpx call and a materialized response back, translating
httpx transport errors into the core's :class:`HttpError`.
"""

import time

import httpx

from comparo.core.http import HttpError
from comparo.core.http import HttpResponse
from comparo.core.http import TimeoutBudget
from comparo.core.resolve import ResolvedRequest


class HttpxClient:
    """Sends resolved requests through an ``httpx.AsyncClient``."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Wrap an httpx client, creating a default one if none is given.

        Args:
            client: An existing async client to use, or ``None`` to create one.
        """
        self._client = client if client is not None else httpx.AsyncClient()

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        """Send *request* and return the materialized response.

        Args:
            request: The resolved request to send.
            timeout: The per-phase timeout budget.

        Returns:
            The materialized response.

        Raises:
            HttpError: If the request fails at the transport level.
        """
        headers = [(key, str(value)) for key, value in request.headers]
        params = {key: str(value) for key, value in request.query.items()}
        httpx_timeout = httpx.Timeout(timeout.read, connect=timeout.connect)
        start = time.perf_counter()
        try:
            response = await self._client.request(
                request.method,
                request.url,
                headers=headers,
                params=params,
                json=request.body,
                timeout=httpx_timeout,
            )
        except httpx.HTTPError as error:
            message = f"{type(error).__name__}: {error}"
            raise HttpError(message) from error
        elapsed_ms = (time.perf_counter() - start) * 1000
        return HttpResponse(
            status=response.status_code,
            headers=list(response.headers.items()),
            body=response.content,
            elapsed_ms=elapsed_ms,
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
