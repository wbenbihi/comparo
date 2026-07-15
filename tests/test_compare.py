"""Tests for the diff-run orchestration."""

import asyncio
from pathlib import Path

from comparo.core.compare import diff_run
from comparo.core.http import HttpResponse
from comparo.core.http import TimeoutBudget
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Request
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import select_environment

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


class _BodyByHost:
    """Returns a canned body chosen by the request URL host."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        host = "prod" if "httpbin.org" in request.url else "local"
        return HttpResponse(200, [], self.bodies[host], 1.0)

    async def aclose(self) -> None:
        return None


def _get_json(loaded: LoadedProject) -> list[Request]:
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    return [request]


def test_diff_run_reports_same_when_bodies_match() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    body = b'{"slideshow": {"author": "x", "title": "t", "slides": []}}'
    client = _BodyByHost({"local": body, "prod": body})
    results = asyncio.run(diff_run(loaded, baseline, candidate, _get_json(loaded), client))
    assert len(results) == 1
    assert not results[0].drifted
    assert results[0].error is None


def test_diff_run_handles_empty_body() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    request = loaded.objects["request.health-status"]
    assert isinstance(request, Request)
    client = _BodyByHost({"local": b"", "prod": b""})
    results = asyncio.run(diff_run(loaded, baseline, candidate, [request], client))
    assert not results[0].drifted
    assert results[0].error is None


def test_diff_run_reports_drift_on_difference() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    unchanged = b'{"slideshow": {"author": "x", "title": "t", "slides": []}}'
    changed = b'{"slideshow": {"author": "CHANGED", "title": "t", "slides": []}}'
    client = _BodyByHost({"local": unchanged, "prod": changed})
    results = asyncio.run(diff_run(loaded, baseline, candidate, _get_json(loaded), client))
    assert results[0].drifted
