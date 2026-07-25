"""Gate-faithful JUnit XML projection of one RunArtifact."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.model import RunArtifact
from comparo.reporters._presentation import safe_plain
from comparo.reporters.contracts import RenderedReport
from comparo.reporters.contracts import ReportError
from comparo.reporters.contracts import ReportErrorCode
from comparo.reporters.contracts import ReportFormat
from comparo.reporters.contracts import complete_report


@dataclass(frozen=True, slots=True)
class _GateCase:
    """One gate dimension without assertion/diff reinterpretation."""

    name: str
    failure: str | None = None
    skipped: str | None = None


def render_junit_report(artifact: RunArtifact, *, maximum_bytes: int) -> RenderedReport:
    """Render gate dimensions as JUnit testcases, never individual rule outcomes."""
    if type(artifact) is not RunArtifact:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    try:
        cases = _gate_cases(artifact)
        failures = sum(item.failure is not None for item in cases)
        skipped = sum(item.skipped is not None for item in cases)
        suite = ElementTree.Element(
            "testsuite",
            {
                "name": "comparo.gate",
                "tests": str(len(cases)),
                "failures": str(failures),
                "errors": "0",
                "skipped": str(skipped),
            },
        )
        properties = ElementTree.SubElement(suite, "properties")
        for name, value in (
            ("artifact.apiVersion", artifact.api_version),
            ("artifact.id", artifact.metadata.artifact_id),
            ("run.id", artifact.metadata.run_id),
            ("gate.verdict", artifact.gate.verdict.value),
            ("completion.kind", artifact.completion.kind.value),
        ):
            ElementTree.SubElement(
                properties,
                "property",
                {"name": name, "value": safe_plain(value)},
            )
        for item in cases:
            case = ElementTree.SubElement(
                suite,
                "testcase",
                {"classname": "comparo.gate", "name": safe_plain(item.name)},
            )
            if item.failure is not None:
                ElementTree.SubElement(
                    case,
                    "failure",
                    {"type": "comparo.gate", "message": safe_plain(item.failure)},
                )
            elif item.skipped is not None:
                ElementTree.SubElement(
                    case,
                    "skipped",
                    {"message": safe_plain(item.skipped)},
                )
        content = ElementTree.tostring(
            suite,
            encoding="utf-8",
            xml_declaration=True,
            short_empty_elements=True,
        )
    except ReportError:
        raise
    except Exception as error:
        raise ReportError(ReportErrorCode.RENDER_FAILED) from error
    return complete_report(
        format=ReportFormat.JUNIT,
        media_type="application/junit+xml",
        extension=".xml",
        content=content,
        maximum_bytes=maximum_bytes,
    )


def _gate_cases(artifact: RunArtifact) -> tuple[_GateCase, ...]:
    gate = artifact.gate
    if gate.verdict is ArtifactGateVerdict.NOT_REQUESTED:
        return (_GateCase(name="gate-verdict", skipped="gate not requested"),)
    cases: list[_GateCase] = []
    reason = ", ".join(safe_plain(item) for item in gate.reasons)
    cases.append(
        _GateCase(
            name="gate-verdict",
            failure=(reason or "gate failed")
            if gate.verdict is ArtifactGateVerdict.FAILED
            else None,
        )
    )
    for assessment in gate.assessments:
        allowed_by_name = {item.name: item.value for item in assessment.allowed}
        for actual in assessment.actual:
            allowed = allowed_by_name[actual.name]
            failure = (
                f"actual {actual.value} exceeds allowed {allowed}"
                if actual.value > allowed
                else None
            )
            cases.append(
                _GateCase(
                    name=f"{assessment.severity.value}.{actual.name}",
                    failure=failure,
                )
            )
    return tuple(cases)
