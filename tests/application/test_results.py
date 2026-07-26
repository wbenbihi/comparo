"""Tests for the immutable body-free result projection."""

from __future__ import annotations

from typing import Any
from typing import cast

import msgspec
import pytest

from comparo.application import result_analysis as analysis_module
from comparo.application.result_analysis import AnalysisProjection
from comparo.application.result_analysis import ResultProjectionWindow
from comparo.application.result_analysis import project_fields
from comparo.application.result_analysis import project_issues
from comparo.application.result_analysis import project_requests
from comparo.application.result_analysis import project_rules
from comparo.application.result_analysis import project_timeline
from comparo.application.results import ResultEntity
from comparo.application.results import ResultEvidence
from comparo.application.results import ResultNamedValue
from comparo.application.results import ResultReference
from comparo.application.results import ResultRelation
from comparo.application.results import project_results
from comparo.core.artifacts import ArtifactComparisonOutcome
from comparo.core.artifacts import ArtifactDefinition
from comparo.core.artifacts import ArtifactExecutionOutcome
from comparo.core.artifacts import ArtifactFailure
from comparo.core.artifacts import ArtifactGate
from comparo.core.artifacts import ArtifactGateCounts
from comparo.core.artifacts import ArtifactGateVerdict
from comparo.core.artifacts import ArtifactMetric
from comparo.core.artifacts import ArtifactSeverity
from comparo.core.artifacts import ArtifactSummary
from comparo.core.artifacts import RunArtifact
from comparo.core.artifacts.values import ArtifactString
from tests.core.artifacts.test_artifact_contracts import _artifact


def _metric(name: str, value: int) -> ArtifactMetric:
    return ArtifactMetric(name=name, value=value)


def _definition(kind: str, resource_id: str) -> ArtifactDefinition:
    return ArtifactDefinition(
        namespace="default",
        kind=kind,
        resource_id=resource_id,
        fingerprint="f" * 64,
    )


def test_projection_exposes_shared_rows_facts_links_and_timings_without_bodies() -> None:
    artifact = _artifact()
    issue = ArtifactFailure(stage="evaluation", code="evidence-limited", retryable=None)
    assertion = msgspec.structs.replace(
        artifact.executions[0].assertions[0],
        issues=(issue,),
    )
    execution = msgspec.structs.replace(
        artifact.executions[0],
        assertions=(assertion,),
    )
    artifact = msgspec.structs.replace(
        artifact,
        executions=(execution, artifact.executions[1]),
    )

    projection = project_results(artifact)

    assert projection.summary.artifact_id == "artifact-0"
    assert projection.summary.completion == "complete"
    assert projection.summary.gate_verdict == "not-requested"
    assert projection.summary.gate_profile is None
    assert projection.summary.executions[0].name == "observed"
    assert projection.summary.coverage_issues == 0

    first = projection.executions[0]
    assert first.reference.entity is ResultEntity.EXECUTION
    assert first.subject.resource_id == "get-items"
    assert first.environment.resource_id == "staging"
    assert first.coordinates[0].axis == "tenant"
    assert first.timing.measurements[0].name == "total"
    assert first.timing.measurements[0].nanoseconds == 10
    assert first.timing.attempts == 1
    assert first.timing.exchanges == 1
    assert first.failure is None
    assert first.assertions == (projection.assertions[0].reference,)
    assert first.comparisons == (projection.comparisons[0].reference,)

    assertion_result = projection.assertions[0]
    assert assertion_result.owner == first.reference
    assert assertion_result.engine == "assertion"
    assert assertion_result.outcome == "satisfied"
    assert assertion_result.rule.resource_id == "r"
    assert assertion_result.counts[0].value == 1
    assert assertion_result.coverage[0].name == "selected"
    assert assertion_result.evidence[0].location == "response.body#/value"
    assert assertion_result.evidence[0].values[0].name == "actual"
    assert assertion_result.issues[0].code == "evidence-limited"

    comparison = projection.comparisons[0]
    assert comparison.reference.entity is ResultEntity.COMPARISON
    assert comparison.baseline == projection.executions[0].reference
    assert comparison.candidate == projection.executions[1].reference
    assert comparison.unavailable == ()
    assert comparison.diffs == (projection.diffs[0].reference,)
    assert projection.diffs[0].owner == comparison.reference
    assert projection.diffs[0].outcome == "equivalent"

    assert tuple(item.relation for item in projection.links) == (
        ResultRelation.ASSERTION,
        ResultRelation.BASELINE,
        ResultRelation.CANDIDATE,
        ResultRelation.DIFF,
    )
    assert projection.links[0].target == assertion_result.reference
    assert projection.links[-1].target == projection.diffs[0].reference

    encoded = msgspec.json.encode(projection)
    assert b"b" * 64 not in encoded
    assert b"c" * 64 not in encoded
    assert b'"blobs"' not in encoded
    assert b'"body"' not in encoded
    with pytest.raises(AttributeError):
        projection.executions = ()  # type: ignore[misc]


def test_projection_preserves_requested_gate_verdict_and_assessments_exactly() -> None:
    artifact = _artifact()
    profile = _definition("GateProfile", "release")
    assessment = ArtifactGateCounts(
        severity=ArtifactSeverity.BLOCKING,
        actual=(_metric("violated", 0),),
        allowed=(_metric("violated", 0),),
    )
    gate = ArtifactGate(
        verdict=ArtifactGateVerdict.FAILED,
        profile=profile,
        reasons=("manual-policy",),
        coverage=(_metric("run-complete", 1),),
        assessments=(assessment,),
    )
    resources = tuple(
        sorted(
            (*artifact.resources, profile),
            key=lambda item: (item.namespace, item.kind, item.resource_id),
        )
    )
    artifact = msgspec.structs.replace(artifact, gate=gate, resources=resources)

    summary = project_results(artifact).summary

    assert summary.gate_verdict == "failed"
    assert summary.gate_reasons == ("manual-policy",)
    assert summary.gate_profile is not None
    assert summary.gate_profile.resource_id == "release"
    assert summary.gate_coverage[0].name == "run-complete"
    assert summary.gate_coverage[0].value == 1
    assert summary.gate_assessments[0].severity == "blocking"
    assert summary.gate_assessments[0].actual[0].value == 0
    assert summary.gate_assessments[0].allowed[0].value == 0
    assert summary.diffs[0].name == "equivalent"


def test_projection_retains_unavailable_execution_and_incomplete_comparison() -> None:
    artifact = _artifact()
    failure = ArtifactFailure(stage="transport", code="unavailable", retryable=True)
    unavailable = msgspec.structs.replace(
        artifact.executions[1],
        outcome=ArtifactExecutionOutcome.UNAVAILABLE,
        failure=failure,
        attempts=(),
    )
    incomplete = msgspec.structs.replace(
        artifact.comparisons[0],
        outcome=ArtifactComparisonOutcome.INCOMPLETE,
        unavailable_execution_ids=(unavailable.execution_id,),
        diffs=(),
    )
    summary = ArtifactSummary(
        executions=(_metric("observed", 1), _metric("unavailable", 1)),
        comparisons=(_metric("incomplete", 1),),
        assertions=artifact.summary.assertions,
        diffs=(),
    )
    artifact = msgspec.structs.replace(
        artifact,
        executions=(artifact.executions[0], unavailable),
        comparisons=(incomplete,),
        summary=summary,
    )

    projection = project_results(artifact)

    candidate = projection.executions[1]
    assert candidate.outcome == "unavailable"
    assert candidate.failure is not None
    assert candidate.failure.code == "unavailable"
    assert candidate.failure.retryable is True
    assert candidate.timing.attempts == 0
    assert candidate.timing.exchanges == 0
    comparison = projection.comparisons[0]
    assert comparison.outcome == "incomplete"
    assert comparison.unavailable == (candidate.reference,)
    assert comparison.diffs == ()
    assert projection.diffs == ()
    assert tuple(item.relation for item in projection.links) == (
        ResultRelation.ASSERTION,
        ResultRelation.BASELINE,
        ResultRelation.CANDIDATE,
    )


def test_projection_rejects_every_input_other_than_a_run_artifact() -> None:
    with pytest.raises(TypeError, match="RunArtifact"):
        project_results(cast("RunArtifact", object()))


def test_bounded_semantic_analysis_projects_distinct_lenses_and_exact_omissions() -> None:
    projection = project_results(_artifact())
    first = projection.assertions[0]
    samples = tuple(
        ResultNamedValue(name=f"sample-{index:02d}", value=ArtifactString(value=str(index)))
        for index in range(12)
    )
    evidence = (
        msgspec.structs.replace(first.evidence[0], values=samples),
        ResultEvidence(
            fact_id="evidence-second",
            outcome="satisfied",
            location="response.headers.content-type",
            values=(),
        ),
    )
    assertion = msgspec.structs.replace(first, outcome="violated", evidence=evidence)
    enriched = msgspec.structs.replace(projection, assertions=(assertion,))
    window = ResultProjectionWindow(maximum_items=10)

    assert project_issues(enriched, window).items[0].rule_id == assertion.effective_rule_id
    assert project_requests(enriched, window).items[0].executions == 2
    assert project_rules(enriched, window).items[0].applications == 1
    fields = project_fields(enriched, window)
    assert fields.total == 2
    sample_field = next(item for item in fields.items if item.samples)
    assert len(sample_field.samples) == 10
    assert sample_field.sample_values_omitted == 3
    timeline = project_timeline(enriched, window)
    assert {item.phase for item in timeline.items} >= {"request", "assertion", "comparison"}


def test_analysis_window_pins_a_selected_aggregate_beyond_the_visible_prefix() -> None:
    projection = project_results(_artifact())
    first = projection.assertions[0]
    second_reference = ResultReference(entity=ResultEntity.ASSERTION, result_id="assertion-2")
    second = msgspec.structs.replace(
        first,
        reference=second_reference,
        effective_rule_id="z-last-rule",
        evidence=(
            ResultEvidence(
                fact_id="evidence-z",
                outcome="satisfied",
                location="z.last.path",
                values=(),
            ),
        ),
    )
    enriched = msgspec.structs.replace(projection, assertions=(first, second))
    visible = project_fields(
        enriched,
        ResultProjectionWindow(maximum_items=1),
        selected_reference=second_reference,
    )

    assert visible.total >= 2
    assert visible.omitted == visible.total - 1
    assert visible.items[0].path == "z.last.path"


def test_analysis_contracts_reject_invalid_windows_counts_and_inputs() -> None:
    for maximum in (0, -1, cast("int", True)):
        with pytest.raises(ValueError, match="positive integer"):
            ResultProjectionWindow(maximum_items=maximum)

    valid = AnalysisProjection(items=("visible",), total=1, omitted=0)
    with pytest.raises(TypeError, match="immutable"):
        msgspec.structs.replace(valid, items=cast("tuple[str, ...]", ["visible"]))
    for changes in ({"total": -1}, {"omitted": cast("int", True)}):
        with pytest.raises(ValueError, match="non-negative integers"):
            msgspec.structs.replace(valid, **changes)
    with pytest.raises(ValueError, match="conserve"):
        msgspec.structs.replace(valid, total=2)

    projection = project_results(_artifact())
    window = ResultProjectionWindow(maximum_items=1)
    for projector in (
        project_issues,
        project_requests,
        project_rules,
        project_fields,
        project_timeline,
    ):
        with pytest.raises(TypeError, match="requires ResultProjection"):
            projector(cast("Any", object()), window)
        with pytest.raises(TypeError, match="requires ResultProjectionWindow"):
            projector(projection, cast("Any", object()))
        with pytest.raises(TypeError, match="reference must be typed"):
            projector(projection, window, cast("Any", object()))


def test_analysis_window_ignores_an_unknown_selected_reference() -> None:
    projection = project_results(_artifact())
    selected = ResultReference(entity=ResultEntity.ASSERTION, result_id="not-retained")

    result = project_fields(
        projection,
        ResultProjectionWindow(maximum_items=1),
        selected_reference=selected,
    )

    assert len(result.items) == min(1, result.total)


def test_analysis_window_defensively_conserves_when_selection_has_no_visible_prefix() -> None:
    selected = ResultReference(entity=ResultEntity.ASSERTION, result_id="selected")

    class AdversarialItems:
        """Iterable whose slice changes between selection and lookup."""

        def __getitem__(self, _selection: object) -> tuple[ResultReference, ...]:
            return ()

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter((selected,))

        def __len__(self) -> int:
            return 1

    result = analysis_module._window(
        cast("tuple[ResultReference, ...]", AdversarialItems()),
        ResultProjectionWindow(maximum_items=1),
        selected,
        lambda item, reference: item == reference,
    )

    assert result.items == ()
    assert result.total == 1
    assert result.omitted == 1
