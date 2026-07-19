"""Built-in reporters: project a :class:`ReportRecord` to JUnit, SARIF, JSON, or markdown.

The reporters are pure projections of the one saved artifact — the same
``ReportRecord`` the archive stores — so a CI report can never disagree with a
replayed one. A CI *finding* is a drifted field or a failed error-severity
assertion; a cell that only warns or passes is green.
"""

import json
import xml.etree.ElementTree as ElementTree
from typing import Protocol

import msgspec

from comparo.core.report_record import Cell
from comparo.core.report_record import FieldDiffRecord
from comparo.core.report_record import ReportRecord

_ICONS = {
    "same": "✅ same",
    "pass": "✅ pass",
    "drift": "❌ drift",
    "fail": "❌ fail",
    "error": "⚠️ error",
}


class Reporter(Protocol):
    """A named renderer that projects a report record to a file's text."""

    filename: str

    def render(self, record: ReportRecord) -> str:
        """Render *record* to this reporter's output format."""
        ...


class JsonReporter:
    """Emits the full report record as pretty JSON — the machine-readable artifact."""

    filename = "report.json"

    def render(self, record: ReportRecord) -> str:
        """Render the whole *record* as indented JSON (camelCase keys)."""
        return json.dumps(msgspec.to_builtins(record), indent=2, ensure_ascii=False)


class JUnitReporter:
    """Renders a report as a JUnit ``testsuites`` document."""

    filename = "junit.xml"

    def render(self, record: ReportRecord) -> str:
        """Render *record* as JUnit XML (a drift/fail is a failure, an error is an error)."""
        failures = sum(1 for cell in record.cells if cell.verdict in ("drift", "fail"))
        errors = sum(1 for cell in record.cells if cell.verdict == "error")
        counts = {"tests": str(len(record.cells)), "failures": str(failures), "errors": str(errors)}
        suites = ElementTree.Element("testsuites", counts)
        suite = ElementTree.SubElement(
            suites, "testsuite", {"name": f"comparo {record.kind}", **counts}
        )
        for cell in record.cells:
            case = ElementTree.SubElement(
                suite, "testcase", name=_xml_safe(_name(cell)), classname="comparo"
            )
            if cell.verdict == "error":
                message = cell.sides.baseline.error or "error"
                ElementTree.SubElement(case, "error", message=_xml_safe(message))
            elif cell.verdict in ("drift", "fail"):
                failure = ElementTree.SubElement(case, "failure", message=cell.verdict)
                failure.text = _xml_safe(
                    "\n".join(f"{path} {detail}" for path, detail in _findings(cell))
                )
        return ElementTree.tostring(suites, encoding="unicode", xml_declaration=True)


class SarifReporter:
    """Renders a report as a SARIF 2.1.0 log for code-scanning."""

    filename = "comparo.sarif"

    def render(self, record: ReportRecord) -> str:
        """Render *record* as SARIF, one result per drift, failed assertion, or error."""
        results: list[dict[str, object]] = []
        for cell in record.cells:
            location = _name(cell)
            if cell.verdict == "error":
                results.append(_result(f"{location}: {cell.sides.baseline.error}", location))
                continue
            for path, detail in _findings(cell):
                results.append(_result(f"{location}: {path} {detail}", location))
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

    def render(self, record: ReportRecord) -> str:
        """Render *record* as a markdown table with a gate line."""
        environments = record.invocation.environments
        title = f"comparo {record.kind} · {environments.baseline.name}"
        if environments.candidate is not None:
            title += f" ⇄ {environments.candidate.name}"
        lines = [f"## {title}", "", "| request | cell | result | detail |", "|---|---|---|---|"]
        for cell in record.cells:
            if cell.verdict == "error":
                detail = _md_cell(cell.sides.baseline.error or "")
            else:
                detail = "<br>".join(
                    f"`{_md_cell(path)}` {_md_cell(text)}" for path, text in _findings(cell)
                )
            icon = _ICONS.get(cell.verdict, cell.verdict)
            lines.append(
                f"| `{_md_cell(cell.request_id)}` | {_md_cell(cell.variant)} | {icon} | {detail} |"
            )
        lines += ["", _summary_line(record)]
        return "\n".join(lines)


REPORTERS: dict[str, Reporter] = {
    "json": JsonReporter(),
    "junit": JUnitReporter(),
    "sarif": SarifReporter(),
    "markdown": MarkdownReporter(),
}


def _name(cell: Cell) -> str:
    return f"{cell.request_id} [{cell.variant}]" if cell.variant else cell.request_id


def _diff_detail(field: FieldDiffRecord) -> str:
    """A compact ``before → after`` for a drift, or the mode note for a skip."""
    if field.state == "skip":
        return f"skipped ({field.mode})"
    before = json.dumps(field.baseline, ensure_ascii=False)
    after = json.dumps(field.candidate, ensure_ascii=False)
    return f"{before} → {after}"


def _findings(cell: Cell) -> list[tuple[str, str]]:
    """The cell's CI findings: each drifted field and each failed error assertion."""
    findings: list[tuple[str, str]] = []
    if cell.comparison is not None:
        for field in cell.comparison.fields:
            if field.state == "drift":
                findings.append((field.path, _diff_detail(field)))
    for label, side in (("baseline", cell.sides.baseline), ("candidate", cell.sides.candidate)):
        if side is None or side.assertions is None:
            continue
        for assertion in side.assertions:
            if not assertion.ok and assertion.severity == "error":
                findings.append((f"assert[{label}] {assertion.target}", assertion.detail or ""))
    return findings


def _summary_line(record: ReportRecord) -> str:
    gate = "**PASS** ✅" if record.summary.gate == "PASS" else f"**{record.summary.gate}** ❌"
    parts: list[str] = []
    if record.summary.diff is not None:
        diff = record.summary.diff
        parts.append(
            f"{diff.same} same · {diff.drift} drift · {diff.error} error · {diff.skipped} skipped"
        )
    if record.summary.assertions is not None:
        asserts = record.summary.assertions
        parts.append(f"{asserts.passed} passed · {asserts.failed} failed · {asserts.warned} warned")
    body = " — ".join(parts) if parts else f"{record.summary.cells} cells"
    return f"**{body}** — gate: {gate}"


def _result(text: str, location: str) -> dict[str, object]:
    return {
        "ruleId": "drift",
        "level": "error",
        "message": {"text": text},
        # GitHub code-scanning drops any result without a
        # ``physicalLocation.artifactLocation.uri``. A report has no source file per
        # cell, so this is a best-effort synthetic anchor: the project config file,
        # with the request/cell carried as the logical location.
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
