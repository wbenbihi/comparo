"""Tests for environment health probing, driven through a fake HTTP client."""

import asyncio
from pathlib import Path

import msgspec

from comparo.core.health import Health
from comparo.core.health import HealthReport
from comparo.core.health import check_health
from comparo.core.http import HttpError
from comparo.core.http import HttpResponse
from comparo.core.http import TimeoutBudget
from comparo.core.loader import LoadedProject
from comparo.core.models import Environment
from comparo.core.models import Object
from comparo.core.resolve import ResolvedRequest


class FakeClient:
    """Answers each probe from a URL-substring → status (or exception) map."""

    def __init__(self, answers: dict[str, int | Exception]) -> None:
        self.answers = answers

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        for needle, answer in self.answers.items():
            if needle in request.url:
                if isinstance(answer, Exception):
                    raise answer
                return HttpResponse(answer, [], b"", 1.0)
        return HttpResponse(200, [], b"", 1.0)

    async def aclose(self) -> None:
        return None


def _environment(*endpoints: str) -> tuple[LoadedProject, Environment]:
    checks = [{"method": "GET", "endpoint": endpoint} for endpoint in endpoints]
    env = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Environment",
            "metadata": {"name": "staging", "id": "environment.staging"},
            "spec": {"baseUrl": "https://api.test", "health": checks},
        },
        type=Object,
        strict=True,
    )
    assert isinstance(env, Environment)
    project = LoadedProject(root=Path(), project=None, objects={"environment.staging": env})
    return project, env


def _run(answers: dict[str, int | Exception], *endpoints: str) -> HealthReport:
    project, env = _environment(*endpoints)
    return asyncio.run(check_health(project, env, FakeClient(answers)))


def test_all_checks_pass_is_healthy() -> None:
    report = _run({"/status/200": 200, "/health": 204}, "/status/200", "/health")
    assert report.status is Health.PASS


def test_some_checks_fail_is_partial() -> None:
    report = _run({"/status/200": 200, "/status/500": 500}, "/status/200", "/status/500")
    assert report.status is Health.PARTIAL


def test_no_checks_pass_is_fail() -> None:
    report = _run({"/down": HttpError("refused")}, "/down")
    assert report.status is Health.FAIL
    assert report.results[0].ok is False


def test_no_declared_checks_is_unknown() -> None:
    report = _run({})
    assert report.status is Health.UNKNOWN
    assert report.results == []
