"""Built-in reporters: render a RunReport to JUnit, SARIF, JSON, or markdown."""

import dataclasses
import json
import xml.etree.ElementTree as ElementTree

from comparo.core.report import Reporter
from comparo.core.report import RunReport

_ICONS = {"same": "✅ same", "drift": "❌ drift", "error": "⚠️ error"}


class JsonReporter:
    """Renders a report as pretty JSON."""

    filename = "report.json"

    def render(self, report: RunReport) -> str:
        """Render *report* as JSON with a summary block."""
        return json.dumps(_as_dict(report), indent=2, ensure_ascii=False)


class JUnitReporter:
    """Renders a report as a JUnit ``testsuites`` document."""

    filename = "junit.xml"

    def render(self, report: RunReport) -> str:
        """Render *report* as JUnit XML (drift is a failure, error is an error)."""
        counts = {
            "tests": str(len(report.cells)),
            "failures": str(report.drift),
            "errors": str(report.errors),
        }
        suites = ElementTree.Element("testsuites", counts)
        suite = ElementTree.SubElement(suites, "testsuite", {"name": "comparo diff", **counts})
        for cell in report.cells:
            case = ElementTree.SubElement(
                suite, "testcase", name=_name(cell.request_id, cell.cell_key), classname="comparo"
            )
            if cell.state == "drift":
                failure = ElementTree.SubElement(case, "failure", message="drift")
                failure.text = "\n".join(f"{drift.path} {drift.detail}" for drift in cell.drifts)
            elif cell.state == "error":
                ElementTree.SubElement(case, "error", message=cell.error or "error")
        return ElementTree.tostring(suites, encoding="unicode", xml_declaration=True)


class SarifReporter:
    """Renders a report as a SARIF 2.1.0 log for code-scanning."""

    filename = "comparo.sarif"

    def render(self, report: RunReport) -> str:
        """Render *report* as SARIF, one result per drift or error."""
        results: list[dict[str, object]] = []
        for cell in report.cells:
            location = _name(cell.request_id, cell.cell_key)
            if cell.state == "drift":
                for drift in cell.drifts:
                    results.append(_result(f"{location}: {drift.path} {drift.detail}", location))
            elif cell.state == "error":
                results.append(_result(f"{location}: {cell.error}", location))
        document = {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "comparo",
                            "informationUri": "https://github.com/wbenbihi/comparo",
                            "rules": [{"id": "drift"}],
                        }
                    },
                    "results": results,
                }
            ],
        }
        return json.dumps(document, indent=2, ensure_ascii=False)


class MarkdownReporter:
    """Renders a report as a markdown summary (e.g. a GitHub step summary)."""

    filename = "summary.md"

    def render(self, report: RunReport) -> str:
        """Render *report* as a markdown table with a gate line."""
        lines = [
            f"## comparo diff · {report.baseline} ⇄ {report.candidate}",
            "",
            "| request | cell | result | detail |",
            "|---|---|---|---|",
        ]
        for cell in report.cells:
            detail = ""
            if cell.state == "drift":
                detail = "<br>".join(f"`{drift.path}` {drift.detail}" for drift in cell.drifts)
            elif cell.state == "error":
                detail = cell.error or ""
            lines.append(
                f"| `{cell.request_id}` | {cell.cell_key} | {_ICONS[cell.state]} | {detail} |"
            )
        gate = "**PASS** ✅" if report.passed else "**FAIL** ❌"
        summary = (
            f"**{report.same} same · {report.drift} drift · "
            f"{report.errors} error · {report.skipped} skipped** — gate: {gate}"
        )
        lines += ["", summary]
        return "\n".join(lines)


REPORTERS: dict[str, Reporter] = {
    "json": JsonReporter(),
    "junit": JUnitReporter(),
    "sarif": SarifReporter(),
    "markdown": MarkdownReporter(),
}


def _name(request_id: str, cell_key: str) -> str:
    return f"{request_id} [{cell_key}]" if cell_key else request_id


def _as_dict(report: RunReport) -> dict[str, object]:
    return {
        "baseline": report.baseline,
        "candidate": report.candidate,
        "summary": {
            "same": report.same,
            "drift": report.drift,
            "errors": report.errors,
            "skipped": report.skipped,
            "passed": report.passed,
        },
        "cells": [dataclasses.asdict(cell) for cell in report.cells],
    }


def _result(text: str, location: str) -> dict[str, object]:
    return {
        "ruleId": "drift",
        "level": "error",
        "message": {"text": text},
        "locations": [{"logicalLocations": [{"fullyQualifiedName": location}]}],
    }
