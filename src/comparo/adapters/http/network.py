"""Route-pinned network primitives that prohibit transport-side name resolution."""

from __future__ import annotations

import ssl
from collections.abc import Iterable
from typing import override

import httpcore

from comparo.core.domain.http.url import NetworkDestination


class RoutePinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to the exact IP and port authorized in a resolved route."""

    __slots__ = ("_backend", "_destination", "_tls_timeout")

    def __init__(
        self,
        destination: NetworkDestination,
        *,
        tls_timeout: float | None,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        """Bind one pool backend to one exact selected network destination."""
        if type(destination) is not NetworkDestination:
            raise TypeError("route-pinned backend requires NetworkDestination")
        if tls_timeout is not None and (type(tls_timeout) is not float or tls_timeout < 0):
            raise ValueError("TLS timeout must be a non-negative float or None")
        if backend is not None and not isinstance(backend, httpcore.AsyncNetworkBackend):
            raise TypeError("route-pinned backend requires AsyncNetworkBackend")
        self._destination = destination
        self._tls_timeout = tls_timeout
        self._backend = backend or httpcore.AnyIOBackend()

    @override
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Validate the logical peer, then replace it with the authorized selected IP."""
        if host != self._destination.host.value or port != self._destination.port.value:
            raise httpcore.ConnectError("transport attempted a non-authorized destination")
        stream = await self._backend.connect_tcp(
            self._destination.selected_address.value,
            self._destination.port.value,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        return _TlsDeadlineStream(stream, self._tls_timeout)

    @override
    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Reject Unix sockets because the authorized HTTP route has no such representation."""
        del path, timeout, socket_options
        raise httpcore.ConnectError("Unix sockets are outside the authorized HTTP route")

    @override
    async def sleep(self, seconds: float) -> None:
        """Delegate cooperative sleeps; httpcore connection retries remain disabled."""
        await self._backend.sleep(seconds)


class _TlsDeadlineStream(httpcore.AsyncNetworkStream):
    """Apply the separately configured TLS-handshake deadline to a connected stream."""

    __slots__ = ("_stream", "_tls_timeout")

    def __init__(
        self,
        stream: httpcore.AsyncNetworkStream,
        tls_timeout: float | None,
    ) -> None:
        self._stream = stream
        self._tls_timeout = tls_timeout

    @override
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        """Delegate bounded network reads."""
        return await self._stream.read(max_bytes, timeout)

    @override
    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """Delegate bounded network writes."""
        await self._stream.write(buffer, timeout)

    @override
    async def aclose(self) -> None:
        """Close the owned connected stream."""
        await self._stream.aclose()

    @override
    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Ignore the library's connect timeout and apply Comparo's TLS deadline."""
        del timeout
        secured = await self._stream.start_tls(
            ssl_context,
            server_hostname,
            self._tls_timeout,
        )
        return _TlsDeadlineStream(secured, self._tls_timeout)

    @override
    def get_extra_info(self, info: str) -> object:
        """Preserve endpoint and TLS instrumentation supplied by the real backend."""
        return self._stream.get_extra_info(info)
