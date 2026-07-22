"""Tests for the assertion engine — targets, ops, severity, and composition."""

import json
from pathlib import Path

import msgspec

from comparo.core.assertions import _source
from comparo.core.assertions import evaluate_rules
from comparo.core.assertions import passed
from comparo.core.assertions import request_response_rules
from comparo.core.assertions import request_rules
from comparo.core.assertions import run_assertions
from comparo.core.execute import Execution
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import AssertionProfile
from comparo.core.models import AssertionRule
from comparo.core.models import Environment
from comparo.core.models import Object
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
    results = evaluate_rules(loaded, _source(rules, "inline"), execution)
    assert all(result.ok for result in results)
    assert passed(results)


def test_latency_accepts_an_hour_unit() -> None:
    # A latency bound written in hours must parse, not silently fail as non-numeric.
    loaded = load_project(SAMPLE)
    execution = _execution(loaded, status=200, body=b"{}", ms=200.0)
    rules = _source([AssertionRule(target="latency", op="lte", value="1h")], "inline")
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
    results = evaluate_rules(loaded, _source(rules, "inline"), execution)
    assert not any(result.ok for result in results)
    assert not passed(results)  # error-severity failures
    # a warn-only failure never fails the gate
    warn = _source(
        [AssertionRule(target="latency", op="lte", value="1ms", severity="warn")], "inline"
    )
    assert passed(evaluate_rules(loaded, warn, execution))


def test_schema_op_with_inline_schema() -> None:
    loaded = load_project(SAMPLE)
    schema = {"type": "object", "required": ["total"]}
    ok = _execution(loaded, body=json.dumps({"total": 1}).encode())
    bad = _execution(loaded, body=json.dumps({"other": 1}).encode())
    rule = _source([AssertionRule(target="body", op="schema", value=schema)], "inline")
    assert evaluate_rules(loaded, rule, ok)[0].ok
    assert not evaluate_rules(loaded, rule, bad)[0].ok


def test_request_rules_compiles_status_and_schema_sugar() -> None:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]  # has response.status + response.schema
    assert isinstance(request, Request)
    rules = request_rules(request)
    assert any(s.rule.target == "status" and s.rule.op == "equals" for s in rules)
    assert any(s.rule.target == "body" and s.rule.op == "schema" for s in rules)
    assert all(s.ref.origin == "inline" for s in rules)  # sugar is owned by the request


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
        "  include:\n    - $use: assert.base\n"
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
    results = evaluate_rules(project, _source([rule], "inline"), execution)
    assert results[0].ok is False  # the path doesn't resolve — a clean miss


def test_an_invalid_regex_fails_the_rule_instead_of_raising() -> None:
    project = LoadedProject(root=Path(), project=None, objects={})
    execution = _exec_with_body(b'{"v": "abc"}')
    rule = AssertionRule(target="body:v", op="matches", value="([")
    results = evaluate_rules(project, _source([rule], "inline"), execution)
    assert results[0].ok is False
    assert "invalid regex" in results[0].detail


def test_request_response_rules_includes_the_assert_block(tmp_path: Path) -> None:
    # A request's ``response.assert`` profile must gate the run path, not only the
    # execution path. ``request_rules`` (status/schema sugar) omits it; the fuller
    # ``request_response_rules`` includes it so ``comparo run`` honours it too.
    (tmp_path / "assert.yaml").write_text(
        "apiVersion: comparo/v1\nkind: AssertionProfile\n"
        "metadata:\n  name: Body\n  id: assert.body\n"
        "spec:\n  rules:\n    - target: body:$.ok\n      op: equals\n      value: true\n",
        encoding="utf-8",
    )
    (tmp_path / "request.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Get\n  id: request.get\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        "  response:\n    assert:\n      $use: assert.body\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: e\n  id: environment.e\n"
        "spec:\n  baseUrl: http://h\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    request = loaded.objects["request.get"]
    environment = loaded.objects["environment.e"]
    assert isinstance(request, Request)
    assert isinstance(environment, Environment)

    assert not request_rules(request)  # the sugar-only compiler misses the assert block
    rules = request_response_rules(loaded, request)
    assert any(s.rule.target == "body:$.ok" for s in rules)  # the full compiler includes it

    bad = Execution(request, environment, "", HttpResponse(200, [], b'{"ok": false}', 1.0))
    good = Execution(request, environment, "", HttpResponse(200, [], b'{"ok": true}', 1.0))
    assert not passed(evaluate_rules(loaded, rules, bad))
    assert passed(evaluate_rules(loaded, rules, good))


def test_result_carries_expected_and_actual() -> None:
    # A structured report shows the declared expectation vs the observed value,
    # so each result exposes both without re-parsing the detail string.
    loaded = load_project(SAMPLE)
    execution = _execution(loaded, status=503)
    [result] = evaluate_rules(
        loaded,
        _source([AssertionRule(target="status", op="equals", value=200)], "inline"),
        execution,
    )
    assert not result.ok
    assert result.expected == 200
    assert result.actual == 503


def test_no_response_result_carries_expectation_and_null_actual() -> None:
    loaded = load_project(SAMPLE)
    request = next(o for o in loaded.objects.values() if isinstance(o, Request))
    environment = next(o for o in loaded.objects.values() if isinstance(o, Environment))
    execution = Execution(request, environment, "", None, "boom")
    [result] = evaluate_rules(
        loaded,
        _source([AssertionRule(target="status", op="equals", value=200)], "inline"),
        execution,
    )
    assert result.expected == 200
    assert result.actual is None


# ── the Run-path contract (formerly tests/test_checks.py) ────────────────────
# checks.py is retired: the Run tab renders full AssertionResults from the same
# single evaluation the CLI and the execution planner use. These pin the same
# invariants at the assertion-pipeline level.


def _run_contract_fixture() -> tuple[LoadedProject, Request, Environment]:
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
                "response": {"status": 200, "schema": {"$use": "schema.order"}},
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


def _contract_execution(
    environment: Environment, request: Request, status: int, body: bytes
) -> Execution:
    return Execution(request, environment, "", HttpResponse(status, [], body, 12.0))


def test_the_response_contract_passes_on_a_valid_response() -> None:
    project, request, environment = _run_contract_fixture()
    execution = _contract_execution(environment, request, 200, b'{"orderId": "A-1"}')
    results = evaluate_rules(project, request_response_rules(project, request), execution)
    assert passed(results)
    # Every result is stamped with its rule's provenance — the sugar is inline,
    # owned by the request, with a stable within-block index.
    assert all(result.ref is not None for result in results)
    refs = [result.ref for result in results if result.ref is not None]
    assert {ref.origin for ref in refs} == {"inline"}
    assert {ref.request for ref in refs} == {"request.checkout"}
    assert [ref.index for ref in refs] == [0, 1]


def test_a_status_mismatch_fails_the_contract() -> None:
    project, request, environment = _run_contract_fixture()
    execution = _contract_execution(environment, request, 500, b'{"orderId": "A-1"}')
    results = evaluate_rules(project, request_response_rules(project, request), execution)
    assert not passed(results)
    assert any(result.target == "status" and not result.ok for result in results)


def test_a_schema_violation_fails_the_contract() -> None:
    project, request, environment = _run_contract_fixture()
    execution = _contract_execution(environment, request, 200, b'{"wrong": true}')
    results = evaluate_rules(project, request_response_rules(project, request), execution)
    assert not passed(results)
    assert any(result.op == "schema" and not result.ok for result in results)


def test_a_transport_error_fails_every_rule_with_the_error_detail() -> None:
    project, request, environment = _run_contract_fixture()
    execution = Execution(request, environment, "", None, "connect timeout")
    results = evaluate_rules(project, request_response_rules(project, request), execution)
    assert results
    assert all(not result.ok for result in results)
    assert all(result.detail == "connect timeout" for result in results)


def test_an_inline_schema_is_enforced_like_a_ref(tmp_path: Path) -> None:
    # H19: the run path must validate an inline response.schema, not only a
    # {$use} — otherwise a request shows green in the TUI and red in CI.
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
    rules = request_response_rules(project, request)
    ok = Execution(request, environment, "", HttpResponse(200, [], b'{"total": 1}', 1.0))
    bad = Execution(request, environment, "", HttpResponse(200, [], b'{"other": 1}', 1.0))
    assert passed(evaluate_rules(project, rules, ok))
    assert not passed(evaluate_rules(project, rules, bad))


def test_response_assert_profiles_gate_the_run_path_with_provenance(tmp_path: Path) -> None:
    # M-a: the run path compiles the whole response contract through the single
    # assertion engine, so a response.assert profile gates it exactly as
    # comparo run and comparo exec do — and its rules carry the profile's id.
    (tmp_path / "assert.yaml").write_text(
        "apiVersion: comparo/v1\nkind: AssertionProfile\n"
        "metadata:\n  name: Body\n  id: assert.body\n"
        "spec:\n  rules:\n    - target: body:$.ok\n      op: equals\n      value: true\n",
        encoding="utf-8",
    )
    (tmp_path / "request.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Get\n  id: request.get\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        "  response:\n    assert:\n      $use: assert.body\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: e\n  id: environment.e\n"
        "spec:\n  baseUrl: http://h\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    request = loaded.objects["request.get"]
    environment = loaded.objects["environment.e"]
    assert isinstance(request, Request)
    assert isinstance(environment, Environment)
    rules = request_response_rules(loaded, request)
    assert [sourced.ref.profile for sourced in rules] == ["assert.body"]
    assert [sourced.ref.origin for sourced in rules] == ["profile"]

    good = Execution(request, environment, "", HttpResponse(200, [], b'{"ok": true}', 1.0))
    bad = Execution(request, environment, "", HttpResponse(200, [], b'{"ok": false}', 1.0))
    assert passed(evaluate_rules(loaded, rules, good))
    assert not passed(evaluate_rules(loaded, rules, bad))


def test_a_broken_warn_rule_never_fails_the_run_gate() -> None:
    project, request, environment = _run_contract_fixture()
    warn_rule = AssertionRule(target="latency", op="lte", value=1, severity="warn")
    sourced = _source([warn_rule], "inline", request="request.checkout")
    execution = _contract_execution(environment, request, 200, b"{}")
    results = evaluate_rules(project, sourced, execution)
    assert not results[0].ok
    assert results[0].severity == "warn"
    assert passed(results)  # advisory: visible, never gating


def test_inline_blocks_never_share_a_ref_identity(tmp_path: Path) -> None:
    # Sugar and every inline response.assert block index continuously, so two
    # written rules in different blocks of one request keep distinct identities.
    (tmp_path / "request.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: R\n  id: request.r\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        "  response:\n    status: 200\n"
        "    assert:\n"
        "      - rules:\n          - target: body:$.a\n            op: exists\n"
        "      - rules:\n          - target: body:$.b\n            op: exists\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: e\n  id: environment.e\nspec:\n  baseUrl: http://h\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    request = loaded.objects["request.r"]
    assert isinstance(request, Request)
    rules = request_response_rules(loaded, request)
    refs = [sourced.ref for sourced in rules]
    assert len(refs) == len(set(refs)) == 3  # sugar + two inline blocks, all distinct
    assert [ref.index for ref in refs] == [0, 1, 2]


def test_a_diamond_include_evaluates_once_on_the_run_path(tmp_path: Path) -> None:
    # A ⊂ (B, C) both including D must not judge (or display) D's rule twice —
    # the run path dedupes like the execution planner, keeping D's provenance.
    (tmp_path / "d.yaml").write_text(
        "apiVersion: comparo/v1\nkind: AssertionProfile\n"
        "metadata:\n  name: D\n  id: assert.d\n"
        "spec:\n  rules:\n    - target: status\n      op: equals\n      value: 200\n",
        encoding="utf-8",
    )
    for name in ("b", "c"):
        (tmp_path / f"{name}.yaml").write_text(
            "apiVersion: comparo/v1\nkind: AssertionProfile\n"
            f"metadata:\n  name: {name.upper()}\n  id: assert.{name}\n"
            "spec:\n  include:\n    - $use: assert.d\n",
            encoding="utf-8",
        )
    (tmp_path / "request.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: R\n  id: request.r\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        "  response:\n    assert:\n      - $use: assert.b\n      - $use: assert.c\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: e\n  id: environment.e\nspec:\n  baseUrl: http://h\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    request = loaded.objects["request.r"]
    assert isinstance(request, Request)
    rules = request_response_rules(loaded, request)
    assert len(rules) == 1  # one written rule, one evaluation
    assert rules[0].ref.profile == "assert.d"  # provenance stays with the base
