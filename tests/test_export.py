"""Tests for masked run export."""

import json
from pathlib import Path

import msgspec

from comparo.core.assertions import AssertionResult
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
    result = AssertionResult(
        "status", "equals", True, "error", "200", "status == 200", expected=200, actual=200
    )
    echoed = AssertionResult(
        "body:$.token",
        "equals",
        False,
        "warn",
        f"got {SECRET}",
        f"token == {SECRET}",
        expected=SECRET,
        actual=SECRET,
    )
    entry = RunEntry(request, MatrixCell("", ()), execution, [result, echoed])

    document = export_run(project, environment, [entry])

    assert SECRET not in document  # request header, echoed body, and result values all mask
    assert "••••••" in document
    parsed = json.loads(document)
    assert parsed["results"][0]["status"] == 200
    assert parsed["results"][0]["durationMs"] == 42.0
    serialized = parsed["results"][0]["results"]
    assert serialized[0] == {
        "label": "status == 200",
        "target": "status",
        "op": "equals",
        "ok": True,
        "severity": "error",
        "expected": 200,
        "actual": 200,
        "detail": "200",
    }
    # The warn rule survives with full fidelity — the old Check rows dropped it.
    assert serialized[1]["severity"] == "warn"
    assert serialized[1]["expected"] == "••••••"
