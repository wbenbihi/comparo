"""Exhaustive malformed-state tests for artifact DTOs, policies, and outcomes."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.artifacts.common import ArtifactAmount
from comparo.core.artifacts.common import ArtifactAmountUnit
from comparo.core.artifacts.common import ArtifactBlob
from comparo.core.artifacts.common import ArtifactBudget
from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactFactEngine
from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.common import ArtifactSourceCaptureState
from comparo.core.artifacts.common import require_metrics
from comparo.core.artifacts.compiler import ArtifactCompilationResult
from comparo.core.artifacts.compiler import ArtifactCompilationStatus
from comparo.core.artifacts.facts import ArtifactComparison
from comparo.core.artifacts.facts import ArtifactCoordinate
from comparo.core.artifacts.facts import ArtifactEvidence
from comparo.core.artifacts.facts import ArtifactExecution
from comparo.core.artifacts.facts import ArtifactFact
from comparo.core.artifacts.facts import ArtifactNamedValue
from comparo.core.artifacts.http import ArtifactAttempt
from comparo.core.artifacts.http import ArtifactExchange
from comparo.core.artifacts.http import ArtifactField
from comparo.core.artifacts.http import ArtifactRequest
from comparo.core.artifacts.http import ArtifactResponse
from comparo.core.artifacts.http import ArtifactTiming
from comparo.core.artifacts.lifecycle import ArtifactCompletion
from comparo.core.artifacts.lifecycle import ArtifactGate
from comparo.core.artifacts.lifecycle import ArtifactGateCounts
from comparo.core.artifacts.model import ArtifactCoverageIssue
from comparo.core.artifacts.policy import ArtifactCapturePolicy
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionAction
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.policy import RedactionRule
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.artifacts.ports import ArtifactPersistenceOutcome
from comparo.core.artifacts.ports import ArtifactPersistenceStatus
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.artifacts.ports import ArtifactStoreFailure
from comparo.core.artifacts.values import ArtifactArray
from comparo.core.artifacts.values import ArtifactBoolean
from comparo.core.artifacts.values import ArtifactDecimal
from comparo.core.artifacts.values import ArtifactInteger
from comparo.core.artifacts.values import ArtifactNull
from comparo.core.artifacts.values import ArtifactObject
from comparo.core.artifacts.values import ArtifactObjectMember
from comparo.core.artifacts.values import ArtifactString
from comparo.core.artifacts.values import ArtifactValue
from comparo.core.artifacts.values import is_artifact_value
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.selectors.model import CompiledSelector
from tests.core.artifacts.test_artifact_contracts import _artifact
from tests.core.artifacts.test_artifact_contracts import _definition
from tests.core.artifacts.test_artifact_contracts import _fact
from tests.core.domain.selectors.test_model import _status_selector


def test_exact_artifact_values_reject_every_malformed_shape() -> None:
    with pytest.raises(TypeError, match="Boolean"):
        ArtifactBoolean(value=cast("bool", 1))
    with pytest.raises(TypeError, match="integer"):
        ArtifactInteger(value=cast("int", True))
    with pytest.raises(ValueError, match="canonical Decimal"):
        ArtifactDecimal(value="not-decimal")
    with pytest.raises(ValueError, match="finite canonical"):
        ArtifactDecimal(value="NaN")
    with pytest.raises(ValueError, match="finite canonical"):
        ArtifactDecimal(value="01")
    with pytest.raises(ValueError, match="bounded text"):
        ArtifactString(value=cast("str", 1))
    with pytest.raises(ValueError, match="bounded text"):
        ArtifactString(value="x" * 16_777_217)
    with pytest.raises(ValueError, match="Unicode scalar"):
        ArtifactString(value="\ud800")
    with pytest.raises(TypeError, match="array items"):
        ArtifactArray(items=cast("tuple[ArtifactValue, ...]", [ArtifactNull()]))
    with pytest.raises(TypeError, match="array items"):
        ArtifactArray(items=(cast("ArtifactValue", object()),))
    with pytest.raises(ValueError, match="member name"):
        ArtifactObjectMember(name=cast("str", 1), value=ArtifactNull())
    with pytest.raises(TypeError, match="exact value"):
        ArtifactObjectMember(name="x", value=cast("ArtifactValue", object()))
    with pytest.raises(TypeError, match="members must"):
        ArtifactObject(members=cast("tuple[ArtifactObjectMember, ...]", []))
    member = ArtifactObjectMember(name="x", value=ArtifactNull())
    with pytest.raises(TypeError, match="members must"):
        ArtifactObject(members=(cast("ArtifactObjectMember", object()),))
    with pytest.raises(ValueError, match="unique"):
        ArtifactObject(members=(member, member))

    values: tuple[ArtifactValue, ...] = (
        ArtifactNull(),
        ArtifactBoolean(value=True),
        ArtifactInteger(value=1),
        ArtifactDecimal(value="1.0"),
        ArtifactString(value=""),
        ArtifactArray(items=()),
        ArtifactObject(members=()),
    )
    assert all(is_artifact_value(item) for item in values)
    assert not is_artifact_value(object())


def test_common_contracts_reject_untyped_and_incoherent_amounts() -> None:
    artifact = _artifact()
    with pytest.raises(TypeError, match="creation instant"):
        msgspec.structs.replace(
            artifact.metadata,
            created_unix_nanoseconds=cast("int", True),
        )
    for field in ("project", "execution_policy", "artifact_profile", "redaction_profile"):
        with pytest.raises(TypeError, match=r"definition|Profile"):
            msgspec.structs.replace(artifact.provenance, **{field: object()})

    with pytest.raises(TypeError, match="amount unit"):
        ArtifactAmount(unit=cast("ArtifactAmountUnit", "count"), numerator=1)
    with pytest.raises(ValueError, match="numerator"):
        ArtifactAmount(unit=ArtifactAmountUnit.COUNT, numerator=-1)
    with pytest.raises(ValueError, match="positive integer"):
        ArtifactAmount(unit=ArtifactAmountUnit.RATIO, numerator=1, denominator=0)
    with pytest.raises(ValueError, match="positive integer"):
        ArtifactAmount(
            unit=ArtifactAmountUnit.RATIO,
            numerator=1,
            denominator=cast("int", True),
        )
    with pytest.raises(ValueError, match="denominator one"):
        ArtifactAmount(unit=ArtifactAmountUnit.COUNT, numerator=1, denominator=2)
    ratio = ArtifactAmount(unit=ArtifactAmountUnit.RATIO, numerator=3, denominator=2)
    assert ratio.fraction.numerator == 3

    amount = ArtifactAmount(unit=ArtifactAmountUnit.COUNT, numerator=1)
    with pytest.raises(TypeError, match="amounts must"):
        ArtifactBudget(
            resource="work",
            scope="run",
            limit=cast("ArtifactAmount", object()),
            used=amount,
            exhausted=False,
        )
    with pytest.raises(ValueError, match="one unit"):
        ArtifactBudget(
            resource="work",
            scope="run",
            limit=amount,
            used=ArtifactAmount(unit=ArtifactAmountUnit.BYTES, numerator=1),
            exhausted=True,
        )
    with pytest.raises(TypeError, match="marker must be bool"):
        ArtifactBudget(
            resource="work",
            scope="run",
            limit=amount,
            used=amount,
            exhausted=cast("bool", 1),
        )


def test_common_metric_blob_capture_and_failure_guards() -> None:
    metric = ArtifactMetric(name="a", value=1)
    with pytest.raises(TypeError, match="metrics"):
        require_metrics([metric], label="test")
    with pytest.raises(ValueError, match="unique and canonical"):
        require_metrics((ArtifactMetric(name="b", value=1), metric), label="test")
    with pytest.raises(ValueError, match="lowercase SHA"):
        ArtifactBlob(sha256="G" * 64, byte_count=0)
    with pytest.raises(ValueError, match="byte count"):
        ArtifactBlob(sha256="f" * 64, byte_count=-1)
    with pytest.raises(ValueError, match="media type"):
        ArtifactBlob(sha256="f" * 64, byte_count=0, media_type="")
    with pytest.raises(ValueError, match="requires only a blob"):
        ArtifactCapture(
            source_state=ArtifactSourceCaptureState.COMPLETE,
            extent=ArtifactCaptureExtent.COMPLETE,
            source_byte_count=0,
            blob=None,
        )
    with pytest.raises(ValueError, match="requires only a blob"):
        ArtifactCapture(
            source_state=ArtifactSourceCaptureState.COMPLETE,
            extent=ArtifactCaptureExtent.COMPLETE,
            source_byte_count=0,
            blob=ArtifactBlob(sha256="f" * 64, byte_count=0),
            reason=ArtifactOmissionReason.POLICY,
        )
    omitted_without_known_size = ArtifactCapture(
        source_state=ArtifactSourceCaptureState.STORAGE_FAILED,
        extent=ArtifactCaptureExtent.OMITTED,
        source_byte_count=None,
        blob=None,
        reason=ArtifactOmissionReason.STORAGE_FAILED,
    )
    assert omitted_without_known_size.source_byte_count is None
    with pytest.raises(TypeError, match="retryable"):
        ArtifactFailure(stage="x", code="y", retryable=cast("bool", 1))


def test_fact_evidence_execution_and_comparison_guards() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    comparison = artifact.comparisons[0]
    fact = execution.assertions[0]
    evidence = fact.evidence[0]
    named = evidence.values[0]

    with pytest.raises(TypeError, match="evidence values"):
        msgspec.structs.replace(evidence, values=cast("tuple[ArtifactNamedValue, ...]", []))
    duplicate = msgspec.structs.replace(named, name="actual")
    with pytest.raises(ValueError, match="unique and canonical"):
        msgspec.structs.replace(evidence, values=(duplicate, duplicate))
    with pytest.raises(ValueError, match="unique and canonical"):
        msgspec.structs.replace(
            evidence,
            values=(
                msgspec.structs.replace(named, name="z"),
                msgspec.structs.replace(named, name="a"),
            ),
        )

    invalid_fact_fields: tuple[tuple[str, object, str], ...] = (
        ("engine", "assertion", "engine"),
        ("rule", object(), "rule"),
        ("severity", "blocking", "severity"),
        ("outcome", "satisfied", "outcome"),
        ("evidence", [], "evidence"),
        ("issues", [ArtifactFailure(stage="x", code="y")], "issues"),
    )
    for field, value, message in invalid_fact_fields:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(fact, **{field: value})

    invalid_execution_fields: tuple[tuple[str, object, str], ...] = (
        ("subject", object(), "definitions"),
        ("environment", object(), "definitions"),
        ("outcome", "observed", "outcome"),
        ("failure", object(), "failure"),
        ("timings", [], "timings"),
        ("coordinates", [], "coordinates"),
        ("attempts", [], "attempts"),
        ("assertions", [], "assertions"),
    )
    for field, value, message in invalid_execution_fields:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(execution, **{field: value})
    coordinate = execution.coordinates[0]
    with pytest.raises(ValueError, match="axes must be unique"):
        msgspec.structs.replace(execution, coordinates=(coordinate, coordinate))
    with pytest.raises(ValueError, match="outcome contradicts"):
        msgspec.structs.replace(execution, attempts=())
    with pytest.raises(ValueError, match="only assertion"):
        msgspec.structs.replace(
            execution,
            assertions=(_fact(ArtifactFactEngine.DIFF, ArtifactFactOutcome.EQUIVALENT),),
        )

    invalid_comparison_fields: tuple[tuple[str, object, str], ...] = (
        ("subject", object(), "subject"),
        ("outcome", "evaluated", "outcome"),
        ("unavailable_execution_ids", [], "unavailable execution"),
        ("diffs", [], "diffs"),
    )
    for field, value, message in invalid_comparison_fields:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(comparison, **{field: value})
    with pytest.raises(ValueError, match="outcome contradicts"):
        msgspec.structs.replace(comparison, unavailable_execution_ids=("missing",))
    with pytest.raises(ValueError, match="only diff"):
        msgspec.structs.replace(
            comparison,
            diffs=(_fact(ArtifactFactEngine.ASSERTION, ArtifactFactOutcome.SATISFIED),),
        )


def test_http_contracts_reject_every_ambiguous_variant() -> None:
    artifact = _artifact()
    attempt = artifact.executions[0].attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    assert exchange.response is not None

    with pytest.raises(ValueError, match="field value"):
        ArtifactField(name="x", value=cast("str", 1))
    with pytest.raises(TypeError, match="redaction marker"):
        ArtifactField(name="x", value="y", redacted=cast("bool", 1))
    with pytest.raises(TypeError, match="request header"):
        msgspec.structs.replace(exchange.request, headers=cast("tuple[ArtifactField, ...]", []))
    with pytest.raises(TypeError, match="request body"):
        msgspec.structs.replace(exchange.request, body=object())
    for nanoseconds, reason in ((None, None), (1, "missing")):
        with pytest.raises(ValueError, match="exactly"):
            ArtifactTiming(name="total", nanoseconds=nanoseconds, reason=reason)
    with pytest.raises(ValueError, match="HTTP status"):
        msgspec.structs.replace(exchange.response, status=99)
    with pytest.raises(TypeError, match="response trailer"):
        msgspec.structs.replace(
            exchange.response,
            trailers=cast("tuple[ArtifactField, ...]", []),
        )
    with pytest.raises(TypeError, match="response body"):
        msgspec.structs.replace(exchange.response, body=object())

    invalid_exchange_fields: tuple[tuple[str, object, str], ...] = (
        ("request", object(), "request must"),
        ("request_omission", "policy", "request omission"),
        ("response", object(), "response must"),
        ("response_omission", "policy", "response omission"),
        ("failure", object(), "failure"),
        ("timings", [], "timings"),
    )
    for field, value, message in invalid_exchange_fields:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(exchange, **{field: value})
    with pytest.raises(ValueError, match="request or explicit omission"):
        msgspec.structs.replace(exchange, request=None, request_omission=None)
    with pytest.raises(ValueError, match="mutually exclusive"):
        msgspec.structs.replace(
            exchange,
            response_omission=ArtifactOmissionReason.POLICY,
        )
    with pytest.raises(ValueError, match="response, failure, or omission"):
        msgspec.structs.replace(exchange, response=None)
    duplicate_timing = ArtifactTiming(name="total", nanoseconds=1, reason=None)
    with pytest.raises(ValueError, match="timing names"):
        msgspec.structs.replace(exchange, timings=(duplicate_timing, duplicate_timing))

    invalid_attempt_fields: tuple[tuple[str, object, str], ...] = (
        ("exchanges", [], "exchanges"),
        ("timings", [], "timings"),
        ("terminal_pre_send_request", object(), "pre-send request"),
        ("terminal_pre_send_omission", "policy", "pre-send omission"),
        ("failure", object(), "failure"),
    )
    for field, value, message in invalid_attempt_fields:
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(attempt, **{field: value})
    failure = ArtifactFailure(stage="resolution", code="denied")
    with pytest.raises(ValueError, match="retained together"):
        msgspec.structs.replace(attempt, failure=failure)
    with pytest.raises(ValueError, match="requires exchanges"):
        ArtifactAttempt(attempt_id="a", exchanges=())
    with pytest.raises(ValueError, match="mutually exclusive"):
        ArtifactAttempt(
            attempt_id="a",
            exchanges=(),
            terminal_pre_send_request=exchange.request,
            terminal_pre_send_omission=ArtifactOmissionReason.POLICY,
            failure=failure,
        )


def test_lifecycle_policy_and_persistence_guards() -> None:
    artifact = _artifact()
    with pytest.raises(TypeError, match="completion kind"):
        ArtifactCompletion(kind=cast("object", "complete"), instant_unix_nanoseconds=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="instant"):
        ArtifactCompletion(
            kind=artifact.completion.kind,
            instant_unix_nanoseconds=cast("int", True),
        )
    with pytest.raises(TypeError, match="reasons"):
        msgspec.structs.replace(artifact.completion, reasons=cast("tuple[str, ...]", []))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(artifact.completion, reasons=("x", "x"))
    diagnostic_completion = ArtifactCompletion(
        kind=artifact.completion.kind,
        instant_unix_nanoseconds=1,
        stage="runtime",
        code="safe-code",
    )
    assert diagnostic_completion.stage == "runtime"

    metric = ArtifactMetric(name="violated", value=0)
    counts = ArtifactGateCounts(
        severity=ArtifactSeverity.BLOCKING,
        actual=(metric,),
        allowed=(metric,),
    )
    with pytest.raises(TypeError, match="severity"):
        msgspec.structs.replace(counts, severity="blocking")
    with pytest.raises(ValueError, match="dimensions"):
        msgspec.structs.replace(counts, allowed=(ArtifactMetric(name="error", value=0),))
    requested_gate = ArtifactGate(
        verdict=ArtifactGateVerdict.PASSED,
        profile=_definition("GateProfile", "g"),
        reasons=(),
        coverage=(ArtifactMetric(name="run-complete", value=1),),
        assessments=(counts,),
    )
    invalid_gate_fields: tuple[tuple[str, object, str], ...] = (
        ("verdict", "passed", "verdict"),
        ("profile", object(), "profile"),
        ("reasons", [], "reasons"),
        ("assessments", [], "assessments"),
    )
    for field, value, message in invalid_gate_fields:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(requested_gate, **{field: value})
    with pytest.raises(ValueError, match="not-requested"):
        msgspec.structs.replace(artifact.gate, profile=_definition("GateProfile", "g"))
    with pytest.raises(ValueError, match="passed artifact gate"):
        msgspec.structs.replace(requested_gate, reasons=("bad",))
    with pytest.raises(ValueError, match="failed artifact gate"):
        msgspec.structs.replace(requested_gate, verdict=ArtifactGateVerdict.FAILED)

    limits = ArtifactLimits(
        maximum_blobs=PositiveCount(value=1),
        maximum_blob_bytes=ByteCount(value=1),
        maximum_total_blob_bytes=ByteCount(value=1),
        maximum_manifest_bytes=ByteCount(value=1),
        maximum_report_bytes=ByteCount(value=1),
    )
    with pytest.raises(TypeError, match="flags"):
        ArtifactCapturePolicy(requests=cast("bool", 1))
    with pytest.raises(TypeError, match="blob limit"):
        msgspec.structs.replace(limits, maximum_blobs=CountLike())
    with pytest.raises(TypeError, match="byte limits"):
        msgspec.structs.replace(limits, maximum_blob_bytes=object())
    with pytest.raises(ValueError, match="aggregate"):
        msgspec.structs.replace(
            limits,
            maximum_blob_bytes=ByteCount(value=2),
        )
    policy = ArtifactPolicy(
        persist=ArtifactPersistMode.ALWAYS,
        unsafe_source=UnsafeSourceBehavior.OMIT,
        capture=ArtifactCapturePolicy(),
        limits=limits,
    )
    for field, value, message in (
        ("persist", "always", "persistence mode"),
        ("unsafe_source", "omit", "unsafe artifact"),
        ("capture", object(), "capture policy"),
        ("limits", object(), "limits"),
    ):
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(policy, **{field: value})

    selector = _status_selector()
    rule = RedactionRule(rule_id="r", selector=selector, action=RedactionAction.MASK)
    for field, value, message in (
        ("rule_id", "", "rule ID"),
        ("selector", object(), "selector"),
        ("action", "mask", "action"),
    ):
        with pytest.raises((TypeError, ValueError), match=message):
            msgspec.structs.replace(rule, **{field: value})
    with pytest.raises(TypeError, match="rules must"):
        RedactionPolicy(rules=cast("tuple[RedactionRule, ...]", [rule]))
    with pytest.raises(ValueError, match="unique"):
        RedactionPolicy(rules=(rule, rule))
    with pytest.raises(ValueError, match="marker"):
        RedactionPolicy(marker="")
    with pytest.raises(ValueError, match="marker"):
        RedactionPolicy(marker="x" * 65)
    with pytest.raises(ValueError, match="marker"):
        RedactionPolicy(marker=cast("str", 1))
    assert RedactionPolicy().marker == "<redacted>"

    failure = ArtifactStoreFailure(code=ArtifactStoreErrorCode.IO_FAILED)
    with pytest.raises(TypeError, match="failure code"):
        ArtifactStoreFailure(code=cast("ArtifactStoreErrorCode", "io-failed"))
    persisted = ArtifactPersistenceOutcome(
        status=ArtifactPersistenceStatus.PERSISTED,
        bundle_id="bundle",
    )
    with pytest.raises(TypeError, match="status"):
        msgspec.structs.replace(persisted, status="persisted")
    with pytest.raises(TypeError, match="failure must"):
        ArtifactPersistenceOutcome(
            status=ArtifactPersistenceStatus.FAILED,
            failure=cast("ArtifactStoreFailure", object()),
        )
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(persisted, failure=failure)
    with pytest.raises(ValueError, match="requires a failure"):
        ArtifactPersistenceOutcome(status=ArtifactPersistenceStatus.FAILED)


class CountLike:
    """Deliberately untyped positive-looking object."""

    value = 1


def test_compilation_and_top_level_artifact_guards_include_pre_send_blobs() -> None:
    artifact = _artifact()
    failure = ArtifactFailure(stage="artifact", code="rejected")
    created = ArtifactCompilationResult(
        status=ArtifactCompilationStatus.CREATED,
        artifact=artifact,
    )
    for field, value, message in (
        ("status", "created", "status"),
        ("artifact", object(), "RunArtifact"),
        ("failure", object(), "failure"),
    ):
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(created, **{field: value})
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(created, failure=failure)
    with pytest.raises(ValueError, match="requires a failure"):
        ArtifactCompilationResult(status=ArtifactCompilationStatus.REJECTED)

    with pytest.raises(TypeError, match="unsupported type"):
        msgspec.structs.replace(artifact, metadata=object())
    with pytest.raises(TypeError, match="immutable and typed"):
        msgspec.structs.replace(artifact, executions=[])
    execution = msgspec.structs.replace(artifact.executions[0], ordinal=1)
    with pytest.raises(ValueError, match=r"execution.*ordinals"):
        msgspec.structs.replace(artifact, executions=(execution,))
    comparison = msgspec.structs.replace(artifact.comparisons[0], ordinal=1)
    with pytest.raises(ValueError, match=r"comparison.*ordinals"):
        msgspec.structs.replace(artifact, comparisons=(comparison,))

    exchange = artifact.executions[0].attempts[0].exchanges[0]
    assert exchange.request is not None
    pre_send = ArtifactAttempt(
        attempt_id="pre-send",
        exchanges=(),
        terminal_pre_send_request=exchange.request,
        failure=ArtifactFailure(stage="resolution", code="denied"),
    )
    execution = msgspec.structs.replace(artifact.executions[0], attempts=(pre_send,))
    pre_send_artifact = msgspec.structs.replace(
        artifact,
        executions=(execution,),
        comparisons=(),
        summary=msgspec.structs.replace(
            artifact.summary,
            executions=(ArtifactMetric(name="observed", value=1),),
            comparisons=(),
            diffs=(),
        ),
        blobs=artifact.blobs[:1],
    )
    assert pre_send_artifact.blobs == artifact.blobs[:1]


def test_coverage_issue_rejects_untyped_reason_and_validates_optional_owner() -> None:
    issue = ArtifactCoverageIssue(
        source="response.body",
        extent=ArtifactCaptureExtent.OMITTED,
        reason=ArtifactOmissionReason.POLICY,
    )
    with pytest.raises(TypeError, match="reason"):
        msgspec.structs.replace(issue, reason="policy")
    with pytest.raises(ValueError, match="execution ID"):
        msgspec.structs.replace(issue, execution_id="")


def test_blob_closure_traverses_optional_request_and_response_body_branches() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    assert exchange.request.body is not None
    assert exchange.response is not None
    assert exchange.response.body is not None

    request_without_body = msgspec.structs.replace(exchange.request, body=None)
    requestless_exchange = msgspec.structs.replace(exchange, request=request_without_body)
    requestless_attempt = msgspec.structs.replace(attempt, exchanges=(requestless_exchange,))
    requestless_execution = msgspec.structs.replace(execution, attempts=(requestless_attempt,))
    requestless_artifact = msgspec.structs.replace(
        artifact,
        executions=(requestless_execution,),
        comparisons=(),
        summary=msgspec.structs.replace(
            artifact.summary,
            executions=(ArtifactMetric(name="observed", value=1),),
            comparisons=(),
            diffs=(),
        ),
        blobs=(exchange.response.body.blob,),
    )
    assert requestless_artifact.blobs == (exchange.response.body.blob,)

    failed_exchange = msgspec.structs.replace(
        exchange,
        response=None,
        failure=ArtifactFailure(stage="read", code="failed"),
    )
    failed_attempt = msgspec.structs.replace(attempt, exchanges=(failed_exchange,))
    failed_execution = msgspec.structs.replace(execution, attempts=(failed_attempt,))
    failed_artifact = msgspec.structs.replace(
        artifact,
        executions=(failed_execution,),
        comparisons=(),
        summary=msgspec.structs.replace(
            artifact.summary,
            executions=(ArtifactMetric(name="observed", value=1),),
            comparisons=(),
            diffs=(),
        ),
        blobs=(exchange.request.body.blob,),
    )
    assert failed_artifact.blobs == (exchange.request.body.blob,)


def _unused_type_assertions(
    _definition_value: ArtifactDefinition,
    _capture_value: ArtifactCapture,
    _coordinate: ArtifactCoordinate,
    _evidence: ArtifactEvidence,
    _execution: ArtifactExecution,
    _comparison: ArtifactComparison,
    _request: ArtifactRequest,
    _response: ArtifactResponse,
    _exchange: ArtifactExchange,
    _fact_value: ArtifactFact,
    _selector_value: CompiledSelector,
) -> None:
    """Keep imported public types visible to strict static analysis."""
