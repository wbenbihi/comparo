"""Tests for the independently versioned safe artifact contracts."""

from __future__ import annotations

from decimal import Decimal

import msgspec
import pytest

from comparo.core.artifacts import ARTIFACT_API_VERSION
from comparo.core.artifacts import ArtifactAttempt
from comparo.core.artifacts import ArtifactBlob
from comparo.core.artifacts import ArtifactBudget
from comparo.core.artifacts import ArtifactCapture
from comparo.core.artifacts import ArtifactCaptureExtent
from comparo.core.artifacts import ArtifactComparison
from comparo.core.artifacts import ArtifactComparisonOutcome
from comparo.core.artifacts import ArtifactCompletion
from comparo.core.artifacts import ArtifactCompletionKind
from comparo.core.artifacts import ArtifactCoordinate
from comparo.core.artifacts import ArtifactCoverageIssue
from comparo.core.artifacts import ArtifactDefinition
from comparo.core.artifacts import ArtifactEvidence
from comparo.core.artifacts import ArtifactExchange
from comparo.core.artifacts import ArtifactExecution
from comparo.core.artifacts import ArtifactExecutionOutcome
from comparo.core.artifacts import ArtifactFact
from comparo.core.artifacts import ArtifactFactEngine
from comparo.core.artifacts import ArtifactFactOutcome
from comparo.core.artifacts import ArtifactField
from comparo.core.artifacts import ArtifactGate
from comparo.core.artifacts import ArtifactGateVerdict
from comparo.core.artifacts import ArtifactMetadata
from comparo.core.artifacts import ArtifactMetric
from comparo.core.artifacts import ArtifactNamedValue
from comparo.core.artifacts import ArtifactOmissionReason
from comparo.core.artifacts import ArtifactProvenance
from comparo.core.artifacts import ArtifactRequest
from comparo.core.artifacts import ArtifactResponse
from comparo.core.artifacts import ArtifactSeverity
from comparo.core.artifacts import ArtifactSourceCaptureState
from comparo.core.artifacts import ArtifactSummary
from comparo.core.artifacts import ArtifactTiming
from comparo.core.artifacts import RunArtifact
from comparo.core.artifacts.model import is_artifact_fact
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger


def _definition(kind: str, resource_id: str) -> ArtifactDefinition:
    return ArtifactDefinition(
        namespace="default",
        kind=kind,
        resource_id=resource_id,
        fingerprint="a" * 64,
    )


def _metric(name: str, value: int = 0) -> ArtifactMetric:
    return ArtifactMetric(name=name, value=value)


def _capture(digest: str = "b" * 64) -> ArtifactCapture:
    return ArtifactCapture(
        source_state=ArtifactSourceCaptureState.COMPLETE,
        extent=ArtifactCaptureExtent.COMPLETE,
        source_byte_count=4,
        blob=ArtifactBlob(sha256=digest, byte_count=4, media_type="text/plain"),
    )


def _fact(engine: ArtifactFactEngine, outcome: ArtifactFactOutcome) -> ArtifactFact:
    return ArtifactFact(
        fact_id=f"{engine.value}-fact",
        application_id=f"{engine.value}-application",
        engine=engine,
        rule=_definition(
            "AssertionRule" if engine is ArtifactFactEngine.ASSERTION else "DiffRule",
            "r",
        ),
        rule_id="rule",
        effective_rule_id="rule",
        severity=ArtifactSeverity.BLOCKING,
        outcome=outcome,
        counts=(_metric("evaluated", 1),),
        coverage=(_metric("selected", 1),),
        evidence=(
            ArtifactEvidence(
                fact_id=f"{engine.value}-fact",
                outcome=outcome.value,
                location="response.body#/value",
                values=(
                    ArtifactNamedValue(
                        name="actual",
                        value=JsonDecimal(value=Decimal("1.5")),
                    ),
                ),
            ),
        ),
        evidence_omitted=0,
    )


def _artifact() -> RunArtifact:
    request_capture = _capture()
    response_capture = _capture("c" * 64)
    assert request_capture.blob is not None
    assert response_capture.blob is not None
    exchange = ArtifactExchange(
        exchange_id="exchange-0",
        request=ArtifactRequest(
            method="GET",
            url="https://example.test/items",
            headers=(ArtifactField(name="accept", value="application/json"),),
            cookies=(),
            body=request_capture,
        ),
        response=ArtifactResponse(
            status=200,
            protocol="HTTP/2",
            headers=(ArtifactField(name="content-type", value="application/json"),),
            trailers=(),
            body=response_capture,
        ),
        failure=None,
        timings=(ArtifactTiming(name="total", nanoseconds=10, reason=None),),
    )
    execution = ArtifactExecution(
        ordinal=0,
        execution_id="execution-0",
        subject=_definition("Request", "get-items"),
        environment=_definition("Environment", "staging"),
        cell_id="cell-0",
        trial_id="trial-0",
        coordinates=(ArtifactCoordinate(axis="tenant", value=JsonInteger(value=1)),),
        outcome=ArtifactExecutionOutcome.OBSERVED,
        failure=None,
        timings=(ArtifactTiming(name="total", nanoseconds=10, reason=None),),
        attempts=(ArtifactAttempt(attempt_id="attempt-0", exchanges=(exchange,)),),
        assertions=(_fact(ArtifactFactEngine.ASSERTION, ArtifactFactOutcome.SATISFIED),),
    )
    comparison = ArtifactComparison(
        ordinal=0,
        comparison_id="comparison-0",
        subject=execution.subject,
        cell_id="cell-0",
        trial_id="trial-0",
        baseline_execution_id="execution-0",
        candidate_execution_id="execution-1",
        outcome=ArtifactComparisonOutcome.EVALUATED,
        unavailable_execution_ids=(),
        diffs=(_fact(ArtifactFactEngine.DIFF, ArtifactFactOutcome.EQUIVALENT),),
    )
    resources = tuple(
        sorted(
            {execution.subject, execution.environment, comparison.diffs[0].rule},
            key=lambda item: (item.namespace, item.kind, item.resource_id, item.fingerprint),
        )
    )
    return RunArtifact(
        api_version=ARTIFACT_API_VERSION,
        metadata=ArtifactMetadata(
            artifact_id="artifact-0",
            run_id="run-0",
            invocation_id="invocation-0",
            created_unix_nanoseconds=1,
            tool_name="comparo",
            tool_version="0.2.0",
            configuration_api_version="comparo/v1alpha1",
        ),
        provenance=ArtifactProvenance(
            project=_definition("Project", "project"),
            execution_policy=_definition("ExecutionPolicy", "ci"),
            artifact_profile=None,
            redaction_profile=None,
            configuration_fingerprint="d" * 64,
            plan_fingerprint="e" * 64,
            ordering="plan-v1",
        ),
        completion=ArtifactCompletion(
            kind=ArtifactCompletionKind.COMPLETE,
            instant_unix_nanoseconds=2,
        ),
        gate=ArtifactGate(
            verdict=ArtifactGateVerdict.NOT_REQUESTED,
            profile=None,
            reasons=(),
            coverage=(),
            assessments=(),
        ),
        summary=ArtifactSummary(
            executions=(_metric("observed", 1),),
            comparisons=(_metric("evaluated", 1),),
            assertions=(_metric("satisfied", 1),),
            diffs=(_metric("equivalent", 1),),
        ),
        executions=(execution,),
        comparisons=(comparison,),
        resources=resources,
        budgets=(
            ArtifactBudget(
                resource="executions",
                scope="run",
                limit=2,
                used=1,
                exhausted=False,
            ),
        ),
        blobs=tuple(
            sorted((request_capture.blob, response_capture.blob), key=lambda item: item.sha256)
        ),
    )


def test_run_artifact_is_self_contained_canonical_and_immutable() -> None:
    artifact = _artifact()

    assert artifact.api_version == ARTIFACT_API_VERSION
    assert artifact.metadata.sanitizer_version == "comparo.sanitizer/v1alpha1"
    assert artifact.metadata.credential_catalog_version == "comparo.credentials/v1alpha1"
    assert is_artifact_fact(artifact.executions[0].assertions[0])
    assert not is_artifact_fact(object())
    with pytest.raises(AttributeError):
        artifact.executions = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source_state": "complete"}, "source capture state"),
        ({"extent": "complete"}, "capture extent"),
        ({"source_byte_count": -1}, "source byte count"),
        ({"blob": object()}, "capture blob"),
        ({"reason": "policy"}, "omission reason"),
        ({"redaction_count": -1}, "redaction count"),
    ],
)
def test_capture_rejects_invalid_component_types(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "source_state": ArtifactSourceCaptureState.COMPLETE,
        "extent": ArtifactCaptureExtent.COMPLETE,
        "source_byte_count": 4,
        "blob": ArtifactBlob(sha256="f" * 64, byte_count=4),
        "reason": None,
        "redaction_count": 0,
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        ArtifactCapture(**values)  # type: ignore[arg-type]


def test_capture_keeps_source_and_persistence_truth_independent() -> None:
    partial = ArtifactCapture(
        source_state=ArtifactSourceCaptureState.REMOTE_TRUNCATED,
        extent=ArtifactCaptureExtent.PARTIAL,
        source_byte_count=4,
        blob=ArtifactBlob(sha256="f" * 64, byte_count=4),
        reason=ArtifactOmissionReason.SOURCE_INCOMPLETE,
    )
    omitted = ArtifactCapture(
        source_state=ArtifactSourceCaptureState.COMPLETE,
        extent=ArtifactCaptureExtent.OMITTED,
        source_byte_count=4,
        blob=None,
        reason=ArtifactOmissionReason.POLICY,
    )

    assert partial.blob is not None
    assert omitted.blob is None
    with pytest.raises(ValueError, match="cannot become a complete artifact"):
        ArtifactCapture(
            source_state=ArtifactSourceCaptureState.REMOTE_TRUNCATED,
            extent=ArtifactCaptureExtent.COMPLETE,
            source_byte_count=4,
            blob=ArtifactBlob(sha256="f" * 64, byte_count=4),
        )
    with pytest.raises(ValueError, match="explicit reason"):
        ArtifactCapture(
            source_state=ArtifactSourceCaptureState.COMPLETE,
            extent=ArtifactCaptureExtent.PARTIAL,
            source_byte_count=4,
            blob=ArtifactBlob(sha256="f" * 64, byte_count=4),
        )
    with pytest.raises(ValueError, match="cannot retain"):
        ArtifactCapture(
            source_state=ArtifactSourceCaptureState.COMPLETE,
            extent=ArtifactCaptureExtent.OMITTED,
            source_byte_count=4,
            blob=ArtifactBlob(sha256="f" * 64, byte_count=4),
            reason=ArtifactOmissionReason.POLICY,
        )


def test_artifact_rejects_noncanonical_or_dangling_records() -> None:
    artifact = _artifact()

    with pytest.raises(ValueError, match="unsupported RunArtifact"):
        msgspec.structs.replace(artifact, api_version="future")

    with pytest.raises(ValueError, match="blob manifest"):
        msgspec.structs.replace(artifact, blobs=())

    reversed_blobs = tuple(reversed(artifact.blobs))
    with pytest.raises(ValueError, match="digest ordered"):
        msgspec.structs.replace(artifact, blobs=reversed_blobs)

    duplicate_resources = (*artifact.resources, artifact.resources[-1])
    with pytest.raises(ValueError, match="resources must be unique"):
        msgspec.structs.replace(artifact, resources=duplicate_resources)


def test_safe_value_contract_rejects_live_non_json_payloads() -> None:
    with pytest.raises(TypeError, match="exact JsonValue"):
        ArtifactNamedValue(name="actual", value=BytesValue(value=b"secret"))  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact JsonValue"):
        ArtifactCoordinate(axis="tenant", value=BytesValue(value=b"secret"))  # type: ignore[arg-type]


def test_coverage_and_budget_shapes_are_explicit() -> None:
    issue = ArtifactCoverageIssue(
        source="response.body",
        extent=ArtifactCaptureExtent.OMITTED,
        reason=ArtifactOmissionReason.REDACTION_FAILED,
        execution_id="execution-0",
    )
    assert issue.reason is ArtifactOmissionReason.REDACTION_FAILED

    with pytest.raises(ValueError, match="incomplete capture"):
        ArtifactCoverageIssue(
            source="response.body",
            extent=ArtifactCaptureExtent.COMPLETE,
            reason=ArtifactOmissionReason.REDACTION_FAILED,
        )
    with pytest.raises(ValueError, match="exhaustion"):
        ArtifactBudget(resource="bytes", scope="run", limit=1, used=1, exhausted=False)
