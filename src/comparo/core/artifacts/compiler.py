"""Compile live runtime products into one independently versioned safe RunArtifact."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
from hashlib import sha256
from typing import final

import msgspec

from comparo.core.artifacts.capture import ArtifactCaptureBatch
from comparo.core.artifacts.capture import ArtifactCapturingDriver
from comparo.core.artifacts.capture import CapturedExecutionHttp
from comparo.core.artifacts.common import ARTIFACT_API_VERSION
from comparo.core.artifacts.common import ArtifactAmount
from comparo.core.artifacts.common import ArtifactAmountUnit
from comparo.core.artifacts.common import ArtifactBudget
from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactComparisonOutcome
from comparo.core.artifacts.common import ArtifactCompletionKind
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactExecutionOutcome
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.common import ArtifactMetadata
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.common import ArtifactProvenance
from comparo.core.artifacts.common import ArtifactRunAnnotations
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.fact_compiler import compile_assertion_fact
from comparo.core.artifacts.fact_compiler import compile_diff_fact
from comparo.core.artifacts.facts import ArtifactComparison
from comparo.core.artifacts.facts import ArtifactCoordinate
from comparo.core.artifacts.facts import ArtifactExecution
from comparo.core.artifacts.facts import ArtifactSummary
from comparo.core.artifacts.lifecycle import ArtifactCompletion
from comparo.core.artifacts.lifecycle import ArtifactGate
from comparo.core.artifacts.lifecycle import ArtifactGateCounts
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.artifacts.values import ArtifactString
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.gates.model import GateEvaluation
from comparo.core.domain.gates.model import GateRuleCounts
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.lifecycle import RunCancelled
from comparo.core.domain.lifecycle import RunComplete
from comparo.core.domain.lifecycle import RunInternalError
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.lifecycle import RunRejected
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.planning.models import DefinitionHandle
from comparo.core.planning.models import PreparedRun
from comparo.core.runtime.models import BlockedExecution
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import EvaluatedComparison
from comparo.core.runtime.models import ExecutionRecord
from comparo.core.runtime.models import IncompleteComparison
from comparo.core.runtime.models import ObservedExecution
from comparo.core.runtime.models import RunResult
from comparo.core.runtime.models import UnavailableExecution


class ArtifactCompilationStatus(DomainStrEnum):
    """Closed compilation states without coupling to persistence policy."""

    CREATED = "created"
    REJECTED = "rejected"


@final
class ArtifactCompilationResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one safe artifact or a value-free rejection failure."""

    status: ArtifactCompilationStatus
    artifact: RunArtifact | None = None
    failure: ArtifactFailure | None = None

    def __post_init__(self) -> None:
        """Keep created and rejected compilation products disjoint."""
        if type(self.status) is not ArtifactCompilationStatus:
            raise TypeError("artifact compilation status must be typed")
        if self.artifact is not None and type(self.artifact) is not RunArtifact:
            raise TypeError("artifact compilation product must be RunArtifact")
        if self.failure is not None and type(self.failure) is not ArtifactFailure:
            raise TypeError("artifact compilation failure must be ArtifactFailure")
        created = self.status is ArtifactCompilationStatus.CREATED
        if created != (self.artifact is not None and self.failure is None):
            raise ValueError("artifact compilation status contradicts its product")
        if not created and self.failure is None:
            raise ValueError("rejected artifact compilation requires a failure")


def compile_run_artifact(
    prepared: PreparedRun,
    result: RunResult,
    capture: ArtifactCapturingDriver,
    *,
    policy: ArtifactPolicy,
    artifact_id: str,
    created_unix_nanoseconds: int,
    tool_version: str,
    annotations: ArtifactRunAnnotations | None = None,
) -> ArtifactCompilationResult:
    """Cross the sole boundary and release all retained secret match patterns afterward."""
    if type(prepared) is not PreparedRun or type(result) is not RunResult:
        raise TypeError("artifact compilation requires PreparedRun and RunResult")
    if result.run_id != prepared.plan.run_id:
        raise ValueError("artifact compilation inputs belong to different runs")
    if type(capture) is not ArtifactCapturingDriver or type(policy) is not ArtifactPolicy:
        raise TypeError("artifact compilation requires typed capture and policy")
    try:
        if annotations is not None and type(annotations) is not ArtifactRunAnnotations:
            raise TypeError("artifact compilation annotations must be typed or absent")
        if capture.policy != policy:
            raise ValueError("artifact compilation policy differs from capture policy")
        batch = capture.batch
        if batch.failure is not None:
            return ArtifactCompilationResult(
                status=ArtifactCompilationStatus.REJECTED,
                failure=batch.failure,
            )
        if not _profiles_are_uniform(prepared):
            return ArtifactCompilationResult(
                status=ArtifactCompilationStatus.REJECTED,
                failure=ArtifactFailure(
                    stage="artifact-compilation",
                    code="ambiguous-effective-profiles",
                ),
            )
        artifact = _compile(
            prepared,
            result,
            batch,
            capture,
            policy=policy,
            artifact_id=artifact_id,
            created_unix_nanoseconds=created_unix_nanoseconds,
            tool_version=tool_version,
            annotations=annotations,
        )
        return ArtifactCompilationResult(
            status=ArtifactCompilationStatus.CREATED,
            artifact=artifact,
        )
    finally:
        capture.release()


def _compile(
    prepared: PreparedRun,
    result: RunResult,
    batch: ArtifactCaptureBatch,
    capture: ArtifactCapturingDriver,
    *,
    policy: ArtifactPolicy,
    artifact_id: str,
    created_unix_nanoseconds: int,
    tool_version: str,
    annotations: ArtifactRunAnnotations | None,
) -> RunArtifact:
    by_execution = {item.execution_id: item for item in batch.executions}
    boundary = capture.boundary
    retain_evidence = policy.capture.evidence and not capture.has_custom_redaction
    executions = tuple(
        _execution(record, by_execution, boundary, retain_evidence) for record in result.executions
    )
    comparisons = tuple(
        _comparison(record, boundary, retain_evidence) for record in result.comparisons
    )
    blobs = tuple(
        sorted(
            {
                capture_record.blob
                for capture_record in _body_captures(executions)
                if capture_record.blob is not None
            },
            key=lambda item: item.sha256,
        )
    )
    provenance = ArtifactProvenance(
        project=_definition(prepared.project.project.definition),
        execution_policy=_definition(prepared.authority.policy),
        artifact_profile=_single_profile(prepared, artifact=True),
        redaction_profile=_single_profile(prepared, artifact=False),
        configuration_fingerprint=_configuration_fingerprint(prepared),
        plan_fingerprint=prepared.plan.fingerprint.digest.hex_value,
        ordering=prepared.plan.ordering.value,
        execution_profile=_execution_profile(prepared),
    )
    gate = _gate(result.gate)
    resources = _resources(prepared, executions, comparisons, provenance, gate)
    return RunArtifact(
        api_version=ARTIFACT_API_VERSION,
        metadata=ArtifactMetadata(
            artifact_id=artifact_id,
            run_id=str(result.run_id.value),
            invocation_id=str(prepared.invocation.invocation_id.value),
            created_unix_nanoseconds=created_unix_nanoseconds,
            tool_name="comparo",
            tool_version=tool_version,
            configuration_api_version=prepared.project.api_version,
        ),
        provenance=provenance,
        completion=_completion(result),
        gate=gate,
        summary=_summary(executions, comparisons),
        executions=executions,
        comparisons=comparisons,
        resources=resources,
        budgets=tuple(_budget(item) for item in result.ledgers),
        blobs=blobs,
        coverage_issues=batch.coverage_issues,
        annotations=annotations,
    )


def _execution(
    record: ExecutionRecord,
    captures: dict[str, CapturedExecutionHttp],
    boundary: SanitizationBoundary,
    retain_evidence: bool,
) -> ArtifactExecution:
    execution_id = str(record.work.scope.execution_id.value)
    observed = type(record.outcome) is ObservedExecution
    captured = captures.get(execution_id)
    if observed and captured is None:
        raise ValueError("observed execution is missing its safe HTTP capture")
    facts = (
        tuple(
            compile_assertion_fact(item.fact, boundary, retain_evidence=retain_evidence)
            for item in record.assertions
        )
        if observed
        else ()
    )
    return ArtifactExecution(
        ordinal=record.work.ordinal.value,
        execution_id=execution_id,
        subject=_definition(record.work.subject),
        environment=_definition(record.work.environment),
        cell_id=str(record.work.trial.cell.cell_id.value),
        trial_id=str(record.work.trial.trial_id.value),
        coordinates=tuple(
            ArtifactCoordinate(
                axis=item.binding.value,
                value=ArtifactString(value=item.case_id.value),
            )
            for item in record.work.trial.cell.matrix_cases
        ),
        outcome=_execution_outcome(record),
        failure=_execution_failure(record),
        timings=() if captured is None else captured.timings,
        attempts=() if captured is None else captured.attempts,
        assertions=facts,
    )


def _comparison(
    record: object,
    boundary: SanitizationBoundary,
    retain_evidence: bool,
) -> ArtifactComparison:
    from comparo.core.runtime.models import ComparisonRecord

    if type(record) is not ComparisonRecord:
        raise TypeError("artifact comparison compilation requires ComparisonRecord")
    outcome = record.outcome
    diffs = (
        tuple(
            compile_diff_fact(item.fact, boundary, retain_evidence=retain_evidence)
            for item in outcome.diffs
        )
        if type(outcome) is EvaluatedComparison
        else ()
    )
    unavailable = (
        tuple(str(item.value) for item in outcome.unavailable_executions)
        if type(outcome) is IncompleteComparison
        else ()
    )
    return ArtifactComparison(
        ordinal=record.work.ordinal.value,
        comparison_id=str(record.work.comparison_id.value),
        subject=_definition(record.work.subject),
        cell_id=str(record.work.trial.cell.cell_id.value),
        trial_id=str(record.work.trial.trial_id.value),
        baseline_execution_id=str(record.work.baseline_execution_id.value),
        candidate_execution_id=str(record.work.candidate_execution_id.value),
        outcome=(
            ArtifactComparisonOutcome.EVALUATED
            if type(outcome) is EvaluatedComparison
            else ArtifactComparisonOutcome.INCOMPLETE
        ),
        unavailable_execution_ids=unavailable,
        diffs=diffs,
    )


def _execution_outcome(record: ExecutionRecord) -> ArtifactExecutionOutcome:
    return {
        ObservedExecution: ArtifactExecutionOutcome.OBSERVED,
        UnavailableExecution: ArtifactExecutionOutcome.UNAVAILABLE,
        BlockedExecution: ArtifactExecutionOutcome.BLOCKED,
        CancelledExecution: ArtifactExecutionOutcome.CANCELLED,
    }[type(record.outcome)]


def _execution_failure(record: ExecutionRecord) -> ArtifactFailure | None:
    outcome = record.outcome
    if type(outcome) is ObservedExecution:
        return None
    if type(outcome) is UnavailableExecution or type(outcome) is BlockedExecution:
        return ArtifactFailure(stage=outcome.stage.value, code=outcome.code.value)
    if type(outcome) is CancelledExecution:
        return ArtifactFailure(stage="execution", code=f"cancelled.{outcome.source.value}")
    raise TypeError("execution outcome has an unsupported type")  # pragma: no cover


def _completion(result: RunResult) -> ArtifactCompletion:
    value = result.completion
    if type(value) is RunComplete:
        return ArtifactCompletion(
            kind=ArtifactCompletionKind.COMPLETE,
            instant_unix_nanoseconds=value.completed_at.unix_nanoseconds,
        )
    if type(value) is RunPartial:
        return ArtifactCompletion(
            kind=ArtifactCompletionKind.PARTIAL,
            instant_unix_nanoseconds=value.completed_at.unix_nanoseconds,
            reasons=tuple(item.value for item in value.reasons),
        )
    if type(value) is RunCancelled:
        return ArtifactCompletion(
            kind=ArtifactCompletionKind.CANCELLED,
            instant_unix_nanoseconds=value.cancelled_at.unix_nanoseconds,
            reasons=(value.source.value,),
            stage=value.stage.value,
        )
    if type(value) is RunRejected:
        diagnostic = value.diagnostics[0]
        return ArtifactCompletion(
            kind=ArtifactCompletionKind.REJECTED,
            instant_unix_nanoseconds=None,
            stage=diagnostic.stage.value,
            code=diagnostic.code.value,
        )
    if type(value) is not RunInternalError:  # pragma: no cover - closed lifecycle union
        raise TypeError("artifact completion has an unsupported type")
    return ArtifactCompletion(
        kind=ArtifactCompletionKind.INTERNAL_ERROR,
        instant_unix_nanoseconds=None,
        stage=value.diagnostic.stage.value,
        code=value.diagnostic.code.value,
    )


def _gate(evaluation: GateEvaluation) -> ArtifactGate:
    verdict = {
        GateVerdict.PASSED: ArtifactGateVerdict.PASSED,
        GateVerdict.FAILED: ArtifactGateVerdict.FAILED,
        GateVerdict.NOT_REQUESTED: ArtifactGateVerdict.NOT_REQUESTED,
    }[evaluation.result.verdict]
    if verdict is ArtifactGateVerdict.NOT_REQUESTED:
        return ArtifactGate(
            verdict=verdict,
            profile=None,
            reasons=(),
            coverage=(),
            assessments=(),
        )
    coverage = evaluation.coverage
    if coverage is None:  # pragma: no cover - GateEvaluation invariant
        raise ValueError("requested gate is missing coverage")
    profile = evaluation.result.profile
    if profile is None:  # pragma: no cover - GateEvaluation invariant
        raise ValueError("requested gate is missing its profile")
    return ArtifactGate(
        verdict=verdict,
        profile=_definition(profile),
        reasons=tuple(item.value for item in evaluation.result.reasons),
        coverage=tuple(
            sorted(
                (
                    ArtifactMetric(
                        name="assertion-covered",
                        value=coverage.covered_assertion_rules.value,
                    ),
                    ArtifactMetric(
                        name="assertion-eligible",
                        value=coverage.eligible_assertion_rules.value,
                    ),
                    ArtifactMetric(name="diff-covered", value=coverage.covered_diff_rules.value),
                    ArtifactMetric(name="diff-eligible", value=coverage.eligible_diff_rules.value),
                    ArtifactMetric(name="run-complete", value=int(coverage.run_complete)),
                ),
                key=lambda item: item.name,
            )
        ),
        assessments=tuple(
            ArtifactGateCounts(
                severity=_artifact_severity(item.severity.value),
                actual=_gate_counts(item.actual),
                allowed=_gate_counts(item.allowed),
            )
            for item in evaluation.severities
        ),
    )


def _gate_counts(counts: GateRuleCounts) -> tuple[ArtifactMetric, ...]:
    return tuple(
        ArtifactMetric(name=name, value=value.value)
        for name, value in sorted(
            (
                ("drifted", counts.drifted_rules),
                ("error", counts.error_rules),
                ("incomparable", counts.incomparable_rules),
                ("incomplete", counts.incomplete_rules),
                ("unavailable", counts.unavailable_rules),
                ("violated", counts.violated_rules),
            )
        )
    )


def _summary(
    executions: tuple[ArtifactExecution, ...],
    comparisons: tuple[ArtifactComparison, ...],
) -> ArtifactSummary:
    assertion_outcomes = tuple(
        fact.outcome.value for item in executions for fact in item.assertions
    )
    diff_outcomes = tuple(fact.outcome.value for item in comparisons for fact in item.diffs)
    return ArtifactSummary(
        executions=_tallies(item.outcome.value for item in executions),
        comparisons=_tallies(item.outcome.value for item in comparisons),
        assertions=_tallies(assertion_outcomes),
        diffs=_tallies(diff_outcomes),
    )


def _tallies(values: Iterable[str]) -> tuple[ArtifactMetric, ...]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return tuple(ArtifactMetric(name=name, value=counts[name]) for name in sorted(counts))


def _definition(value: DefinitionRef) -> ArtifactDefinition:
    identity = value.identity
    return ArtifactDefinition(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=value.fingerprint.digest.hex_value,
    )


def _profile_definition(value: DefinitionHandle | None) -> ArtifactDefinition | None:
    if value is None:
        return None
    if type(value) is DefinitionRef:
        return _definition(value)
    inline = value
    owner = inline.origin.owner
    return ArtifactDefinition(
        namespace=owner.namespace.value,
        kind=inline.kind.value,
        resource_id=f"{owner.resource_id.value}{inline.origin.fragment.value}",
        fingerprint=inline.fingerprint.digest.hex_value,
    )


def _profiles_are_uniform(prepared: PreparedRun) -> bool:
    artifact_profiles = {item.profiles.artifacts for item in prepared.effective.subjects}
    redaction_profiles = {item.profiles.redaction for item in prepared.effective.subjects}
    return len(artifact_profiles) == 1 and len(redaction_profiles) == 1


def _single_profile(prepared: PreparedRun, *, artifact: bool) -> ArtifactDefinition | None:
    handles = {
        subject.profiles.artifacts if artifact else subject.profiles.redaction
        for subject in prepared.effective.subjects
    }
    if len(handles) != 1:
        raise ValueError("artifact compilation requires one uniform effective profile")
    return _profile_definition(next(iter(handles)))


def _execution_profile(prepared: PreparedRun) -> ArtifactDefinition | None:
    selected = prepared.invocation.execution_profile
    if selected is None:
        return None
    resource = prepared.project.find(ResourceKind.EXECUTION_PROFILE, selected)
    if resource is None:  # pragma: no cover - admitted planner invariant
        raise ValueError("artifact execution profile is unavailable")
    return _definition(resource.definition)


def _configuration_fingerprint(prepared: PreparedRun) -> str:
    definitions = (
        prepared.project.project.definition,
        *(item.definition for item in prepared.project.resources),
        prepared.authority.policy,
    )
    payload = "\n".join(
        f"{item.identity.namespace.value}:{item.identity.kind.value}:"
        f"{item.identity.resource_id.value}:{item.fingerprint.digest.hex_value}"
        for item in sorted(
            set(definitions),
            key=lambda item: (
                item.identity.namespace.value,
                item.identity.kind.value,
                item.identity.resource_id.value,
                item.fingerprint.digest.hex_value,
            ),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _resources(
    prepared: PreparedRun,
    executions: tuple[ArtifactExecution, ...],
    comparisons: tuple[ArtifactComparison, ...],
    provenance: ArtifactProvenance,
    gate: ArtifactGate,
) -> tuple[ArtifactDefinition, ...]:
    optional_references = (
        provenance.execution_profile,
        provenance.artifact_profile,
        provenance.redaction_profile,
        gate.profile,
    )
    definitions = {
        provenance.project,
        provenance.execution_policy,
        *(item for item in optional_references if item is not None),
        *(_definition(item.definition) for item in prepared.project.resources),
        *(item.subject for item in executions),
        *(item.environment for item in executions),
        *(item.subject for item in comparisons),
        *(fact.rule for item in executions for fact in item.assertions),
        *(fact.rule for item in comparisons for fact in item.diffs),
    }
    return tuple(
        sorted(
            definitions,
            key=lambda item: (item.namespace, item.kind, item.resource_id, item.fingerprint),
        )
    )


def _budget(value: ResourceBudget) -> ArtifactBudget:
    limit = _amount(value.effective)
    consumed = _amount(value.usage.consumed)
    high_water = _amount(value.usage.high_water)
    used = consumed if consumed.fraction >= high_water.fraction else high_water
    return ArtifactBudget(
        resource=f"{value.stage.value}.{value.resource.value}.{value.operation.value}",
        scope=value.scope_kind.value,
        limit=limit,
        used=used,
        exhausted=used.fraction >= limit.fraction,
    )


def _amount(value: Count | ByteCount | Duration | Ratio) -> ArtifactAmount:
    if type(value) is Count:
        return ArtifactAmount(unit=ArtifactAmountUnit.COUNT, numerator=value.value)
    if type(value) is ByteCount:
        return ArtifactAmount(unit=ArtifactAmountUnit.BYTES, numerator=value.value)
    if type(value) is Duration:
        return ArtifactAmount(
            unit=ArtifactAmountUnit.NANOSECONDS,
            numerator=value.nanoseconds,
        )
    if type(value) is Ratio:
        return ArtifactAmount(
            unit=ArtifactAmountUnit.RATIO,
            numerator=value.numerator,
            denominator=value.denominator.value,
        )
    raise TypeError("artifact budget amount has an unsupported type")  # pragma: no cover


def _body_captures(executions: tuple[ArtifactExecution, ...]) -> Iterator[ArtifactCapture]:
    for execution in executions:
        for attempt in execution.attempts:
            for exchange in attempt.exchanges:
                if exchange.request is not None and exchange.request.body is not None:
                    yield exchange.request.body
                if exchange.response is not None and exchange.response.body is not None:
                    yield exchange.response.body
            request = attempt.terminal_pre_send_request
            if request is not None and request.body is not None:
                yield request.body


def _artifact_severity(value: str) -> ArtifactSeverity:
    return ArtifactSeverity(value)
