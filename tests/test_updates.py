"""Tests for the opt-in version check (adapters/updates.py)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from comparo.adapters import updates


def test_is_newer_compares_pep440_versions() -> None:
    assert updates.is_newer("1.2.0", "1.1.0")
    assert updates.is_newer("2.0.0", "2.0.0rc1")
    assert not updates.is_newer("1.0.0", "1.0.0")
    assert not updates.is_newer("0.9.0", "1.0.0")
    assert not updates.is_newer("not-a-version", "1.0.0")  # never raises


class _FakeResponse:
    def __init__(self, version: str) -> None:
        self._version = version

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, dict[str, str]]:
        return {"info": {"version": self._version}}


def _fake_client(version: str | None = None, error: Exception | None = None) -> type:
    class _FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
            if error is not None:
                raise error
            assert version is not None
            return _FakeResponse(version)

    return _FakeClient


def test_check_latest_returns_a_newer_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(version="9.9.9"))
    assert asyncio.run(updates.check_latest("0.1.0")) == "9.9.9"


def test_check_latest_returns_none_when_current_is_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(version="1.0.0"))
    assert asyncio.run(updates.check_latest("1.0.0")) is None


def test_check_latest_swallows_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(error=httpx.ConnectError("offline")))
    assert asyncio.run(updates.check_latest("0.1.0")) is None
