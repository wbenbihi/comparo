"""Adversarial terminal stream truth derived from exchanges, views, and stop evidence."""

from __future__ import annotations

import msgspec
import pytest

from comparo.core.domain.expressions import SymbolPath
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyCaptureState
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CaptureBudgetKind
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ContentCoding
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import ExplicitlyTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.stream_termination import AcceptedManualWindowEvidence
from comparo.core.domain.http.stream_termination import ConditionWindowEvidence
from comparo.core.domain.http.stream_termination import DurationWindowEvidence
from comparo.core.domain.http.stream_termination import EventCountWindowEvidence
from comparo.core.domain.http.stream_termination import FramingDecodeFailureEvidence
from comparo.core.domain.http.stream_termination import IdleWindowEvidence
from comparo.core.domain.http.stream_termination import ManualStopEvidence
from comparo.core.domain.http.stream_termination import SafetyByteLimitEvidence
from comparo.core.domain.http.stream_termination import SafetyCountLimitEvidence
from comparo.core.domain.http.stream_termination import SafetyDurationLimitEvidence
from comparo.core.domain.http.stream_termination import StreamTerminationEvidence
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamRecordsHandle
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.views import DecodedViewHandle
from comparo.core.domain.http.views import ViewCompleteness
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from tests.core.domain.http.budget_factories import capture_exhaustion
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_execution
from tests.core.domain.http.stream_factories import stream_timings
from tests.core.domain.http.stream_factories import view_handle


def _single_owner(
    observed_exchange: HttpExchange,
    *,
    kind: ViewKind = ViewKind.SSE,
    completeness: ViewCompleteness = ViewCompleteness.FULL,
) -> tuple[EnvironmentExecutionObservation, DecodedViewHandle]:
    concrete = observed_exchange
    assert concrete.response is not None
    assert type(concrete.response.body) is ResponseEntityBody
    assert concrete.response.body.handle is not None
    view = view_handle(
        kind,
        completeness=completeness,
        source=concrete.response.body.handle,
        view_number=1201,
    )
    owner = EnvironmentExecutionObservation(
        execution_id=concrete.request.occurrence.execution_id,
        environment=concrete.request.request.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=concrete.request.occurrence.attempt_id,
                retry=None,
                exchanges=(concrete,),
                timings=attempt_timings(),
            ),
        ),
        timings=stream_execution(kind).timings,
        views=(view,),
    )
    return owner, view


def _observation(
    owner: EnvironmentExecutionObservation,
    view: DecodedViewHandle,
    *,
    end: SegmentEndReason,
    completeness: StreamCompleteness,
    termination: StreamTerminationReason,
    evidence: StreamTerminationEvidence | None = None,
    records: int = 1,
    errors: int = 0,
) -> StreamObservation:
    kind = StreamKind(view.decoder.view.value)
    connection = msgspec.structs.replace(
        segment(
            index=0,
            first=0,
            count=records,
            errors=errors,
            kind=kind,
            end_reason=end,
            byte_start=0,
            byte_end=view.source.byte_count.value,
        ),
        attempt_id=owner.attempts[0].attempt_id,
        exchange_id=owner.attempts[0].exchanges[-1].exchange_id,
        view=view,
    )
    return StreamObservation(
        records=StreamRecordsHandle(
            owner=owner,
            kind=kind,
            record_count=Count(value=records),
            control_count=Count(value=0),
            error_count=Count(value=errors),
        ),
        segments=(connection,),
        captured_bytes=view.source.byte_count,
        extent=StreamExtent(completeness=completeness, termination=termination),
        trailing_incomplete=(),
        timings=stream_timings(),
        termination_evidence=evidence,
    )


def _entity(
    *,
    capture: BodyCaptureState | CaptureBudgetKind,
    size: int,
    encoded: int | None = None,
) -> ResponseEntityBody:
    handle = body_handle(size=size, extent=BodyCaptureExtent.PARTIAL)
    actual_capture: BodyCaptureState
    if isinstance(capture, CaptureBudgetKind):
        actual_capture = BudgetExceededCapture(
            exhaustion=capture_exhaustion(
                handle=handle,
                kind=capture,
                encoded_byte_count=ByteCount(value=encoded) if encoded is not None else None,
            )
        )
    else:
        actual_capture = capture
    return ResponseEntityBody(
        capture=actual_capture,
        handle=handle,
        encoded_byte_count=ByteCount(value=encoded) if encoded is not None else None,
    )


def _response_for_budget(
    body: ResponseEntityBody,
    budget: CaptureBudgetKind,
) -> ResponseObservation:
    observed = (
        response(body=body) if budget is not CaptureBudgetKind.EXPANSION_RATIO else response()
    )
    if budget is not CaptureBudgetKind.EXPANSION_RATIO:
        return observed
    return msgspec.structs.replace(
        observed,
        headers=ResponseFields(
            fields=(
                ResponseField(
                    name=HeaderName(value="content-encoding"),
                    value=HeaderText(value="gzip"),
                ),
            )
        ),
        content_encodings=(ContentCoding(value="gzip"),),
        body=body,
    )


def test_terminal_exchange_truth_rejects_user_asserted_extent_and_end() -> None:
    effective = request()
    interrupted = exchange(
        exchange_number=1200,
        effective_request=effective,
        observed_response=response(
            body=_entity(
                capture=BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
                size=4,
            )
        ),
        failure=failure(FailurePhase.BODY_READ),
    )
    interrupted_owner, interrupted_view = _single_owner(
        interrupted,
        completeness=ViewCompleteness.PREFIX,
    )
    assert (
        _observation(
            interrupted_owner,
            interrupted_view,
            end=SegmentEndReason.FAILURE,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.TRANSPORT_FAILURE,
        ).extent.termination
        is StreamTerminationReason.TRANSPORT_FAILURE
    )
    with pytest.raises(ValueError, match=r"exact logical extent|exact segment end"):
        _observation(
            interrupted_owner,
            interrupted_view,
            end=SegmentEndReason.REMOTE_EOF,
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        )

    clean = exchange(
        exchange_number=1201,
        effective_request=effective,
        observed_response=response(
            body=ResponseEntityBody(
                capture=CompleteCapture(),
                handle=body_handle(size=4),
                encoded_byte_count=ByteCount(value=4),
            )
        ),
    )
    clean_owner, clean_view = _single_owner(clean)
    assert (
        _observation(
            clean_owner,
            clean_view,
            end=SegmentEndReason.REMOTE_EOF,
            completeness=StreamCompleteness.FULL,
            termination=StreamTerminationReason.REMOTE_EOF,
        ).extent.completeness
        is StreamCompleteness.FULL
    )
    with pytest.raises(ValueError, match="exact logical extent"):
        _observation(
            clean_owner,
            clean_view,
            end=SegmentEndReason.FAILURE,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.POLICY_BLOCKED,
        )


@pytest.mark.parametrize(
    ("capture", "phase", "termination", "end"),
    [
        (
            ContentDecodingFailedCapture(),
            FailurePhase.CONTENT_DECODING,
            StreamTerminationReason.DECODE_FAILURE,
            SegmentEndReason.FAILURE,
        ),
        (
            StorageFailedCapture(),
            FailurePhase.STORAGE,
            StreamTerminationReason.STORAGE_FAILURE,
            SegmentEndReason.FAILURE,
        ),
        (
            CancelledCapture(),
            FailurePhase.CANCELLATION,
            StreamTerminationReason.CANCELLED,
            SegmentEndReason.CANCELLED,
        ),
    ],
)
def test_terminal_operational_failures_keep_their_exact_taxonomy(
    capture: BodyCaptureState,
    phase: FailurePhase,
    termination: StreamTerminationReason,
    end: SegmentEndReason,
) -> None:
    observed = exchange(
        exchange_number=1210,
        effective_request=request(),
        observed_response=response(body=_entity(capture=capture, size=4)),
        failure=failure(phase),
    )
    owner, view = _single_owner(observed, completeness=ViewCompleteness.PREFIX)
    assert (
        _observation(
            owner,
            view,
            end=end,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=termination,
        ).extent.termination
        is termination
    )


@pytest.mark.parametrize(
    ("budget", "size", "encoded"),
    [
        (
            CaptureBudgetKind.ENTITY_BYTES,
            4,
            4,
        ),
        (
            CaptureBudgetKind.ENCODED_BYTES,
            4,
            4,
        ),
        (
            CaptureBudgetKind.EXPANSION_RATIO,
            8,
            4,
        ),
    ],
)
def test_body_capture_budget_evidence_is_dimension_specific(
    budget: CaptureBudgetKind,
    size: int,
    encoded: int,
) -> None:
    observed = exchange(
        exchange_number=1220,
        effective_request=request(),
        observed_response=_response_for_budget(
            _entity(
                capture=budget,
                size=size,
                encoded=encoded,
            ),
            budget,
        ),
        failure=failure(FailurePhase.RESOURCE_BUDGET),
    )
    owner, view = _single_owner(observed, completeness=ViewCompleteness.PREFIX)
    assert (
        _observation(
            owner,
            view,
            end=SegmentEndReason.FAILURE,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.RESOURCE_BUDGET,
        ).extent.termination
        is StreamTerminationReason.RESOURCE_BUDGET
    )
    with pytest.raises(ValueError, match="must not be duplicated"):
        _observation(
            owner,
            view,
            end=SegmentEndReason.FAILURE,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.RESOURCE_BUDGET,
            evidence=SafetyByteLimitEvidence(limit=ByteCount(value=size)),
        )


def test_authored_window_and_stream_limit_evidence_reconciles_exact_facts() -> None:
    observed = exchange(
        exchange_number=1230,
        effective_request=request(),
        observed_response=response(
            body=_entity(
                capture=ExplicitlyTruncatedCapture(limit=ByteCount(value=4)),
                size=4,
            )
        ),
    )
    owner, view = _single_owner(observed, completeness=ViewCompleteness.PREFIX)
    windows: tuple[tuple[StreamTerminationEvidence, StreamTerminationReason], ...] = (
        (
            EventCountWindowEvidence(limit=PositiveCount(value=1)),
            StreamTerminationReason.EVENT_COUNT_WINDOW,
        ),
        (
            DurationWindowEvidence(limit=Duration(nanoseconds=10)),
            StreamTerminationReason.DURATION_WINDOW,
        ),
        (
            IdleWindowEvidence(
                timeout=Duration(nanoseconds=5),
                observed_idle=Duration(nanoseconds=10),
            ),
            StreamTerminationReason.IDLE_WINDOW,
        ),
        (
            ConditionWindowEvidence(condition=SymbolPath(segments=("stop",))),
            StreamTerminationReason.CONDITION_WINDOW,
        ),
        (AcceptedManualWindowEvidence(), StreamTerminationReason.ACCEPTED_MANUAL_WINDOW),
    )
    for evidence, termination in windows:
        assert (
            _observation(
                owner,
                view,
                end=SegmentEndReason.WINDOW_CLOSED,
                completeness=StreamCompleteness.WINDOW,
                termination=termination,
                evidence=evidence,
            ).extent.termination
            is termination
        )

    for evidence, termination in (
        (
            SafetyCountLimitEvidence(limit=PositiveCount(value=1)),
            StreamTerminationReason.SAFETY_COUNT_LIMIT,
        ),
        (
            SafetyDurationLimitEvidence(limit=Duration(nanoseconds=10)),
            StreamTerminationReason.SAFETY_DURATION_LIMIT,
        ),
        (
            SafetyByteLimitEvidence(limit=ByteCount(value=4)),
            StreamTerminationReason.SAFETY_BYTE_LIMIT,
        ),
    ):
        assert (
            _observation(
                owner,
                view,
                end=SegmentEndReason.FAILURE,
                completeness=StreamCompleteness.INCOMPLETE,
                termination=termination,
                evidence=evidence,
            ).extent.termination
            is termination
        )

    with pytest.raises(ValueError, match="equal its safety limit"):
        _observation(
            owner,
            view,
            end=SegmentEndReason.FAILURE,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.SAFETY_COUNT_LIMIT,
            evidence=SafetyCountLimitEvidence(limit=PositiveCount(value=2)),
        )
    with pytest.raises(ValueError, match="exact authored limit"):
        _observation(
            owner,
            view,
            end=SegmentEndReason.WINDOW_CLOSED,
            completeness=StreamCompleteness.WINDOW,
            termination=StreamTerminationReason.EVENT_COUNT_WINDOW,
            evidence=EventCountWindowEvidence(limit=PositiveCount(value=2)),
        )
    with pytest.raises(ValueError, match="equal their safety limit"):
        _observation(
            owner,
            view,
            end=SegmentEndReason.FAILURE,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.SAFETY_BYTE_LIMIT,
            evidence=SafetyByteLimitEvidence(limit=ByteCount(value=3)),
        )
    with pytest.raises(ValueError, match="observed idle"):
        _observation(
            owner,
            view,
            end=SegmentEndReason.WINDOW_CLOSED,
            completeness=StreamCompleteness.WINDOW,
            termination=StreamTerminationReason.IDLE_WINDOW,
            evidence=IdleWindowEvidence(
                timeout=Duration(nanoseconds=1),
                observed_idle=Duration(nanoseconds=3_600_000_000_000),
            ),
        )

    assert (
        _observation(
            owner,
            view,
            end=SegmentEndReason.CANCELLED,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.MANUAL_STOP,
            evidence=ManualStopEvidence(),
        ).extent.termination
        is StreamTerminationReason.MANUAL_STOP
    )


def test_clean_partial_response_cannot_be_relabelled_as_cancelled() -> None:
    observed = exchange(
        exchange_number=1240,
        effective_request=request(),
        observed_response=response(
            body=_entity(
                capture=ExplicitlyTruncatedCapture(limit=ByteCount(value=4)),
                size=4,
            )
        ),
    )
    owner, view = _single_owner(observed, completeness=ViewCompleteness.PREFIX)
    with pytest.raises(ValueError, match="typed stop evidence"):
        _observation(
            owner,
            view,
            end=SegmentEndReason.CANCELLED,
            completeness=StreamCompleteness.INCOMPLETE,
            termination=StreamTerminationReason.CANCELLED,
        )


def test_framing_decode_evidence_binds_the_terminal_segment_only() -> None:
    owner = stream_execution(ViewKind.NDJSON)
    records = StreamRecordsHandle(
        owner=owner,
        kind=StreamKind.NDJSON,
        record_count=Count(value=2),
        control_count=Count(value=0),
        error_count=Count(value=1),
    )
    first = segment(
        index=0,
        first=0,
        count=1,
        errors=1,
        kind=StreamKind.NDJSON,
        end_reason=SegmentEndReason.RECONNECT,
    )
    second = segment(
        index=1,
        first=1,
        count=1,
        first_item=2,
        first_error=1,
        kind=StreamKind.NDJSON,
        end_reason=SegmentEndReason.FAILURE,
    )
    with pytest.raises(ValueError, match="framing decode failure"):
        StreamObservation(
            records=records,
            segments=(first, second),
            captured_bytes=ByteCount(value=8),
            extent=StreamExtent(
                completeness=StreamCompleteness.INCOMPLETE,
                termination=StreamTerminationReason.DECODE_FAILURE,
            ),
            trailing_incomplete=(),
            timings=stream_timings(),
            termination_evidence=FramingDecodeFailureEvidence(),
        )
