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
from comparo.core.streams import parse_stream


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
        build = self._client.build_request(
            request.method,
            request.url,
            headers=headers,
            params=params,
            json=json_body,
            data=data_body,
            content=content_body,
            cookies=cookies,
            timeout=httpx_timeout,
        )
        start = time.perf_counter()
        try:
            status, resp_headers, body = await self._roundtrip(
                build, auth, streaming=request.streaming
            )
        except httpx.HTTPError as error:
            message = f"{type(error).__name__}: {error}"
            raise HttpError(message) from error
        elapsed_ms = (time.perf_counter() - start) * 1000
        events = parse_stream(body, _content_type(resp_headers)) if request.streaming else None
        return HttpResponse(
            status=status, headers=resp_headers, body=body, elapsed_ms=elapsed_ms, events=events
        )

    async def _roundtrip(
        self,
        request: httpx.Request,
        auth: httpx.Auth | None,
        *,
        streaming: bool,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        if streaming:
            response = await self._client.send(request, auth=auth, stream=True)
            try:
                chunks = [chunk async for chunk in response.aiter_bytes()]
            finally:
                await response.aclose()
            return response.status_code, list(response.headers.items()), b"".join(chunks)
        response = await self._client.send(request, auth=auth)
        return response.status_code, list(response.headers.items()), response.content

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


def _content_type(headers: list[tuple[str, str]]) -> str:
    for key, value in headers:
        if key.lower() == "content-type":
            return value
    return ""


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
