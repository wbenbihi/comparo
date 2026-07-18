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
                suite,
                "testcase",
                name=_xml_safe(_name(cell.request_id, cell.cell_key)),
                classname="comparo",
            )
            if cell.state == "drift":
                failure = ElementTree.SubElement(case, "failure", message="drift")
                failure.text = _xml_safe(
                    "\n".join(f"{drift.path} {drift.detail}" for drift in cell.drifts)
                )
            elif cell.state == "error":
                ElementTree.SubElement(case, "error", message=_xml_safe(cell.error or "error"))
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
                detail = "<br>".join(
                    f"`{_md_cell(drift.path)}` {_md_cell(drift.detail)}" for drift in cell.drifts
                )
            elif cell.state == "error":
                detail = _md_cell(cell.error or "")
            lines.append(
                f"| `{_md_cell(cell.request_id)}` | {_md_cell(cell.cell_key)} "
                f"| {_ICONS[cell.state]} | {detail} |"
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
        # GitHub code-scanning drops any result without a
        # ``physicalLocation.artifactLocation.uri``. A RunReport has no source
        # file per cell, so this is a best-effort synthetic anchor: the project
        # config file, with the request/cell carried as the logical location.
        "locations": [
            {
                "physicalLocation": {"artifactLocation": {"uri": "comparo.yaml"}},
                "logicalLocations": [{"fullyQualifiedName": location}],
            }
        ],
    }


def _xml_safe(text: str) -> str:
    """Drop characters that are not well-formed in XML 1.0.

    ElementTree writes control characters verbatim even though they make the
    document not-well-formed, and a server controls the JSON keys/values that
    reach a drift path or an error message. Keep tab/newline/carriage-return and
    drop the rest of the forbidden range so the JUnit document stays parseable.
    """
    return "".join(char for char in text if _xml_ok(ord(char)))


def _xml_ok(code: int) -> bool:
    """Whether *code* is a codepoint allowed in a well-formed XML 1.0 document."""
    if code in (0x09, 0x0A, 0x0D):
        return True
    if 0x20 <= code <= 0xD7FF:
        return True
    if 0xE000 <= code <= 0xFFFD:
        return True
    return 0x10000 <= code <= 0x10FFFF


def _md_cell(text: str) -> str:
    """Escape *text* for a Markdown table cell.

    A raw ``|`` starts a new column and a newline ends the row, so a server-
    controlled field path or detail could shatter the table; escape the pipe and
    fold every newline into a ``<br>``.
    """
    escaped = text.replace("|", "\\|")
    for newline in ("\r\n", "\r", "\n"):
        escaped = escaped.replace(newline, "<br>")
    return escaped
