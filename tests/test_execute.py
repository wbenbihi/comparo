"""Tests for the execution engine and the httpx adapter."""

import asyncio
from pathlib import Path

import httpx
import pytest

from comparo.adapters.httpx_client import HttpxClient
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


def test_timeout_budget_request_wins() -> None:
    budget = TimeoutBudget.resolve(Duration(read="300s"), Duration(connect="5s", read="30s"))
    assert budget.read == 300.0
    assert budget.connect == 5.0


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
