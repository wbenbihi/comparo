"""Tests for masked run export."""

import json
from pathlib import Path

import msgspec

from comparo.core.checks import Check
from comparo.core.execute import Execution
from comparo.core.export import RunEntry
from comparo.core.export import export_run
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.matrix import MatrixCell
from comparo.core.models import Environment
from comparo.core.models import Object
from comparo.core.models import Request

SECRET = "super-secret-token-value"


def _fixture() -> tuple[LoadedProject, Environment, Request]:
    environment = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Environment",
            "metadata": {"name": "staging", "id": "environment.staging"},
            "spec": {
                "baseUrl": "https://api.test",
                "secrets": {"API_TOKEN": {"$literal": SECRET}},
            },
        },
        type=Object,
    )
    request = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Request",
            "metadata": {"name": "Echo", "id": "request.echo"},
            "spec": {
                "request": {
                    "method": "POST",
                    "endpoint": "/anything",
                    "headers": [{"key": "authorization", "value": "Bearer ${API_TOKEN}"}],
                }
            },
        },
        type=Object,
    )
    assert isinstance(environment, Environment)
    assert isinstance(request, Request)
    project = LoadedProject(root=Path(), project=None, objects={"request.echo": request})
    return project, environment, request


def test_export_masks_secrets_in_request_and_echoed_response() -> None:
    project, environment, request = _fixture()
    # The server echoes the real bearer token back in the response body.
    body = json.dumps({"headers": {"Authorization": f"Bearer {SECRET}"}}).encode()
    execution = Execution(request, environment, "", HttpResponse(200, [], body, 42.0))
    check = Check("status", ok=True, detail="200")
    entry = RunEntry(request, MatrixCell("", ()), execution, [check])

    document = export_run(project, environment, [entry])

    assert SECRET not in document  # neither the request header nor the echoed body leaks it
    assert "••••••" in document
    parsed = json.loads(document)
    assert parsed["results"][0]["status"] == 200
    assert parsed["results"][0]["durationMs"] == 42.0
