"""Exhaustive branch tests for RunResult-to-artifact compilation helpers."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.artifacts.memory import InMemorySanitizedBlobStore
from comparo.core.artifacts import compiler
from comparo.core.artifacts.capture import ArtifactCaptureBatch
from comparo.core.artifacts.capture import ArtifactCaptureUnavailableError
from comparo.core.artifacts.capture import ArtifactCapturingDriver
from comparo.core.artifacts.common import ArtifactAmountUnit
from comparo.core.artifacts.common import ArtifactCompletionKind
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.gates.model import GateEvaluation
from comparo.core.domain.gates.model import GateResult
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.lifecycle import PartialRunReason
from comparo.core.domain.lifecycle import RunCancelled
from comparo.core.domain.lifecycle import RunInternalError
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.lifecycle import RunRejected
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import UtcInstant
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime import run_prepared
from comparo.core.runtime.models import BlockedExecution
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import RunResult
from comparo.core.runtime.models import UnavailableExecution
from tests.core.artifacts.test_artifact_contracts import _artifact
from tests.core.artifacts.test_capture_compiler import _EmptyBodies
from tests.core.artifacts.test_capture_compiler import _EmptySnapshotProvider
from tests.core.artifacts.test_capture_compiler import _policy
from tests.core.artifacts.test_capture_compiler import _prepared
from tests.core.domain.http.factories import definition
from tests.core.runtime.conftest import FixedClock
from tests.core.runtime.test_kernel_integration import KernelPreparation
from tests.core.runtime.test_vertical_runtime import _driver


def _boundary() -> SanitizationBoundary:
    boundary = SanitizationBoundary(_policy().limits.sanitization)
    boundary.seal()
    return boundary


async def _state() -> tuple[PreparedRun, RunResult, ArtifactCapturingDriver]:
    prepared = _prepared()
    cancellation = RunCancellation()
    inner, _transport = _driver(prepared, cancellation)
    policy = _policy()
    capture = ArtifactCapturingDriver(
        driver=inner,
        snapshots=_EmptySnapshotProvider(prepared.plan.run_id),
        bodies=_EmptyBodies(),
        blobs=InMemorySanitizedBlobStore(policy.limits),
        policy=policy,
        redaction=RedactionPolicy(),
    )
    result = await run_prepared(
        prepared,
        driver=capture,
        evaluation=KernelPreparation(),
        cancellation=cancellation,
        clock=FixedClock(),
        events=RecordingEventSink(),
    )
    return prepared, result, capture


def _compile(
    prepared: PreparedRun,
    result: RunResult,
    capture: ArtifactCapturingDriver,
    policy: ArtifactPolicy,
) -> compiler.ArtifactCompilationResult:
    return compiler.compile_run_artifact(
        prepared,
        result,
        capture,
        policy=policy,
        artifact_id="artifact-test",
        created_unix_nanoseconds=1,
        tool_version="0.2.0",
    )


def test_profile_definition_projects_named_and_inline_provenance() -> None:
    owner = definition(ResourceKind.REQUEST, "request")
    inline = InlineDefinitionRef(
        kind=ResourceKind.ARTIFACT_PROFILE,
        origin=InlineDefinitionOrigin(
            owner=owner.identity,
            fragment=InlineFragment(value="#artifacts"),
        ),
        fingerprint=owner.fingerprint,
    )

    assert compiler._profile_definition(None) is None
    assert compiler._profile_definition(owner) == compiler._definition(owner)
    projected = compiler._profile_definition(inline)
    assert projected is not None
    assert projected.namespace == owner.identity.namespace.value
    assert projected.kind == ResourceKind.ARTIFACT_PROFILE.value
    assert projected.resource_id == f"{owner.identity.resource_id.value}#artifacts"
    assert projected.fingerprint == owner.fingerprint.digest.hex_value


def test_public_compiler_validates_every_boundary_before_releasing_capture() -> None:
    prepared, result, capture = asyncio.run(_state())
    policy = _policy()
    with pytest.raises(TypeError, match="PreparedRun and RunResult"):
        compiler.compile_run_artifact(
            cast("PreparedRun", object()),
            result,
            capture,
            policy=policy,
            artifact_id="artifact-test",
            created_unix_nanoseconds=1,
            tool_version="0.2.0",
        )
    with pytest.raises(TypeError, match="PreparedRun and RunResult"):
        compiler.compile_run_artifact(
            prepared,
            cast("RunResult", object()),
            capture,
            policy=policy,
            artifact_id="artifact-test",
            created_unix_nanoseconds=1,
            tool_version="0.2.0",
        )

    mismatched = RunResult(
        run_id=RunId(value=UUID(int=999)),
        completion=result.completion,
        executions=(),
        comparisons=(),
        gate=result.gate,
        ledgers=(),
    )
    with pytest.raises(ValueError, match="different runs"):
        compiler.compile_run_artifact(
            prepared,
            mismatched,
            capture,
            policy=policy,
            artifact_id="artifact-test",
            created_unix_nanoseconds=1,
            tool_version="0.2.0",
        )
    with pytest.raises(TypeError, match="typed capture and policy"):
        compiler.compile_run_artifact(
            prepared,
            result,
            cast("ArtifactCapturingDriver", object()),
            policy=policy,
            artifact_id="artifact-test",
            created_unix_nanoseconds=1,
            tool_version="0.2.0",
        )
    with pytest.raises(TypeError, match="typed capture and policy"):
        compiler.compile_run_artifact(
            prepared,
            result,
            capture,
            policy=cast("ArtifactPolicy", object()),
            artifact_id="artifact-test",
            created_unix_nanoseconds=1,
            tool_version="0.2.0",
        )

    assert _compile(prepared, result, capture, policy).artifact is not None


def test_compilation_result_rejects_contradictory_or_untyped_products() -> None:
    failure = ArtifactFailure(stage="artifact", code="artifact.failed")
    with pytest.raises(TypeError, match="status must be typed"):
        compiler.ArtifactCompilationResult(
            status=cast("compiler.ArtifactCompilationStatus", "created")
        )
    with pytest.raises(TypeError, match="product must be RunArtifact"):
        compiler.ArtifactCompilationResult(
            status=compiler.ArtifactCompilationStatus.CREATED,
            artifact=cast("RunArtifact", object()),
        )
    with pytest.raises(TypeError, match="failure must be ArtifactFailure"):
        compiler.ArtifactCompilationResult(
            status=compiler.ArtifactCompilationStatus.REJECTED,
            failure=cast("ArtifactFailure", object()),
        )
    with pytest.raises(ValueError, match="contradicts its product"):
        compiler.ArtifactCompilationResult(
            status=compiler.ArtifactCompilationStatus.CREATED,
            failure=failure,
        )
    with pytest.raises(ValueError, match="requires a failure"):
        compiler.ArtifactCompilationResult(status=compiler.ArtifactCompilationStatus.REJECTED)


def test_compiler_rejects_a_policy_different_from_live_capture_and_releases_it() -> None:
    prepared, result, capture = asyncio.run(_state())
    different = msgspec.structs.replace(
        _policy(),
        capture=msgspec.structs.replace(_policy().capture, evidence=False),
    )

    with pytest.raises(ValueError, match="policy differs from capture policy"):
        _compile(prepared, result, capture, different)
    with pytest.raises(
        ArtifactCaptureUnavailableError,
        match="sanitization boundary is unavailable",
    ):
        _ = capture.boundary


def test_compiler_preserves_a_value_free_capture_failure() -> None:
    prepared, result, capture = asyncio.run(_state())
    failure = ArtifactFailure(stage="artifact-capture", code="capture-failed")
    capture._batch = ArtifactCaptureBatch(executions=(), coverage_issues=(), failure=failure)

    compiled = _compile(prepared, result, capture, _policy())

    assert compiled.status is compiler.ArtifactCompilationStatus.REJECTED
    assert compiled.failure == failure
    assert compiled.artifact is None


def test_compiler_rejects_ambiguous_per_subject_profile_provenance() -> None:
    prepared, result, capture = asyncio.run(_state())
    subject = prepared.effective.subjects[0]
    different = msgspec.structs.replace(
        subject,
        profiles=msgspec.structs.replace(
            subject.profiles,
            artifacts=definition(ResourceKind.ARTIFACT_PROFILE, "artifact-profile"),
        ),
    )
    prepared = msgspec.structs.replace(
        prepared,
        effective=msgspec.structs.replace(
            prepared.effective,
            subjects=(subject, different),
        ),
    )

    compiled = _compile(prepared, result, capture, _policy())

    assert compiled.status is compiler.ArtifactCompilationStatus.REJECTED
    assert compiled.failure == ArtifactFailure(
        stage="artifact-compilation",
        code="ambiguous-effective-profiles",
    )
    with pytest.raises(ArtifactCaptureUnavailableError, match="sanitization boundary"):
        _ = capture.boundary


def test_observed_execution_requires_a_safe_capture_and_comparisons_are_typed() -> None:
    _prepared_run, result, capture = asyncio.run(_state())
    try:
        with pytest.raises(ValueError, match="missing its safe HTTP capture"):
            compiler._execution(result.executions[0], {}, _boundary(), False)
        with pytest.raises(TypeError, match="ComparisonRecord"):
            compiler._comparison(object(), _boundary(), False)
    finally:
        capture.release()


def test_execution_failure_projection_covers_every_unobserved_outcome() -> None:
    _prepared_run, result, capture = asyncio.run(_state())
    boundary = _boundary()
    cases = (
        (
            UnavailableExecution(
                stage=DiagnosticStage.TRANSPORT,
                code=DiagnosticCode(value="http.unavailable"),
            ),
            "transport",
            "http.unavailable",
        ),
        (
            BlockedExecution(
                stage=DiagnosticStage.AUTHORIZATION,
                code=DiagnosticCode(value="http.blocked"),
            ),
            "authorization",
            "http.blocked",
        ),
        (
            CancelledExecution(source=CancellationSource.DEADLINE),
            "execution",
            "cancelled.deadline",
        ),
    )
    try:
        for outcome, stage, code in cases:
            record = msgspec.structs.replace(
                result.executions[0],
                outcome=outcome,
                assertions=(),
            )
            projected = compiler._execution(record, {}, boundary, False)
            assert projected.failure == ArtifactFailure(stage=stage, code=code)
            assert not projected.assertions
            assert not projected.attempts
            assert projected.outcome is compiler._execution_outcome(record)
        assert compiler._execution_failure(result.executions[0]) is None
    finally:
        capture.release()


def test_completion_projection_covers_every_terminal_lifecycle() -> None:
    _prepared_run, result, capture = asyncio.run(_state())
    diagnostic = Diagnostic(
        code=DiagnosticCode(value="run.failed"),
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.ARTIFACT,
    )
    cases = (
        (
            RunPartial(
                reasons=(PartialRunReason.ARTIFACT_INCOMPLETE,),
                completed_at=UtcInstant(unix_nanoseconds=2),
            ),
            ArtifactCompletionKind.PARTIAL,
        ),
        (
            RunCancelled(
                source=CancellationSource.USER,
                stage=DiagnosticStage.TRANSPORT,
                cancelled_at=UtcInstant(unix_nanoseconds=3),
            ),
            ArtifactCompletionKind.CANCELLED,
        ),
        (RunRejected(diagnostics=(diagnostic,)), ArtifactCompletionKind.REJECTED),
        (RunInternalError(diagnostic=diagnostic), ArtifactCompletionKind.INTERNAL_ERROR),
    )
    try:
        for completion, expected in cases:
            replaced = msgspec.structs.replace(result, completion=completion)
            assert compiler._completion(replaced).kind is expected
    finally:
        capture.release()


def test_not_requested_gate_has_no_profile_or_assessments() -> None:
    evaluation = GateEvaluation(
        result=GateResult(
            verdict=GateVerdict.NOT_REQUESTED,
            profile=None,
        ),
        coverage=None,
    )
    artifact = compiler._gate(evaluation)
    assert artifact.verdict is ArtifactGateVerdict.NOT_REQUESTED
    assert artifact.profile is None
    assert not artifact.coverage


def test_multiple_effective_artifact_profiles_are_explicitly_ambiguous() -> None:
    prepared = _prepared()
    subject = prepared.effective.subjects[0]
    artifact_profile = definition(ResourceKind.ARTIFACT_PROFILE, "full")
    second = msgspec.structs.replace(
        subject,
        profiles=msgspec.structs.replace(subject.profiles, artifacts=artifact_profile),
    )
    effective = msgspec.structs.replace(
        prepared.effective,
        subjects=(subject, second),
    )
    prepared = msgspec.structs.replace(prepared, effective=effective)

    assert not compiler._profiles_are_uniform(prepared)
    with pytest.raises(ValueError, match="uniform effective profile"):
        compiler._single_profile(prepared, artifact=True)


def test_resource_collection_closes_optional_provenance_and_gate_definitions() -> None:
    prepared = _prepared()
    artifact = _artifact()
    artifact_profile, redaction_profile, gate_profile = artifact.resources[:3]
    provenance = msgspec.structs.replace(
        artifact.provenance,
        artifact_profile=artifact_profile,
        redaction_profile=redaction_profile,
    )
    gate = msgspec.structs.replace(
        artifact.gate,
        verdict=ArtifactGateVerdict.PASSED,
        profile=gate_profile,
    )

    resources = compiler._resources(
        prepared,
        artifact.executions,
        artifact.comparisons,
        provenance,
        gate,
    )

    assert artifact_profile in resources
    assert redaction_profile in resources
    assert gate_profile in resources
    assert artifact.comparisons[0].subject in resources


def test_budget_amount_projection_covers_every_exact_unit() -> None:
    values = (
        (Count(value=2), ArtifactAmountUnit.COUNT, 2, 1),
        (ByteCount(value=3), ArtifactAmountUnit.BYTES, 3, 1),
        (Duration(nanoseconds=4), ArtifactAmountUnit.NANOSECONDS, 4, 1),
        (
            Ratio(numerator=2, denominator=PositiveCount(value=3)),
            ArtifactAmountUnit.RATIO,
            2,
            3,
        ),
    )
    for value, unit, numerator, denominator in values:
        amount = compiler._amount(value)
        assert (amount.unit, amount.numerator, amount.denominator) == (
            unit,
            numerator,
            denominator,
        )


def test_body_capture_iterator_visits_request_response_and_pre_send_bodies() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    assert exchange.request.body is not None
    assert exchange.response is not None
    assert exchange.response.body is not None
    empty_request = msgspec.structs.replace(exchange.request, body=None)
    empty_response = msgspec.structs.replace(exchange.response, body=None)
    empty_exchange = msgspec.structs.replace(
        exchange,
        request=empty_request,
        response=empty_response,
    )
    failed_exchange = msgspec.structs.replace(
        exchange,
        request=None,
        request_omission=ArtifactOmissionReason.POLICY,
        response=None,
        failure=ArtifactFailure(stage="dns", code="http.failed"),
    )
    pre_send_attempt = msgspec.structs.replace(
        attempt,
        exchanges=(exchange, empty_exchange, failed_exchange),
        terminal_pre_send_request=exchange.request,
        failure=ArtifactFailure(stage="dns", code="http.failed"),
    )
    empty_pre_send_attempt = msgspec.structs.replace(
        attempt,
        exchanges=(empty_exchange,),
        terminal_pre_send_request=empty_request,
        failure=ArtifactFailure(stage="dns", code="http.failed"),
    )
    execution = msgspec.structs.replace(
        execution,
        attempts=(pre_send_attempt, empty_pre_send_attempt),
    )

    captures = tuple(compiler._body_captures((execution,)))

    assert captures == (
        exchange.request.body,
        exchange.response.body,
        exchange.request.body,
    )
