"""Tests for response validation checks (status + JSON schema)."""

from pathlib import Path

import msgspec

from comparo.core.checks import passed
from comparo.core.checks import run_checks
from comparo.core.execute import Execution
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.models import Environment
from comparo.core.models import Object
from comparo.core.models import Request


def _fixture() -> tuple[LoadedProject, Request, Environment]:
    schema = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Schema",
            "metadata": {"name": "Order", "id": "schema.order"},
            "spec": {
                "type": "object",
                "required": ["orderId"],
                "properties": {"orderId": {"type": "string"}},
            },
        },
        type=Object,
    )
    request = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Request",
            "metadata": {"name": "Checkout", "id": "request.checkout"},
            "spec": {
                "request": {"method": "POST", "endpoint": "/checkout"},
                "response": {"status": 200, "schema": {"$ref": "schema.order"}},
            },
        },
        type=Object,
    )
    environment = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Environment",
            "metadata": {"name": "staging", "id": "environment.staging"},
            "spec": {"baseUrl": "https://api.test"},
        },
        type=Object,
    )
    assert isinstance(request, Request)
    assert isinstance(environment, Environment)
    project = LoadedProject(
        root=Path(),
        project=None,
        objects={"schema.order": schema, "request.checkout": request},
    )
    return project, request, environment


def _execution(environment: Environment, request: Request, status: int, body: bytes) -> Execution:
    return Execution(request, environment, "", HttpResponse(status, [], body, 12.0))


def test_all_checks_pass_on_a_valid_response() -> None:
    project, request, environment = _fixture()
    execution = _execution(environment, request, 200, b'{"orderId": "A-1"}')
    checks = run_checks(project, request, execution)
    assert passed(checks)
    assert {check.name for check in checks} == {"reachable", "status", "schema"}


def test_status_mismatch_fails() -> None:
    project, request, environment = _fixture()
    execution = _execution(environment, request, 500, b'{"orderId": "A-1"}')
    checks = run_checks(project, request, execution)
    assert not passed(checks)
    assert any(c.name == "status" and not c.ok for c in checks)


def test_schema_violation_fails() -> None:
    project, request, environment = _fixture()
    execution = _execution(environment, request, 200, b'{"wrong": true}')
    checks = run_checks(project, request, execution)
    assert not passed(checks)
    assert any(c.name == "schema" and not c.ok for c in checks)


def test_transport_error_is_unreachable() -> None:
    project, request, environment = _fixture()
    execution = Execution(request, environment, "", None, "connect timeout")
    checks = run_checks(project, request, execution)
    assert checks[0].name == "reachable"
    assert not checks[0].ok


def test_run_checks_enforces_an_inline_schema_like_the_assertion_engine(tmp_path: Path) -> None:
    # H19: the TUI Run tab (run_checks) must validate an inline response.schema, not
    # only a {$ref} — otherwise a request shows green in the TUI and red in CI.
    import msgspec

    from comparo.core.checks import passed
    from comparo.core.checks import run_checks
    from comparo.core.execute import Execution
    from comparo.core.http import HttpResponse
    from comparo.core.loader import LoadedProject
    from comparo.core.models import Environment
    from comparo.core.models import Request

    request = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Request",
            "metadata": {"name": "R", "id": "request.r"},
            "spec": {
                "request": {"method": "GET", "endpoint": "/x"},
                "response": {"schema": {"type": "object", "required": ["total"]}},
            },
        },
        type=Request,
    )
    environment = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Environment",
            "metadata": {"name": "E", "id": "environment.e"},
            "spec": {"baseUrl": "http://h"},
        },
        type=Environment,
    )
    assert isinstance(request, Request)
    assert isinstance(environment, Environment)
    project = LoadedProject(root=Path(), project=None, objects={})

    ok = Execution(request, environment, "", HttpResponse(200, [], b'{"total": 1}', 1.0))
    bad = Execution(request, environment, "", HttpResponse(200, [], b'{"other": 1}', 1.0))
    assert passed(run_checks(project, request, ok))
    assert not passed(run_checks(project, request, bad))  # inline schema now enforced
