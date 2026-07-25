"""Local protocol integration tests for the production HTTP adapter."""

from __future__ import annotations

import asyncio
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import msgspec

from comparo.adapters.http.capture import DefaultCaptureBudgetProvider
from comparo.adapters.http.capture import ResponseCaptureLimits
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.adapters.http.tls import ExplicitTlsContextProvider
from comparo.adapters.http.transport import HttpcoreTransport
from comparo.adapters.http.transport import HttpcoreTransportConfig
from comparo.adapters.http.transport import ResponseClassification
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.policies import UnlimitedTimeout
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import Sensitivity
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request


@dataclass(slots=True)
class _LocalServer:
    server: asyncio.AbstractServer
    requests: list[bytes]
    response: bytes
    port_value: int

    @property
    def port(self) -> int:
        return self.port_value

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()


async def _start_server(response: bytes) -> _LocalServer:
    requests: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                content_length = 0
                for line in head.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                body = await reader.readexactly(content_length)
                requests.append(head + body)
                writer.write(response)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None
    port = int(sockets[0].getsockname()[1])
    return _LocalServer(server=server, requests=requests, response=response, port_value=port)


def _store(tmp_path: Path) -> TransientBodyStore:
    return TransientBodyStore(
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=8),
            maximum_transient_bytes=ByteCount(value=8_192),
            io_chunk_size=PositiveCount(value=3),
            root=tmp_path,
        )
    )


def _config() -> HttpcoreTransportConfig:
    return HttpcoreTransportConfig(
        maximum_connections=PositiveCount(value=4),
        maximum_keepalive_connections=Count(value=2),
        keepalive_expiry=Duration(nanoseconds=30_000_000_000),
        capture=ResponseCaptureLimits(
            maximum_encoded_body_bytes=ByteCount(value=4_096),
            maximum_entity_body_bytes=ByteCount(value=4_096),
            maximum_expansion_ratio=Ratio(numerator=100, denominator=PositiveCount(value=1)),
        ),
        classification=ResponseClassification(body=Sensitivity.PUBLIC),
    )


def _authorized(
    port: int,
    exchange: int,
    *,
    attempt: int = 1,
    execution: int = 1,
    attempt_total: int | None = 5_000_000_000,
    execution_total: int | None = 5_000_000_000,
) -> AuthorizedExchangeRequest:
    effective = request()
    effective = msgspec.structs.replace(
        effective,
        url=msgspec.structs.replace(
            effective.url,
            scheme=HttpScheme.HTTP,
            authority=HttpAuthority(
                host=DnsHost(value="service.example"),
                port=HttpPort(value=port),
            ),
        ),
        policies=msgspec.structs.replace(
            effective.policies,
            timeouts=msgspec.structs.replace(
                effective.policies.timeouts,
                attempt_total=(
                    UnlimitedTimeout()
                    if attempt_total is None
                    else Duration(nanoseconds=attempt_total)
                ),
                execution_total=(
                    UnlimitedTimeout()
                    if execution_total is None
                    else Duration(nanoseconds=execution_total)
                ),
            ),
        ),
    )
    destination = NetworkDestination(
        scheme=HttpScheme.HTTP,
        host=DnsHost(value="service.example"),
        port=HttpPort(value=port),
        resolved_addresses=(Ipv4Host(value="127.0.0.1"),),
        selected_address=Ipv4Host(value="127.0.0.1"),
    )
    return authorized_request(
        effective,
        route=DirectResolvedRoute(destination=destination),
        request_occurrence=occurrence(
            execution=execution,
            attempt=attempt,
            exchange=exchange,
            environment=effective.environment.identity,
        ),
    )


async def _start_slow_body_server() -> _LocalServer:
    requests: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            requests.append(head)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nabc")
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.write(b"def")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None
    port = int(sockets[0].getsockname()[1])
    return _LocalServer(server=server, requests=requests, response=b"", port_value=port)


def test_real_transport_preserves_response_facts_decodes_gzip_and_reuses_pool(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        entity = b'{"message":"hello"}'
        encoded = gzip.compress(entity, mtime=0)
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Encoding: gzip\r\n"
            b"X-Repeat: one\r\n"
            b"X-Repeat: two\r\n"
            b"Set-Cookie: session=private\r\n"
            + f"Content-Length: {len(encoded)}\r\n".encode("ascii")
            + b"Connection: keep-alive\r\n\r\n"
            + encoded
        )
        server = await _start_server(response)
        store = _store(tmp_path)
        config = _config()
        transport = HttpcoreTransport(
            body_store=store,
            config=config,
            tls=ExplicitTlsContextProvider(),
            budgets=DefaultCaptureBudgetProvider(
                config.capture,
                default_budget_origin(),
            ),
        )
        first = await transport.exchange(_authorized(server.port, 601))
        second = await transport.exchange(_authorized(server.port, 602))

        assert first.failure is None
        assert first.response is not None
        assert first.response.status.value == 200
        assert first.response.reason is not None
        assert first.response.reason.value == "OK"
        assert [
            cast(HeaderText, field.value).value
            for field in first.response.headers.fields
            if field.name.value == "x-repeat"
        ] == ["one", "two"]
        cookie = next(
            field for field in first.response.headers.fields if field.name.value == "set-cookie"
        )
        assert str(cookie.value) == "<sensitive>"
        assert type(first.response.body) is ResponseEntityBody
        assert type(first.response.body.capture) is CompleteCapture
        assert first.response.body.encoded_byte_count == ByteCount(value=len(encoded))
        assert first.response.body.handle is not None
        captured = b"".join(
            [chunk async for chunk in store.open_response(first.response.body.handle)]
        )
        assert captured == entity
        assert type(first.timings.connect) is Measured
        assert type(first.timings.body_read) is Measured

        assert second.response is not None
        assert second.response.endpoint.connection_reused
        assert second.response.endpoint.connection_id == first.response.endpoint.connection_id
        assert type(second.timings.connect) is NotPerformed
        assert second.timings.connect.reason is NotPerformedReason.CONNECTION_REUSED
        assert len(server.requests) == 2
        assert server.requests[0].startswith(
            f"GET / HTTP/1.1\r\nHost: service.example:{server.port}\r\n".encode("ascii")
        )
        assert b"Accept-Encoding: gzip\r\n" in server.requests[0]

        await transport.close()
        await store.close()
        await server.close()

    asyncio.run(scenario())


def test_attempt_total_deadline_preserves_cancelled_partial_response(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = await _start_slow_body_server()
        store = _store(tmp_path)
        config = _config()
        transport = HttpcoreTransport(
            body_store=store,
            config=config,
            tls=ExplicitTlsContextProvider(),
            budgets=DefaultCaptureBudgetProvider(config.capture, default_budget_origin()),
        )
        result = await transport.exchange(
            _authorized(
                server.port,
                701,
                attempt_total=10_000_000,
                execution_total=100_000_000,
            )
        )

        assert result.response is not None
        assert result.failure is not None
        assert result.failure.phase is FailurePhase.CANCELLATION
        assert result.failure.code.value == "http.attempt-total-timeout"
        assert type(result.response.body) is ResponseEntityBody
        assert type(result.response.body.capture) is CancelledCapture
        assert result.response.body.handle is not None
        captured = b"".join(
            [chunk async for chunk in store.open_response(result.response.body.handle)]
        )
        assert captured == b"abc"

        await asyncio.sleep(0.06)
        await transport.close()
        await store.close()
        await server.close()

    asyncio.run(scenario())


def test_attempt_and_execution_deadlines_span_hops_and_attempts(tmp_path: Path) -> None:
    async def scenario() -> None:
        response_bytes = b"HTTP/1.1 204 No Content\r\nConnection: keep-alive\r\n\r\n"
        server = await _start_server(response_bytes)
        store = _store(tmp_path)
        config = _config()
        transport = HttpcoreTransport(
            body_store=store,
            config=config,
            tls=ExplicitTlsContextProvider(),
            budgets=DefaultCaptureBudgetProvider(config.capture, default_budget_origin()),
        )

        first = await transport.exchange(
            _authorized(
                server.port,
                801,
                attempt=11,
                execution=11,
                attempt_total=20_000_000,
                execution_total=100_000_000,
            )
        )
        assert first.failure is None
        await asyncio.sleep(0.03)
        expired_attempt = await transport.exchange(
            _authorized(
                server.port,
                802,
                attempt=11,
                execution=11,
                attempt_total=20_000_000,
                execution_total=100_000_000,
            )
        )
        assert expired_attempt.response is None
        assert expired_attempt.failure is not None
        assert expired_attempt.failure.code.value == "http.attempt-total-timeout"

        initial = await transport.exchange(
            _authorized(
                server.port,
                803,
                attempt=12,
                execution=12,
                attempt_total=10_000_000,
                execution_total=20_000_000,
            )
        )
        assert initial.failure is None
        await asyncio.sleep(0.03)
        expired_execution = await transport.exchange(
            _authorized(
                server.port,
                804,
                attempt=13,
                execution=12,
                attempt_total=10_000_000,
                execution_total=20_000_000,
            )
        )
        assert expired_execution.response is None
        assert expired_execution.failure is not None
        assert expired_execution.failure.code.value == "http.execution-total-timeout"

        unlimited = await transport.exchange(
            _authorized(
                server.port,
                805,
                attempt=14,
                execution=14,
                attempt_total=None,
                execution_total=None,
            )
        )
        assert unlimited.failure is None
        assert len(server.requests) == 3

        await transport.close()
        await store.close()
        await server.close()

    asyncio.run(scenario())
