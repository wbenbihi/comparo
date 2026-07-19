"""An httpx-backed implementation of the :class:`HttpClient` port.

This is the one module that imports httpx; the core engine never does. It maps a
resolved request onto an httpx call and a materialized response back, translating
httpx transport errors into the core's :class:`HttpError`.
"""

import asyncio
import json
import time
from collections.abc import Mapping
from typing import cast

import httpx

from comparo.core.http import HttpError
from comparo.core.http import HttpResponse
from comparo.core.http import HttpTimeoutError
from comparo.core.http import TimeoutBudget
from comparo.core.resolve import ResolvedRequest
from comparo.core.streams import parse_stream

#: Upper bound on a buffered (non-streaming) response body — generous enough for
#: any real API payload, but a ceiling so a runaway response can't exhaust memory.
_MAX_BODY_BYTES = 64 * 1024 * 1024


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
        # An unset optional (``${VAR?}``) resolves to None — omit it entirely rather
        # than send the literal string "None" as a header/query/cookie value (M-2).
        headers = [(key, str(value)) for key, value in request.headers if value is not None]
        params = {key: str(value) for key, value in request.query.items() if value is not None}
        # For a streaming read the read timeout is really an idle timeout — how long
        # to wait for the next event before deciding the stream has ended.
        read_timeout = (
            timeout.stream_idle
            if request.streaming and timeout.stream_idle is not None
            else timeout.read
        )
        httpx_timeout = httpx.Timeout(read_timeout, connect=timeout.connect)
        json_body, data_body, content_body = _encode_body(request)
        auth, auth_header = _auth(request.auth)
        if auth_header is not None:
            headers.append(auth_header)
        cookies = {
            key: str(value) for key, value in (request.cookies or {}).items() if value is not None
        } or None
        # A total deadline for the whole non-streaming read: httpx's read timeout is
        # per-socket-read, so a server trickling bytes never trips it. Sum the
        # phase budgets into one wall-clock cap.
        total = (timeout.connect or 0.0) + (timeout.read or 0.0)
        start = time.perf_counter()
        try:
            # build_request stays inside the try: a malformed resolved URL raises
            # httpx.InvalidURL, which must be captured on THIS cell's result, not
            # raised out to abort the whole run (M-4).
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
            status, resp_headers, body = await self._roundtrip(
                build,
                auth,
                streaming=request.streaming,
                stream_max=timeout.stream_max,
                total=total or None,
            )
        except (httpx.HTTPError, httpx.InvalidURL) as error:
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
        stream_max: float | None = None,
        total: float | None = None,
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        if streaming:
            response = await self._client.send(request, auth=auth, stream=True)
            chunks: list[bytes] = []
            try:
                # stream_max caps the whole read (a steady, never-idle SSE feed still
                # ends); stream_idle (the httpx read timeout) catches a quiet stream.
                # asyncio.timeout(None) is a no-op, so an unset cap means read to close.
                async with asyncio.timeout(stream_max):
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
            except (httpx.ReadTimeout, TimeoutError):
                # Idle read or the total cap — both mean "the stream is done", not a
                # failure. Diff whatever arrived.
                pass
            finally:
                await response.aclose()
            return response.status_code, list(response.headers.items()), b"".join(chunks)
        body_chunks: list[bytes] = []
        try:
            # A trickling server resets httpx's per-read timeout on every byte; the
            # total cap ends the read regardless. asyncio.timeout(None) is a no-op.
            # Stream the body (rather than buffering .content) so a runaway response
            # is bounded at _MAX_BODY_BYTES instead of exhausting memory.
            async with asyncio.timeout(total):
                response = await self._client.send(request, auth=auth, stream=True)
                size = 0
                try:
                    async for chunk in response.aiter_bytes():
                        body_chunks.append(chunk)
                        size += len(chunk)
                        if size > _MAX_BODY_BYTES:
                            # Fail closed rather than silently truncate: a diff over a
                            # cut-off body would compare a prefix as if it were the whole
                            # response, hiding any regression past the cap (S-1).
                            cap_mb = _MAX_BODY_BYTES // (1024 * 1024)
                            message = f"response body exceeded the {cap_mb} MB cap"
                            raise HttpError(message)
                finally:
                    await response.aclose()
        except TimeoutError as error:
            message = f"read exceeded the {total:g}s total deadline"
            raise HttpTimeoutError(message) from error
        return response.status_code, list(response.headers.items()), b"".join(body_chunks)

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
