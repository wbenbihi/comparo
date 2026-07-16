"""An httpx-backed implementation of the :class:`HttpClient` port.

This is the one module that imports httpx; the core engine never does. It maps a
resolved request onto an httpx call and a materialized response back, translating
httpx transport errors into the core's :class:`HttpError`.
"""

import json
import time
from collections.abc import Mapping
from typing import cast

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
        json_body, data_body, content_body = _encode_body(request)
        auth, auth_header = _auth(request.auth)
        if auth_header is not None:
            headers.append(auth_header)
        cookies = {key: str(value) for key, value in (request.cookies or {}).items()} or None
        start = time.perf_counter()
        try:
            response = await self._client.request(
                request.method,
                request.url,
                headers=headers,
                params=params,
                json=json_body,
                data=data_body,
                content=content_body,
                cookies=cookies,
                auth=auth,
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


def _encode_body(
    request: ResolvedRequest,
) -> tuple[object, Mapping[str, object] | None, str | bytes | None]:
    """Split a resolved body into httpx's ``json`` / ``data`` / ``content`` slots."""
    body = request.body
    if body is None:
        return None, None, None
    if request.body_type == "form":
        return None, cast("Mapping[str, object]", body), None
    if request.body_type == "raw":
        content = body if isinstance(body, str | bytes) else json.dumps(body)
        return None, None, content
    return body, None, None


def _auth(auth: object) -> tuple[httpx.Auth | None, tuple[str, str] | None]:
    """Turn a resolved auth block into an httpx auth or an Authorization header."""
    if not isinstance(auth, dict):
        return None, None
    basic = auth.get("basic")
    if isinstance(basic, dict):
        return httpx.BasicAuth(str(basic.get("username", "")), str(basic.get("password", ""))), None
    bearer = auth.get("bearer")
    if bearer is not None:
        return None, ("Authorization", f"Bearer {bearer}")
    return None, None
