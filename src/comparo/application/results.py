"""Pure body-free result projections shared by interactive frontends.

The independently sanitized :class:`RunArtifact` is the only accepted input.  This module
projects its manifest-resident result facts into immutable navigation models; it never opens a
blob, re-evaluates a fact, or derives a gate verdict.
"""

from __future__ import annotations

from enum import StrEnum
from typing import final

import msgspec

from comparo.application.artifact_references import ArtifactBodyMessage
from comparo.application.artifact_references import ArtifactBodyReference
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import require_stable_token
from comparo.core.artifacts.common import require_text
from comparo.core.artifacts.facts import ArtifactEvidence
from comparo.core.artifacts.facts import ArtifactExecution
from comparo.core.artifacts.facts import ArtifactFact
from comparo.core.artifacts.http import ArtifactAttempt
from comparo.core.artifacts.http import ArtifactCookie
from comparo.core.artifacts.http import ArtifactExchange
from comparo.core.artifacts.http import ArtifactField
from comparo.core.artifacts.http import ArtifactRequest
from comparo.core.artifacts.http import ArtifactResponse
from comparo.core.artifacts.http import ArtifactTiming
from comparo.core.artifacts.lifecycle import ArtifactGateCounts
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.values import ArtifactArray
from comparo.core.artifacts.values import ArtifactBoolean
from comparo.core.artifacts.values import ArtifactDecimal
from comparo.core.artifacts.values import ArtifactInteger
from comparo.core.artifacts.values import ArtifactNull
from comparo.core.artifacts.values import ArtifactObject
from comparo.core.artifacts.values import ArtifactString
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


class ResultHttpMessageState(StrEnum):
    """Closed persistence state for one sanitized HTTP message side."""

    RETAINED = "retained"
    OMITTED = "omitted"
    NOT_PRODUCED = "not-produced"


class ResultHttpOmissionReason(StrEnum):
    """Value-free reason one sanitized HTTP message was not retained."""

    POLICY = "policy"
    SOURCE_UNAVAILABLE = "source-unavailable"
    SOURCE_INCOMPLETE = "source-incomplete"
    REDACTION_FAILED = "redaction-failed"
    DECODE_FAILED = "decode-failed"
    BUDGET_EXCEEDED = "budget-exceeded"
    STORAGE_FAILED = "storage-failed"
    CANCELLED = "cancelled"


@final
class ResultReference(msgspec.Struct, frozen=True, kw_only=True):
    """Stable address of one projected result entity."""

    entity: ResultEntity
    result_id: str

    def __post_init__(self) -> None:
        """Require a typed entity and one route-safe stable occurrence token."""
        if type(self.entity) is not ResultEntity:
            raise TypeError("result reference entity must be typed")
        require_stable_token(self.result_id, label="result reference ID", maximum=128)


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
class ResultHttpField(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One ordered, sanitized HTTP field occurrence safe for presentation."""

    name: str
    value: str
    redacted: bool

    def __post_init__(self) -> None:
        """Preserve the artifact field's bounded text and redaction invariants."""
        ArtifactField(name=self.name, value=self.value, redacted=self.redacted)


@final
class ResultHttpCookie(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """One sanitized cookie whose value is always the fixed redaction marker."""

    name: str
    value: str
    redacted: bool

    def __post_init__(self) -> None:
        """Preserve the artifact cookie's unconditional masking invariant."""
        ArtifactCookie(name=self.name, value=self.value, redacted=self.redacted)


@final
class ResultHttpRequestMetadata(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
):
    """Sanitized request metadata without body content or storage locators."""

    method: str
    url: str
    headers: tuple[ResultHttpField, ...]
    cookies: tuple[ResultHttpCookie, ...]
    has_body_capture: bool

    def __post_init__(self) -> None:
        """Require immutable typed metadata and preserve aggregate redaction rules."""
        if type(self.headers) is not tuple or any(
            type(item) is not ResultHttpField for item in self.headers
        ):
            raise TypeError("result HTTP request headers must be immutable and typed")
        if type(self.cookies) is not tuple or any(
            type(item) is not ResultHttpCookie for item in self.cookies
        ):
            raise TypeError("result HTTP request cookies must be immutable and typed")
        if type(self.has_body_capture) is not bool:
            raise TypeError("result HTTP request capture flag must be bool")
        ArtifactRequest(
            method=self.method,
            url=self.url,
            headers=tuple(_artifact_field(item) for item in self.headers),
            cookies=tuple(_artifact_cookie(item) for item in self.cookies),
            body=None,
        )


@final
class ResultHttpResponseMetadata(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
):
    """Sanitized response metadata without body content or storage locators."""

    status: int
    protocol: str
    headers: tuple[ResultHttpField, ...]
    trailers: tuple[ResultHttpField, ...]
    has_body_capture: bool

    def __post_init__(self) -> None:
        """Require immutable typed metadata and preserve aggregate redaction rules."""
        for values, label in (
            (self.headers, "headers"),
            (self.trailers, "trailers"),
        ):
            if type(values) is not tuple or any(
                type(item) is not ResultHttpField for item in values
            ):
                raise TypeError(f"result HTTP response {label} must be immutable and typed")
        if type(self.has_body_capture) is not bool:
            raise TypeError("result HTTP response capture flag must be bool")
        ArtifactResponse(
            status=self.status,
            protocol=self.protocol,
            headers=tuple(_artifact_field(item) for item in self.headers),
            trailers=tuple(_artifact_field(item) for item in self.trailers),
            body=None,
        )


@final
class ResultHttpRequestSlot(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
):
    """Exactly one retained request or one explicit request omission."""

    state: ResultHttpMessageState
    metadata: ResultHttpRequestMetadata | None
    omission_reason: ResultHttpOmissionReason | None

    def __post_init__(self) -> None:
        """Reject contradictory or impossible request persistence states."""
        if type(self.state) is not ResultHttpMessageState:
            raise TypeError("result HTTP request state must be typed")
        if self.metadata is not None and type(self.metadata) is not ResultHttpRequestMetadata:
            raise TypeError("result HTTP request metadata must be typed or absent")
        if (
            self.omission_reason is not None
            and type(self.omission_reason) is not ResultHttpOmissionReason
        ):
            raise TypeError("result HTTP request omission reason must be typed or absent")
        if self.state is ResultHttpMessageState.RETAINED:
            valid = self.metadata is not None and self.omission_reason is None
        elif self.state is ResultHttpMessageState.OMITTED:
            valid = self.metadata is None and self.omission_reason is not None
        else:
            valid = False
        if not valid:
            raise ValueError("result HTTP request state contradicts its payload")


@final
class ResultHttpResponseSlot(
    msgspec.Struct,
    frozen=True,
    kw_only=True,
    forbid_unknown_fields=True,
):
    """Exactly one retained, omitted, or not-produced response state."""

    state: ResultHttpMessageState
    metadata: ResultHttpResponseMetadata | None
    omission_reason: ResultHttpOmissionReason | None

    def __post_init__(self) -> None:
        """Reject contradictory response persistence states."""
        if type(self.state) is not ResultHttpMessageState:
            raise TypeError("result HTTP response state must be typed")
        if self.metadata is not None and type(self.metadata) is not ResultHttpResponseMetadata:
            raise TypeError("result HTTP response metadata must be typed or absent")
        if (
            self.omission_reason is not None
            and type(self.omission_reason) is not ResultHttpOmissionReason
        ):
            raise TypeError("result HTTP response omission reason must be typed or absent")
        if self.state is ResultHttpMessageState.RETAINED:
            valid = self.metadata is not None and self.omission_reason is None
        elif self.state is ResultHttpMessageState.OMITTED:
            valid = self.metadata is None and self.omission_reason is not None
        else:
            valid = self.metadata is None and self.omission_reason is None
        if not valid:
            raise ValueError("result HTTP response state contradicts its payload")


@final
class ResultHttpExchange(msgspec.Struct, frozen=True, kw_only=True):
    """Body-free identity, metadata, and ordering for one HTTP exchange."""

    exchange_id: str
    ordinal: int
    terminal: bool
    failure: ResultFailure | None
    request: ResultHttpRequestSlot
    response: ResultHttpResponseSlot

    def __post_init__(self) -> None:
        """Require one valid artifact identity, message state, and terminal outcome."""
        require_text(self.exchange_id, label="result HTTP exchange ID", maximum=64)
        _require_http_ordinal(self.ordinal, label="result HTTP exchange ordinal")
        if type(self.terminal) is not bool:
            raise TypeError("result HTTP exchange terminal marker must be bool")
        if self.failure is not None and type(self.failure) is not ResultFailure:
            raise TypeError("result HTTP exchange failure must be typed or absent")
        if type(self.request) is not ResultHttpRequestSlot:
            raise TypeError("result HTTP exchange request must be typed")
        if type(self.response) is not ResultHttpResponseSlot:
            raise TypeError("result HTTP exchange response must be typed")
        if (self.response.state is ResultHttpMessageState.NOT_PRODUCED) != (
            self.failure is not None
        ):
            raise ValueError("result HTTP response state and exchange failure must agree")


@final
class ResultHttpAttempt(msgspec.Struct, frozen=True, kw_only=True):
    """Body-free identity and topology for one retry attempt."""

    attempt_id: str
    ordinal: int
    terminal: bool
    pre_send_terminal: bool
    failure: ResultFailure | None
    exchanges: tuple[ResultHttpExchange, ...]
    terminal_pre_send: ResultHttpRequestSlot | None

    def __post_init__(self) -> None:
        """Require canonical exchange order and truthful pre-send state."""
        require_text(self.attempt_id, label="result HTTP attempt ID", maximum=64)
        _require_http_ordinal(self.ordinal, label="result HTTP attempt ordinal")
        if type(self.terminal) is not bool or type(self.pre_send_terminal) is not bool:
            raise TypeError("result HTTP attempt terminal markers must be bool")
        if self.failure is not None and type(self.failure) is not ResultFailure:
            raise TypeError("result HTTP attempt failure must be typed or absent")
        if (
            self.terminal_pre_send is not None
            and type(self.terminal_pre_send) is not ResultHttpRequestSlot
        ):
            raise TypeError("result HTTP terminal pre-send request must be typed or absent")
        if not (
            self.pre_send_terminal
            == (self.failure is not None)
            == (self.terminal_pre_send is not None)
        ):
            raise ValueError("result HTTP pre-send terminal, request, and failure must agree")
        if type(self.exchanges) is not tuple or any(
            type(item) is not ResultHttpExchange for item in self.exchanges
        ):
            raise TypeError("result HTTP attempt exchanges must be immutable and typed")
        _require_canonical_http_exchanges(self.exchanges)


@final
class ResultHttpMessage(msgspec.Struct, frozen=True, kw_only=True):
    """Body-free coordinates and topology for one retained sanitized HTTP message."""

    reference: ArtifactBodyReference
    attempt_ordinal: int
    exchange_ordinal: int
    terminal_attempt: bool
    terminal_exchange: bool
    has_body_capture: bool

    def __post_init__(self) -> None:
        """Require a typed coordinate and exact ordering and presence metadata."""
        if type(self.reference) is not ArtifactBodyReference:
            raise TypeError("result HTTP message reference must be typed")
        _require_http_ordinal(self.attempt_ordinal, label="result HTTP message attempt ordinal")
        _require_http_ordinal(self.exchange_ordinal, label="result HTTP message exchange ordinal")
        if type(self.terminal_attempt) is not bool or type(self.terminal_exchange) is not bool:
            raise TypeError("result HTTP message terminal markers must be bool")
        if type(self.has_body_capture) is not bool:
            raise TypeError("result HTTP message capture flag must be bool")


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
    attempts: tuple[ResultHttpAttempt, ...] = ()
    messages: tuple[ResultHttpMessage, ...] = ()

    def __post_init__(self) -> None:
        """Keep HTTP topology canonical and message coordinates consistent with it."""
        if type(self.attempts) is not tuple or any(
            type(item) is not ResultHttpAttempt for item in self.attempts
        ):
            raise TypeError("execution result attempts must be immutable and typed")
        _require_canonical_http_attempts(self.attempts)
        if type(self.messages) is not tuple or any(
            type(item) is not ResultHttpMessage for item in self.messages
        ):
            raise TypeError("execution result messages must be immutable and typed")
        coordinates = tuple(item.reference for item in self.messages)
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("execution result message coordinates must be unique")
        if any(item.execution_id != self.reference.result_id for item in coordinates):
            raise ValueError("execution result message coordinates must belong to the execution")
        _require_messages_match_http_topology(self.messages, self.attempts)


def _require_http_ordinal(value: object, *, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _require_canonical_http_exchanges(exchanges: tuple[ResultHttpExchange, ...]) -> None:
    ids = tuple(item.exchange_id for item in exchanges)
    if len(set(ids)) != len(ids):
        raise ValueError("result HTTP exchange IDs must be unique within an attempt")
    if tuple(item.ordinal for item in exchanges) != tuple(range(len(exchanges))):
        raise ValueError("result HTTP exchange ordinals must be canonical")
    if tuple(item.terminal for item in exchanges) != tuple(
        index == len(exchanges) - 1 for index in range(len(exchanges))
    ):
        raise ValueError("result HTTP exchange terminal marker must identify the final exchange")


def _require_canonical_http_attempts(attempts: tuple[ResultHttpAttempt, ...]) -> None:
    ids = tuple(item.attempt_id for item in attempts)
    if len(set(ids)) != len(ids):
        raise ValueError("result HTTP attempt IDs must be unique")
    if tuple(item.ordinal for item in attempts) != tuple(range(len(attempts))):
        raise ValueError("result HTTP attempt ordinals must be canonical")
    if tuple(item.terminal for item in attempts) != tuple(
        index == len(attempts) - 1 for index in range(len(attempts))
    ):
        raise ValueError("result HTTP attempt terminal marker must identify the final attempt")


def _require_messages_match_http_topology(
    messages: tuple[ResultHttpMessage, ...],
    attempts: tuple[ResultHttpAttempt, ...],
) -> None:
    attempts_by_id = {item.attempt_id: item for item in attempts}
    for message in messages:
        attempt = attempts_by_id.get(message.reference.attempt_id)
        if attempt is None:
            raise ValueError("result HTTP message attempt must exist in execution topology")
        exchanges_by_id = {item.exchange_id: item for item in attempt.exchanges}
        exchange = exchanges_by_id.get(message.reference.exchange_id)
        if exchange is None:
            raise ValueError("result HTTP message exchange must exist in attempt topology")
        if (
            message.attempt_ordinal != attempt.ordinal
            or message.exchange_ordinal != exchange.ordinal
            or message.terminal_attempt is not attempt.terminal
            or message.terminal_exchange is not exchange.terminal
        ):
            raise ValueError("result HTTP message metadata must match execution topology")
        slot: ResultHttpRequestSlot | ResultHttpResponseSlot
        if message.reference.message is ArtifactBodyMessage.REQUEST:
            slot = exchange.request
        else:
            slot = exchange.response
        if slot.state is not ResultHttpMessageState.RETAINED or slot.metadata is None:
            raise ValueError("result HTTP message must identify retained metadata")
        if message.has_body_capture is not slot.metadata.has_body_capture:
            raise ValueError("result HTTP message capture flag must match retained metadata")

    expected = tuple(
        (
            attempt.attempt_id,
            exchange.exchange_id,
            side,
        )
        for attempt in attempts
        for exchange in attempt.exchanges
        for side, slot in (
            (ArtifactBodyMessage.REQUEST, exchange.request),
            (ArtifactBodyMessage.RESPONSE, exchange.response),
        )
        if slot.state is ResultHttpMessageState.RETAINED
    )
    actual = tuple(
        (
            message.reference.attempt_id,
            message.reference.exchange_id,
            message.reference.message,
        )
        for message in messages
    )
    if actual != expected:
        raise ValueError("result HTTP messages must completely match retained metadata in order")


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
        attempts=_http_attempts(execution),
        messages=_http_messages(execution),
    )


def _http_attempts(execution: ArtifactExecution) -> tuple[ResultHttpAttempt, ...]:
    terminal_attempt_ordinal = len(execution.attempts) - 1
    return tuple(
        _http_attempt(
            attempt,
            ordinal=attempt_ordinal,
            terminal=attempt_ordinal == terminal_attempt_ordinal,
        )
        for attempt_ordinal, attempt in enumerate(execution.attempts)
    )


def _http_attempt(
    attempt: ArtifactAttempt,
    *,
    ordinal: int,
    terminal: bool,
) -> ResultHttpAttempt:
    terminal_exchange_ordinal = len(attempt.exchanges) - 1
    return ResultHttpAttempt(
        attempt_id=attempt.attempt_id,
        ordinal=ordinal,
        terminal=terminal,
        pre_send_terminal=(
            attempt.terminal_pre_send_request is not None
            or attempt.terminal_pre_send_omission is not None
        ),
        failure=None if attempt.failure is None else _failure(attempt.failure),
        terminal_pre_send=_request_slot(
            attempt.terminal_pre_send_request,
            attempt.terminal_pre_send_omission,
            optional=True,
        ),
        exchanges=tuple(
            _http_exchange(
                exchange,
                ordinal=exchange_ordinal,
                terminal=exchange_ordinal == terminal_exchange_ordinal,
            )
            for exchange_ordinal, exchange in enumerate(attempt.exchanges)
        ),
    )


def _http_exchange(
    exchange: ArtifactExchange,
    *,
    ordinal: int,
    terminal: bool,
) -> ResultHttpExchange:
    request = _request_slot(exchange.request, exchange.request_omission, optional=False)
    if request is None:  # pragma: no cover - authenticated artifact invariant
        raise AssertionError("authenticated artifact exchange requires a request state")
    return ResultHttpExchange(
        exchange_id=exchange.exchange_id,
        ordinal=ordinal,
        terminal=terminal,
        failure=None if exchange.failure is None else _failure(exchange.failure),
        request=request,
        response=_response_slot(
            exchange.response,
            exchange.response_omission,
            failed=exchange.failure is not None,
        ),
    )


def _request_slot(
    request: ArtifactRequest | None,
    omission: ArtifactOmissionReason | None,
    *,
    optional: bool,
) -> ResultHttpRequestSlot | None:
    if request is not None:
        return ResultHttpRequestSlot(
            state=ResultHttpMessageState.RETAINED,
            metadata=_request_metadata(request),
            omission_reason=None,
        )
    if omission is not None:
        return ResultHttpRequestSlot(
            state=ResultHttpMessageState.OMITTED,
            metadata=None,
            omission_reason=ResultHttpOmissionReason(omission.value),
        )
    if optional:
        return None
    raise AssertionError(  # pragma: no cover - authenticated artifact invariant
        "authenticated artifact exchange requires a request state"
    )


def _response_slot(
    response: ArtifactResponse | None,
    omission: ArtifactOmissionReason | None,
    *,
    failed: bool,
) -> ResultHttpResponseSlot:
    if response is not None:
        return ResultHttpResponseSlot(
            state=ResultHttpMessageState.RETAINED,
            metadata=_response_metadata(response),
            omission_reason=None,
        )
    if omission is not None:
        return ResultHttpResponseSlot(
            state=ResultHttpMessageState.OMITTED,
            metadata=None,
            omission_reason=ResultHttpOmissionReason(omission.value),
        )
    if failed:
        return ResultHttpResponseSlot(
            state=ResultHttpMessageState.NOT_PRODUCED,
            metadata=None,
            omission_reason=None,
        )
    raise AssertionError(  # pragma: no cover - authenticated artifact invariant
        "authenticated artifact exchange requires a response state"
    )


def _request_metadata(request: ArtifactRequest) -> ResultHttpRequestMetadata:
    return ResultHttpRequestMetadata(
        method=request.method,
        url=request.url,
        headers=tuple(_http_field(item) for item in request.headers),
        cookies=tuple(_http_cookie(item) for item in request.cookies),
        has_body_capture=request.body is not None,
    )


def _response_metadata(response: ArtifactResponse) -> ResultHttpResponseMetadata:
    return ResultHttpResponseMetadata(
        status=response.status,
        protocol=response.protocol,
        headers=tuple(_http_field(item) for item in response.headers),
        trailers=tuple(_http_field(item) for item in response.trailers),
        has_body_capture=response.body is not None,
    )


def _http_field(field: ArtifactField) -> ResultHttpField:
    return ResultHttpField(name=field.name, value=field.value, redacted=field.redacted)


def _http_cookie(cookie: ArtifactCookie) -> ResultHttpCookie:
    return ResultHttpCookie(name=cookie.name, value=cookie.value, redacted=cookie.redacted)


def _artifact_field(field: ResultHttpField) -> ArtifactField:
    return ArtifactField(name=field.name, value=field.value, redacted=field.redacted)


def _artifact_cookie(cookie: ResultHttpCookie) -> ArtifactCookie:
    return ArtifactCookie(name=cookie.name, value=cookie.value, redacted=cookie.redacted)


def _http_messages(execution: ArtifactExecution) -> tuple[ResultHttpMessage, ...]:
    messages: list[ResultHttpMessage] = []
    terminal_attempt_ordinal = len(execution.attempts) - 1
    for attempt_ordinal, attempt in enumerate(execution.attempts):
        terminal_exchange_ordinal = len(attempt.exchanges) - 1
        for exchange_ordinal, exchange in enumerate(attempt.exchanges):
            if exchange.request is not None:
                messages.append(
                    _http_message(
                        execution.execution_id,
                        attempt.attempt_id,
                        exchange.exchange_id,
                        ArtifactBodyMessage.REQUEST,
                        attempt_ordinal=attempt_ordinal,
                        exchange_ordinal=exchange_ordinal,
                        terminal_attempt=attempt_ordinal == terminal_attempt_ordinal,
                        terminal_exchange=exchange_ordinal == terminal_exchange_ordinal,
                        has_body_capture=exchange.request.body is not None,
                    )
                )
            if exchange.response is not None:
                messages.append(
                    _http_message(
                        execution.execution_id,
                        attempt.attempt_id,
                        exchange.exchange_id,
                        ArtifactBodyMessage.RESPONSE,
                        attempt_ordinal=attempt_ordinal,
                        exchange_ordinal=exchange_ordinal,
                        terminal_attempt=attempt_ordinal == terminal_attempt_ordinal,
                        terminal_exchange=exchange_ordinal == terminal_exchange_ordinal,
                        has_body_capture=exchange.response.body is not None,
                    )
                )
    return tuple(messages)


def _http_message(
    execution_id: str,
    attempt_id: str,
    exchange_id: str,
    message: ArtifactBodyMessage,
    *,
    attempt_ordinal: int,
    exchange_ordinal: int,
    terminal_attempt: bool,
    terminal_exchange: bool,
    has_body_capture: bool,
) -> ResultHttpMessage:
    return ResultHttpMessage(
        reference=ArtifactBodyReference(
            execution_id=execution_id,
            attempt_id=attempt_id,
            exchange_id=exchange_id,
            message=message,
        ),
        attempt_ordinal=attempt_ordinal,
        exchange_ordinal=exchange_ordinal,
        terminal_attempt=terminal_attempt,
        terminal_exchange=terminal_exchange,
        has_body_capture=has_body_capture,
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


def result_value_preview(value: ArtifactValue, *, maximum_characters: int) -> str:
    """Return one bounded scalar preview or structural summary without JSON encoding."""
    if type(maximum_characters) is not int or maximum_characters < 1:
        raise ValueError("result value preview limit must be a positive integer")
    if type(value) is ArtifactNull:
        rendered = "null"
    elif type(value) is ArtifactBoolean:
        rendered = "true" if value.value else "false"
    elif type(value) is ArtifactInteger:
        absolute = abs(value.value)
        magnitude = (
            f"{absolute:,}"
            if absolute.bit_length() <= 80
            else f"≥2^{absolute.bit_length() - 1} (exact integer retained)"
        )
        rendered = f"-{magnitude}" if value.value < 0 else magnitude
    elif type(value) is ArtifactDecimal or type(value) is ArtifactString:
        rendered = value.value
    elif type(value) is ArtifactArray:
        rendered = f"array · {len(value.items):,} items"
    elif type(value) is ArtifactObject:
        rendered = f"object · {len(value.members):,} members"
    else:  # pragma: no cover - ArtifactValue is a closed union
        raise TypeError("unsupported result ArtifactValue")
    if len(rendered) <= maximum_characters:
        return rendered
    if maximum_characters == 1:
        return "…"
    return rendered[: maximum_characters - 1] + "…"


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
    "ResultHttpAttempt",
    "ResultHttpCookie",
    "ResultHttpExchange",
    "ResultHttpField",
    "ResultHttpMessage",
    "ResultHttpMessageState",
    "ResultHttpOmissionReason",
    "ResultHttpRequestMetadata",
    "ResultHttpRequestSlot",
    "ResultHttpResponseMetadata",
    "ResultHttpResponseSlot",
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
    "result_value_preview",
)
