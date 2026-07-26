"""Shared artifact-backed analysis lens vocabulary and summaries."""

from __future__ import annotations

from enum import StrEnum

from comparo.application.results import ResultEntity
from comparo.application.results import ResultFact
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.tui.components.status import status_for_outcome


class ResultIndexKind(StrEnum):
    """Artifact-backed analysis lenses shared by live and stored runs."""

    ISSUES = "issues"
    REQUESTS = "requests"
    RULES = "rules"
    FIELDS = "fields"
    TIMELINE = "timeline"

    @classmethod
    def for_reference(cls, reference: ResultReference) -> ResultIndexKind:
        """Return the product-facing lens containing one typed result reference."""
        return {
            ResultEntity.EXECUTION: cls.REQUESTS,
            ResultEntity.ASSERTION: cls.RULES,
            ResultEntity.COMPARISON: cls.ISSUES,
            ResultEntity.DIFF: cls.RULES,
        }[reference.entity]


def analysis_lenses() -> tuple[ResultIndexKind, ...]:
    """Return the stable product-facing lens order."""
    return (
        ResultIndexKind.ISSUES,
        ResultIndexKind.REQUESTS,
        ResultIndexKind.RULES,
        ResultIndexKind.FIELDS,
        ResultIndexKind.TIMELINE,
    )


def is_issue(fact: ResultFact) -> bool:
    """Return whether an artifact-owned fact belongs in the Issues lens."""
    return status_for_outcome(fact.outcome).value in {
        "advisory",
        "failure",
        "error",
    } or bool(fact.issues)


def lens_heading(kind: ResultIndexKind) -> str:
    """Return the hierarchy heading for one product-facing lens."""
    return {
        ResultIndexKind.ISSUES: "IMPORTANT FINDINGS",
        ResultIndexKind.REQUESTS: "REQUEST EXECUTIONS",
        ResultIndexKind.RULES: "RULE EVALUATIONS",
        ResultIndexKind.FIELDS: "FIELD EVIDENCE",
        ResultIndexKind.TIMELINE: "EXECUTION TIMELINE",
    }.get(kind, kind.value.upper())


def lens_summary(projection: ResultProjection, kind: ResultIndexKind) -> str:
    """Summarize one lens without recomputing verdict or fact outcome."""
    if kind is ResultIndexKind.ISSUES:
        count = sum(is_issue(item) for item in (*projection.assertions, *projection.diffs))
        return f"{count} findings that can affect the verdict"
    if kind is ResultIndexKind.REQUESTS:
        return f"{len(projection.executions)} environment executions"
    if kind is ResultIndexKind.RULES:
        return f"{len(projection.assertions)} assertions · {len(projection.diffs)} diffs"
    if kind is ResultIndexKind.FIELDS:
        evidence = sum(len(item.evidence) for item in (*projection.assertions, *projection.diffs))
        return f"{evidence} sanitized evidence locations"
    return f"{len(projection.executions)} ordered request occurrences"


__all__ = (
    "ResultIndexKind",
    "analysis_lenses",
    "is_issue",
    "lens_heading",
    "lens_summary",
)
