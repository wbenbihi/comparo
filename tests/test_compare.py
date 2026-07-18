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


class _ConstClient:
    """Always returns the same canned body — a stand-in per environment."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        return HttpResponse(200, [], self.body, 1.0)

    async def aclose(self) -> None:
        return None


def test_compare_cell_diffs_streamed_event_sequences() -> None:
    from comparo.core.compare import compare_cell
    from comparo.core.execute import Execution

    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]  # -> diff.strict (exact)
    assert isinstance(request, Request)
    env = select_environment(loaded, "local")

    def execution(events: list[object]) -> Execution:
        return Execution(request, env, "", HttpResponse(200, [], b"", 1.0, events=events))

    baseline_exec, candidate_exec = execution([{"n": 1}, {"n": 2}]), execution([{"n": 1}, {"n": 9}])
    cell = compare_cell(loaded, baseline_exec, candidate_exec)
    assert cell.drifted
    assert any("[1]" in field.path for field in cell.drifts)  # the second event drifted
    assert cell.baseline_body == [{"n": 1}, {"n": 2}]  # the event sequence is the diffed body
    # Both executions are threaded onto the cell so a report can serialize each side.
    assert cell.baseline is baseline_exec
    assert cell.candidate is candidate_exec


def test_diff_run_routes_candidate_to_its_own_client() -> None:
    loaded = load_project(SAMPLE)
    baseline = select_environment(loaded, "local")
    candidate = select_environment(loaded, "prod")
    base_client = _ConstClient(b'{"v": 1}')
    candidate_client = _ConstClient(b'{"v": 2}')
    results = asyncio.run(
        diff_run(loaded, baseline, candidate, _get_json(loaded), base_client, candidate_client)
    )
    # The candidate body came from its own client, so $.v drifts 1 -> 2.
    assert results[0].drifted


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
