"""Tests for the run report and built-in reporters."""

import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from comparo.adapters.reporters import REPORTERS
from comparo.core.compare import CellDiff
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.loader import load_project
from comparo.core.models import Request
from comparo.core.report import RunReport
from comparo.core.report import build_report

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def _report() -> RunReport:
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    cells = [
        CellDiff(request, "", [FieldDiff("$", State.SAME, "exact")]),
        CellDiff(request, "locale=en-US", [FieldDiff("$.x", State.DRIFT, "exact", "1 → 2")]),
        CellDiff(request, "", [], "boom"),
    ]
    return build_report("local", "prod", cells)


def _hostile_report() -> RunReport:
    """Build a report whose paths/details carry server-controlled XML/Markdown poison."""
    loaded = load_project(SAMPLE)
    request = loaded.objects["request.get-json"]
    assert isinstance(request, Request)
    cells = [
        CellDiff(
            request,
            "region=a|b",
            [FieldDiff("$.x\x00|y", State.DRIFT, "exact", "1|2\nnext \x01 end → 3")],
        ),
        CellDiff(request, "", [], "boom \x00 | pipe\ntwo"),
    ]
    return build_report("local", "prod", cells)


def test_build_report_counts() -> None:
    report = _report()
    assert report.same == 1
    assert report.drift == 1
    assert report.errors == 1
    assert report.passed is False


def test_json_reporter_is_valid_json() -> None:
    document = json.loads(REPORTERS["json"].render(_report()))
    assert document["summary"]["drift"] == 1
    assert document["summary"]["passed"] is False


def test_junit_reporter_is_valid_xml() -> None:
    tree = ElementTree.fromstring(REPORTERS["junit"].render(_report()))
    assert tree.tag == "testsuites"
    assert tree.get("failures") == "1"
    assert tree.get("errors") == "1"


def test_junit_reporter_sanitizes_control_chars() -> None:
    tree = ElementTree.fromstring(REPORTERS["junit"].render(_hostile_report()))
    failure = tree.find(".//failure")
    assert failure is not None
    assert "\x00" not in (failure.text or "")
    assert "\x01" not in (failure.text or "")
    error = tree.find(".//error")
    assert error is not None
    assert "\x00" not in (error.get("message") or "")


def test_markdown_reporter_escapes_pipes_and_newlines() -> None:
    rendered = REPORTERS["markdown"].render(_hostile_report())
    rows = [line for line in rendered.splitlines() if line.startswith("|")]
    # Header + separator + exactly one row per cell: a raw pipe or newline in a
    # field path / detail / error must not shatter the table into extra rows.
    assert len(rows) == 4
    assert all(row.count("|") - row.count("\\|") == 5 for row in rows[2:])
    assert "\\|" in rendered


def test_sarif_reporter_is_valid() -> None:
    document = json.loads(REPORTERS["sarif"].render(_report()))
    assert document["version"] == "2.1.0"
    assert len(document["runs"][0]["results"]) >= 2


def test_sarif_reporter_has_physical_location() -> None:
    document = json.loads(REPORTERS["sarif"].render(_report()))
    results = document["runs"][0]["results"]
    assert results
    for result in results:
        location = result["locations"][0]
        assert location["physicalLocation"]["artifactLocation"]["uri"] == "comparo.yaml"
        assert location["logicalLocations"][0]["fullyQualifiedName"]


def test_markdown_reporter_has_gate() -> None:
    rendered = REPORTERS["markdown"].render(_report())
    assert "gate: **FAIL**" in rendered
    assert "request.get-json" in rendered
