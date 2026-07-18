"""Tests for the assertion engine — targets, ops, severity, and composition."""

import json
from pathlib import Path

import msgspec

from comparo.core.assertions import evaluate_rules
from comparo.core.assertions import passed
from comparo.core.assertions import request_rules
from comparo.core.assertions import run_assertions
from comparo.core.execute import Execution
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import AssertionProfile
from comparo.core.models import AssertionRule
from comparo.core.models import Environment
from comparo.core.models import Request

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def _execution(
    loaded: LoadedProject,
    *,
    status: int = 200,
    headers: list[tuple[str, str]] | None = None,
    body: bytes = b"",
    ms: float = 100.0,
) -> Execution:
    request = next(o for o in loaded.objects.values() if isinstance(o, Request))
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    response = HttpResponse(status=status, headers=headers or [], body=body, elapsed_ms=ms)
    return Execution(request, environment, "", response)


def test_targets_and_ops_that_hold() -> None:
    loaded = load_project(SAMPLE)
    body = json.dumps({"total": 500, "currency": "USD"}).encode()
    execution = _execution(
        loaded,
        status=200,
        headers=[("content-type", "application/json")],
        body=body,
        ms=200.0,
    )
    rules = [
        AssertionRule(target="status", op="equals", value=200),
        AssertionRule(target="latency", op="lte", value="800ms"),
        AssertionRule(target="body:$.total", op="between", value=[0, 1000]),
        AssertionRule(target="body:$.currency", op="oneOf", value=["USD", "EUR"]),
        AssertionRule(target="header:content-type", op="matches", value="application/json"),
        AssertionRule(target="body:$.total", op="exists"),
    ]
    results = evaluate_rules(loaded, rules, execution)
    assert all(result.ok for result in results)
    assert passed(results)


def test_latency_accepts_an_hour_unit() -> None:
    # A latency bound written in hours must parse, not silently fail as non-numeric.
    loaded = load_project(SAMPLE)
    execution = _execution(loaded, status=200, body=b"{}", ms=200.0)
    rules = [AssertionRule(target="latency", op="lte", value="1h")]
    results = evaluate_rules(loaded, rules, execution)
    assert all(result.ok for result in results)  # 200ms is under 1h


def test_failing_and_severity() -> None:
    loaded = load_project(SAMPLE)
    execution = _execution(loaded, status=500, body=json.dumps({"total": 5000}).encode(), ms=2000.0)
    rules = [
        AssertionRule(target="status", op="equals", value=200),
        AssertionRule(target="body:$.total", op="lte", value=1000),
        AssertionRule(target="body:$.missing", op="exists"),
    ]
    results = evaluate_rules(loaded, rules, execution)
    assert not any(result.ok for result in results)
    assert not passed(results)  # error-severity failures
    # a warn-only failure never fails the gate
    warn = [AssertionRule(target="latency", op="lte", value="1ms", severity="warn")]
    assert passed(evaluate_rules(loaded, warn, execution))


def test_schema_op_with_inline_schema() -> None:
    loaded = load_project(SAMPLE)
    schema = {"type": "object", "required": ["total"]}
    ok = _execution(loaded, body=json.dumps({"total": 1}).encode())
    bad = _execution(loaded, body=json.dumps({"other": 1}).encode())
    rule = [AssertionRule(target="body", op="schema", value=schema)]
    assert evaluate_rules(loaded, rule, ok)[0].ok
    assert not evaluate_rules(loaded, rule, bad)[0].ok


def test_request_rules_compiles_status_and_schema_sugar() -> None:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]  # has response.status + response.schema
    assert isinstance(request, Request)
    rules = request_rules(request)
    assert any(r.target == "status" and r.op == "equals" for r in rules)
    assert any(r.target == "body" and r.op == "schema" for r in rules)


def test_include_composes_rules(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text(
        "apiVersion: comparo/v1\nkind: AssertionProfile\n"
        "metadata:\n  name: Base\n  id: assert.base\n"
        "spec:\n  rules:\n    - target: status\n      op: exists\n",
        encoding="utf-8",
    )
    (tmp_path / "derived.yaml").write_text(
        "apiVersion: comparo/v1\nkind: AssertionProfile\n"
        "metadata:\n  name: Derived\n  id: assert.derived\n"
        "spec:\n"
        "  include:\n    - $ref: assert.base\n"
        "  rules:\n    - target: status\n      op: equals\n      value: 200\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: E\n  id: environment.e\nspec:\n  baseUrl: https://example.test\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    profile = loaded.objects["assert.derived"]
    assert isinstance(profile, AssertionProfile)
    request = load_project(SAMPLE).objects["request.get-json"]
    assert isinstance(request, Request)
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    response = HttpResponse(status=200, headers=[], body=b"", elapsed_ms=1.0)
    execution = Execution(request, environment, "", response)
    results = run_assertions(loaded, profile, execution)
    # the included "exists" rule and the own "equals 200" rule both ran and held
    assert [r.op for r in results] == ["exists", "equals"]
    assert passed(results)


# ── Phase 3: a bad rule fails the assertion, it never crashes the run ──


def _exec_with_body(body: bytes) -> Execution:
    request = msgspec.convert(
        {
            "apiVersion": "comparo/v1",
            "kind": "Request",
            "metadata": {"name": "R", "id": "request.r"},
            "spec": {"request": {"method": "GET", "endpoint": "/x"}},
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
    return Execution(request, environment, "", HttpResponse(200, [], body, 1.0))


def test_a_wildcard_or_quoted_body_path_is_a_clean_miss_not_a_crash() -> None:
    project = LoadedProject(root=Path(), project=None, objects={})
    execution = _exec_with_body(b'{"items": [{"name": "a"}]}')
    rule = AssertionRule(target="body:items[*].name", op="exists")
    results = evaluate_rules(project, [rule], execution)
    assert results[0].ok is False  # the path doesn't resolve — a clean miss


def test_an_invalid_regex_fails_the_rule_instead_of_raising() -> None:
    project = LoadedProject(root=Path(), project=None, objects={})
    execution = _exec_with_body(b'{"v": "abc"}')
    rule = AssertionRule(target="body:v", op="matches", value="([")
    results = evaluate_rules(project, [rule], execution)
    assert results[0].ok is False
    assert "invalid regex" in results[0].detail
