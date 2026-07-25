"""Exhaustive branch tests for live HTTP artifact capture and sanitization."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Never
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.artifacts.memory import InMemorySanitizedBlobStore
from comparo.core.artifacts import capture
from comparo.core.artifacts.capture import ArtifactCaptureBatch
from comparo.core.artifacts.capture import ArtifactCaptureUnavailableError
from comparo.core.artifacts.capture import ArtifactCapturingDriver
from comparo.core.artifacts.capture import CapturedExecutionHttp
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import ArtifactSourceCaptureState
from comparo.core.artifacts.http import ArtifactAttempt
from comparo.core.artifacts.http import ArtifactExchange
from comparo.core.artifacts.http import ArtifactTiming
from comparo.core.artifacts.model import ArtifactCoverageIssue
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionAction
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.policy import RedactionRule
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.artifacts.ports import SanitizedBlobWriter
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import ExplicitlyTruncatedCapture
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import PolicyOmittedCapture
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.responses import InformationalResponse
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.selector_semantics import derive_selector_capabilities
from comparo.core.domain.selectors.sources import SelectorSource
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.runtime.models import ExecutionOutcome
from comparo.core.runtime.models import ExecutionWork
from tests.core.artifacts.test_capture_compiler import _EmptyBodies
from tests.core.artifacts.test_capture_compiler import _EmptySnapshotProvider
from tests.core.artifacts.test_capture_compiler import _FailingBodies
from tests.core.artifacts.test_capture_compiler import _policy
from tests.core.artifacts.test_capture_compiler import _prepared
from tests.core.artifacts.test_compiler_coverage import _state
from tests.core.domain.gates.test_normalization import _fingerprint as _gate_fingerprint
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import body_handle
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import measured
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_denial
from tests.core.domain.http.observation_factories import resolution_failure
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.test_exchange_observations import _resource_budget_body

_ORIGIN = TaintOriginId(value="secret/artifact/test")


def _secret[ValueT](value: ValueT) -> SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=(_ORIGIN,))


def _boundary(*values: object) -> SanitizationBoundary:
    boundary = SanitizationBoundary()
    for value in values:
        boundary.register(_secret(value))
    boundary.seal()
    return boundary


def _store(policy: ArtifactPolicy | None = None) -> InMemorySanitizedBlobStore:
    return InMemorySanitizedBlobStore((policy or _policy()).limits)


def _capture_policy(**changes: bool) -> ArtifactPolicy:
    policy = _policy()
    capture_policy = msgspec.structs.replace(policy.capture, **changes)
    return msgspec.structs.replace(policy, capture=capture_policy)


def _selector(source: SelectorSource) -> CompiledSelector:
    path = NoPath()
    capabilities = derive_selector_capabilities(source, path)
    value_kind = {
        SelectorSource.REQUEST_METHOD: ValueKind.STRING,
        SelectorSource.REQUEST_URL: ValueKind.STRING,
        SelectorSource.RESPONSE_PROTOCOL: ValueKind.STRING,
        SelectorSource.RESPONSE_STATUS: ValueKind.INTEGER,
    }.get(source, ValueKind.HTTP_FIELD_VALUE)
    return CompiledSelector(
        fingerprint=_gate_fingerprint(),
        source=source,
        path=path,
        capabilities=capabilities,
        output=ValueContract(
            kinds=(value_kind,),
            cardinality=capabilities.cardinality,
        ),
    )


def _redaction(*sources: SelectorSource) -> RedactionPolicy:
    return RedactionPolicy(
        rules=tuple(
            RedactionRule(
                rule_id=f"rule-{index}",
                selector=_selector(source),
                action=RedactionAction.OMIT_SOURCE,
            )
            for index, source in enumerate(sources)
        )
    )


class _NoopDriver:
    def __init__(self) -> None:
        self.close_count = 0

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        del work
        raise AssertionError("no execution expected")

    async def close(self) -> None:
        self.close_count += 1


class _CancellingBodies:
    def read(self, source: RepeatableByteSource | BodyHandle) -> AsyncIterator[bytes]:
        del source
        return _CancellingIterator()


class _CancellingIterator:
    def __aiter__(self) -> _CancellingIterator:
        return self

    async def __anext__(self) -> bytes:
        raise asyncio.CancelledError


class _FailingBeginBlobs:
    async def begin(self) -> SanitizedBlobWriter:
        raise OSError


class _CancellingBeginBlobs:
    async def begin(self) -> Never:
        raise asyncio.CancelledError


def _driver_fixture(
    redaction: RedactionPolicy | None = None,
) -> tuple[ArtifactCapturingDriver, _NoopDriver]:
    inner = _NoopDriver()
    prepared = _prepared()
    policy = _policy()
    driver = ArtifactCapturingDriver(
        driver=inner,
        snapshots=_EmptySnapshotProvider(prepared.plan.run_id),
        bodies=_EmptyBodies(),
        blobs=_store(policy),
        policy=policy,
        redaction=redaction or RedactionPolicy(),
    )
    return driver, inner


def test_capture_contracts_reject_invalid_shapes_and_duplicates() -> None:
    timing = ArtifactTiming(name="elapsed", nanoseconds=1, reason=None)
    attempt = ArtifactAttempt(
        attempt_id="attempt",
        exchanges=(),
        terminal_pre_send_omission=ArtifactOmissionReason.POLICY,
        failure=ArtifactFailure(stage="authorization", code="http.denied"),
    )
    valid = CapturedExecutionHttp(
        execution_id="execution",
        timings=(timing,),
        attempts=(attempt,),
    )
    with pytest.raises(ValueError, match="non-empty text"):
        CapturedExecutionHttp(execution_id="", timings=(), attempts=())
    with pytest.raises(TypeError, match="timings"):
        CapturedExecutionHttp(
            execution_id="execution",
            timings=cast("tuple[ArtifactTiming, ...]", (object(),)),
            attempts=(),
        )
    with pytest.raises(TypeError, match="attempts"):
        CapturedExecutionHttp(
            execution_id="execution",
            timings=(),
            attempts=cast("tuple[ArtifactAttempt, ...]", (object(),)),
        )
    with pytest.raises(TypeError, match="executions"):
        ArtifactCaptureBatch(
            executions=cast("tuple[CapturedExecutionHttp, ...]", (object(),)),
            coverage_issues=(),
        )
    with pytest.raises(ValueError, match="must be unique"):
        ArtifactCaptureBatch(executions=(valid, valid), coverage_issues=())
    with pytest.raises(TypeError, match="coverage"):
        ArtifactCaptureBatch(
            executions=(),
            coverage_issues=cast("tuple[ArtifactCoverageIssue, ...]", (object(),)),
        )
    with pytest.raises(TypeError, match="failure"):
        ArtifactCaptureBatch(
            executions=(),
            coverage_issues=(),
            failure=cast("ArtifactFailure", object()),
        )


def test_driver_validates_policy_and_exposes_lifecycle_fail_closed() -> None:
    prepared = _prepared()
    inner = _NoopDriver()
    with pytest.raises(TypeError, match="typed policies"):
        ArtifactCapturingDriver(
            driver=inner,
            snapshots=_EmptySnapshotProvider(prepared.plan.run_id),
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=cast("ArtifactPolicy", object()),
            redaction=RedactionPolicy(),
        )

    driver, inner = _driver_fixture(_redaction(SelectorSource.REQUEST_HEADERS))
    with pytest.raises(ArtifactCaptureUnavailableError, match="batch"):
        _ = driver.batch
    with pytest.raises(ArtifactCaptureUnavailableError, match="boundary"):
        _ = driver.boundary
    assert driver.has_custom_redaction
    asyncio.run(driver.close())
    asyncio.run(driver.close())
    assert inner.close_count == 1
    assert not driver.batch.executions
    assert type(driver.boundary) is SanitizationBoundary
    driver.release()
    driver.release()
    with pytest.raises(ArtifactCaptureUnavailableError, match="boundary"):
        _ = driver.boundary


def test_execute_after_runtime_close_is_rejected() -> None:
    _prepared_run, result, driver = asyncio.run(_state())
    try:
        with pytest.raises(ArtifactCaptureUnavailableError, match="closed"):
            asyncio.run(driver.execute(result.executions[0].work))
    finally:
        driver.release()


@pytest.mark.parametrize(
    ("failure_kind", "with_boundary", "expected_code"),
    [
        ("cancelled", False, "cancelled"),
        ("cancelled", True, "cancelled"),
        ("rejected", False, "unsafe-source"),
        ("rejected", True, "unsafe-source"),
        ("failed", False, "capture-failed"),
        ("failed", True, "capture-failed"),
    ],
)
def test_close_converts_capture_failures_to_value_free_batches(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    with_boundary: bool,
    expected_code: str,
) -> None:
    driver, inner = _driver_fixture()

    async def fail(current: ArtifactCapturingDriver) -> None:
        if with_boundary:
            current._boundary = _boundary()
        if failure_kind == "cancelled":
            raise asyncio.CancelledError
        if failure_kind == "rejected":
            raise capture._RejectArtifactCaptureError
        raise RuntimeError

    monkeypatch.setattr(ArtifactCapturingDriver, "_capture", fail)
    if failure_kind == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(driver.close())
    else:
        asyncio.run(driver.close())
    assert driver.batch.failure is not None
    assert driver.batch.failure.code == expected_code
    assert inner.close_count == 1
    if failure_kind == "cancelled" and with_boundary:
        with pytest.raises(ArtifactCaptureUnavailableError, match="boundary"):
            _ = driver.boundary


def _source() -> RepeatableByteSource:
    return RepeatableByteSource(
        source_id=BodyId(value=UUID(int=50)),
        byte_count=ByteCount(value=0),
        digest=_secret(digest("d")),
        provenance=provenance(),
    )


def _complex_request() -> tuple[AbsoluteHttpUrl, FinalizedWireRequest]:
    source = _source()
    url = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(
            host=Ipv6Host(value="2001:db8::1"),
            port=HttpPort(value=8443),
        ),
        path=UrlPath(
            segments=(
                _secret(DecodedPathSegment(value="secret path")),
                DecodedPathSegment(value="plain path"),
                EncodedPathSegment(value="already%20encoded"),
            ),
            trailing_slash=True,
        ),
        query=QueryParameters(
            parameters=(
                QueryParameter(
                    name=QueryName(value="flag"),
                    value=BareQueryValue(),
                    source=QuerySource.AUTHORED,
                    provenance=provenance(),
                ),
                QueryParameter(
                    name=QueryName(value="page"),
                    value=QueryText(value="two words"),
                    source=QuerySource.AUTHORED,
                    provenance=provenance(),
                ),
                QueryParameter(
                    name=QueryName(value="token"),
                    value=_secret(QueryText(value="credential")),
                    source=QuerySource.AUTHENTICATION,
                    provenance=provenance(),
                ),
            )
        ),
    )
    effective = msgspec.structs.replace(
        request(),
        url=url,
        headers=RequestHeaders(
            fields=(
                HeaderField(
                    name=HeaderName(value="x-public"),
                    value=HeaderText(value="public"),
                    source=HeaderSource.AUTHORED,
                    provenance=provenance(),
                ),
                HeaderField(
                    name=HeaderName(value="x-secret"),
                    value=_secret(HeaderText(value="header-secret")),
                    source=HeaderSource.AUTHORED,
                    provenance=provenance(),
                ),
            )
        ),
        cookies=RequestCookies(
            cookies=(
                RequestCookie(
                    name=CookieName(value="session"),
                    value=_secret(CookieText(value="cookie-secret")),
                    source=CookieSource.SESSION,
                    provenance=provenance(),
                ),
            )
        ),
        body=BytesBody(value=source),
    )
    return url, finalized_wire_request(effective, body=MaterializedBody(source=source))


def test_request_registration_url_and_fields_cover_all_typed_values() -> None:
    url, wire = _complex_request()
    boundary = SanitizationBoundary()
    capture._register_request_values(boundary, wire)
    boundary.seal()

    rendered = capture._sanitize_url(url, boundary, RedactionPolicy())
    headers = capture._request_headers(wire, boundary, RedactionPolicy())
    cookies = capture._request_cookies(wire, boundary, RedactionPolicy())

    assert rendered.startswith("https://[2001:db8::1]:8443/")
    assert rendered.endswith("/?flag&page=two%20words&token=%3Credacted%3E")
    assert "%3Credacted%3E/plain%20path/already%20encoded/" in rendered
    assert headers[0].value == "public"
    assert headers[1].value == "<redacted>"
    assert cookies[0].redacted
    assert (
        capture._request_headers(wire, boundary, _redaction(SelectorSource.REQUEST_HEADERS)) == ()
    )
    assert (
        capture._request_cookies(wire, boundary, _redaction(SelectorSource.REQUEST_COOKIES)) == ()
    )
    assert "?" not in capture._sanitize_url(url, boundary, _redaction(SelectorSource.REQUEST_QUERY))


def test_request_capture_resanitizes_the_complete_method_and_url_authority() -> None:
    effective = msgspec.structs.replace(
        request(),
        method=HttpMethod(value="SECRET"),
        url=msgspec.structs.replace(
            request().url,
            authority=HttpAuthority(host=DnsHost(value="secret-host.example")),
        ),
    )
    boundary = _boundary("SECRET", "secret-host")
    issues: list[ArtifactCoverageIssue] = []
    captured = asyncio.run(
        capture._capture_request(
            finalized_wire_request(effective),
            source="request",
            execution_id="execution",
            boundary=boundary,
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=_policy(),
            redaction=RedactionPolicy(),
            issues=issues,
        )
    )

    assert captured.method == "<redacted>"
    assert "SECRET" not in captured.method
    assert "secret-host" not in captured.url
    assert "<redacted>" in captured.url


def test_response_registration_and_field_redaction_cover_all_sections() -> None:
    public = ResponseField(
        name=HeaderName(value="x-public"),
        value=HeaderText(value="public"),
    )
    secret = ResponseField(
        name=HeaderName(value="x-secret"),
        value=_secret(HeaderText(value="response-secret")),
    )
    observed = msgspec.structs.replace(
        response(),
        informational=(
            InformationalResponse(
                protocol=HttpProtocol.HTTP_2,
                status=HttpStatus(value=103),
                headers=ResponseFields(fields=(secret,)),
            ),
        ),
        headers=ResponseFields(fields=(public, secret)),
        trailers=ResponseFields(fields=(secret,)),
    )
    boundary = SanitizationBoundary()
    capture._register_response_values(boundary, observed)
    capture._register_response_values(
        boundary,
        msgspec.structs.replace(
            observed,
            body=ResponseEntityBody(
                capture=StorageFailedCapture(),
                handle=None,
                encoded_byte_count=None,
            ),
        ),
    )
    capture._register_if_sensitive(boundary, "public")
    boundary.seal()

    headers = capture._response_fields(
        observed.headers,
        boundary,
        RedactionPolicy(),
        SelectorSource.RESPONSE_HEADERS,
    )
    assert headers[0].value == "public"
    assert headers[1].value == "<redacted>"
    assert (
        capture._response_fields(
            observed.headers,
            boundary,
            _redaction(SelectorSource.RESPONSE_HEADERS),
            SelectorSource.RESPONSE_HEADERS,
        )
        == ()
    )


def _observation_with_exchange(*, with_response: bool) -> EnvironmentExecutionObservation:
    effective = request()
    observed_exchange = exchange(
        exchange_number=1,
        effective_request=effective,
        observed_response=response() if with_response else None,
        failure=None if with_response else failure(FailurePhase.CONNECT),
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=1)),
        environment=effective.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=1)),
                retry=None,
                exchanges=(observed_exchange,),
                timings=attempt_timings(),
            ),
        ),
        timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
    )


def _observation_with_pre_send() -> EnvironmentExecutionObservation:
    effective = request()
    request_occurrence = occurrence(execution=2, attempt=2, exchange=2)
    pre_send = resolution_failure(
        effective,
        request_occurrence=request_occurrence,
    )
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionObservation(
        execution_id=EnvironmentExecutionId(value=UUID(int=2)),
        environment=effective.environment,
        step_owner=None,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=UUID(int=2)),
                retry=None,
                exchanges=(),
                timings=attempt_timings(),
                pre_send=pre_send,
            ),
        ),
        timings=EnvironmentExecutionTimings(queue=metric, elapsed=metric),
    )


def test_observation_registration_covers_responses_failures_and_pre_send_requests() -> None:
    for observation in (
        _observation_with_exchange(with_response=True),
        _observation_with_exchange(with_response=False),
        _observation_with_pre_send(),
    ):
        boundary = SanitizationBoundary()
        capture._register_observation_values(boundary, observation)
        boundary.seal()


async def _capture_exchange_with(
    *,
    policy: ArtifactPolicy,
    redaction: RedactionPolicy,
    with_response: bool,
) -> tuple[ArtifactExchange, list[ArtifactCoverageIssue]]:
    effective = request()
    observed = exchange(
        exchange_number=1,
        effective_request=effective,
        observed_response=response() if with_response else None,
        failure=None if with_response else failure(FailurePhase.CONNECT),
    )
    issues: list[ArtifactCoverageIssue] = []
    result = await capture._capture_exchange(
        observed,
        execution_id="execution",
        boundary=_boundary(),
        bodies=_EmptyBodies(),
        blobs=_store(policy),
        policy=policy,
        redaction=redaction,
        issues=issues,
    )
    return result, issues


def test_exchange_capture_records_policy_and_whole_scalar_omissions() -> None:
    omitted, issues = asyncio.run(
        _capture_exchange_with(
            policy=_capture_policy(requests=False, responses=False),
            redaction=RedactionPolicy(),
            with_response=True,
        )
    )
    assert omitted.request_omission is ArtifactOmissionReason.POLICY
    assert omitted.response_omission is ArtifactOmissionReason.POLICY
    assert len(issues) == 2

    custom, issues = asyncio.run(
        _capture_exchange_with(
            policy=_policy(),
            redaction=_redaction(
                SelectorSource.REQUEST_METHOD,
                SelectorSource.RESPONSE_STATUS,
            ),
            with_response=True,
        )
    )
    assert custom.request is None
    assert custom.response is None
    assert len(issues) == 2

    failed, _issues = asyncio.run(
        _capture_exchange_with(
            policy=_policy(),
            redaction=RedactionPolicy(),
            with_response=False,
        )
    )
    assert failed.response is None
    assert failed.failure is not None


async def _capture_attempt_with(
    *,
    denied: bool,
    policy: ArtifactPolicy,
    redaction: RedactionPolicy,
) -> tuple[ArtifactAttempt, list[ArtifactCoverageIssue]]:
    effective = request()
    request_occurrence = occurrence(execution=2, attempt=2, exchange=2)
    pre_send = (
        resolution_denial(effective, request_occurrence=request_occurrence)
        if denied
        else resolution_failure(effective, request_occurrence=request_occurrence)
    )
    attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=None,
        exchanges=(),
        timings=attempt_timings(),
        pre_send=pre_send,
    )
    issues: list[ArtifactCoverageIssue] = []
    result = await capture._capture_attempt(
        attempt,
        execution_id="execution",
        boundary=_boundary(),
        bodies=_EmptyBodies(),
        blobs=_store(policy),
        policy=policy,
        redaction=redaction,
        issues=issues,
    )
    return result, issues


def test_pre_send_attempt_capture_covers_request_omission_and_failure_variants() -> None:
    captured, _issues = asyncio.run(
        _capture_attempt_with(
            denied=False,
            policy=_policy(),
            redaction=RedactionPolicy(),
        )
    )
    assert captured.terminal_pre_send_request is not None
    assert captured.failure is not None
    assert captured.failure.stage == "dns"

    omitted, issues = asyncio.run(
        _capture_attempt_with(
            denied=True,
            policy=_capture_policy(requests=False),
            redaction=RedactionPolicy(),
        )
    )
    assert omitted.terminal_pre_send_omission is ArtifactOmissionReason.POLICY
    assert omitted.failure is not None
    assert omitted.failure.stage == "authorization"
    assert len(issues) == 1

    whole, _issues = asyncio.run(
        _capture_attempt_with(
            denied=True,
            policy=_policy(),
            redaction=_redaction(SelectorSource.REQUEST_URL),
        )
    )
    assert whole.terminal_pre_send_request is None


def test_pre_send_helpers_retain_resolution_and_authorization_shapes() -> None:
    effective = request()
    request_occurrence = occurrence(execution=3, attempt=3, exchange=3)
    resolution = resolution_failure(effective, request_occurrence=request_occurrence)
    denial = resolution_denial(effective, request_occurrence=request_occurrence)
    assert capture._pre_send_request(resolution) == resolution.request.request
    assert capture._pre_send_request(denial) == denial.request
    assert capture._pre_send_failure(resolution).stage == "dns"
    assert capture._pre_send_failure(denial).stage == "authorization"


def test_custom_source_omissions_are_explicit_and_required_scalars_fail_closed() -> None:
    redaction = _redaction(
        SelectorSource.REQUEST_HEADERS,
        SelectorSource.RESPONSE_TRAILERS,
    )
    issues: list[ArtifactCoverageIssue] = []
    capture._record_custom_source_omissions(
        issues,
        source="exchange.request",
        execution_id="execution",
        redaction=redaction,
        sources=(
            (SelectorSource.REQUEST_QUERY, "query"),
            (SelectorSource.REQUEST_HEADERS, "headers"),
        ),
    )
    assert tuple(item.source for item in issues) == ("exchange.request.headers",)
    assert not capture._requires_whole_request_omission(redaction)
    assert not capture._requires_whole_response_omission(redaction)
    assert capture._requires_whole_request_omission(_redaction(SelectorSource.REQUEST_METHOD))
    assert capture._requires_whole_response_omission(_redaction(SelectorSource.RESPONSE_PROTOCOL))


def test_materialized_request_capture_sanitizes_body_and_records_custom_coverage() -> None:
    _url, wire = _complex_request()
    boundary = SanitizationBoundary()
    capture._register_request_values(boundary, wire)
    boundary.seal()
    issues: list[ArtifactCoverageIssue] = []
    artifact = asyncio.run(
        capture._capture_request(
            wire,
            source="exchange.request",
            execution_id="execution",
            boundary=boundary,
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=_policy(),
            redaction=_redaction(
                SelectorSource.REQUEST_QUERY,
                SelectorSource.REQUEST_HEADERS,
                SelectorSource.REQUEST_COOKIES,
            ),
            issues=issues,
        )
    )

    assert artifact.body is not None
    assert artifact.body.extent is ArtifactCaptureExtent.COMPLETE
    assert artifact.headers == ()
    assert artifact.cookies == ()
    assert tuple(item.source for item in issues) == (
        "exchange.request.query",
        "exchange.request.headers",
        "exchange.request.cookies",
    )


def test_response_capture_supports_absent_entities_and_custom_field_omissions() -> None:
    no_body = response(
        status=204,
        body=NoResponseBody(reason=NoBodyReason.STATUS_204),
    )
    issues: list[ArtifactCoverageIssue] = []
    artifact = asyncio.run(
        capture._capture_response(
            no_body,
            source="exchange.response",
            execution_id="execution",
            boundary=_boundary(),
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=_policy(),
            redaction=_redaction(
                SelectorSource.RESPONSE_HEADERS,
                SelectorSource.RESPONSE_TRAILERS,
            ),
            issues=issues,
        )
    )

    assert artifact.body is None
    assert artifact.headers == ()
    assert artifact.trailers == ()
    assert tuple(item.source for item in issues) == (
        "exchange.response.headers",
        "exchange.response.trailers",
    )


def test_response_body_without_a_handle_is_omitted_or_rejects_by_policy() -> None:
    body = ResponseEntityBody(
        capture=StorageFailedCapture(),
        handle=None,
        encoded_byte_count=None,
    )
    issues: list[ArtifactCoverageIssue] = []
    omitted = asyncio.run(
        capture._capture_response_body(
            body,
            source="exchange.response.body",
            execution_id="execution",
            boundary=_boundary(),
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=_policy(),
            custom_redaction=False,
            issues=issues,
        )
    )
    assert omitted.extent is ArtifactCaptureExtent.OMITTED
    assert omitted.reason is ArtifactOmissionReason.STORAGE_FAILED
    assert len(issues) == 1

    reject = msgspec.structs.replace(
        _policy(),
        unsafe_source=UnsafeSourceBehavior.REJECT_ARTIFACT,
    )
    with pytest.raises(capture._RejectArtifactCaptureError):
        asyncio.run(
            capture._capture_response_body(
                body,
                source="exchange.response.body",
                execution_id="execution",
                boundary=_boundary(),
                bodies=_EmptyBodies(),
                blobs=_store(reject),
                policy=reject,
                custom_redaction=False,
                issues=[],
            )
        )


def test_body_capture_covers_policy_partial_storage_rejection_and_cancellation() -> None:
    descriptor = body_handle(size=0)
    policy = _policy()
    omitted_issues: list[ArtifactCoverageIssue] = []
    omitted = asyncio.run(
        capture._capture_body(
            descriptor,
            source_state=ArtifactSourceCaptureState.COMPLETE,
            source="body",
            execution_id="execution",
            boundary=_boundary(),
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=_capture_policy(bodies=False),
            custom_redaction=False,
            issues=omitted_issues,
        )
    )
    assert omitted.reason is ArtifactOmissionReason.POLICY
    custom = asyncio.run(
        capture._capture_body(
            descriptor,
            source_state=ArtifactSourceCaptureState.COMPLETE,
            source="body",
            execution_id="execution",
            boundary=_boundary(),
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=policy,
            custom_redaction=True,
            issues=[],
        )
    )
    assert custom.reason is ArtifactOmissionReason.POLICY

    partial_issues: list[ArtifactCoverageIssue] = []
    partial = asyncio.run(
        capture._capture_body(
            descriptor,
            source_state=ArtifactSourceCaptureState.REMOTE_TRUNCATED,
            source="body",
            execution_id="execution",
            boundary=_boundary(),
            bodies=_EmptyBodies(),
            blobs=_store(),
            policy=policy,
            custom_redaction=False,
            issues=partial_issues,
        )
    )
    assert partial.extent is ArtifactCaptureExtent.PARTIAL
    assert partial.reason is ArtifactOmissionReason.SOURCE_INCOMPLETE
    assert len(partial_issues) == 1

    storage_issues: list[ArtifactCoverageIssue] = []
    storage_failed = asyncio.run(
        capture._capture_body(
            descriptor,
            source_state=ArtifactSourceCaptureState.COMPLETE,
            source="body",
            execution_id="execution",
            boundary=_boundary(),
            bodies=_EmptyBodies(),
            blobs=_FailingBeginBlobs(),
            policy=policy,
            custom_redaction=False,
            issues=storage_issues,
        )
    )
    assert storage_failed.reason is ArtifactOmissionReason.STORAGE_FAILED

    writer_failure = asyncio.run(
        capture._capture_body(
            descriptor,
            source_state=ArtifactSourceCaptureState.COMPLETE,
            source="body",
            execution_id="execution",
            boundary=_boundary(),
            bodies=_FailingBodies(),
            blobs=_store(),
            policy=policy,
            custom_redaction=False,
            issues=[],
        )
    )
    assert writer_failure.reason is ArtifactOmissionReason.STORAGE_FAILED

    reject = msgspec.structs.replace(
        policy,
        unsafe_source=UnsafeSourceBehavior.REJECT_ARTIFACT,
    )
    with pytest.raises(capture._RejectArtifactCaptureError):
        asyncio.run(
            capture._capture_body(
                descriptor,
                source_state=ArtifactSourceCaptureState.COMPLETE,
                source="body",
                execution_id="execution",
                boundary=_boundary(),
                bodies=_EmptyBodies(),
                blobs=_FailingBeginBlobs(),
                policy=reject,
                custom_redaction=False,
                issues=[],
            )
        )

    for bodies, blobs in (
        (_EmptyBodies(), _CancellingBeginBlobs()),
        (_CancellingBodies(), _store()),
    ):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                capture._capture_body(
                    descriptor,
                    source_state=ArtifactSourceCaptureState.COMPLETE,
                    source="body",
                    execution_id="execution",
                    boundary=_boundary(),
                    bodies=bodies,
                    blobs=blobs,
                    policy=policy,
                    custom_redaction=False,
                    issues=[],
                )
            )


def test_body_state_reason_failure_and_timing_projections_are_exhaustive() -> None:
    budget_capture = _resource_budget_body().capture
    states = (
        (CompleteCapture(), ArtifactSourceCaptureState.COMPLETE),
        (RemoteTruncatedCapture(), ArtifactSourceCaptureState.REMOTE_TRUNCATED),
        (budget_capture, ArtifactSourceCaptureState.BUDGET_EXCEEDED),
        (StorageFailedCapture(), ArtifactSourceCaptureState.STORAGE_FAILED),
        (CancelledCapture(), ArtifactSourceCaptureState.CANCELLED),
        (
            ContentDecodingFailedCapture(),
            ArtifactSourceCaptureState.CONTENT_DECODING_FAILED,
        ),
        (PolicyOmittedCapture(), ArtifactSourceCaptureState.POLICY_OMITTED),
        (
            ExplicitlyTruncatedCapture(limit=ByteCount(value=1)),
            ArtifactSourceCaptureState.EXPLICITLY_TRUNCATED,
        ),
        (
            BodyReadFailedCapture(kind=BodyReadFailureKind.IDLE_TIMEOUT),
            ArtifactSourceCaptureState.IDLE_TIMEOUT,
        ),
        (
            BodyReadFailedCapture(kind=BodyReadFailureKind.READ_FAILURE),
            ArtifactSourceCaptureState.READ_FAILED,
        ),
    )
    assert tuple(capture._source_state(value) for value, _expected in states) == tuple(
        expected for _value, expected in states
    )
    reasons = {
        state: capture._source_reason(state)
        for state in (
            ArtifactSourceCaptureState.BUDGET_EXCEEDED,
            ArtifactSourceCaptureState.STORAGE_FAILED,
            ArtifactSourceCaptureState.CANCELLED,
            ArtifactSourceCaptureState.POLICY_OMITTED,
            ArtifactSourceCaptureState.CONTENT_DECODING_FAILED,
            ArtifactSourceCaptureState.READ_FAILED,
        )
    }
    assert reasons[ArtifactSourceCaptureState.BUDGET_EXCEEDED] is (
        ArtifactOmissionReason.BUDGET_EXCEEDED
    )
    assert reasons[ArtifactSourceCaptureState.STORAGE_FAILED] is (
        ArtifactOmissionReason.STORAGE_FAILED
    )
    assert reasons[ArtifactSourceCaptureState.CANCELLED] is ArtifactOmissionReason.CANCELLED
    assert reasons[ArtifactSourceCaptureState.POLICY_OMITTED] is ArtifactOmissionReason.POLICY
    assert reasons[ArtifactSourceCaptureState.CONTENT_DECODING_FAILED] is (
        ArtifactOmissionReason.DECODE_FAILED
    )
    assert reasons[ArtifactSourceCaptureState.READ_FAILED] is (
        ArtifactOmissionReason.SOURCE_INCOMPLETE
    )

    transport = capture._transport_failure(failure(FailurePhase.CONNECT))
    assert transport is not None
    assert transport.stage == "connect"
    assert capture._transport_failure(None) is None

    metric = measured(10)
    measured_artifact = capture._timing("elapsed", metric)
    not_performed = capture._timing("dns", NotPerformed(reason=NotPerformedReason.DNS_CACHE_HIT))
    unavailable = capture._timing(
        "dns",
        TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_DISABLED),
    )
    assert measured_artifact.nanoseconds == 10
    assert not_performed.reason == "dns-cache-hit"
    assert unavailable.reason == "instrumentation-disabled"
