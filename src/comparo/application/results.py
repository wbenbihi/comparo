"""Pure body-free result projections shared by interactive frontends.

The independently sanitized :class:`RunArtifact` is the only accepted input.  This module
projects its manifest-resident result facts into immutable navigation models; it never opens a
blob, re-evaluates a fact, or derives a gate verdict.
"""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.facts import ArtifactEvidence
from comparo.core.artifacts.facts import ArtifactExecution
from comparo.core.artifacts.facts import ArtifactFact
from comparo.core.artifacts.http import ArtifactTiming
from comparo.core.artifacts.lifecycle import ArtifactGateCounts
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.values import ArtifactValue


class ResultEntity(StrEnum):
    """Closed addressable result entities used by frontend navigation."""

    EXECUTION = "execution"
    ASSERTION = "assertion"
    COMPARISON = "comparison"
    DIFF = "diff"


class ResultRelation(StrEnum):
    """Typed relationships that a frontend may follow in either direction."""

    ASSERTION = "assertion"
    BASELINE = "baseline"
    CANDIDATE = "candidate"
    DIFF = "diff"


@final
class ResultReference(msgspec.Struct, frozen=True, kw_only=True):
    """Stable address of one projected result entity."""

    entity: ResultEntity
    result_id: str


@final
class ResultCrossLink(msgspec.Struct, frozen=True, kw_only=True):
    """One explicit navigable relationship between projected entities."""

    source: ResultReference
    relation: ResultRelation
    target: ResultReference


@final
class ResultMetric(msgspec.Struct, frozen=True, kw_only=True):
    """One exact artifact-owned metric without presentation formatting."""

    name: str
    value: int


@final
class ResultResource(msgspec.Struct, frozen=True, kw_only=True):
    """Sanitized stable definition identity retained by a result row."""

    namespace: str
    kind: str
    resource_id: str
    fingerprint: str


@final
class ResultFailure(msgspec.Struct, frozen=True, kw_only=True):
    """Structured value-free failure retained for frontend explanation."""

    stage: str
    code: str
    retryable: bool | None


@final
class ResultCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """One sanitized Matrix coordinate for an execution/request row."""

    axis: str
    value: ArtifactValue


@final
class ResultTiming(msgspec.Struct, frozen=True, kw_only=True):
    """One exact measured duration or artifact-owned absence reason."""

    name: str
    nanoseconds: int | None
    reason: str | None


@final
class ResultTimingSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Body-free timing and occurrence summary for one execution."""

    measurements: tuple[ResultTiming, ...]
    attempts: int
    exchanges: int


@final
class ResultNamedValue(msgspec.Struct, frozen=True, kw_only=True):
    """One manifest-resident sanitized evidence value."""

    name: str
    value: ArtifactValue


@final
class ResultEvidence(msgspec.Struct, frozen=True, kw_only=True):
    """Bounded sanitized fact evidence without detached body content."""

    fact_id: str
    outcome: str
    location: str
    values: tuple[ResultNamedValue, ...]


@final
class ResultFact(msgspec.Struct, frozen=True, kw_only=True):
    """Assertion or diff fact projected without reinterpretation."""

    reference: ResultReference
    owner: ResultReference
    engine: str
    rule: ResultResource
    rule_id: str
    effective_rule_id: str
    severity: str
    outcome: str
    counts: tuple[ResultMetric, ...]
    coverage: tuple[ResultMetric, ...]
    evidence: tuple[ResultEvidence, ...]
    evidence_omitted: int
    issues: tuple[ResultFailure, ...]


@final
class ExecutionResultRow(msgspec.Struct, frozen=True, kw_only=True):
    """One Environment execution row for a Request or future subject kind."""

    ordinal: int
    reference: ResultReference
    subject: ResultResource
    environment: ResultResource
    cell_id: str
    trial_id: str
    coordinates: tuple[ResultCoordinate, ...]
    outcome: str
    failure: ResultFailure | None
    timing: ResultTimingSummary
    assertions: tuple[ResultReference, ...]
    comparisons: tuple[ResultReference, ...]


@final
class ComparisonResultRow(msgspec.Struct, frozen=True, kw_only=True):
    """One directed comparison/diff row with exact side relationships."""

    ordinal: int
    reference: ResultReference
    subject: ResultResource
    cell_id: str
    trial_id: str
    outcome: str
    baseline: ResultReference
    candidate: ResultReference
    unavailable: tuple[ResultReference, ...]
    diffs: tuple[ResultReference, ...]


@final
class ResultGateAssessment(msgspec.Struct, frozen=True, kw_only=True):
    """Artifact-owned gate threshold facts, retained without evaluating them."""

    severity: str
    actual: tuple[ResultMetric, ...]
    allowed: tuple[ResultMetric, ...]


@final
class ResultSummary(msgspec.Struct, frozen=True, kw_only=True):
    """Run header and exact artifact-owned completion, gate, and outcome summaries."""

    artifact_api_version: str
    artifact_id: str
    run_id: str
    invocation_id: str
    created_unix_nanoseconds: int
    project: ResultResource
    completion: str
    completion_instant_unix_nanoseconds: int | None
    completion_reasons: tuple[str, ...]
    completion_stage: str | None
    completion_code: str | None
    gate_verdict: str
    gate_profile: ResultResource | None
    gate_reasons: tuple[str, ...]
    gate_coverage: tuple[ResultMetric, ...]
    gate_assessments: tuple[ResultGateAssessment, ...]
    executions: tuple[ResultMetric, ...]
    comparisons: tuple[ResultMetric, ...]
    assertions: tuple[ResultMetric, ...]
    diffs: tuple[ResultMetric, ...]
    coverage_issues: int


@final
class ResultProjection(msgspec.Struct, frozen=True, kw_only=True):
    """Complete manifest-only result model shared by Compare and Report."""

    summary: ResultSummary
    executions: tuple[ExecutionResultRow, ...]
    assertions: tuple[ResultFact, ...]
    comparisons: tuple[ComparisonResultRow, ...]
    diffs: tuple[ResultFact, ...]
    links: tuple[ResultCrossLink, ...]


def project_results(artifact: RunArtifact) -> ResultProjection:
    """Project one sanitized artifact without loading bodies or recomputing outcomes."""
    if type(artifact) is not RunArtifact:
        raise TypeError("result projection requires RunArtifact")

    execution_references = {
        item.execution_id: ResultReference(
            entity=ResultEntity.EXECUTION,
            result_id=item.execution_id,
        )
        for item in artifact.executions
    }
    comparison_references = {
        item.comparison_id: ResultReference(
            entity=ResultEntity.COMPARISON,
            result_id=item.comparison_id,
        )
        for item in artifact.comparisons
    }
    comparisons_by_execution: dict[str, list[ResultReference]] = {
        item.execution_id: [] for item in artifact.executions
    }
    for item in artifact.comparisons:
        reference = comparison_references[item.comparison_id]
        comparisons_by_execution[item.baseline_execution_id].append(reference)
        comparisons_by_execution[item.candidate_execution_id].append(reference)

    assertions = tuple(
        _fact(
            fact,
            owner=execution_references[execution.execution_id],
            entity=ResultEntity.ASSERTION,
        )
        for execution in artifact.executions
        for fact in execution.assertions
    )
    diffs = tuple(
        _fact(
            fact,
            owner=comparison_references[comparison.comparison_id],
            entity=ResultEntity.DIFF,
        )
        for comparison in artifact.comparisons
        for fact in comparison.diffs
    )
    executions = tuple(
        _execution_row(
            item,
            reference=execution_references[item.execution_id],
            comparisons=tuple(comparisons_by_execution[item.execution_id]),
        )
        for item in artifact.executions
    )
    comparisons = tuple(
        ComparisonResultRow(
            ordinal=item.ordinal,
            reference=comparison_references[item.comparison_id],
            subject=_resource(item.subject),
            cell_id=item.cell_id,
            trial_id=item.trial_id,
            outcome=item.outcome.value,
            baseline=execution_references[item.baseline_execution_id],
            candidate=execution_references[item.candidate_execution_id],
            unavailable=tuple(
                execution_references[execution_id]
                for execution_id in item.unavailable_execution_ids
            ),
            diffs=tuple(
                ResultReference(entity=ResultEntity.DIFF, result_id=fact.fact_id)
                for fact in item.diffs
            ),
        )
        for item in artifact.comparisons
    )
    return ResultProjection(
        summary=_summary(artifact),
        executions=executions,
        assertions=assertions,
        comparisons=comparisons,
        diffs=diffs,
        links=_links(artifact, execution_references, comparison_references),
    )


def _summary(artifact: RunArtifact) -> ResultSummary:
    gate = artifact.gate
    completion = artifact.completion
    return ResultSummary(
        artifact_api_version=artifact.api_version,
        artifact_id=artifact.metadata.artifact_id,
        run_id=artifact.metadata.run_id,
        invocation_id=artifact.metadata.invocation_id,
        created_unix_nanoseconds=artifact.metadata.created_unix_nanoseconds,
        project=_resource(artifact.provenance.project),
        completion=completion.kind.value,
        completion_instant_unix_nanoseconds=completion.instant_unix_nanoseconds,
        completion_reasons=completion.reasons,
        completion_stage=completion.stage,
        completion_code=completion.code,
        gate_verdict=gate.verdict.value,
        gate_profile=None if gate.profile is None else _resource(gate.profile),
        gate_reasons=gate.reasons,
        gate_coverage=_metrics(gate.coverage),
        gate_assessments=tuple(_gate_assessment(item) for item in gate.assessments),
        executions=_metrics(artifact.summary.executions),
        comparisons=_metrics(artifact.summary.comparisons),
        assertions=_metrics(artifact.summary.assertions),
        diffs=_metrics(artifact.summary.diffs),
        coverage_issues=len(artifact.coverage_issues),
    )


def _execution_row(
    execution: ArtifactExecution,
    *,
    reference: ResultReference,
    comparisons: tuple[ResultReference, ...],
) -> ExecutionResultRow:
    return ExecutionResultRow(
        ordinal=execution.ordinal,
        reference=reference,
        subject=_resource(execution.subject),
        environment=_resource(execution.environment),
        cell_id=execution.cell_id,
        trial_id=execution.trial_id,
        coordinates=tuple(
            ResultCoordinate(axis=item.axis, value=item.value) for item in execution.coordinates
        ),
        outcome=execution.outcome.value,
        failure=None if execution.failure is None else _failure(execution.failure),
        timing=ResultTimingSummary(
            measurements=tuple(_timing(item) for item in execution.timings),
            attempts=len(execution.attempts),
            exchanges=sum(len(item.exchanges) for item in execution.attempts),
        ),
        assertions=tuple(
            ResultReference(entity=ResultEntity.ASSERTION, result_id=item.fact_id)
            for item in execution.assertions
        ),
        comparisons=comparisons,
    )


def _fact(
    fact: ArtifactFact,
    *,
    owner: ResultReference,
    entity: ResultEntity,
) -> ResultFact:
    return ResultFact(
        reference=ResultReference(entity=entity, result_id=fact.fact_id),
        owner=owner,
        engine=fact.engine.value,
        rule=_resource(fact.rule),
        rule_id=fact.rule_id,
        effective_rule_id=fact.effective_rule_id,
        severity=fact.severity.value,
        outcome=fact.outcome.value,
        counts=_metrics(fact.counts),
        coverage=_metrics(fact.coverage),
        evidence=tuple(_evidence(item) for item in fact.evidence),
        evidence_omitted=fact.evidence_omitted,
        issues=tuple(_failure(item) for item in fact.issues),
    )


def _evidence(evidence: ArtifactEvidence) -> ResultEvidence:
    return ResultEvidence(
        fact_id=evidence.fact_id,
        outcome=evidence.outcome,
        location=evidence.location,
        values=tuple(
            ResultNamedValue(name=item.name, value=item.value) for item in evidence.values
        ),
    )


def _links(
    artifact: RunArtifact,
    executions: dict[str, ResultReference],
    comparisons: dict[str, ResultReference],
) -> tuple[ResultCrossLink, ...]:
    assertion_links = tuple(
        ResultCrossLink(
            source=executions[execution.execution_id],
            relation=ResultRelation.ASSERTION,
            target=ResultReference(entity=ResultEntity.ASSERTION, result_id=fact.fact_id),
        )
        for execution in artifact.executions
        for fact in execution.assertions
    )
    comparison_links = tuple(
        link
        for comparison in artifact.comparisons
        for link in (
            ResultCrossLink(
                source=comparisons[comparison.comparison_id],
                relation=ResultRelation.BASELINE,
                target=executions[comparison.baseline_execution_id],
            ),
            ResultCrossLink(
                source=comparisons[comparison.comparison_id],
                relation=ResultRelation.CANDIDATE,
                target=executions[comparison.candidate_execution_id],
            ),
            *(
                ResultCrossLink(
                    source=comparisons[comparison.comparison_id],
                    relation=ResultRelation.DIFF,
                    target=ResultReference(entity=ResultEntity.DIFF, result_id=fact.fact_id),
                )
                for fact in comparison.diffs
            ),
        )
    )
    return (*assertion_links, *comparison_links)


def _resource(value: ArtifactDefinition) -> ResultResource:
    return ResultResource(
        namespace=value.namespace,
        kind=value.kind,
        resource_id=value.resource_id,
        fingerprint=value.fingerprint,
    )


def _failure(value: ArtifactFailure) -> ResultFailure:
    return ResultFailure(stage=value.stage, code=value.code, retryable=value.retryable)


def _metrics(values: tuple[ArtifactMetric, ...]) -> tuple[ResultMetric, ...]:
    return tuple(ResultMetric(name=item.name, value=item.value) for item in values)


def _timing(value: ArtifactTiming) -> ResultTiming:
    return ResultTiming(
        name=value.name,
        nanoseconds=value.nanoseconds,
        reason=value.reason,
    )


def _gate_assessment(value: ArtifactGateCounts) -> ResultGateAssessment:
    return ResultGateAssessment(
        severity=value.severity.value,
        actual=_metrics(value.actual),
        allowed=_metrics(value.allowed),
    )


__all__ = (
    "ComparisonResultRow",
    "ExecutionResultRow",
    "ResultCoordinate",
    "ResultCrossLink",
    "ResultEntity",
    "ResultEvidence",
    "ResultFact",
    "ResultFailure",
    "ResultGateAssessment",
    "ResultMetric",
    "ResultNamedValue",
    "ResultProjection",
    "ResultReference",
    "ResultRelation",
    "ResultResource",
    "ResultSummary",
    "ResultTiming",
    "ResultTimingSummary",
    "project_results",
)
