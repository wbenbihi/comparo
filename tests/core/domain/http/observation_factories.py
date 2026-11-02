"""Shared factories for immutable HTTP observation-domain tests."""

from __future__ import annotations

from uuid import UUID

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.authorization import SecretEgressApproval
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ProxyTlsDisabled
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.pre_send import AuthorizedResolutionRequest
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.pre_send import HttpResolutionGrant
from comparo.core.domain.http.pre_send import HttpResolutionIntent
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.response_bodies import BodyCaptureExtent
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.http.response_bodies import BodyStorageToken
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CompleteCapture
from comparo.core.domain.http.response_bodies import MissingMediaType
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.secret_egress import http_secret_egress_origins
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import Measured
from comparo.core.domain.http.timings import NotPerformed
from comparo.core.domain.http.timings import NotPerformedReason
from comparo.core.domain.http.timings import TimingMetric
from comparo.core.domain.http.timings import TimingSource
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transport import AppliedTlsPolicy
from comparo.core.domain.http.transport import EndpointMetadata
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsMetadata
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.transport import TlsVersion
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import BodyBudgetScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import digest
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import materialization_fingerprint
from tests.core.domain.http.factories import policies
from tests.core.domain.http.factories import provenance


def request(
    *,
    method: str = "GET",
    attempts: int = 1,
    redirect_hops: int = 0,
    environment_id: str = "staging",
    scheme: HttpScheme = HttpScheme.HTTPS,
) -> EffectiveRequest:
    """Build one Environment-effective request."""
    request_policies = policies(attempts=attempts)
    request_policies = msgspec.structs.replace(
        request_policies,
        redirects=msgspec.structs.replace(
            request_policies.redirects,
            max_hops=Count(value=redirect_hops),
        ),
    )
    return EffectiveRequest(
        environment=definition(ResourceKind.ENVIRONMENT, environment_id),
        logical_request_fingerprint=fingerprint("a"),
        method=HttpMethod(value=method),
        url=AbsoluteHttpUrl(
            scheme=scheme,
            authority=HttpAuthority(host=DnsHost(value="api.example.test")),
        ),
        headers=RequestHeaders(),
        cookies=RequestCookies(),
        body=NoBody(),
        policies=request_policies,
        provenance=provenance(),
    )


def endpoint(*, scheme: HttpScheme = HttpScheme.HTTPS) -> EndpointMetadata:
    """Build one authorized and selected network endpoint."""
    address = Ipv4Host(value="192.0.2.10")
    port = HttpPort(value=443 if scheme is HttpScheme.HTTPS else 80)
    destination = NetworkDestination(
        scheme=scheme,
        host=DnsHost(value="api.example.test"),
        port=port,
        resolved_addresses=(address,),
        selected_address=address,
    )
    return EndpointMetadata(
        destination=destination,
        remote_address=address,
        remote_port=port,
        local_address=None,
        local_port=None,
        connection_id="connection-1",
        connection_reused=False,
    )


def tls(
    protocol: HttpProtocol = HttpProtocol.HTTP_2,
    *,
    applied_policy: AppliedTlsPolicy | None = None,
) -> TlsMetadata:
    """Build safe negotiated TLS metadata."""
    return TlsMetadata(
        version=TlsVersion.TLS_1_3,
        cipher_suite="TLS_AES_128_GCM_SHA256",
        alpn=protocol,
        peer_certificate_sha256=digest("c"),
        session_resumed=False,
        applied_policy=applied_policy or policies().tls,
    )


def body_handle(
    *,
    size: int = 0,
    media: MissingMediaType | None = None,
    extent: BodyCaptureExtent = BodyCaptureExtent.COMPLETE,
) -> BodyHandle:
    """Build a sealed storage-backed decoded entity descriptor."""
    locator = SensitiveValue(
        BodyStorageToken(value="private/body/path"),
        origins=(TaintOriginId(value="body-store"),),
    )
    sealed_digest = SensitiveValue(
        digest("d"),
        origins=(TaintOriginId(value="body-content"),),
    )
    return BodyHandle(
        body_id=BodyId(value=UUID(int=size + 1)),
        locator=locator,
        byte_count=ByteCount(value=size),
        digest=sealed_digest,
        media=media or MissingMediaType(),
        sensitivity=Sensitivity.SENSITIVE,
        extent=extent,
    )


def response(
    *,
    status: int = 200,
    body: NoResponseBody | ResponseEntityBody | None = None,
    scheme: HttpScheme = HttpScheme.HTTPS,
) -> ResponseObservation:
    """Build a terminal response with explicit body, endpoint, and TLS facts."""
    media = MissingMediaType()
    actual_body = body or ResponseEntityBody(
        capture=CompleteCapture(),
        handle=body_handle(media=media),
        encoded_byte_count=ByteCount(value=0),
    )
    return ResponseObservation(
        protocol=HttpProtocol.HTTP_2 if scheme is HttpScheme.HTTPS else HttpProtocol.HTTP_1_1,
        status=HttpStatus(value=status),
        reason=None,
        informational=(),
        headers=ResponseFields(),
        trailers=ResponseFields(),
        media=media,
        content_encodings=(),
        body=actual_body,
        endpoint=endpoint(scheme=scheme),
        tls=TlsLayers(connect=tls() if scheme is HttpScheme.HTTPS else TlsNotPerformed()),
    )


def exchange_timings(
    *,
    proxy: bool = False,
    clear_text: bool = False,
    reused: bool = False,
    failure_phase: FailurePhase | None = None,
) -> HttpExchangeTimings:
    """Build an explicit vector of unavailable or intentionally omitted intervals."""
    metric = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    reused_metric = NotPerformed(reason=NotPerformedReason.CONNECTION_REUSED)
    proxy_metric: TimingMetric = (
        metric if proxy else NotPerformed(reason=NotPerformedReason.NO_PROXY)
    )
    if proxy and reused:
        proxy_metric = reused_metric
    if failure_phase in {FailurePhase.POLICY, FailurePhase.DNS, FailurePhase.CONNECT}:
        proxy_metric = metric
    tls_metric: TimingMetric = metric
    if clear_text:
        tls_metric = NotPerformed(reason=NotPerformedReason.CLEAR_TEXT_CONNECTION)
    elif reused:
        tls_metric = reused_metric
    return HttpExchangeTimings(
        connection_acquisition=metric,
        dns=reused_metric if reused else metric,
        connect=reused_metric if reused else metric,
        proxy_tunnel=proxy_metric,
        tls=tls_metric,
        request_write=metric,
        response_headers=metric,
        server_wait=metric,
        first_body_byte=metric,
        body_read=metric,
        elapsed=metric,
    )


def measured(nanoseconds: int = 10) -> Measured:
    """Build one exact monotonic timing fact."""
    return Measured(
        duration=Duration(nanoseconds=nanoseconds),
        precision=Duration(nanoseconds=1),
        source=TimingSource(value="comparo/monotonic@1"),
    )


def uniform_exchange_timings(metric: TimingMetric) -> HttpExchangeTimings:
    """Build a timing vector containing one common metric."""
    return HttpExchangeTimings(
        connection_acquisition=metric,
        dns=metric,
        connect=metric,
        proxy_tunnel=metric,
        tls=metric,
        request_write=metric,
        response_headers=metric,
        server_wait=metric,
        first_body_byte=metric,
        body_read=metric,
        elapsed=metric,
    )


def attempt_timings() -> HttpAttemptTimings:
    """Build an explicit attempt elapsed timing fact."""
    return HttpAttemptTimings(
        elapsed=TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    )


def occurrence(
    *,
    execution: int = 1,
    attempt: int = 1,
    exchange: int = 1,
    environment: ResourceIdentity | None = None,
) -> HttpRequestOccurrence:
    """Build one deterministic outbound authorization occurrence."""
    run = RunScope(run_id=RunId(value=UUID(int=10)))
    return HttpRequestOccurrence(
        attempt=AttemptScope(
            execution=ExecutionScope(
                trial=TrialScope(
                    cell=CellScope(run=run, cell_id=CellId(value=UUID(int=11))),
                    trial_id=TrialId(value=UUID(int=12)),
                ),
                environment=EnvironmentScope(
                    run=run,
                    environment=environment
                    or definition(ResourceKind.ENVIRONMENT, "staging").identity,
                ),
                execution_id=EnvironmentExecutionId(value=UUID(int=execution)),
            ),
            attempt_id=AttemptId(value=UUID(int=attempt)),
        ),
        exchange_id=ExchangeId(value=UUID(int=exchange)),
    )


def authorized_request(
    effective_request: EffectiveRequest,
    route: DirectResolvedRoute | ProxyResolvedRoute | None = None,
    *,
    request_occurrence: HttpRequestOccurrence | None = None,
    body: FinalizedBody | None = None,
) -> AuthorizedExchangeRequest:
    """Bind one finalized request to a resolved route and authorization grant."""
    resolved_route = route or DirectResolvedRoute(
        destination=endpoint(scheme=effective_request.url.scheme).destination
    )
    wire_request = finalized_wire_request(effective_request, body=body)
    actual_occurrence = request_occurrence or occurrence(
        environment=effective_request.environment.identity
    )
    policy_authority = authority()
    secret_approval = (
        SecretEgressApproval(
            request=wire_request,
            route=resolved_route,
            occurrence=actual_occurrence,
            plan_fingerprint=fingerprint("f"),
            authority=policy_authority,
        )
        if http_secret_egress_origins(wire_request)
        else None
    )
    intent = HttpAuthorizationIntent(
        request=wire_request,
        route=resolved_route,
        occurrence=actual_occurrence,
        plan_fingerprint=fingerprint("f"),
        effective_policy_fingerprint=fingerprint("e"),
        secret_egress_approval=secret_approval,
    )
    authorization = HttpAuthorizationGrant(intent=intent, authority=policy_authority)
    return AuthorizedExchangeRequest(authorization=authorization)


def finalized_wire_request(
    effective_request: EffectiveRequest,
    *,
    body: FinalizedBody | None = None,
) -> FinalizedWireRequest:
    """Materialize one effective request without constructing route or send authority."""
    finalized_body = body or NoMaterializedBody()
    return FinalizedWireRequest(
        environment=effective_request.environment,
        logical_request_fingerprint=effective_request.logical_request_fingerprint,
        method=effective_request.method,
        url=effective_request.url,
        headers=effective_request.headers,
        cookies=effective_request.cookies,
        body=finalized_body,
        materialization=prove_request_materialization(
            effective_request,
            finalized_body,
            effective_request_fingerprint=materialization_fingerprint(effective_request),
        ),
        policies=effective_request.policies,
        provenance=effective_request.provenance,
    )


def resolution_failure(
    effective_request: EffectiveRequest,
    *,
    request_occurrence: HttpRequestOccurrence,
    retryable: bool = True,
) -> HttpResolutionFailure:
    """Build one route-free, policy-authorized DNS failure observation."""
    intent = HttpResolutionIntent(
        request=finalized_wire_request(effective_request),
        occurrence=request_occurrence,
        plan_fingerprint=fingerprint("f"),
        effective_policy_fingerprint=fingerprint("e"),
    )
    return HttpResolutionFailure(
        request=AuthorizedResolutionRequest(
            authorization=HttpResolutionGrant(intent=intent, authority=authority())
        ),
        failure=TransportFailure(
            phase=FailurePhase.DNS,
            code=DiagnosticCode(value="http.dns-failed"),
            retryable=retryable,
        ),
        timings=exchange_timings(failure_phase=FailurePhase.DNS),
    )


def resolution_denial(
    effective_request: EffectiveRequest,
    *,
    request_occurrence: HttpRequestOccurrence,
) -> HttpResolutionDenial:
    """Build one route-free policy denial without constructing a resolution grant."""
    return HttpResolutionDenial(
        intent=HttpResolutionIntent(
            request=finalized_wire_request(effective_request),
            occurrence=request_occurrence,
            plan_fingerprint=fingerprint("f"),
            effective_policy_fingerprint=fingerprint("e"),
        ),
        authority=authority(),
        code=DiagnosticCode(value="http.resolution-denied"),
        timings=exchange_timings(failure_phase=FailurePhase.POLICY),
    )


def authorization_denial(
    effective_request: EffectiveRequest,
    *,
    request_occurrence: HttpRequestOccurrence,
    route: DirectResolvedRoute | ProxyResolvedRoute | None = None,
) -> HttpAuthorizationDenial:
    """Build one route-specific policy denial without constructing a send grant."""
    resolved_route = route or DirectResolvedRoute(
        destination=endpoint(scheme=effective_request.url.scheme).destination
    )
    return HttpAuthorizationDenial(
        intent=HttpAuthorizationIntent(
            request=finalized_wire_request(effective_request),
            route=resolved_route,
            occurrence=request_occurrence,
            plan_fingerprint=fingerprint("f"),
            effective_policy_fingerprint=fingerprint("e"),
        ),
        authority=authority(),
        code=DiagnosticCode(value="http.policy-denied"),
        timings=exchange_timings(failure_phase=FailurePhase.POLICY),
    )


def proxy_request_route_endpoint(
    effective_request: EffectiveRequest,
    *,
    proxy_scheme: HttpScheme = HttpScheme.HTTP,
) -> tuple[EffectiveRequest, ProxyResolvedRoute, EndpointMetadata]:
    """Bind an effective request to one explicit proxy and selected proxy endpoint."""
    proxy_address = Ipv4Host(value="192.0.2.30")
    proxy_port = HttpPort(value=8443 if proxy_scheme is HttpScheme.HTTPS else 8080)
    proxy_destination = NetworkDestination(
        scheme=proxy_scheme,
        host=DnsHost(value="proxy.example.test"),
        port=proxy_port,
        resolved_addresses=(proxy_address,),
        selected_address=proxy_address,
    )
    route = ProxyResolvedRoute(
        proxy_destination=proxy_destination,
        target=ClientResolvedTarget(
            destination=endpoint(scheme=effective_request.url.scheme).destination
        ),
    )
    proxy_policy = ExplicitProxy(
        scheme=proxy_scheme,
        authority=HttpAuthority(
            host=DnsHost(value="proxy.example.test"),
            port=proxy_port,
        ),
        authentication=NoAuthentication(),
        tls=(
            ProxyTlsPolicy(policy=policies().tls)
            if proxy_scheme is HttpScheme.HTTPS
            else ProxyTlsDisabled()
        ),
    )
    proxied_request = msgspec.structs.replace(
        effective_request,
        policies=msgspec.structs.replace(effective_request.policies, proxy=proxy_policy),
    )
    proxy_endpoint = EndpointMetadata(
        destination=proxy_destination,
        remote_address=proxy_address,
        remote_port=proxy_port,
        local_address=None,
        local_port=None,
        connection_id="proxy-connection",
        connection_reused=False,
    )
    return proxied_request, route, proxy_endpoint


def exchange(
    *,
    exchange_number: int,
    effective_request: EffectiveRequest,
    observed_response: ResponseObservation | None,
    failure: TransportFailure | None = None,
    redirect: FollowedRedirect | None = None,
    execution_number: int = 1,
    attempt_number: int | None = None,
) -> HttpExchange:
    """Build one deterministic exchange occurrence."""
    actual_occurrence = occurrence(
        execution=execution_number,
        attempt=exchange_number if attempt_number is None else attempt_number,
        exchange=exchange_number,
        environment=effective_request.environment.identity,
    )
    actual_response = observed_response
    if observed_response is not None and redirect is not None:
        actual_response = msgspec.structs.replace(
            observed_response,
            headers=ResponseFields(
                fields=(
                    *observed_response.headers.fields,
                    ResponseField(
                        name=HeaderName(value="location"),
                        value=redirect.raw_location,
                    ),
                )
            ),
        )
    if (
        actual_response is not None
        and type(actual_response.body) is ResponseEntityBody
        and actual_response.body.handle is not None
    ):
        unique_handle = msgspec.structs.replace(
            actual_response.body.handle,
            body_id=BodyId(value=UUID(int=10_000 + exchange_number)),
        )
        capture = actual_response.body.capture
        if type(capture) is BudgetExceededCapture:
            scope = capture.exhaustion.budget.scope
            assert type(scope) is BodyBudgetScope
            capture = msgspec.structs.replace(
                capture,
                exhaustion=msgspec.structs.replace(
                    capture.exhaustion,
                    budget=msgspec.structs.replace(
                        capture.exhaustion.budget,
                        scope=msgspec.structs.replace(
                            scope,
                            attempt=actual_occurrence.attempt,
                            body_id=unique_handle.body_id,
                        ),
                    ),
                ),
            )
        actual_response = msgspec.structs.replace(
            actual_response,
            body=msgspec.structs.replace(
                actual_response.body,
                capture=capture,
                handle=unique_handle,
            ),
        )
    timings = exchange_timings(
        clear_text=effective_request.url.scheme is HttpScheme.HTTP,
        failure_phase=None if failure is None else failure.phase,
    )
    if (
        actual_response is not None
        and type(actual_response.body) is ResponseEntityBody
        and actual_response.body.handle is not None
        and actual_response.body.handle.byte_count.value == 0
    ):
        empty = NotPerformed(reason=NotPerformedReason.EMPTY_RESPONSE_BODY)
        timings = msgspec.structs.replace(
            timings,
            first_body_byte=empty,
            body_read=empty,
        )
    return HttpExchange(
        exchange_id=ExchangeId(value=UUID(int=exchange_number)),
        request=authorized_request(
            effective_request,
            request_occurrence=actual_occurrence,
        ),
        response=actual_response,
        failure=failure,
        redirect=redirect,
        timings=timings,
    )


def followed_redirect(request: EffectiveRequest) -> FollowedRedirect:
    """Retain a deterministic same-origin Location for transition tests."""
    return FollowedRedirect(
        raw_location=HeaderText(value="https://api.example.test/redirected"),
        resolved_url=request.url,
    )


def failure(
    phase: FailurePhase = FailurePhase.BODY_READ,
    *,
    retryable: bool = True,
) -> TransportFailure:
    """Build a typed transport failure without raw error text."""
    return TransportFailure(
        phase=phase,
        code=DiagnosticCode(value="http.transport-failed"),
        retryable=retryable,
    )
