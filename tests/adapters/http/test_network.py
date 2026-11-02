"""Route-pinned network backend contracts."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Iterable
from typing import cast
from typing import override

import httpcore
import pytest

from comparo.adapters.http.network import RoutePinnedNetworkBackend
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination


def _destination() -> NetworkDestination:
    return NetworkDestination(
        scheme=HttpScheme.HTTPS,
        host=Ipv4Host(value="192.0.2.8"),
        port=HttpPort(value=443),
        resolved_addresses=(Ipv4Host(value="192.0.2.8"),),
        selected_address=Ipv4Host(value="192.0.2.8"),
    )


class RecordingStream(httpcore.AsyncNetworkStream):
    """Minimal stream recording every delegated operation."""

    def __init__(self) -> None:
        self.operations: list[object] = []

    @override
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self.operations.append(("read", max_bytes, timeout))
        return b"data"

    @override
    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.operations.append(("write", buffer, timeout))

    @override
    async def aclose(self) -> None:
        self.operations.append("close")

    @override
    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.operations.append(("tls", ssl_context, server_hostname, timeout))
        return self

    @override
    def get_extra_info(self, info: str) -> object:
        return {"server_addr": ("192.0.2.8", 443)}.get(info)


class RecordingBackend(httpcore.AsyncNetworkBackend):
    """Record the concrete destination passed to the composed backend."""

    def __init__(self, stream: RecordingStream) -> None:
        self.stream = stream
        self.calls: list[object] = []

    @override
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.calls.append((host, port, timeout, local_address, socket_options))
        return self.stream

    @override
    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("unexpected Unix socket")

    @override
    async def sleep(self, seconds: float) -> None:
        self.calls.append(("sleep", seconds))


def test_backend_connects_only_to_pinned_ip_and_applies_tls_deadline() -> None:
    async def scenario() -> None:
        stream = RecordingStream()
        backend = RecordingBackend(stream)
        pinned = RoutePinnedNetworkBackend(
            _destination(),
            tls_timeout=2.5,
            backend=backend,
        )
        connected = await pinned.connect_tcp("192.0.2.8", 443, 1.0, "127.0.0.1", ())
        assert backend.calls == [("192.0.2.8", 443, 1.0, "127.0.0.1", ())]
        assert await connected.read(10, 0.5) == b"data"
        await connected.write(b"request", 0.75)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        secured = await connected.start_tls(context, "service.example", 99.0)
        assert secured.get_extra_info("server_addr") == ("192.0.2.8", 443)
        await secured.aclose()
        assert stream.operations == [
            ("read", 10, 0.5),
            ("write", b"request", 0.75),
            ("tls", context, "service.example", 2.5),
            "close",
        ]
        await pinned.sleep(0.25)
        assert backend.calls[-1] == ("sleep", 0.25)

    asyncio.run(scenario())


def test_backend_rejects_every_unpinned_or_unsupported_destination() -> None:
    async def scenario() -> None:
        pinned = RoutePinnedNetworkBackend(
            _destination(),
            tls_timeout=None,
            backend=RecordingBackend(RecordingStream()),
        )
        for host, port in (("other.example", 443), ("192.0.2.8", 444)):
            with pytest.raises(httpcore.ConnectError, match="non-authorized"):
                await pinned.connect_tcp(host, port)
        with pytest.raises(httpcore.ConnectError, match="Unix sockets"):
            await pinned.connect_unix_socket("not-authorized.sock")

    asyncio.run(scenario())


def test_backend_validates_constructor_inputs() -> None:
    with pytest.raises(TypeError, match="NetworkDestination"):
        RoutePinnedNetworkBackend(cast("NetworkDestination", object()), tls_timeout=None)
    for value in (-1.0, 1, "1"):
        with pytest.raises(ValueError, match="TLS timeout"):
            RoutePinnedNetworkBackend(_destination(), tls_timeout=value)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AsyncNetworkBackend"):
        RoutePinnedNetworkBackend(
            _destination(),
            tls_timeout=None,
            backend=cast("httpcore.AsyncNetworkBackend", object()),
        )
