"""Tests for the gate helpers and the built-in reporters (projections of the record)."""

import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from comparo.adapters.reporters import REPORTERS
from comparo.core.compare import compare_cell
from comparo.core.execute import Execution
from comparo.core.http import HttpResponse
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.report import diff_gate
from comparo.core.report import diff_passed
from comparo.core.report import execution_gate
from comparo.core.report import run_gate
from comparo.core.report_builder import record_from_diff
from comparo.core.report_record import ReportRecord
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import select_environment

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def _bits() -> tuple[LoadedProject, Environment, Request]:
    loaded = load_project(SAMPLE)
    env = select_environment(loaded, "local")
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    return loaded, env, request


def _execution(
    request: Request, env: Environment, body: object, *, error: str | None = None
) -> Execution:
    response = None if error else HttpResponse(200, [], json.dumps(body).encode(), 5.0)
    resolved = ResolvedRequest("GET", "http://localhost:8080/json", [], {}, None, [])
    return Execution(request, env, "", response, error, resolved=resolved)


def _record() -> ReportRecord:
    loaded, env, request = _bits()
    same = compare_cell(
        loaded, _execution(request, env, {"a": 1}), _execution(request, env, {"a": 1})
    )
    drift = compare_cell(
        loaded, _execution(request, env, {"x": 1}), _execution(request, env, {"x": 2})
    )
    error = compare_cell(
        loaded, _execution(request, env, {}, error="boom"), _execution(request, env, {})
    )
    return record_from_diff(
        env,
        env,
        [same, drift, error],
        record_id="r",
        created="t",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )


def _hostile_record() -> ReportRecord:
    """A record whose paths/details/errors carry server-controlled XML/Markdown poison."""
    loaded, env, request = _bits()
    drift = compare_cell(
        loaded,
        _execution(request, env, {"x\x00|y": "1|2\nnext \x01 end"}),
        _execution(request, env, {"x\x00|y": "3"}),
    )
    error = compare_cell(
        loaded,
        _execution(request, env, {}, error="boom \x00 | pipe\ntwo"),
        _execution(request, env, {}),
    )
    return record_from_diff(
        env,
        env,
        [drift, error],
        record_id="r",
        created="t",
        tool="comparo 0",
        project=None,
        concurrency=1,
        redact=str,
    )


def test_gate_helpers() -> None:
    assert diff_passed(3, 0, 0) is True
    assert diff_passed(0, 0, 0) is False  # fail closed on an empty run
    assert diff_gate(3, 1, 0) == "FAIL"
    assert diff_gate(3, 0, 1) == "ERROR"
    assert diff_gate(3, 0, 0) == "PASS"
    assert diff_gate(3, 1, 1) == "FAIL"  # a broken rule outranks errors
    assert diff_gate(0, 0, 0) == "FAIL"  # empty fails closed


def test_run_and_execution_gates_share_the_precedence() -> None:
    assert run_gate(0, 0, 3) == "PASS"
    assert run_gate(1, 0, 3) == "FAIL"
    assert run_gate(0, 1, 3) == "ERROR"  # errors are the only failure
    assert run_gate(1, 1, 3) == "FAIL"  # a broken rule outranks errors
    assert run_gate(0, 0, 0) == "FAIL"  # empty fails closed
    assert execution_gate(0, 0, 0, 3) == "PASS"
    assert execution_gate(1, 0, 1, 3) == "FAIL"  # drift outranks the errored cell
    assert execution_gate(0, 1, 1, 3) == "FAIL"  # a failed assertion does too
    assert execution_gate(0, 0, 1, 3) == "ERROR"
    assert execution_gate(0, 0, 0, 0) == "FAIL"  # empty plan fails closed


def test_json_reporter_emits_the_full_record() -> None:
    document = json.loads(REPORTERS["json"].render(_record()))
    assert document["schemaVersion"] == 1
    assert document["kind"] == "diff"
    assert document["summary"]["fields"]["drift"] == 1
    assert document["summary"]["cellVerdicts"]["errors"] == 1
    assert document["summary"]["gate"] == "FAIL"  # the drift outranks the errored cell


def test_junit_reporter_is_valid_xml() -> None:
    tree = ElementTree.fromstring(REPORTERS["junit"].render(_record()))
    assert tree.tag == "testsuites"
    assert tree.get("failures") == "1"
    assert tree.get("errors") == "1"


def test_junit_reporter_sanitizes_control_chars() -> None:
    tree = ElementTree.fromstring(REPORTERS["junit"].render(_hostile_record()))
    failure = tree.find(".//failure")
    assert failure is not None
    assert "\x00" not in (failure.text or "")
    assert "\x01" not in (failure.text or "")
    error = tree.find(".//error")
    assert error is not None
    assert "\x00" not in (error.get("message") or "")


def test_markdown_reporter_escapes_pipes_and_newlines() -> None:
    rendered = REPORTERS["markdown"].render(_hostile_record())
    rows = [line for line in rendered.splitlines() if line.startswith("|")]
    # Header + separator + exactly one row per cell: a raw pipe or newline in a
    # field path / detail / error must not shatter the table into extra rows.
    assert len(rows) == 4
    assert all(row.count("|") - row.count("\\|") == 5 for row in rows[2:])
    assert "\\|" in rendered


def test_sarif_reporter_is_valid() -> None:
    document = json.loads(REPORTERS["sarif"].render(_record()))
    assert document["version"] == "2.1.0"
    assert len(document["runs"][0]["results"]) >= 2


def test_sarif_reporter_has_physical_location() -> None:
    document = json.loads(REPORTERS["sarif"].render(_record()))
    results = document["runs"][0]["results"]
    assert results
    for result in results:
        location = result["locations"][0]
        assert location["physicalLocation"]["artifactLocation"]["uri"] == "comparo.yaml"
        assert location["logicalLocations"][0]["fullyQualifiedName"]


def test_markdown_reporter_has_gate() -> None:
    rendered = REPORTERS["markdown"].render(_record())
    assert "gate: **FAIL**" in rendered  # drift outranks the errored cell
    assert "request.get-json" in rendered
