"""Capture live HTTP observations into safe artifact records before runtime cleanup."""

from __future__ import annotations

import asyncio
from typing import cast
from typing import final
from urllib.parse import quote

import msgspec

from comparo.core.artifacts.common import ArtifactCapture
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import ArtifactSourceCaptureState
from comparo.core.artifacts.http import ArtifactAttempt
from comparo.core.artifacts.http import ArtifactExchange
from comparo.core.artifacts.http import ArtifactField
from comparo.core.artifacts.http import ArtifactRequest
from comparo.core.artifacts.http import ArtifactResponse
from comparo.core.artifacts.http import ArtifactTiming
from comparo.core.artifacts.model import ArtifactCoverageIssue
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.artifacts.ports import ArtifactBodyReader
from comparo.core.artifacts.ports import SanitizationSnapshotProvider
from comparo.core.artifacts.ports import SanitizedBlobTarget
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.fields import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.domain.http.fields import MANDATORY_SENSITIVE_QUERY_NAMES
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import BodyCaptureState
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import ExplicitlyTruncatedCapture
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.runtime.models import ExecutionOutcome
from comparo.core.runtime.models import ExecutionWork
from comparo.core.runtime.models import ObservedExecution
from comparo.core.runtime.ports import ExecutionDriver


@final
class CapturedExecutionHttp(msgspec.Struct, frozen=True, kw_only=True):
    """One safe HTTP graph captured while transient body storage remained live."""

    execution_id: str
    timings: tuple[ArtifactTiming, ...]
    attempts: tuple[ArtifactAttempt, ...]

    def __post_init__(self) -> None:
        """Require a stable identity and immutable safe HTTP products."""
        if type(self.execution_id) is not str or not self.execution_id:
            raise ValueError("captured execution ID must be non-empty text")
        if type(self.timings) is not tuple or any(
            type(item) is not ArtifactTiming for item in self.timings
        ):
            raise TypeError("captured execution timings must be immutable and typed")
        if type(self.attempts) is not tuple or any(
            type(item) is not ArtifactAttempt for item in self.attempts
        ):
            raise TypeError("captured execution attempts must be immutable and typed")


@final
class ArtifactCaptureBatch(msgspec.Struct, frozen=True, kw_only=True):
    """Safe HTTP products and coverage from one completed capture boundary."""

    executions: tuple[CapturedExecutionHttp, ...]
    coverage_issues: tuple[ArtifactCoverageIssue, ...]
    failure: ArtifactFailure | None = None

    def __post_init__(self) -> None:
        """Require deterministic unique execution captures and typed coverage."""
        if type(self.executions) is not tuple or any(
            type(item) is not CapturedExecutionHttp for item in self.executions
        ):
            raise TypeError("artifact capture executions must be immutable and typed")
        identities = tuple(item.execution_id for item in self.executions)
        if len(set(identities)) != len(identities):
            raise ValueError("artifact capture execution IDs must be unique")
        if type(self.coverage_issues) is not tuple or any(
            type(item) is not ArtifactCoverageIssue for item in self.coverage_issues
        ):
            raise TypeError("artifact capture coverage must be immutable and typed")
        if self.failure is not None and type(self.failure) is not ArtifactFailure:
            raise TypeError("artifact capture failure must be typed")


class ArtifactCaptureUnavailableError(RuntimeError):
    """The safe capture batch was requested before driver cleanup or after capture failure."""


class _RejectArtifactCaptureError(RuntimeError):
    """Internal value-free signal for an ArtifactProfile fail-closed rejection."""


class ArtifactCapturingDriver:
    """ExecutionDriver decorator that captures before closing live run resources."""

    __slots__ = (
        "_batch",
        "_blobs",
        "_bodies",
        "_boundary",
        "_closed",
        "_driver",
        "_outcomes",
        "_policy",
        "_redaction",
        "_snapshots",
    )

    def __init__(
        self,
        *,
        driver: ExecutionDriver,
        snapshots: SanitizationSnapshotProvider,
        bodies: ArtifactBodyReader,
        blobs: SanitizedBlobTarget,
        policy: ArtifactPolicy,
        redaction: RedactionPolicy,
    ) -> None:
        """Compose capture around explicit run-owned ports without inspecting adapters."""
        if type(policy) is not ArtifactPolicy or type(redaction) is not RedactionPolicy:
            raise TypeError("artifact capturing driver requires typed policies")
        self._driver = driver
        self._snapshots = snapshots
        self._bodies = bodies
        self._blobs = blobs
        self._policy = policy
        self._redaction = redaction
        self._outcomes: dict[int, tuple[ExecutionWork, ExecutionOutcome]] = {}
        self._boundary: SanitizationBoundary | None = None
        self._batch: ArtifactCaptureBatch | None = None
        self._closed = False

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        """Delegate execution and retain its live outcome only until close-time capture."""
        if self._closed:
            raise ArtifactCaptureUnavailableError("artifact capturing driver is closed")
        outcome = await self._driver.execute(work)
        self._outcomes[work.ordinal.value] = (work, outcome)
        return outcome

    async def close(self) -> None:
        """Capture with the final registry and live body store, then close the delegate."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._capture()
        except asyncio.CancelledError:
            self._batch = _failed_batch("cancelled")
            raise
        except _RejectArtifactCaptureError:
            if self._boundary is not None:
                self._boundary.close()
                self._boundary = None
            self._batch = _failed_batch("unsafe-source")
        except Exception:  # noqa: BLE001 - retain only a fixed failure code
            if self._boundary is not None:
                self._boundary.close()
                self._boundary = None
            self._batch = _failed_batch("capture-failed")
        finally:
            self._outcomes.clear()
            await self._driver.close()

    @property
    def batch(self) -> ArtifactCaptureBatch:
        """Return the safe batch only after the scheduler has closed the decorator."""
        if not self._closed or self._batch is None:
            raise ArtifactCaptureUnavailableError("artifact capture batch is unavailable")
        return self._batch

    @property
    def boundary(self) -> SanitizationBoundary:
        """Return the still-live boundary for sanitizing later evaluation evidence."""
        if self._boundary is None:
            raise ArtifactCaptureUnavailableError("artifact sanitization boundary is unavailable")
        return self._boundary

    @property
    def has_custom_redaction(self) -> bool:
        """Return whether retained evaluation evidence needs conservative omission."""
        return bool(self._redaction.rules)

    def release(self) -> None:
        """Wipe the sanitizer after artifact compilation and reject later boundary access."""
        if self._boundary is not None:
            self._boundary.close()
            self._boundary = None

    async def _capture(self) -> None:
        snapshot = await self._snapshots.sanitization_snapshot()
        boundary = SanitizationBoundary.from_snapshot(snapshot, marker=self._redaction.marker)
        observations = tuple(
            outcome.observation
            for _work, outcome in self._outcomes.values()
            if type(outcome) is ObservedExecution
        )
        for observation in observations:
            _register_observation_values(boundary, observation)
        boundary.seal()
        self._boundary = boundary
        issues: list[ArtifactCoverageIssue] = []
        captures = tuple(
            [
                await _capture_observation(
                    observation,
                    boundary=boundary,
                    bodies=self._bodies,
                    blobs=self._blobs,
                    policy=self._policy,
                    redaction=self._redaction,
                    issues=issues,
                )
                for observation in observations
            ]
        )
        self._batch = ArtifactCaptureBatch(
            executions=captures,
            coverage_issues=tuple(issues),
        )


def _failed_batch(code: str) -> ArtifactCaptureBatch:
    return ArtifactCaptureBatch(
        executions=(),
        coverage_issues=(),
        failure=ArtifactFailure(stage="artifact-capture", code=code),
    )


def _register_observation_values(
    boundary: SanitizationBoundary,
    observation: EnvironmentExecutionObservation,
) -> None:
    for attempt in observation.attempts:
        for exchange in attempt.exchanges:
            _register_request_values(boundary, exchange.request.request)
            if exchange.response is not None:
                _register_response_values(boundary, exchange.response)
        for pre_send in (attempt.pre_send, attempt.redirect_pre_send):
            if pre_send is not None:
                _register_request_values(boundary, _pre_send_request(pre_send))


def _register_request_values(
    boundary: SanitizationBoundary,
    request: FinalizedWireRequest,
) -> None:
    for value in request.url.path.segments:
        _register_if_sensitive(boundary, value)
    for parameter in request.url.query.parameters:
        _register_if_sensitive(boundary, parameter.value)
    for field in request.headers.fields:
        _register_if_sensitive(boundary, field.value)
    for cookie in request.cookies.cookies:
        _register_if_sensitive(boundary, cookie.value)
    if type(request.body) is MaterializedBody:
        _register_if_sensitive(boundary, request.body.source.digest)


def _register_response_values(
    boundary: SanitizationBoundary,
    response: ResponseObservation,
) -> None:
    for fields in (
        *(item.headers for item in response.informational),
        response.headers,
        response.trailers,
    ):
        for field in fields.fields:
            _register_if_sensitive(boundary, field.value)
    if type(response.body) is ResponseEntityBody and response.body.handle is not None:
        _register_if_sensitive(boundary, response.body.handle.digest)


def _register_if_sensitive(boundary: SanitizationBoundary, value: object) -> None:
    if is_sensitive_value(value):
        boundary.register(value)


async def _capture_observation(
    observation: EnvironmentExecutionObservation,
    *,
    boundary: SanitizationBoundary,
    bodies: ArtifactBodyReader,
    blobs: SanitizedBlobTarget,
    policy: ArtifactPolicy,
    redaction: RedactionPolicy,
    issues: list[ArtifactCoverageIssue],
) -> CapturedExecutionHttp:
    execution_id = str(observation.execution_id.value)
    attempts = tuple(
        [
            await _capture_attempt(
                attempt,
                execution_id=execution_id,
                boundary=boundary,
                bodies=bodies,
                blobs=blobs,
                policy=policy,
                redaction=redaction,
                issues=issues,
            )
            for attempt in observation.attempts
        ]
    )
    return CapturedExecutionHttp(
        execution_id=execution_id,
        timings=_execution_timings(observation.timings),
        attempts=attempts,
    )


async def _capture_attempt(
    attempt: HttpAttempt,
    *,
    execution_id: str,
    boundary: SanitizationBoundary,
    bodies: ArtifactBodyReader,
    blobs: SanitizedBlobTarget,
    policy: ArtifactPolicy,
    redaction: RedactionPolicy,
    issues: list[ArtifactCoverageIssue],
) -> ArtifactAttempt:
    exchanges = tuple(
        [
            await _capture_exchange(
                exchange,
                execution_id=execution_id,
                boundary=boundary,
                bodies=bodies,
                blobs=blobs,
                policy=policy,
                redaction=redaction,
                issues=issues,
            )
            for exchange in attempt.exchanges
        ]
    )
    pre_send = attempt.pre_send or attempt.redirect_pre_send
    request: ArtifactRequest | None = None
    omission: ArtifactOmissionReason | None = None
    failure: ArtifactFailure | None = None
    if pre_send is not None:
        if policy.capture.requests:
            request = await _capture_request(
                _pre_send_request(pre_send),
                source=f"attempts.{attempt.attempt_id.value}.pre-send.request",
                execution_id=execution_id,
                boundary=boundary,
                bodies=bodies,
                blobs=blobs,
                policy=policy,
                redaction=redaction,
                issues=issues,
            )
        else:
            omission = ArtifactOmissionReason.POLICY
            _coverage(
                issues,
                "request",
                execution_id,
                ArtifactCaptureExtent.OMITTED,
                omission,
            )
        failure = _pre_send_failure(pre_send)
    return ArtifactAttempt(
        attempt_id=str(attempt.attempt_id.value),
        exchanges=exchanges,
        timings=_attempt_timings(attempt.timings),
        terminal_pre_send_request=request,
        terminal_pre_send_omission=omission,
        failure=failure,
    )


async def _capture_exchange(
    exchange: HttpExchange,
    *,
    execution_id: str,
    boundary: SanitizationBoundary,
    bodies: ArtifactBodyReader,
    blobs: SanitizedBlobTarget,
    policy: ArtifactPolicy,
    redaction: RedactionPolicy,
    issues: list[ArtifactCoverageIssue],
) -> ArtifactExchange:
    exchange_id = str(exchange.exchange_id.value)
    request: ArtifactRequest | None
    request_omission: ArtifactOmissionReason | None
    if policy.capture.requests:
        request = await _capture_request(
            exchange.request.request,
            source=f"exchanges.{exchange_id}.request",
            execution_id=execution_id,
            boundary=boundary,
            bodies=bodies,
            blobs=blobs,
            policy=policy,
            redaction=redaction,
            issues=issues,
        )
        request_omission = None
    else:
        request = None
        request_omission = ArtifactOmissionReason.POLICY
        _coverage(
            issues,
            "request",
            execution_id,
            ArtifactCaptureExtent.OMITTED,
            request_omission,
        )
    response: ArtifactResponse | None = None
    response_omission: ArtifactOmissionReason | None = None
    if exchange.response is not None:
        if policy.capture.responses:
            response = await _capture_response(
                exchange.response,
                source=f"exchanges.{exchange_id}.response",
                execution_id=execution_id,
                boundary=boundary,
                bodies=bodies,
                blobs=blobs,
                policy=policy,
                redaction=redaction,
                issues=issues,
            )
        else:
            response_omission = ArtifactOmissionReason.POLICY
            _coverage(
                issues,
                "response",
                execution_id,
                ArtifactCaptureExtent.OMITTED,
                response_omission,
            )
    return ArtifactExchange(
        exchange_id=exchange_id,
        request=request,
        request_omission=request_omission,
        response=response,
        response_omission=response_omission,
        failure=_transport_failure(exchange.failure),
        timings=_exchange_timings(exchange.timings),
    )


async def _capture_request(
    request: FinalizedWireRequest,
    *,
    source: str,
    execution_id: str,
    boundary: SanitizationBoundary,
    bodies: ArtifactBodyReader,
    blobs: SanitizedBlobTarget,
    policy: ArtifactPolicy,
    redaction: RedactionPolicy,
    issues: list[ArtifactCoverageIssue],
) -> ArtifactRequest:
    body: ArtifactCapture | None = None
    if type(request.body) is MaterializedBody:
        body = await _capture_body(
            request.body.source,
            source_state=ArtifactSourceCaptureState.COMPLETE,
            source=f"{source}.body",
            execution_id=execution_id,
            boundary=boundary,
            bodies=bodies,
            blobs=blobs,
            policy=policy,
            custom_redaction=_source_has_custom_redaction(
                redaction,
                SelectorSource.REQUEST_BODY,
            ),
            issues=issues,
        )
    elif type(request.body) is not NoMaterializedBody:  # pragma: no cover - domain invariant
        raise TypeError("captured request body has an unsupported type")
    return ArtifactRequest(
        method=request.method.value,
        url=_sanitize_url(request.url, boundary, redaction),
        headers=_request_headers(request, boundary, redaction),
        cookies=_request_cookies(request, boundary, redaction),
        body=body,
    )


async def _capture_response(
    response: ResponseObservation,
    *,
    source: str,
    execution_id: str,
    boundary: SanitizationBoundary,
    bodies: ArtifactBodyReader,
    blobs: SanitizedBlobTarget,
    policy: ArtifactPolicy,
    redaction: RedactionPolicy,
    issues: list[ArtifactCoverageIssue],
) -> ArtifactResponse:
    body: ArtifactCapture | None = None
    if type(response.body) is ResponseEntityBody:
        body = await _capture_response_body(
            response.body,
            source=f"{source}.body",
            execution_id=execution_id,
            boundary=boundary,
            bodies=bodies,
            blobs=blobs,
            policy=policy,
            custom_redaction=_source_has_custom_redaction(
                redaction,
                SelectorSource.RESPONSE_BODY,
            ),
            issues=issues,
        )
    elif type(response.body) is not NoResponseBody:  # pragma: no cover - domain invariant
        raise TypeError("captured response body has an unsupported type")
    return ArtifactResponse(
        status=response.status.value,
        protocol=response.protocol.value,
        headers=_response_fields(
            response.headers,
            boundary,
            redaction,
            SelectorSource.RESPONSE_HEADERS,
        ),
        trailers=_response_fields(
            response.trailers,
            boundary,
            redaction,
            SelectorSource.RESPONSE_TRAILERS,
        ),
        body=body,
    )


async def _capture_response_body(
    body: ResponseEntityBody,
    *,
    source: str,
    execution_id: str,
    boundary: SanitizationBoundary,
    bodies: ArtifactBodyReader,
    blobs: SanitizedBlobTarget,
    policy: ArtifactPolicy,
    custom_redaction: bool,
    issues: list[ArtifactCoverageIssue],
) -> ArtifactCapture:
    state = _source_state(body.capture)
    if body.handle is None:
        reason = _source_reason(state)
        _coverage(
            issues,
            source,
            execution_id,
            ArtifactCaptureExtent.OMITTED,
            reason,
        )
        if policy.unsafe_source is UnsafeSourceBehavior.REJECT_ARTIFACT:
            raise _RejectArtifactCaptureError
        return ArtifactCapture(
            source_state=state,
            extent=ArtifactCaptureExtent.OMITTED,
            source_byte_count=None,
            blob=None,
            reason=reason,
        )
    return await _capture_body(
        body.handle,
        source_state=state,
        source=source,
        execution_id=execution_id,
        boundary=boundary,
        bodies=bodies,
        blobs=blobs,
        policy=policy,
        custom_redaction=custom_redaction,
        issues=issues,
    )


async def _capture_body(
    descriptor: RepeatableByteSource | BodyHandle,
    *,
    source_state: ArtifactSourceCaptureState,
    source: str,
    execution_id: str,
    boundary: SanitizationBoundary,
    bodies: ArtifactBodyReader,
    blobs: SanitizedBlobTarget,
    policy: ArtifactPolicy,
    custom_redaction: bool,
    issues: list[ArtifactCoverageIssue],
) -> ArtifactCapture:
    byte_count = descriptor.byte_count.value
    if not policy.capture.bodies or custom_redaction:
        policy_reason = ArtifactOmissionReason.POLICY
        _coverage(
            issues,
            source,
            execution_id,
            ArtifactCaptureExtent.OMITTED,
            policy_reason,
        )
        return ArtifactCapture(
            source_state=source_state,
            extent=ArtifactCaptureExtent.OMITTED,
            source_byte_count=byte_count,
            blob=None,
            reason=policy_reason,
        )
    writer = None
    try:
        writer = await blobs.begin()
        redactions = await boundary.sanitize_stream(bodies.read(descriptor), writer.write)
        blob = await writer.commit(media_type=None)
    except asyncio.CancelledError:
        if writer is not None:
            await writer.abort()
        raise
    except Exception:  # noqa: BLE001 - persistence receives no exception text
        if writer is not None:
            await writer.abort()
        if policy.unsafe_source is UnsafeSourceBehavior.REJECT_ARTIFACT:
            raise _RejectArtifactCaptureError from None
        failure_reason = ArtifactOmissionReason.STORAGE_FAILED
        _coverage(
            issues,
            source,
            execution_id,
            ArtifactCaptureExtent.OMITTED,
            failure_reason,
        )
        return ArtifactCapture(
            source_state=source_state,
            extent=ArtifactCaptureExtent.OMITTED,
            source_byte_count=byte_count,
            blob=None,
            reason=failure_reason,
        )
    extent = (
        ArtifactCaptureExtent.COMPLETE
        if source_state is ArtifactSourceCaptureState.COMPLETE
        else ArtifactCaptureExtent.PARTIAL
    )
    final_reason = (
        None if extent is ArtifactCaptureExtent.COMPLETE else _source_reason(source_state)
    )
    if final_reason is not None:
        _coverage(issues, source, execution_id, extent, final_reason)
    return ArtifactCapture(
        source_state=source_state,
        extent=extent,
        source_byte_count=byte_count,
        blob=blob,
        reason=final_reason,
        redaction_count=redactions,
    )


def _sanitize_url(
    url: AbsoluteHttpUrl,
    boundary: SanitizationBoundary,
    redaction: RedactionPolicy,
) -> str:
    host = url.authority.host.value
    if type(url.authority.host) is Ipv6Host:
        host = f"[{host}]"
    authority = host if url.authority.port is None else f"{host}:{url.authority.port.value}"
    segments = tuple(_sanitize_path_segment(item, boundary) for item in url.path.segments)
    path = "/" + "/".join(segments)
    if url.path.trailing_slash:
        path += "/"
    query: list[str] = []
    if not _source_has_custom_redaction(redaction, SelectorSource.REQUEST_QUERY):
        for parameter in url.query.parameters:
            name = boundary.sanitize_text(parameter.name.value).value
            encoded_name = quote(name, safe="-._~", encoding="utf-8", errors="strict")
            if type(parameter.value) is BareQueryValue:
                query.append(encoded_name)
                continue
            mandatory = parameter.name.value.casefold() in MANDATORY_SENSITIVE_QUERY_NAMES
            value = boundary.sanitize_text(
                cast("str | SensitiveValue[object]", parameter.value),
                mandatory=mandatory,
            ).value
            encoded = quote(value, safe="-._~", encoding="utf-8", errors="strict")
            query.append(f"{encoded_name}={encoded}")
    suffix = "" if not query else "?" + "&".join(query)
    return f"{url.scheme.value}://{authority}{path}{suffix}"


def _sanitize_path_segment(value: object, boundary: SanitizationBoundary) -> str:
    if is_sensitive_value(value):
        sanitized = boundary.sanitize_text(value).value
        return quote(sanitized, safe="-._~", encoding="utf-8", errors="strict")
    if type(value) is DecodedPathSegment:
        sanitized = boundary.sanitize_text(value.value).value
        return quote(sanitized, safe="-._~", encoding="utf-8", errors="strict")
    encoded = cast(EncodedPathSegment, value)
    return boundary.sanitize_text(encoded.value).value


def _request_headers(
    request: FinalizedWireRequest,
    boundary: SanitizationBoundary,
    redaction: RedactionPolicy,
) -> tuple[ArtifactField, ...]:
    if _source_has_custom_redaction(redaction, SelectorSource.REQUEST_HEADERS):
        return ()
    return tuple(
        _artifact_field(
            field.name.value,
            field.value,
            boundary,
            mandatory=field.name.value in MANDATORY_SENSITIVE_HEADER_NAMES,
        )
        for field in request.headers.fields
    )


def _request_cookies(
    request: FinalizedWireRequest,
    boundary: SanitizationBoundary,
    redaction: RedactionPolicy,
) -> tuple[ArtifactField, ...]:
    if _source_has_custom_redaction(redaction, SelectorSource.REQUEST_COOKIES):
        return ()
    return tuple(
        _artifact_field(cookie.name.value, cookie.value, boundary, mandatory=True)
        for cookie in request.cookies.cookies
    )


def _response_fields(
    fields: ResponseFields,
    boundary: SanitizationBoundary,
    redaction: RedactionPolicy,
    source: SelectorSource,
) -> tuple[ArtifactField, ...]:
    if _source_has_custom_redaction(redaction, source):
        return ()
    return tuple(
        _artifact_field(
            field.name.value,
            field.value,
            boundary,
            mandatory=field.name.value in MANDATORY_SENSITIVE_HEADER_NAMES | {"set-cookie"},
        )
        for field in fields.fields
    )


def _artifact_field(
    name: str,
    value: object,
    boundary: SanitizationBoundary,
    *,
    mandatory: bool,
) -> ArtifactField:
    safe_name = boundary.sanitize_text(name)
    safe_value = boundary.sanitize_text(
        cast("str | SensitiveValue[object]", value),
        mandatory=mandatory,
    )
    return ArtifactField(
        name=safe_name.value,
        value=safe_value.value,
        redacted=(safe_name.redaction_count + safe_value.redaction_count > 0),
    )


def _source_has_custom_redaction(
    redaction: RedactionPolicy,
    source: SelectorSource,
) -> bool:
    return any(rule.selector.source is source for rule in redaction.rules)


def _source_state(capture: BodyCaptureState) -> ArtifactSourceCaptureState:
    mapping = {
        CompleteCapture: ArtifactSourceCaptureState.COMPLETE,
        RemoteTruncatedCapture: ArtifactSourceCaptureState.REMOTE_TRUNCATED,
        BudgetExceededCapture: ArtifactSourceCaptureState.BUDGET_EXCEEDED,
        StorageFailedCapture: ArtifactSourceCaptureState.STORAGE_FAILED,
        CancelledCapture: ArtifactSourceCaptureState.CANCELLED,
        ContentDecodingFailedCapture: ArtifactSourceCaptureState.CONTENT_DECODING_FAILED,
        PolicyOmittedCapture: ArtifactSourceCaptureState.POLICY_OMITTED,
        ExplicitlyTruncatedCapture: ArtifactSourceCaptureState.EXPLICITLY_TRUNCATED,
    }
    if type(capture) is BodyReadFailedCapture:
        return (
            ArtifactSourceCaptureState.IDLE_TIMEOUT
            if capture.kind is BodyReadFailureKind.IDLE_TIMEOUT
            else ArtifactSourceCaptureState.READ_FAILED
        )
    return mapping[type(capture)]


def _source_reason(state: ArtifactSourceCaptureState) -> ArtifactOmissionReason:
    if state is ArtifactSourceCaptureState.BUDGET_EXCEEDED:
        return ArtifactOmissionReason.BUDGET_EXCEEDED
    if state is ArtifactSourceCaptureState.STORAGE_FAILED:
        return ArtifactOmissionReason.STORAGE_FAILED
    if state is ArtifactSourceCaptureState.CANCELLED:
        return ArtifactOmissionReason.CANCELLED
    if state is ArtifactSourceCaptureState.POLICY_OMITTED:
        return ArtifactOmissionReason.POLICY
    if state is ArtifactSourceCaptureState.CONTENT_DECODING_FAILED:
        return ArtifactOmissionReason.DECODE_FAILED
    return ArtifactOmissionReason.SOURCE_INCOMPLETE


def _coverage(
    issues: list[ArtifactCoverageIssue],
    source: str,
    execution_id: str,
    extent: ArtifactCaptureExtent,
    reason: ArtifactOmissionReason,
) -> None:
    issues.append(
        ArtifactCoverageIssue(
            source=source,
            extent=extent,
            reason=reason,
            execution_id=execution_id,
        )
    )


def _transport_failure(failure: TransportFailure | None) -> ArtifactFailure | None:
    if failure is None:
        return None
    return ArtifactFailure(
        stage=failure.phase.value,
        code=failure.code.value,
        retryable=failure.retryable,
    )


def _pre_send_failure(outcome: HttpPreSendOutcome) -> ArtifactFailure:
    if type(outcome) is HttpResolutionFailure:
        failure = outcome.failure
        return ArtifactFailure(
            stage=failure.phase.value,
            code=failure.code.value,
            retryable=failure.retryable,
        )
    return ArtifactFailure(
        stage="authorization",
        code=outcome.code.value,
        retryable=False,
    )


def _pre_send_request(outcome: HttpPreSendOutcome) -> FinalizedWireRequest:
    if type(outcome) is HttpResolutionFailure:
        return outcome.request.request
    return outcome.request


def _execution_timings(timings: EnvironmentExecutionTimings) -> tuple[ArtifactTiming, ...]:
    return _timings(("elapsed", timings.elapsed), ("queue", timings.queue))


def _attempt_timings(timings: HttpAttemptTimings) -> tuple[ArtifactTiming, ...]:
    return _timings(("elapsed", timings.elapsed))


def _exchange_timings(timings: HttpExchangeTimings) -> tuple[ArtifactTiming, ...]:
    return _timings(
        ("body-read", timings.body_read),
        ("connect", timings.connect),
        ("connection-acquisition", timings.connection_acquisition),
        ("dns", timings.dns),
        ("elapsed", timings.elapsed),
        ("first-body-byte", timings.first_body_byte),
        ("proxy-tunnel", timings.proxy_tunnel),
        ("request-write", timings.request_write),
        ("response-headers", timings.response_headers),
        ("server-wait", timings.server_wait),
        ("tls", timings.tls),
    )


def _timings(*values: tuple[str, TimingMetric]) -> tuple[ArtifactTiming, ...]:
    return tuple(_timing(name, value) for name, value in sorted(values))


def _timing(name: str, value: TimingMetric) -> ArtifactTiming:
    if type(value) is Measured:
        return ArtifactTiming(name=name, nanoseconds=value.duration.nanoseconds, reason=None)
    if type(value) is NotPerformed:
        return ArtifactTiming(name=name, nanoseconds=None, reason=value.reason.value)
    return ArtifactTiming(name=name, nanoseconds=None, reason=value.reason.value)
