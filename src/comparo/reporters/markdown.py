"""Fixed-section human-readable Markdown projection of one RunArtifact."""

from __future__ import annotations

from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.facts import ArtifactFact
from comparo.core.artifacts.model import RunArtifact
from comparo.reporters._presentation import markdown_cell
from comparo.reporters.contracts import RenderedReport
from comparo.reporters.contracts import ReportError
from comparo.reporters.contracts import ReportErrorCode
from comparo.reporters.contracts import ReportFormat
from comparo.reporters.contracts import complete_report

_NON_ADVERSE = frozenset(
    {
        ArtifactFactOutcome.SATISFIED,
        ArtifactFactOutcome.EQUIVALENT,
        ArtifactFactOutcome.SKIPPED,
        ArtifactFactOutcome.NOT_EVALUATED,
    }
)


def render_markdown_report(artifact: RunArtifact, *, maximum_bytes: int) -> RenderedReport:
    """Render a stable overview without embedding detached body blob contents."""
    if type(artifact) is not RunArtifact:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    content = b""
    failed = False
    try:
        lines = [
            "# Comparo report",
            "",
            "## Overview",
            "",
            "| Field | Value |",
            "|---|---|",
            _row("Artifact", artifact.metadata.artifact_id),
            _row("Run", artifact.metadata.run_id),
            _row("Completion", artifact.completion.kind.value),
            _row("Gate", artifact.gate.verdict.value),
            _row("Artifact API", artifact.api_version),
            _row("Configuration API", artifact.metadata.configuration_api_version),
            "",
            "## Gate",
            "",
        ]
        lines.extend(_gate_lines(artifact))
        lines.extend(("", "## Coverage", ""))
        lines.extend(_coverage_lines(artifact))
        lines.extend(("", "## Executions", ""))
        lines.extend(_execution_lines(artifact))
        lines.extend(("", "## Comparisons", ""))
        lines.extend(_comparison_lines(artifact))
        lines.extend(("", "## Findings", ""))
        lines.extend(_finding_lines(artifact))
        lines.extend(("", "## Budgets", ""))
        lines.extend(_budget_lines(artifact))
        content = ("\n".join(lines) + "\n").encode("utf-8")
    except ReportError:
        raise
    except Exception:  # noqa: BLE001 - reporter boundary exposes only a closed safe code
        failed = True
    if failed:
        raise ReportError(ReportErrorCode.RENDER_FAILED)
    return complete_report(
        format=ReportFormat.MARKDOWN,
        media_type="text/markdown; charset=utf-8",
        extension=".md",
        content=content,
        maximum_bytes=maximum_bytes,
    )


def _gate_lines(artifact: RunArtifact) -> list[str]:
    lines = [
        "| Severity | Dimension | Actual | Allowed |",
        "|---|---|---:|---:|",
    ]
    for assessment in artifact.gate.assessments:
        allowed = {item.name: item.value for item in assessment.allowed}
        lines.extend(
            _row(assessment.severity.value, item.name, item.value, allowed[item.name])
            for item in assessment.actual
        )
    if not artifact.gate.assessments:
        lines.append(_row("—", "gate not requested or no count thresholds", "—", "—"))
    if artifact.gate.reasons:
        lines.extend(("", "Reasons: " + ", ".join(map(markdown_cell, artifact.gate.reasons))))
    return lines


def _coverage_lines(artifact: RunArtifact) -> list[str]:
    lines = ["| Source | Execution | Extent | Reason |", "|---|---|---|---|"]
    lines.extend(
        _row(item.source, item.execution_id or "run", item.extent.value, item.reason.value)
        for item in artifact.coverage_issues
    )
    if not artifact.coverage_issues:
        lines.append(_row("all retained sources", "—", "complete", "—"))
    return lines


def _execution_lines(artifact: RunArtifact) -> list[str]:
    lines = [
        "| # | Subject | Environment | Outcome | Attempts | Assertions |",
        "|---:|---|---|---|---:|---:|",
    ]
    lines.extend(
        _row(
            item.ordinal,
            item.subject.resource_id,
            item.environment.resource_id,
            item.outcome.value,
            len(item.attempts),
            len(item.assertions),
        )
        for item in artifact.executions
    )
    return lines


def _comparison_lines(artifact: RunArtifact) -> list[str]:
    lines = [
        "| # | Subject | Baseline execution | Candidate execution | Outcome | Diffs |",
        "|---:|---|---|---|---|---:|",
    ]
    lines.extend(
        _row(
            item.ordinal,
            item.subject.resource_id,
            item.baseline_execution_id,
            item.candidate_execution_id,
            item.outcome.value,
            len(item.diffs),
        )
        for item in artifact.comparisons
    )
    return lines


def _finding_lines(artifact: RunArtifact) -> list[str]:
    lines = [
        "| Engine | Rule | Severity | Outcome | Evidence | Omitted |",
        "|---|---|---|---|---:|---:|",
    ]
    facts = tuple(_facts(artifact))
    adverse = tuple(item for item in facts if item.outcome not in _NON_ADVERSE)
    lines.extend(
        _row(
            item.engine.value,
            item.effective_rule_id,
            item.severity.value,
            item.outcome.value,
            len(item.evidence),
            item.evidence_omitted,
        )
        for item in adverse
    )
    if not adverse:
        lines.append(_row("—", "—", "—", "no adverse findings", 0, 0))
    return lines


def _budget_lines(artifact: RunArtifact) -> list[str]:
    lines = ["| Resource | Scope | Used | Limit | Exhausted |", "|---|---|---:|---:|---|"]
    lines.extend(
        _row(
            item.resource,
            item.scope,
            _amount(item.used.numerator, item.used.denominator, item.used.unit.value),
            _amount(item.limit.numerator, item.limit.denominator, item.limit.unit.value),
            "yes" if item.exhausted else "no",
        )
        for item in artifact.budgets
    )
    if not artifact.budgets:
        lines.append(_row("—", "—", "—", "—", "no"))
    return lines


def _facts(artifact: RunArtifact) -> tuple[ArtifactFact, ...]:
    return (
        *(fact for execution in artifact.executions for fact in execution.assertions),
        *(fact for comparison in artifact.comparisons for fact in comparison.diffs),
    )


def _amount(numerator: int, denominator: int, unit: str) -> str:
    exact = str(numerator) if denominator == 1 else f"{numerator}/{denominator}"
    return f"{exact} {unit}"


def _row(*values: object) -> str:
    return "| " + " | ".join(markdown_cell(item) for item in values) + " |"
