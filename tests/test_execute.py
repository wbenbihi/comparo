"""Tests for the execution engine and the httpx adapter."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from comparo.adapters.httpx_client import HttpxClient
from comparo.core.execute import execute_all
from comparo.core.execute import execute_request
from comparo.core.http import HttpResponse
from comparo.core.http import TimeoutBudget
from comparo.core.loader import load_project
from comparo.core.models import Duration
from comparo.core.models import Request
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import select_environment

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


class _FakeClient:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.sent: list[ResolvedRequest] = []

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        self.sent.append(request)
        return self.response

    async def aclose(self) -> None:
        return None


def test_execute_request_returns_response() -> None:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    fake = _FakeClient(HttpResponse(200, [], b"{}", 12.0))
    result = asyncio.run(execute_request(loaded, env, request, fake))
    assert result.ok
    assert result.response is not None
    assert result.response.status == 200
    assert fake.sent[0].url == "http://localhost:8080/json"


def test_unresolved_secret_becomes_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPARO_DEMO_TOKEN", raising=False)
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "prod")
    request = loaded.objects["request.echo-anything"]
    assert isinstance(request, Request)
    fake = _FakeClient(HttpResponse(200, [], b"", 1.0))
    result = asyncio.run(execute_request(loaded, env, request, fake))
    assert not result.ok
    assert result.error is not None
    assert "API_TOKEN" in result.error


def test_unused_secret_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPARO_DEMO_TOKEN", raising=False)
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "prod")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    fake = _FakeClient(HttpResponse(200, [], b"{}", 5.0))
    result = asyncio.run(execute_request(loaded, env, request, fake))
    assert result.ok


def test_execute_all_expands_matrix() -> None:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    requests = [obj for obj in loaded.objects.values() if isinstance(obj, Request)]
    fake = _FakeClient(HttpResponse(200, [], b"{}", 1.0))
    results = asyncio.run(execute_all(loaded, env, requests, fake))
    assert len(results) == 6  # echo-anything expands to 3 cells; the other 3 are 1 each
    assert any("ja-JP" in execution.cell_key for execution in results)


def test_timeout_budget_request_wins() -> None:
    budget = TimeoutBudget.resolve(
        Duration(read="300s", stream_idle="8s"), Duration(connect="5s", read="30s")
    )
    assert budget.read == 300.0
    assert budget.connect == 5.0
    assert budget.stream_idle == 8.0  # the streaming idle bound is resolved too


def test_streaming_read_ends_gracefully_on_idle_timeout() -> None:
    # An open SSE stream that goes quiet must end with the events collected so far,
    # not raise — stream_idle is that idle bound (a never-closing feed still ends).
    class _IdleStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'event: tick\ndata: {"seq": 1}\n\n'
            yield b'event: tick\ndata: {"seq": 2}\n\n'
            raise httpx.ReadTimeout("stream went idle")

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_IdleStream()
        )

    async def go() -> HttpResponse:
        client = HttpxClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            resolved = ResolvedRequest("GET", "http://x/events", [], {}, None, [], streaming=True)
            return await client.send(resolved, TimeoutBudget(stream_idle=1.0))
        finally:
            await client.aclose()

    response = asyncio.run(go())
    assert response.status == 200
    assert response.events is not None
    assert len(response.events) == 2  # both events collected before the idle timeout


def test_streaming_read_is_bounded_by_stream_max() -> None:
    # A steady, never-idle SSE feed (like a public one) must still end — stream_max
    # is the total cap that stops it no matter how busy the stream is.
    class _SteadyStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            seq = 0
            while True:  # never closes, never idles
                seq += 1
                yield f'data: {{"seq": {seq}}}\n\n'.encode()
                await asyncio.sleep(0.02)

        async def aclose(self) -> None:
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_SteadyStream()
        )

    async def go() -> HttpResponse:
        client = HttpxClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            resolved = ResolvedRequest("GET", "http://x/events", [], {}, None, [], streaming=True)
            return await client.send(resolved, TimeoutBudget(stream_max=0.2))
        finally:
            await client.aclose()

    response = asyncio.run(go())  # must return (not hang) despite the infinite stream
    assert response.status == 200
    assert response.events is not None
    assert 1 <= len(response.events) < 100  # collected some, then the cap ended it


def test_httpx_adapter_roundtrip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async def go() -> HttpResponse:
        client = HttpxClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            resolved = ResolvedRequest(
                "GET", "http://x/y", [("accept", "application/json")], {}, None, []
            )
            return await client.send(resolved, TimeoutBudget())
        finally:
            await client.aclose()

    response = asyncio.run(go())
    assert response.status == 200
    assert b'"ok"' in response.body


# ── Phase 5: retry-with-backoff and configured concurrency ──


class _FlakyClient:
    """Fails with an HttpError for the first *fail_times* sends, then succeeds."""

    def __init__(self, response: HttpResponse, fail_times: int) -> None:
        self.response = response
        self.remaining = fail_times
        self.calls = 0

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        from comparo.core.http import HttpError

        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise HttpError("transient")
        return self.response

    async def aclose(self) -> None:
        return None


def test_retry_recovers_a_transient_transport_failure() -> None:
    from comparo.core.models import RetryConfig

    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    flaky = _FlakyClient(HttpResponse(200, [], b"{}", 1.0), fail_times=1)
    retry = RetryConfig(attempts=3, backoff="constant")
    result = asyncio.run(execute_request(loaded, env, request, flaky, retry=retry))
    assert result.ok  # the second attempt succeeded
    assert flaky.calls == 2


def test_retry_gives_up_after_attempts_and_captures_the_error() -> None:
    from comparo.core.models import RetryConfig

    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    flaky = _FlakyClient(HttpResponse(200, [], b"{}", 1.0), fail_times=5)
    retry = RetryConfig(attempts=2, backoff="constant")
    result = asyncio.run(execute_request(loaded, env, request, flaky, retry=retry))
    assert not result.ok  # exhausted the two attempts
    assert flaky.calls == 2
    assert result.error is not None


def test_execute_all_honors_configured_concurrency() -> None:
    from comparo.core.execute import run_settings

    loaded = load_project(SAMPLE)
    # sample-project declares run.concurrency: 4
    concurrency, _ = run_settings(loaded)
    assert concurrency == 4
