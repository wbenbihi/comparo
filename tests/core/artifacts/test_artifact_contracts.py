"""Tests for the independently versioned safe artifact contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import msgspec
import pytest

from comparo.core.artifacts import ARTIFACT_API_VERSION
from comparo.core.artifacts import ArtifactAmount
from comparo.core.artifacts import ArtifactAmountUnit
from comparo.core.artifacts import ArtifactAttempt
from comparo.core.artifacts import ArtifactBlob
from comparo.core.artifacts import ArtifactBudget
from comparo.core.artifacts import ArtifactCapture
from comparo.core.artifacts import ArtifactCaptureExtent
from comparo.core.artifacts import ArtifactComparison
from comparo.core.artifacts import ArtifactComparisonOutcome
from comparo.core.artifacts import ArtifactCompletion
from comparo.core.artifacts import ArtifactCompletionKind
from comparo.core.artifacts import ArtifactCookie
from comparo.core.artifacts import ArtifactCoordinate
from comparo.core.artifacts import ArtifactCoverageIssue
from comparo.core.artifacts import ArtifactDecimal
from comparo.core.artifacts import ArtifactDefinition
from comparo.core.artifacts import ArtifactEvidence
from comparo.core.artifacts import ArtifactExchange
from comparo.core.artifacts import ArtifactExecution
from comparo.core.artifacts import ArtifactExecutionOutcome
from comparo.core.artifacts import ArtifactFact
from comparo.core.artifacts import ArtifactFactEngine
from comparo.core.artifacts import ArtifactFactOutcome
from comparo.core.artifacts import ArtifactFailure
from comparo.core.artifacts import ArtifactField
from comparo.core.artifacts import ArtifactGate
from comparo.core.artifacts import ArtifactGateVerdict
from comparo.core.artifacts import ArtifactInteger
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
                fact_id=f"{engine.value}-evidence",
                outcome=outcome.value,
                location="response.body#/value",
                values=(
                    ArtifactNamedValue(
                        name="actual",
                        value=ArtifactDecimal(value=str(Decimal("1.5"))),
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
        request_omission=None,
        response=ArtifactResponse(
            status=200,
            protocol="HTTP/2",
            headers=(ArtifactField(name="content-type", value="application/json"),),
            trailers=(),
            body=response_capture,
        ),
        response_omission=None,
        failure=None,
        timings=(ArtifactTiming(name="total", nanoseconds=10, reason=None),),
    )
    subject = _definition("Request", "get-items")
    staging = _definition("Environment", "staging")
    prod = _definition("Environment", "prod")
    assertion = _fact(ArtifactFactEngine.ASSERTION, ArtifactFactOutcome.SATISFIED)
    diff = _fact(ArtifactFactEngine.DIFF, ArtifactFactOutcome.EQUIVALENT)
    execution = ArtifactExecution(
        ordinal=0,
        execution_id="execution-0",
        subject=subject,
        environment=staging,
        cell_id="cell-0",
        trial_id="trial-0",
        coordinates=(ArtifactCoordinate(axis="tenant", value=ArtifactInteger(value=1)),),
        outcome=ArtifactExecutionOutcome.OBSERVED,
        failure=None,
        timings=(ArtifactTiming(name="total", nanoseconds=10, reason=None),),
        attempts=(ArtifactAttempt(attempt_id="attempt-0", exchanges=(exchange,)),),
        assertions=(assertion,),
    )
    candidate_exchange = msgspec.structs.replace(exchange, exchange_id="exchange-1")
    candidate = ArtifactExecution(
        ordinal=1,
        execution_id="execution-1",
        subject=subject,
        environment=prod,
        cell_id="cell-0",
        trial_id="trial-0",
        coordinates=(ArtifactCoordinate(axis="tenant", value=ArtifactInteger(value=1)),),
        outcome=ArtifactExecutionOutcome.OBSERVED,
        failure=None,
        timings=(ArtifactTiming(name="total", nanoseconds=10, reason=None),),
        attempts=(ArtifactAttempt(attempt_id="attempt-1", exchanges=(candidate_exchange,)),),
        assertions=(),
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
        diffs=(diff,),
    )
    project = _definition("Project", "project")
    execution_policy = _definition("ExecutionPolicy", "ci")
    resources = tuple(
        sorted(
            {
                project,
                execution_policy,
                subject,
                staging,
                prod,
                assertion.rule,
                diff.rule,
            },
            key=lambda item: (item.namespace, item.kind, item.resource_id),
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
            project=project,
            execution_policy=execution_policy,
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
            executions=(_metric("observed", 2),),
            comparisons=(_metric("evaluated", 1),),
            assertions=(_metric("satisfied", 1),),
            diffs=(_metric("equivalent", 1),),
        ),
        executions=(execution, candidate),
        comparisons=(comparison,),
        resources=resources,
        budgets=(
            ArtifactBudget(
                resource="executions",
                scope="run",
                limit=ArtifactAmount(unit=ArtifactAmountUnit.COUNT, numerator=2),
                used=ArtifactAmount(unit=ArtifactAmountUnit.COUNT, numerator=1),
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


def test_http_artifact_dtos_enforce_the_mandatory_credential_floor() -> None:
    with pytest.raises(ValueError, match="credential artifact fields"):
        ArtifactRequest(
            method="GET",
            url="https://example.test/",
            headers=(ArtifactField(name="authorization", value="Bearer LEAK-ME"),),
            cookies=(),
            body=None,
        )
    with pytest.raises(ValueError, match="cookie values"):
        ArtifactRequest(
            method="GET",
            url="https://example.test/",
            headers=(),
            cookies=(ArtifactCookie(name="session", value="LEAK-ME", redacted=False),),
            body=None,
        )
    with pytest.raises(ValueError, match="credential query"):
        ArtifactRequest(
            method="GET",
            url="https://example.test/?api_key=LEAK-ME",
            headers=(),
            cookies=(),
            body=None,
        )
    with pytest.raises(ValueError, match="absolute HTTP"):
        ArtifactRequest(
            method="GET",
            url="https://user:LEAK-ME@example.test/",
            headers=(),
            cookies=(),
            body=None,
        )
    with pytest.raises(ValueError, match="credential artifact fields"):
        ArtifactResponse(
            status=200,
            protocol="HTTP/2",
            headers=(ArtifactField(name="set-cookie", value="session=LEAK-ME"),),
            trailers=(),
            body=None,
        )

    safe = ArtifactRequest(
        method="GET",
        url="https://example.test/?api_key=%3Credacted%3E&token",
        headers=(
            ArtifactField(
                name="authorization",
                value="<redacted>",
                redacted=True,
            ),
        ),
        cookies=(),
        body=None,
    )
    assert safe.url.endswith("token")
    uppercase_cookie = ArtifactCookie(
        name="SessionID",
        value="<redacted>",
        redacted=True,
    )
    assert uppercase_cookie.name == "SessionID"
    with pytest.raises(ValueError, match="canonical lowercase HTTP token"):
        ArtifactField(name="Authorization ", value="LEAK-ME")
    with pytest.raises(ValueError, match="cookie name"):
        ArtifactCookie(name="invalid cookie", value="<redacted>", redacted=True)
    with pytest.raises(TypeError, match="request cookies"):
        msgspec.structs.replace(safe, cookies=cast("tuple[ArtifactCookie, ...]", (object(),)))
    with pytest.raises(ValueError, match="request URL must be valid"):
        msgspec.structs.replace(safe, url="https://[invalid")


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
    with pytest.raises(ValueError, match="stable identities must be unique"):
        msgspec.structs.replace(artifact, resources=duplicate_resources)


def test_artifact_result_ids_are_globally_unique() -> None:
    artifact = _artifact()
    duplicate_execution = msgspec.structs.replace(
        artifact.executions[1],
        execution_id=artifact.executions[0].execution_id,
    )
    with pytest.raises(ValueError, match="execution IDs must be unique"):
        msgspec.structs.replace(
            artifact,
            executions=(artifact.executions[0], duplicate_execution),
        )

    duplicate_comparison = msgspec.structs.replace(artifact.comparisons[0], ordinal=1)
    with pytest.raises(ValueError, match="comparison IDs must be unique"):
        msgspec.structs.replace(
            artifact,
            comparisons=(artifact.comparisons[0], duplicate_comparison),
        )


def test_artifact_occurrence_ids_are_globally_unique_and_unambiguous() -> None:
    artifact = _artifact()
    first, second = artifact.executions
    first_attempt = first.attempts[0]
    second_attempt = second.attempts[0]

    duplicate_attempt = msgspec.structs.replace(
        second_attempt,
        attempt_id=first_attempt.attempt_id,
    )
    with pytest.raises(ValueError, match="attempt IDs must be globally unique"):
        msgspec.structs.replace(
            artifact,
            executions=(
                first,
                msgspec.structs.replace(second, attempts=(duplicate_attempt,)),
            ),
        )

    duplicate_exchange = msgspec.structs.replace(
        second_attempt.exchanges[0],
        exchange_id=first_attempt.exchanges[0].exchange_id,
    )
    with pytest.raises(ValueError, match="exchange IDs must be globally unique"):
        msgspec.structs.replace(
            artifact,
            executions=(
                first,
                msgspec.structs.replace(
                    second,
                    attempts=(
                        msgspec.structs.replace(
                            second_attempt,
                            exchanges=(duplicate_exchange,),
                        ),
                    ),
                ),
            ),
        )

    assertion = first.assertions[0]
    diff = artifact.comparisons[0].diffs[0]
    for field, message in (
        ("fact_id", "aggregate fact IDs must be globally unique"),
        ("application_id", "application IDs must be globally unique"),
    ):
        duplicate = msgspec.structs.replace(diff, **{field: getattr(assertion, field)})
        comparison = msgspec.structs.replace(artifact.comparisons[0], diffs=(duplicate,))
        with pytest.raises(ValueError, match=message):
            msgspec.structs.replace(artifact, comparisons=(comparison,))

    duplicate_evidence = msgspec.structs.replace(
        diff.evidence[0],
        fact_id=assertion.evidence[0].fact_id,
    )
    comparison = msgspec.structs.replace(
        artifact.comparisons[0],
        diffs=(msgspec.structs.replace(diff, evidence=(duplicate_evidence,)),),
    )
    with pytest.raises(ValueError, match="evidence fact IDs must be globally unique"):
        msgspec.structs.replace(artifact, comparisons=(comparison,))

    parent_collision = msgspec.structs.replace(
        assertion.evidence[0],
        fact_id=assertion.fact_id,
    )
    execution = msgspec.structs.replace(
        first,
        assertions=(msgspec.structs.replace(assertion, evidence=(parent_collision,)),),
    )
    with pytest.raises(ValueError, match="aggregate and evidence fact IDs must be distinct"):
        msgspec.structs.replace(artifact, executions=(execution, second))


def test_artifact_coverage_must_refer_to_a_retained_execution() -> None:
    artifact = _artifact()
    issue = ArtifactCoverageIssue(
        source="response.body",
        extent=ArtifactCaptureExtent.OMITTED,
        reason=ArtifactOmissionReason.POLICY,
        execution_id="missing-execution",
    )

    with pytest.raises(ValueError, match="coverage references an unknown execution"):
        msgspec.structs.replace(artifact, coverage_issues=(issue,))


def test_artifact_comparison_sides_must_be_distinct_and_declared() -> None:
    artifact = _artifact()
    comparison = artifact.comparisons[0]

    with pytest.raises(ValueError, match="distinct executions"):
        msgspec.structs.replace(
            artifact,
            comparisons=(
                msgspec.structs.replace(
                    comparison,
                    candidate_execution_id=comparison.baseline_execution_id,
                ),
            ),
        )
    with pytest.raises(ValueError, match="unknown execution"):
        msgspec.structs.replace(
            artifact,
            comparisons=(
                msgspec.structs.replace(
                    comparison,
                    candidate_execution_id="missing-execution",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject", _definition("Request", "other-request")),
        ("cell_id", "other-cell"),
        ("trial_id", "other-trial"),
    ],
)
def test_artifact_comparison_sides_align_with_comparison_identity(
    field: str,
    value: object,
) -> None:
    artifact = _artifact()
    candidate = msgspec.structs.replace(artifact.executions[1], **{field: value})

    with pytest.raises(ValueError, match=r"contradict.*subject, cell, or trial"):
        msgspec.structs.replace(
            artifact,
            executions=(artifact.executions[0], candidate),
        )


def test_artifact_comparison_requires_distinct_environments_and_equal_coordinates() -> None:
    artifact = _artifact()
    baseline, candidate = artifact.executions

    same_environment = msgspec.structs.replace(candidate, environment=baseline.environment)
    with pytest.raises(ValueError, match="distinct Environments"):
        msgspec.structs.replace(artifact, executions=(baseline, same_environment))

    different_coordinates = msgspec.structs.replace(
        candidate,
        coordinates=(ArtifactCoordinate(axis="tenant", value=ArtifactInteger(value=2)),),
    )
    with pytest.raises(ValueError, match="exact Matrix coordinates"):
        msgspec.structs.replace(artifact, executions=(baseline, different_coordinates))


def test_artifact_comparison_outcome_tracks_side_availability_exactly() -> None:
    artifact = _artifact()
    candidate = msgspec.structs.replace(
        artifact.executions[1],
        outcome=ArtifactExecutionOutcome.UNAVAILABLE,
        failure=ArtifactFailure(stage="transport", code="unavailable"),
        attempts=(),
    )
    with pytest.raises(ValueError, match=r"evaluated.*observed executions"):
        msgspec.structs.replace(
            artifact,
            executions=(artifact.executions[0], candidate),
        )

    incomplete = msgspec.structs.replace(
        artifact.comparisons[0],
        outcome=ArtifactComparisonOutcome.INCOMPLETE,
        unavailable_execution_ids=(candidate.execution_id,),
        diffs=(),
    )
    valid = msgspec.structs.replace(
        artifact,
        executions=(artifact.executions[0], candidate),
        comparisons=(incomplete,),
        summary=msgspec.structs.replace(
            artifact.summary,
            executions=(_metric("observed", 1), _metric("unavailable", 1)),
            comparisons=(_metric("incomplete", 1),),
            diffs=(),
        ),
    )
    assert valid.comparisons[0].unavailable_execution_ids == (candidate.execution_id,)

    wrong = msgspec.structs.replace(
        incomplete,
        unavailable_execution_ids=(artifact.executions[0].execution_id,),
    )
    with pytest.raises(ValueError, match="exactly its unavailable sides"):
        msgspec.structs.replace(
            artifact,
            executions=(artifact.executions[0], candidate),
            comparisons=(wrong,),
        )


def test_incomplete_comparison_uses_canonical_baseline_candidate_unavailable_order() -> None:
    artifact = _artifact()
    unavailable = tuple(
        msgspec.structs.replace(
            execution,
            outcome=ArtifactExecutionOutcome.BLOCKED,
            failure=ArtifactFailure(stage="policy", code="blocked"),
            attempts=(),
            assertions=(),
        )
        for execution in artifact.executions
    )
    comparison = msgspec.structs.replace(
        artifact.comparisons[0],
        outcome=ArtifactComparisonOutcome.INCOMPLETE,
        unavailable_execution_ids=(unavailable[1].execution_id, unavailable[0].execution_id),
        diffs=(),
    )

    with pytest.raises(ValueError, match="exactly its unavailable sides"):
        msgspec.structs.replace(
            artifact,
            executions=unavailable,
            comparisons=(comparison,),
        )


@pytest.mark.parametrize("dimension", ["executions", "comparisons", "assertions", "diffs"])
def test_artifact_summary_exactly_conserves_each_result_dimension(dimension: str) -> None:
    artifact = _artifact()
    summary = msgspec.structs.replace(
        artifact.summary,
        **{dimension: (_metric("invented", 1),)},
    )

    with pytest.raises(ValueError, match="summary must exactly conserve"):
        msgspec.structs.replace(artifact, summary=summary)


def test_artifact_resource_identities_are_unique_and_canonically_ordered() -> None:
    artifact = _artifact()
    original = artifact.resources[-1]
    conflicting = msgspec.structs.replace(original, fingerprint="f" * 64)
    duplicated_identity = tuple(
        sorted(
            (*artifact.resources, conflicting),
            key=lambda item: (item.namespace, item.kind, item.resource_id),
        )
    )
    with pytest.raises(ValueError, match="stable identities must be unique"):
        msgspec.structs.replace(artifact, resources=duplicated_identity)
    with pytest.raises(ValueError, match="canonical stable identity order"):
        msgspec.structs.replace(artifact, resources=tuple(reversed(artifact.resources)))


@pytest.mark.parametrize(
    ("kind", "resource_id"),
    [
        ("Project", "project"),
        ("ExecutionPolicy", "ci"),
        ("Request", "get-items"),
        ("Environment", "staging"),
        ("Environment", "prod"),
        ("AssertionRule", "r"),
        ("DiffRule", "r"),
    ],
)
def test_artifact_resources_cover_every_required_reference(
    kind: str,
    resource_id: str,
) -> None:
    artifact = _artifact()
    resources = tuple(
        item for item in artifact.resources if (item.kind, item.resource_id) != (kind, resource_id)
    )

    with pytest.raises(ValueError, match="every referenced definition"):
        msgspec.structs.replace(artifact, resources=resources)


@pytest.mark.parametrize("reference", ["artifact_profile", "redaction_profile", "gate"])
def test_artifact_resources_cover_optional_provenance_and_gate_references(
    reference: str,
) -> None:
    artifact = _artifact()
    artifact_profile = _definition("ArtifactProfile", "full")
    redaction_profile = _definition("RedactionProfile", "safe")
    gate_profile = _definition("GateProfile", "ci")
    provenance = msgspec.structs.replace(
        artifact.provenance,
        artifact_profile=artifact_profile,
        redaction_profile=redaction_profile,
    )
    gate = ArtifactGate(
        verdict=ArtifactGateVerdict.PASSED,
        profile=gate_profile,
        reasons=(),
        coverage=(),
        assessments=(),
    )
    resources = tuple(
        sorted(
            (*artifact.resources, artifact_profile, redaction_profile, gate_profile),
            key=lambda item: (item.namespace, item.kind, item.resource_id),
        )
    )
    complete = msgspec.structs.replace(
        artifact,
        provenance=provenance,
        gate=gate,
        resources=resources,
    )
    omitted = {
        "artifact_profile": artifact_profile,
        "redaction_profile": redaction_profile,
        "gate": gate_profile,
    }[reference]

    with pytest.raises(ValueError, match="every referenced definition"):
        msgspec.structs.replace(
            complete,
            resources=tuple(item for item in complete.resources if item != omitted),
        )


def test_artifact_resources_must_contain_the_exact_referenced_definition() -> None:
    artifact = _artifact()
    subject = artifact.executions[0].subject
    conflicting = msgspec.structs.replace(subject, fingerprint="f" * 64)
    resources = tuple(
        sorted(
            (conflicting if item == subject else item for item in artifact.resources),
            key=lambda item: (item.namespace, item.kind, item.resource_id),
        )
    )

    with pytest.raises(ValueError, match="every referenced definition"):
        msgspec.structs.replace(artifact, resources=resources)


def test_safe_value_contract_rejects_live_non_json_payloads() -> None:
    with pytest.raises(TypeError, match="exact ArtifactValue"):
        ArtifactNamedValue(name="actual", value=BytesValue(value=b"secret"))  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="exact ArtifactValue"):
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
        ArtifactBudget(
            resource="bytes",
            scope="run",
            limit=ArtifactAmount(unit=ArtifactAmountUnit.BYTES, numerator=1),
            used=ArtifactAmount(unit=ArtifactAmountUnit.BYTES, numerator=1),
            exhausted=False,
        )
