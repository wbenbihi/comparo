"""Tests for the immutable body-free result projection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from typing import cast

import msgspec
import pytest

from comparo.application import ArtifactBodyMessage as ExportedArtifactBodyMessage
from comparo.application import ArtifactBodyReference as ExportedArtifactBodyReference
from comparo.application import ResultHttpAttempt as ExportedResultHttpAttempt
from comparo.application import ResultHttpCookie as ExportedResultHttpCookie
from comparo.application import ResultHttpExchange as ExportedResultHttpExchange
from comparo.application import ResultHttpField as ExportedResultHttpField
from comparo.application import ResultHttpMessage as ExportedResultHttpMessage
from comparo.application import ResultHttpMessageState as ExportedResultHttpMessageState
from comparo.application import ResultHttpOmissionReason as ExportedResultHttpOmissionReason
from comparo.application import ResultHttpRequestMetadata as ExportedResultHttpRequestMetadata
from comparo.application import ResultHttpRequestSlot as ExportedResultHttpRequestSlot
from comparo.application import ResultHttpResponseMetadata as ExportedResultHttpResponseMetadata
from comparo.application import ResultHttpResponseSlot as ExportedResultHttpResponseSlot
from comparo.application import result_analysis as analysis_module
from comparo.application.artifact_references import ArtifactBodyMessage
from comparo.application.artifact_references import ArtifactBodyReference
from comparo.application.artifacts import ArtifactBodyMessage as ServiceArtifactBodyMessage
from comparo.application.artifacts import ArtifactBodyReference as ServiceArtifactBodyReference
from comparo.application.artifacts import read_artifact_body_window
from comparo.application.curl_export import CurlExportLimits
from comparo.application.curl_export import CurlExportRequest
from comparo.application.curl_export import export_artifact_curl
from comparo.application.result_analysis import AnalysisProjection
from comparo.application.result_analysis import ResultProjectionWindow
from comparo.application.result_analysis import project_fields
from comparo.application.result_analysis import project_issues
from comparo.application.result_analysis import project_requests
from comparo.application.result_analysis import project_rules
from comparo.application.result_analysis import project_timeline
from comparo.application.results import ExecutionResultRow
from comparo.application.results import ResultCiProvenance
from comparo.application.results import ResultEntity
from comparo.application.results import ResultEvidence
from comparo.application.results import ResultFailure
from comparo.application.results import ResultHttpAttempt
from comparo.application.results import ResultHttpCookie
from comparo.application.results import ResultHttpExchange
from comparo.application.results import ResultHttpField
from comparo.application.results import ResultHttpMessage
from comparo.application.results import ResultHttpMessageState
from comparo.application.results import ResultHttpOmissionReason
from comparo.application.results import ResultHttpRequestMetadata
from comparo.application.results import ResultHttpRequestSlot
from comparo.application.results import ResultHttpResponseMetadata
from comparo.application.results import ResultHttpResponseSlot
from comparo.application.results import ResultNamedValue
from comparo.application.results import ResultProjection
from comparo.application.results import ResultReference
from comparo.application.results import ResultRelation
from comparo.application.results import ResultRunAnnotations
from comparo.application.results import ResultTiming
from comparo.application.results import ResultVcsProvenance
from comparo.application.results import project_results
from comparo.core.artifacts import ArtifactAttempt
from comparo.core.artifacts import ArtifactCiProvenance
from comparo.core.artifacts import ArtifactComparisonOutcome
from comparo.core.artifacts import ArtifactCookie as CoreArtifactCookie
from comparo.core.artifacts import ArtifactDefinition
from comparo.core.artifacts import ArtifactExchange
from comparo.core.artifacts import ArtifactExecutionOutcome
from comparo.core.artifacts import ArtifactFailure
from comparo.core.artifacts import ArtifactGate
from comparo.core.artifacts import ArtifactGateCounts
from comparo.core.artifacts import ArtifactGateVerdict
from comparo.core.artifacts import ArtifactMetric
from comparo.core.artifacts import ArtifactOmissionReason
from comparo.core.artifacts import ArtifactRunAnnotations
from comparo.core.artifacts import ArtifactSeverity
from comparo.core.artifacts import ArtifactSummary
from comparo.core.artifacts import ArtifactTiming
from comparo.core.artifacts import ArtifactVcsProvenance
from comparo.core.artifacts import RunArtifact
from comparo.core.artifacts import fingerprint_run_artifact
from comparo.core.artifacts.values import ArtifactString
from tests.application.test_artifacts import _bundle
from tests.application.test_artifacts import _limits
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
    assert projection.annotations is None

    first = projection.executions[0]
    assert first.reference.entity is ResultEntity.EXECUTION
    assert first.subject.resource_id == "get-items"
    assert first.environment.resource_id == "staging"
    assert first.coordinates[0].axis == "tenant"
    assert first.timing.measurements[0].name == "total"
    assert first.timing.measurements[0].nanoseconds == 10
    assert first.timing.attempts == 1
    assert first.timing.exchanges == 1
    assert first.messages == (
        ResultHttpMessage(
            reference=ArtifactBodyReference(
                artifact_api_version=artifact.api_version,
                artifact_id=artifact.metadata.artifact_id,
                artifact_results_sha256=fingerprint_run_artifact(artifact),
                execution_id="execution-0",
                attempt_id="attempt-0",
                exchange_id="exchange-0",
                message=ArtifactBodyMessage.REQUEST,
            ),
            attempt_ordinal=0,
            exchange_ordinal=0,
            terminal_attempt=True,
            terminal_exchange=True,
            has_body_capture=True,
        ),
        ResultHttpMessage(
            reference=ArtifactBodyReference(
                artifact_api_version=artifact.api_version,
                artifact_id=artifact.metadata.artifact_id,
                artifact_results_sha256=fingerprint_run_artifact(artifact),
                execution_id="execution-0",
                attempt_id="attempt-0",
                exchange_id="exchange-0",
                message=ArtifactBodyMessage.RESPONSE,
            ),
            attempt_ordinal=0,
            exchange_ordinal=0,
            terminal_attempt=True,
            terminal_exchange=True,
            has_body_capture=True,
        ),
    )
    assert len(first.attempts) == 1
    attempt = first.attempts[0]
    assert (
        attempt.attempt_id,
        attempt.ordinal,
        attempt.terminal,
        attempt.pre_send_terminal,
        attempt.failure,
        attempt.terminal_pre_send,
    ) == ("attempt-0", 0, True, False, None, None)
    assert len(attempt.exchanges) == 1
    exchange = attempt.exchanges[0]
    assert (exchange.exchange_id, exchange.ordinal, exchange.terminal, exchange.failure) == (
        "exchange-0",
        0,
        True,
        None,
    )
    assert exchange.request.state is ResultHttpMessageState.RETAINED
    assert exchange.request.omission_reason is None
    assert exchange.request.metadata is not None
    assert exchange.request.metadata.method == "GET"
    assert exchange.request.metadata.url == "https://example.test/items"
    assert exchange.request.metadata.url_redaction_count == 0
    assert exchange.request.metadata.header_redaction_count == 0
    assert exchange.request.metadata.cookie_redaction_count == 0
    assert exchange.request.metadata.headers == (
        ResultHttpField(name="accept", value="application/json", redacted=False),
    )
    assert exchange.request.metadata.cookies == ()
    assert exchange.request.metadata.has_body_capture is True
    assert exchange.response.state is ResultHttpMessageState.RETAINED
    assert exchange.response.omission_reason is None
    assert exchange.response.metadata is not None
    assert exchange.response.metadata.status == 200
    assert exchange.response.metadata.protocol == "HTTP/2"
    assert exchange.response.metadata.headers == (
        ResultHttpField(name="content-type", value="application/json", redacted=False),
    )
    assert exchange.response.metadata.trailers == ()
    assert exchange.response.metadata.has_body_capture is True
    assert attempt.timings == ()
    assert exchange.timings == (ResultTiming(name="total", nanoseconds=10, reason=None),)
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


def test_projection_preserves_attempt_exchange_timings_and_url_redaction_accounting() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    updated_exchange = msgspec.structs.replace(
        exchange,
        request=msgspec.structs.replace(
            exchange.request,
            url="https://example.test/items?api_key=%3Credacted%3E",
            url_redaction_count=1,
            header_redaction_count=2,
            cookie_redaction_count=3,
        ),
        timings=(
            ArtifactTiming(name="connect", nanoseconds=None, reason="connection-reused"),
            ArtifactTiming(name="total", nanoseconds=20, reason=None),
        ),
    )
    updated_attempt = msgspec.structs.replace(
        attempt,
        exchanges=(updated_exchange,),
        timings=(ArtifactTiming(name="backoff", nanoseconds=7, reason=None),),
    )
    updated_execution = msgspec.structs.replace(execution, attempts=(updated_attempt,))
    updated_artifact = msgspec.structs.replace(
        artifact,
        executions=(updated_execution, artifact.executions[1]),
    )

    original = project_results(artifact)
    projected = project_results(updated_artifact)
    projected_attempt = projected.executions[0].attempts[0]
    projected_exchange = projected_attempt.exchanges[0]

    assert projected != original
    assert projected_attempt.timings == (ResultTiming(name="backoff", nanoseconds=7, reason=None),)
    assert projected_exchange.timings == (
        ResultTiming(name="connect", nanoseconds=None, reason="connection-reused"),
        ResultTiming(name="total", nanoseconds=20, reason=None),
    )
    assert projected_exchange.request.metadata is not None
    assert projected_exchange.request.metadata.url_redaction_count == 1
    assert projected_exchange.request.metadata.header_redaction_count == 2
    assert projected_exchange.request.metadata.cookie_redaction_count == 3
    assert all(
        message.reference.artifact_api_version == artifact.api_version
        and message.reference.artifact_id == artifact.metadata.artifact_id
        and message.reference.artifact_results_sha256 == fingerprint_run_artifact(updated_artifact)
        for message in projected.executions[0].messages
    )
    assert {
        message.reference.artifact_results_sha256 for message in projected.executions[0].messages
    } != {message.reference.artifact_results_sha256 for message in original.executions[0].messages}


def test_projection_exposes_every_exchange_message_in_artifact_order() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    redirected = msgspec.structs.replace(exchange, exchange_id="exchange-redirect")
    retried = msgspec.structs.replace(exchange, exchange_id="exchange-retry")
    execution = msgspec.structs.replace(
        execution,
        attempts=(
            msgspec.structs.replace(attempt, exchanges=(exchange, redirected)),
            msgspec.structs.replace(
                attempt,
                attempt_id="attempt-retry",
                exchanges=(retried,),
            ),
        ),
    )
    artifact = msgspec.structs.replace(
        artifact,
        executions=(execution, artifact.executions[1]),
    )

    messages = project_results(artifact).executions[0].messages

    assert tuple(
        (
            item.reference.attempt_id,
            item.reference.exchange_id,
            item.reference.message,
        )
        for item in messages
    ) == (
        ("attempt-0", "exchange-0", ArtifactBodyMessage.REQUEST),
        ("attempt-0", "exchange-0", ArtifactBodyMessage.RESPONSE),
        ("attempt-0", "exchange-redirect", ArtifactBodyMessage.REQUEST),
        ("attempt-0", "exchange-redirect", ArtifactBodyMessage.RESPONSE),
        ("attempt-retry", "exchange-retry", ArtifactBodyMessage.REQUEST),
        ("attempt-retry", "exchange-retry", ArtifactBodyMessage.RESPONSE),
    )
    assert all(item.has_body_capture for item in messages)
    assert tuple(
        (
            item.attempt_ordinal,
            item.exchange_ordinal,
            item.terminal_attempt,
            item.terminal_exchange,
        )
        for item in messages
    ) == (
        (0, 0, False, False),
        (0, 0, False, False),
        (0, 1, False, True),
        (0, 1, False, True),
        (1, 0, True, True),
        (1, 0, True, True),
    )

    attempts = project_results(artifact).executions[0].attempts
    assert tuple((item.attempt_id, item.ordinal, item.terminal) for item in attempts) == (
        ("attempt-0", 0, False),
        ("attempt-retry", 1, True),
    )
    assert tuple(
        (exchange.exchange_id, exchange.ordinal, exchange.terminal)
        for exchange in attempts[0].exchanges
    ) == (
        ("exchange-0", 0, False),
        ("exchange-redirect", 1, True),
    )


def test_projection_distinguishes_message_presence_from_body_capture_presence() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    assert exchange.request is not None
    assert exchange.response is not None
    bodyless = msgspec.structs.replace(
        exchange,
        request=msgspec.structs.replace(exchange.request, body=None),
        response=msgspec.structs.replace(exchange.response, body=None),
    )
    execution = msgspec.structs.replace(
        execution,
        attempts=(msgspec.structs.replace(attempt, exchanges=(bodyless,)),),
    )
    artifact = msgspec.structs.replace(
        artifact,
        executions=(execution, artifact.executions[1]),
    )

    messages = project_results(artifact).executions[0].messages

    assert tuple(item.reference.message for item in messages) == (
        ArtifactBodyMessage.REQUEST,
        ArtifactBodyMessage.RESPONSE,
    )
    assert tuple(item.has_body_capture for item in messages) == (False, False)
    assert all(item.reference.execution_id == "execution-0" for item in messages)
    projected_exchange = project_results(artifact).executions[0].attempts[0].exchanges[0]
    assert projected_exchange.request.metadata is not None
    assert projected_exchange.request.metadata.has_body_capture is False
    assert projected_exchange.response.metadata is not None
    assert projected_exchange.response.metadata.has_body_capture is False
    encoded = msgspec.json.encode(messages)
    assert b"example.test" not in encoded
    assert b"application/json" not in encoded
    assert b"b" * 64 not in encoded


def test_projection_omits_message_coordinates_for_explicitly_absent_messages() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    request_omitted = msgspec.structs.replace(
        exchange,
        request=None,
        request_omission=ArtifactOmissionReason.POLICY,
    )
    response_omitted = msgspec.structs.replace(
        exchange,
        response=None,
        response_omission=ArtifactOmissionReason.POLICY,
    )

    def projected_for(updated_exchange: ArtifactExchange) -> ExecutionResultRow:
        updated = msgspec.structs.replace(
            execution,
            attempts=(
                msgspec.structs.replace(
                    attempt,
                    exchanges=(updated_exchange,),
                ),
            ),
        )
        updated_artifact = msgspec.structs.replace(
            artifact,
            executions=(updated, artifact.executions[1]),
        )
        return project_results(updated_artifact).executions[0]

    request_absent = projected_for(request_omitted)
    response_absent = projected_for(response_omitted)

    assert tuple(item.reference.message for item in request_absent.messages) == (
        ArtifactBodyMessage.RESPONSE,
    )
    request_slot = request_absent.attempts[0].exchanges[0].request
    assert request_slot.state is ResultHttpMessageState.OMITTED
    assert request_slot.metadata is None
    assert request_slot.omission_reason is ResultHttpOmissionReason.POLICY
    assert tuple(item.reference.message for item in response_absent.messages) == (
        ArtifactBodyMessage.REQUEST,
    )
    response_slot = response_absent.attempts[0].exchanges[0].response
    assert response_slot.state is ResultHttpMessageState.OMITTED
    assert response_slot.metadata is None
    assert response_slot.omission_reason is ResultHttpOmissionReason.POLICY


def test_projection_retains_a_terminal_pre_send_attempt_without_fabricating_messages() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    first_attempt = execution.attempts[0]
    request = first_attempt.exchanges[0].request
    assert request is not None
    pre_send_failure = ArtifactFailure(
        stage="authorization",
        code="destination-denied",
        retryable=False,
    )
    pre_send_attempt = ArtifactAttempt(
        attempt_id="attempt-pre-send",
        exchanges=(),
        terminal_pre_send_request=request,
        failure=pre_send_failure,
    )
    execution = msgspec.structs.replace(
        execution,
        attempts=(first_attempt, pre_send_attempt),
    )
    artifact = msgspec.structs.replace(
        artifact,
        executions=(execution, artifact.executions[1]),
    )

    projected = project_results(artifact).executions[0]

    assert tuple(
        (
            item.attempt_id,
            item.ordinal,
            item.terminal,
            item.pre_send_terminal,
            len(item.exchanges),
        )
        for item in projected.attempts
    ) == (
        ("attempt-0", 0, False, False, 1),
        ("attempt-pre-send", 1, True, True, 0),
    )
    assert projected.attempts[-1].failure is not None
    assert projected.attempts[-1].failure.code == "destination-denied"
    assert projected.attempts[-1].terminal_pre_send is not None
    assert projected.attempts[-1].terminal_pre_send.state is ResultHttpMessageState.RETAINED
    assert projected.attempts[-1].terminal_pre_send.metadata is not None
    assert projected.attempts[-1].terminal_pre_send.metadata.method == "GET"
    assert projected.attempts[-1].terminal_pre_send.metadata.url == "https://example.test/items"
    assert projected.attempts[-1].terminal_pre_send.metadata.has_body_capture is True
    assert all(not item.terminal_attempt for item in projected.messages)
    assert not any(item.reference.attempt_id == "attempt-pre-send" for item in projected.messages)


def test_projection_retains_an_explicitly_omitted_terminal_pre_send_request() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    failure = ArtifactFailure(stage="authorization", code="destination-denied", retryable=False)
    attempt = ArtifactAttempt(
        attempt_id="attempt-pre-send",
        exchanges=(),
        terminal_pre_send_omission=ArtifactOmissionReason.POLICY,
        failure=failure,
    )
    execution = msgspec.structs.replace(execution, attempts=(attempt,))
    artifact = msgspec.structs.replace(
        artifact,
        executions=(execution, artifact.executions[1]),
    )

    projected = project_results(artifact).executions[0]

    assert projected.messages == ()
    slot = projected.attempts[0].terminal_pre_send
    assert slot is not None
    assert slot.state is ResultHttpMessageState.OMITTED
    assert slot.metadata is None
    assert slot.omission_reason is ResultHttpOmissionReason.POLICY


def test_projected_coordinates_directly_drive_body_reads_and_curl_export(
    tmp_path: Path,
) -> None:
    root, artifact = _bundle(tmp_path)
    messages = project_results(artifact).executions[0].messages
    request = next(
        item for item in messages if item.reference.message is ArtifactBodyMessage.REQUEST
    )
    response = next(
        item for item in messages if item.reference.message is ArtifactBodyMessage.RESPONSE
    )

    window = asyncio.run(
        read_artifact_body_window(
            root,
            artifact.metadata.artifact_id,
            response.reference,
            _limits(),
            offset=0,
            maximum_output_bytes=16,
        )
    )
    exported = asyncio.run(
        export_artifact_curl(
            CurlExportRequest(
                root=root,
                bundle_id=artifact.metadata.artifact_id,
                reference=request.reference,
                artifact_limits=_limits(),
                export_limits=CurlExportLimits(
                    maximum_embedded_body_bytes=16,
                    maximum_command_characters=4096,
                ),
            )
        )
    )

    assert request.has_body_capture is True
    assert response.has_body_capture is True
    assert window.reference is response.reference
    assert window.content == b"right"
    assert "left" in exported.command
    assert "right" not in exported.command


def test_result_http_message_and_execution_message_contracts_fail_closed() -> None:
    projection = project_results(_artifact())
    execution = projection.executions[0]
    message = execution.messages[0]

    assert ExportedArtifactBodyMessage is ServiceArtifactBodyMessage is ArtifactBodyMessage
    assert ExportedArtifactBodyReference is ServiceArtifactBodyReference is ArtifactBodyReference
    assert ExportedResultHttpAttempt is ResultHttpAttempt
    assert ExportedResultHttpCookie is ResultHttpCookie
    assert ExportedResultHttpExchange is ResultHttpExchange
    assert ExportedResultHttpField is ResultHttpField
    assert ExportedResultHttpMessage is ResultHttpMessage
    assert ExportedResultHttpMessageState is ResultHttpMessageState
    assert ExportedResultHttpOmissionReason is ResultHttpOmissionReason
    assert ExportedResultHttpRequestMetadata is ResultHttpRequestMetadata
    assert ExportedResultHttpRequestSlot is ResultHttpRequestSlot
    assert ExportedResultHttpResponseMetadata is ResultHttpResponseMetadata
    assert ExportedResultHttpResponseSlot is ResultHttpResponseSlot
    with pytest.raises(TypeError, match="reference must be typed"):
        msgspec.structs.replace(
            message,
            reference=cast("ArtifactBodyReference", object()),
        )
    with pytest.raises(TypeError, match="capture flag must be bool"):
        msgspec.structs.replace(message, has_body_capture=cast("bool", 1))
    for field in ("attempt_ordinal", "exchange_ordinal"):
        for invalid in (-1, cast("int", True)):
            with pytest.raises(ValueError, match="non-negative integer"):
                msgspec.structs.replace(message, **{field: invalid})
    for field in ("terminal_attempt", "terminal_exchange"):
        with pytest.raises(TypeError, match="terminal markers must be bool"):
            msgspec.structs.replace(message, **{field: cast("bool", 1)})
    for invalid_attempts in (cast("tuple[ResultHttpAttempt, ...]", []), (object(),)):
        with pytest.raises(TypeError, match="attempts must be immutable and typed"):
            msgspec.structs.replace(execution, attempts=invalid_attempts)
    for invalid_messages in (cast("tuple[ResultHttpMessage, ...]", []), (object(),)):
        with pytest.raises(TypeError, match="immutable and typed"):
            msgspec.structs.replace(execution, messages=invalid_messages)
    with pytest.raises(ValueError, match="must be unique"):
        msgspec.structs.replace(execution, messages=(message, message))
    foreign = msgspec.structs.replace(
        message,
        reference=msgspec.structs.replace(
            message.reference,
            execution_id="execution-foreign",
        ),
    )
    with pytest.raises(ValueError, match="belong to the execution"):
        msgspec.structs.replace(execution, messages=(foreign,))
    missing_attempt = msgspec.structs.replace(
        message,
        reference=msgspec.structs.replace(message.reference, attempt_id="attempt-missing"),
    )
    with pytest.raises(ValueError, match="attempt must exist"):
        msgspec.structs.replace(execution, messages=(missing_attempt,))
    missing_exchange = msgspec.structs.replace(
        message,
        reference=msgspec.structs.replace(message.reference, exchange_id="exchange-missing"),
    )
    with pytest.raises(ValueError, match="exchange must exist"):
        msgspec.structs.replace(execution, messages=(missing_exchange,))
    for changes in (
        {"attempt_ordinal": 1},
        {"exchange_ordinal": 1},
        {"terminal_attempt": False},
        {"terminal_exchange": False},
    ):
        mismatched = msgspec.structs.replace(message, **changes)
        with pytest.raises(ValueError, match="metadata must match"):
            msgspec.structs.replace(execution, messages=(mismatched,))
    capture_mismatch = msgspec.structs.replace(message, has_body_capture=False)
    with pytest.raises(ValueError, match="capture flag must match"):
        msgspec.structs.replace(
            execution,
            messages=(capture_mismatch, *execution.messages[1:]),
        )
    with pytest.raises(ValueError, match="completely match"):
        msgspec.structs.replace(execution, messages=(message,))
    omitted_request = ResultHttpRequestSlot(
        state=ResultHttpMessageState.OMITTED,
        metadata=None,
        omission_reason=ResultHttpOmissionReason.POLICY,
    )
    exchange = msgspec.structs.replace(execution.attempts[0].exchanges[0], request=omitted_request)
    attempt = msgspec.structs.replace(execution.attempts[0], exchanges=(exchange,))
    with pytest.raises(ValueError, match="must identify retained metadata"):
        msgspec.structs.replace(execution, attempts=(attempt,))
    with pytest.raises(AttributeError):
        message.has_body_capture = False  # type: ignore[misc]


def test_result_http_attempt_and_exchange_contracts_fail_closed() -> None:
    execution = project_results(_artifact()).executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]

    for exchange_changes, error, match in (
        ({"exchange_id": ""}, ValueError, "bounded non-empty"),
        ({"ordinal": -1}, ValueError, "non-negative integer"),
        ({"ordinal": cast("int", True)}, ValueError, "non-negative integer"),
        ({"terminal": cast("bool", 1)}, TypeError, "terminal marker must be bool"),
        ({"failure": object()}, TypeError, "failure must be typed or absent"),
        ({"timings": []}, TypeError, "timings must be immutable and typed"),
        ({"timings": (object(),)}, TypeError, "timings must be immutable and typed"),
        (
            {
                "timings": (
                    ResultTiming(name="connect", nanoseconds=1, reason=None),
                    ResultTiming(name="connect", nanoseconds=2, reason=None),
                )
            },
            ValueError,
            "unique and lexically canonical",
        ),
        (
            {
                "timings": (
                    ResultTiming(name="total", nanoseconds=2, reason=None),
                    ResultTiming(name="connect", nanoseconds=1, reason=None),
                )
            },
            ValueError,
            "unique and lexically canonical",
        ),
    ):
        with pytest.raises(error, match=match):
            msgspec.structs.replace(exchange, **exchange_changes)

    for attempt_changes, error, match in (
        ({"attempt_id": ""}, ValueError, "bounded non-empty"),
        ({"ordinal": -1}, ValueError, "non-negative integer"),
        ({"ordinal": cast("int", True)}, ValueError, "non-negative integer"),
        ({"terminal": cast("bool", 1)}, TypeError, "terminal markers must be bool"),
        ({"pre_send_terminal": cast("bool", 1)}, TypeError, "terminal markers must be bool"),
        ({"failure": object()}, TypeError, "failure must be typed or absent"),
        ({"timings": []}, TypeError, "timings must be immutable and typed"),
        ({"timings": (object(),)}, TypeError, "timings must be immutable and typed"),
        (
            {"pre_send_terminal": True},
            ValueError,
            "pre-send terminal, request, and failure must agree",
        ),
        (
            {"failure": ResultFailure(stage="dns", code="failed", retryable=True)},
            ValueError,
            "pre-send terminal, request, and failure must agree",
        ),
        ({"exchanges": []}, TypeError, "exchanges must be immutable and typed"),
        ({"exchanges": (object(),)}, TypeError, "exchanges must be immutable and typed"),
    ):
        with pytest.raises(error, match=match):
            msgspec.structs.replace(attempt, **attempt_changes)

    for changes, match in (
        ({"name": ""}, "bounded non-empty"),
        ({"nanoseconds": None, "reason": None}, "exactly a duration or reason"),
        ({"nanoseconds": 1, "reason": "also-present"}, "exactly a duration or reason"),
    ):
        with pytest.raises(ValueError, match=match):
            msgspec.structs.replace(exchange.timings[0], **changes)

    noncanonical_exchange = msgspec.structs.replace(
        exchange,
        ordinal=2,
        terminal=True,
    )
    for exchanges, match in (
        ((msgspec.structs.replace(exchange, terminal=False), exchange), "IDs must be unique"),
        (
            (exchange, msgspec.structs.replace(noncanonical_exchange, exchange_id="exchange-1")),
            "ordinals must be canonical",
        ),
        ((msgspec.structs.replace(exchange, terminal=False),), "terminal marker"),
    ):
        with pytest.raises(ValueError, match=match):
            msgspec.structs.replace(attempt, exchanges=exchanges)

    noncanonical_attempt = msgspec.structs.replace(attempt, ordinal=2, terminal=True)
    for attempts, match in (
        ((msgspec.structs.replace(attempt, terminal=False), attempt), "IDs must be unique"),
        (
            (attempt, msgspec.structs.replace(noncanonical_attempt, attempt_id="attempt-1")),
            "ordinals must be canonical",
        ),
        ((msgspec.structs.replace(attempt, terminal=False),), "terminal marker"),
    ):
        with pytest.raises(ValueError, match=match):
            msgspec.structs.replace(execution, attempts=attempts)


def test_result_http_metadata_and_message_slots_fail_closed() -> None:
    execution = project_results(_artifact()).executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    request = exchange.request
    response = exchange.response
    assert request.metadata is not None
    assert response.metadata is not None
    request_metadata = request.metadata
    response_metadata = response.metadata
    field = request_metadata.headers[0]

    for field_changes, error, match in (
        ({"name": "INVALID"}, ValueError, "canonical lowercase HTTP token"),
        ({"value": cast("str", object())}, ValueError, "bounded text"),
        ({"redacted": cast("bool", 1)}, TypeError, "redaction marker must be bool"),
    ):
        with pytest.raises(error, match=match):
            msgspec.structs.replace(field, **field_changes)

    cookie = ResultHttpCookie(name="SessionID", value="<redacted>", redacted=True)
    assert cookie.name == "SessionID"
    for cookie_changes, match in (
        ({"name": "invalid cookie"}, "cookie name"),
        ({"value": "unsafe"}, "cookie values"),
        ({"redacted": False}, "cookie values"),
    ):
        with pytest.raises(ValueError, match=match):
            msgspec.structs.replace(cookie, **cookie_changes)

    for request_changes, error, match in (
        ({"headers": []}, TypeError, "headers must be immutable and typed"),
        ({"headers": (object(),)}, TypeError, "headers must be immutable and typed"),
        ({"cookies": []}, TypeError, "cookies must be immutable and typed"),
        ({"cookies": (object(),)}, TypeError, "cookies must be immutable and typed"),
        ({"has_body_capture": 1}, TypeError, "capture flag must be bool"),
        ({"url_redaction_count": -1}, ValueError, "URL redactions"),
        ({"url_redaction_count": True}, ValueError, "URL redactions"),
        ({"header_redaction_count": -1}, ValueError, "header redactions"),
        ({"header_redaction_count": True}, ValueError, "header redactions"),
        ({"cookie_redaction_count": -1}, ValueError, "cookie redactions"),
        ({"cookie_redaction_count": True}, ValueError, "cookie redactions"),
        ({"url": "relative"}, ValueError, "absolute HTTP"),
    ):
        with pytest.raises(error, match=match):
            msgspec.structs.replace(request_metadata, **request_changes)
    unsafe = ResultHttpField(name="authorization", value="LEAK", redacted=False)
    with pytest.raises(ValueError, match="credential artifact fields"):
        msgspec.structs.replace(request_metadata, headers=(unsafe,))
    masked_header = ResultHttpField(name="x-secret", value="<redacted>", redacted=True)
    with pytest.raises(ValueError, match="under-report masked fields"):
        msgspec.structs.replace(
            request_metadata,
            headers=(masked_header,),
            header_redaction_count=0,
        )
    with pytest.raises(ValueError, match="under-report masked cookies"):
        msgspec.structs.replace(
            request_metadata,
            cookies=(cookie,),
            cookie_redaction_count=0,
        )

    for response_changes, error, match in (
        ({"headers": []}, TypeError, "headers must be immutable and typed"),
        ({"trailers": (object(),)}, TypeError, "trailers must be immutable and typed"),
        ({"has_body_capture": 1}, TypeError, "capture flag must be bool"),
        ({"status": 99}, ValueError, "HTTP status"),
    ):
        with pytest.raises(error, match=match):
            msgspec.structs.replace(response_metadata, **response_changes)
    unsafe_set_cookie = ResultHttpField(name="set-cookie", value="LEAK", redacted=False)
    with pytest.raises(ValueError, match="credential artifact fields"):
        msgspec.structs.replace(response_metadata, trailers=(unsafe_set_cookie,))

    for request_slot_changes, error, match in (
        ({"state": "retained"}, TypeError, "state must be typed"),
        ({"metadata": object()}, TypeError, "metadata must be typed or absent"),
        ({"omission_reason": object()}, TypeError, "reason must be typed or absent"),
        ({"metadata": None}, ValueError, "state contradicts"),
        (
            {"omission_reason": ResultHttpOmissionReason.POLICY},
            ValueError,
            "state contradicts",
        ),
        (
            {
                "state": ResultHttpMessageState.OMITTED,
                "metadata": request_metadata,
                "omission_reason": ResultHttpOmissionReason.POLICY,
            },
            ValueError,
            "state contradicts",
        ),
        (
            {
                "state": ResultHttpMessageState.OMITTED,
                "metadata": None,
                "omission_reason": None,
            },
            ValueError,
            "state contradicts",
        ),
        (
            {
                "state": ResultHttpMessageState.NOT_PRODUCED,
                "metadata": None,
                "omission_reason": None,
            },
            ValueError,
            "state contradicts",
        ),
    ):
        with pytest.raises(error, match=match):
            msgspec.structs.replace(request, **request_slot_changes)

    for response_slot_changes, error, match in (
        ({"state": "retained"}, TypeError, "state must be typed"),
        ({"metadata": object()}, TypeError, "metadata must be typed or absent"),
        ({"omission_reason": object()}, TypeError, "reason must be typed or absent"),
        ({"metadata": None}, ValueError, "state contradicts"),
        (
            {
                "state": ResultHttpMessageState.OMITTED,
                "metadata": None,
                "omission_reason": None,
            },
            ValueError,
            "state contradicts",
        ),
        (
            {
                "state": ResultHttpMessageState.NOT_PRODUCED,
                "metadata": response_metadata,
                "omission_reason": None,
            },
            ValueError,
            "state contradicts",
        ),
        (
            {
                "state": ResultHttpMessageState.NOT_PRODUCED,
                "metadata": None,
                "omission_reason": ResultHttpOmissionReason.POLICY,
            },
            ValueError,
            "state contradicts",
        ),
    ):
        with pytest.raises(error, match=match):
            msgspec.structs.replace(response, **response_slot_changes)

    with pytest.raises(TypeError, match="exchange request must be typed"):
        msgspec.structs.replace(exchange, request=object())
    with pytest.raises(TypeError, match="exchange response must be typed"):
        msgspec.structs.replace(exchange, response=object())
    failure = ResultFailure(stage="transport", code="unavailable", retryable=True)
    with pytest.raises(ValueError, match="response state and exchange failure must agree"):
        msgspec.structs.replace(exchange, failure=failure)
    not_produced = ResultHttpResponseSlot(
        state=ResultHttpMessageState.NOT_PRODUCED,
        metadata=None,
        omission_reason=None,
    )
    with pytest.raises(ValueError, match="response state and exchange failure must agree"):
        msgspec.structs.replace(exchange, response=not_produced)
    failed_exchange = msgspec.structs.replace(
        exchange,
        failure=failure,
        response=not_produced,
    )
    assert failed_exchange.failure is failure

    with pytest.raises(TypeError, match="terminal pre-send request must be typed or absent"):
        msgspec.structs.replace(attempt, terminal_pre_send=object())
    with pytest.raises(ValueError, match="terminal, request, and failure must agree"):
        msgspec.structs.replace(attempt, terminal_pre_send=request)
    terminal = msgspec.structs.replace(
        attempt,
        pre_send_terminal=True,
        failure=failure,
        terminal_pre_send=request,
    )
    assert terminal.terminal_pre_send is request


def test_projection_exposes_failed_response_state_without_fabricating_a_response() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    exchange = attempt.exchanges[0]
    failure = ArtifactFailure(stage="transport", code="read-failed", retryable=True)
    failed = msgspec.structs.replace(
        exchange,
        response=None,
        response_omission=None,
        failure=failure,
    )
    execution = msgspec.structs.replace(
        execution,
        attempts=(msgspec.structs.replace(attempt, exchanges=(failed,)),),
    )
    artifact = msgspec.structs.replace(
        artifact,
        executions=(execution, artifact.executions[1]),
    )

    projected = project_results(artifact).executions[0]

    assert tuple(item.reference.message for item in projected.messages) == (
        ArtifactBodyMessage.REQUEST,
    )
    projected_exchange = projected.attempts[0].exchanges[0]
    assert projected_exchange.failure is not None
    assert projected_exchange.failure.code == "read-failed"
    assert projected_exchange.response.state is ResultHttpMessageState.NOT_PRODUCED
    assert projected_exchange.response.metadata is None
    assert projected_exchange.response.omission_reason is None


def test_projection_is_total_for_valid_bounded_http_ids_and_round_trips() -> None:
    artifact = _artifact()
    execution = artifact.executions[0]
    attempt = execution.attempts[0]
    retained_request = attempt.exchanges[0].request
    assert retained_request is not None
    retained_request = msgspec.structs.replace(
        retained_request,
        cookies=(CoreArtifactCookie(name="SessionID", value="<redacted>", redacted=True),),
        cookie_redaction_count=1,
    )
    exchange = msgspec.structs.replace(
        attempt.exchanges[0],
        exchange_id="exchange with space λ",
        request=retained_request,
    )
    attempt = msgspec.structs.replace(
        attempt,
        attempt_id="attempt with space λ",
        exchanges=(exchange,),
    )
    execution = msgspec.structs.replace(execution, attempts=(attempt,))
    artifact = msgspec.structs.replace(
        artifact,
        executions=(execution, artifact.executions[1]),
    )

    projection = project_results(artifact)
    projected = projection.executions[0]

    assert projected.attempts[0].attempt_id == "attempt with space λ"
    assert projected.attempts[0].exchanges[0].exchange_id == "exchange with space λ"
    assert projected.messages[0].reference.attempt_id == "attempt with space λ"
    assert projected.messages[0].reference.exchange_id == "exchange with space λ"
    projected_request = projected.attempts[0].exchanges[0].request.metadata
    assert projected_request is not None
    assert projected_request.cookies == (
        ResultHttpCookie(name="SessionID", value="<redacted>", redacted=True),
    )
    assert msgspec.json.decode(msgspec.json.encode(projection), type=ResultProjection) == projection


def test_projection_exposes_sanitized_run_annotations() -> None:
    artifact = msgspec.structs.replace(
        _artifact(),
        annotations=ArtifactRunAnnotations(
            title="Release candidate",
            notes="Review the warning facts.",
            tags=("ci", "release"),
            vcs=ArtifactVcsProvenance(
                provider="git",
                repository="example/comparo",
                revision="abc123",
                branch="main",
            ),
            ci=ArtifactCiProvenance(
                provider="github-actions",
                run_id="42",
                job="compare",
                url="https://example.test/actions/runs/42",
            ),
            redaction_count=1,
        ),
    )

    annotations = project_results(artifact).annotations

    assert annotations == ResultRunAnnotations(
        title="Release candidate",
        notes="Review the warning facts.",
        tags=("ci", "release"),
        vcs=ResultVcsProvenance(
            provider="git",
            repository="example/comparo",
            revision="abc123",
            branch="main",
        ),
        ci=ResultCiProvenance(
            provider="github-actions",
            run_id="42",
            job="compare",
            url="https://example.test/actions/runs/42",
        ),
        redaction_count=1,
    )


def test_result_annotation_projection_rejects_untyped_nested_values() -> None:
    valid = ResultRunAnnotations()
    with pytest.raises(TypeError, match="VCS provenance"):
        msgspec.structs.replace(valid, vcs=cast("ResultVcsProvenance", object()))
    with pytest.raises(TypeError, match="CI provenance"):
        msgspec.structs.replace(valid, ci=cast("ResultCiProvenance", object()))

    projection = project_results(_artifact())
    with pytest.raises(TypeError, match="result annotations"):
        msgspec.structs.replace(
            projection,
            annotations=cast("ResultRunAnnotations", object()),
        )


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
