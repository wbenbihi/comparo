"""Small deterministic factories shared by HTTP domain tests."""

from __future__ import annotations

from typing import cast
from uuid import UUID
from uuid import uuid4

import msgspec

from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.http.auth import ApiKeyCredential
from comparo.core.domain.http.auth import BasicPassword
from comparo.core.domain.http.auth import BearerToken
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.authorization import SecretEgressApproval
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import MultipartBoundaryValidation
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import multipart_content_type
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import DeclaredIdempotentRetry
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import NoProxy
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import RedirectPolicy
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.policies import TimeoutPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.request_materialization import EffectiveRequestFingerprint
from comparo.core.domain.http.request_materialization import effective_request_taint_origins
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import LogicalRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.secret_egress import http_secret_egress_origins
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import BaseRelativeTarget
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.http.url import RequestTarget
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId


def provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def authority(character: str = "e") -> ExecutionPolicyAuthority:
    """Return one exact fingerprinted ExecutionPolicy authority."""
    return ExecutionPolicyAuthority(
        policy=definition(
            ResourceKind.EXECUTION_POLICY,
            "execution-policy",
            fingerprint_character=character,
        )
    )


def request_occurrence(
    *,
    execution: int = 1,
    attempt: int = 1,
    exchange: int = 1,
    environment: ResourceIdentity | None = None,
) -> HttpRequestOccurrence:
    """Return one deterministic outbound HTTP occurrence identity."""
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


def digest(character: str = "a") -> Sha256Digest:
    return Sha256Digest(hex_value=character * 64)


def fingerprint(character: str = "a") -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=digest(character))


def materialization_fingerprint(
    effective_request: EffectiveRequest,
) -> EffectiveRequestFingerprint:
    """Build a deterministic test identity with complete request sensitivity lineage."""
    value = fingerprint("9")
    origins = effective_request_taint_origins(
        effective_request.url,
        effective_request.headers,
        effective_request.cookies,
        effective_request.body,
    )
    return SensitiveValue(value, origins=origins) if origins else value


def definition(
    kind: ResourceKind,
    resource_id: str,
    *,
    fingerprint_character: str = "a",
) -> DefinitionRef:
    return DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="project"),
            kind=kind,
            resource_id=ResourceId(value=resource_id),
        ),
        fingerprint=fingerprint(fingerprint_character),
    )


def api_key_secret(value: str = "super-secret") -> SensitiveValue[ApiKeyCredential]:
    return SensitiveValue(
        ApiKeyCredential(value=value),
        origins=(TaintOriginId(value="test-secret"),),
    )


def bearer_secret(value: str = "super-secret") -> SensitiveValue[BearerToken]:
    return SensitiveValue(
        BearerToken(value=value),
        origins=(TaintOriginId(value="test-secret"),),
    )


def password_secret(value: str = "super-secret") -> SensitiveValue[BasicPassword]:
    return SensitiveValue(
        BasicPassword(value=value),
        origins=(TaintOriginId(value="test-secret"),),
    )


def policies(*, attempts: int = 1) -> RequestPolicies:
    five_seconds = Duration(nanoseconds=5_000_000_000)
    return RequestPolicies(
        timeouts=TimeoutPolicy(
            connect=five_seconds,
            tls_handshake=five_seconds,
            response_headers=five_seconds,
            idle=five_seconds,
            attempt_total=five_seconds,
            execution_total=five_seconds,
        ),
        retries=RetryPolicy(
            max_attempts=PositiveCount(value=attempts),
            conditions=() if attempts == 1 else (RetryCondition.CONNECT_FAILURE,),
            backoff=NoBackoff(),
            jitter=Ratio(numerator=0, denominator=PositiveCount(value=1)),
        ),
        tls=TlsPolicy(
            verify_certificates=True,
            verify_hostname=True,
            minimum_version=TlsVersion.TLS_1_2,
            trust_source=TrustSource.OPERATING_SYSTEM,
        ),
        proxy=NoProxy(),
        redirects=RedirectPolicy(max_hops=Count(value=0)),
    )


def sensitive[ValueT](value: ValueT) -> SensitiveValue[ValueT]:
    """Seal an immutable request value for validation tests."""
    return SensitiveValue(value, origins=(TaintOriginId(value="validation-test"),))


def destination(
    host: str = "api.example.test",
    address: str = "192.0.2.10",
    *,
    scheme: HttpScheme = HttpScheme.HTTPS,
    port: int = 443,
) -> NetworkDestination:
    """Build one pinned DNS destination."""
    selected = Ipv4Host(value=address)
    return NetworkDestination(
        scheme=scheme,
        host=DnsHost(value=host),
        port=HttpPort(value=port),
        resolved_addresses=(selected,),
        selected_address=selected,
    )


def finalized_request(
    *,
    method: str = "GET",
    url: AbsoluteHttpUrl | None = None,
    headers: RequestHeaders | None = None,
    cookies: RequestCookies | None = None,
    body: FinalizedBody | None = None,
    request_policies: RequestPolicies | None = None,
) -> FinalizedWireRequest:
    """Build one valid finalized request with configurable aggregate components."""
    actual_environment = definition(ResourceKind.ENVIRONMENT, "staging")
    actual_fingerprint = fingerprint("a")
    actual_method = HttpMethod(value=method)
    actual_url = url or AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="api.example.test")),
    )
    actual_headers = headers or RequestHeaders()
    actual_cookies = cookies or RequestCookies()
    actual_body = body or NoMaterializedBody()
    actual_policies = request_policies or policies()
    actual_provenance = provenance()
    if type(actual_body) is NoMaterializedBody:
        effective_body: RequestBody = NoBody()
    elif actual_body.multipart is None:
        effective_body = BytesBody(value=actual_body.source)
    else:
        effective_body = MultipartBody(
            boundary=actual_body.multipart.boundary,
            parts=(),
        )
    effective = EffectiveRequest(
        environment=actual_environment,
        logical_request_fingerprint=actual_fingerprint,
        method=actual_method,
        url=actual_url,
        headers=actual_headers,
        cookies=actual_cookies,
        body=effective_body,
        policies=actual_policies,
        provenance=actual_provenance,
    )
    return FinalizedWireRequest(
        environment=actual_environment,
        logical_request_fingerprint=actual_fingerprint,
        method=actual_method,
        url=actual_url,
        headers=actual_headers,
        cookies=actual_cookies,
        body=actual_body,
        materialization=prove_request_materialization(
            effective,
            actual_body,
            effective_request_fingerprint=materialization_fingerprint(effective),
        ),
        policies=actual_policies,
        provenance=actual_provenance,
    )


def logical_request(
    *,
    method: str = "GET",
    target: RequestTarget | None = None,
    headers: RequestHeaders | None = None,
    cookies: RequestCookies | None = None,
    body: RequestBody | None = None,
    authentication: object | None = None,
    request_policies: RequestPolicies | None = None,
) -> LogicalRequest:
    """Build one valid logical request unless an invalid component is supplied."""
    return LogicalRequest(
        definition=definition(ResourceKind.REQUEST, "request"),
        method=HttpMethod(value=method),
        target=target or BaseRelativeTarget(),
        headers=headers or RequestHeaders(),
        cookies=cookies or RequestCookies(),
        body=body or NoBody(),
        authentication=cast("NoAuthentication", authentication or NoAuthentication()),
        policies=request_policies or policies(),
        provenance=provenance(),
    )


def retry_policies(*, mutation: object) -> RequestPolicies:
    """Build an enabled retry policy using an explicitly supplied mutation strategy."""
    defaults = policies()
    return msgspec.structs.replace(
        defaults,
        retries=RetryPolicy(
            max_attempts=PositiveCount(value=2),
            conditions=(RetryCondition.CONNECT_FAILURE,),
            backoff=NoBackoff(),
            jitter=Ratio(numerator=0, denominator=PositiveCount(value=1)),
            mutation_retry=cast("DeclaredIdempotentRetry", mutation),
        ),
    )


def proxy_request_and_route(
    *,
    delegated: bool,
) -> tuple[FinalizedWireRequest, ProxyResolvedRoute]:
    """Build a finalized request and matching explicit proxy route."""
    proxy_host = "proxy.example.test"
    proxy_destination = destination(proxy_host, "192.0.2.20")
    target = (
        ProxyResolvedTarget(
            scheme=HttpScheme.HTTPS,
            authority=HttpAuthority(host=DnsHost(value="api.example.test")),
        )
        if delegated
        else ClientResolvedTarget(destination=destination())
    )
    proxy_policy = ExplicitProxy(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value=proxy_host)),
        authentication=NoAuthentication(),
        tls=ProxyTlsPolicy(policy=policies().tls),
    )
    request = finalized_request(
        request_policies=msgspec.structs.replace(policies(), proxy=proxy_policy)
    )
    return request, ProxyResolvedRoute(proxy_destination=proxy_destination, target=target)


def direct_intent(
    *,
    request: FinalizedWireRequest | None = None,
    occurrence: HttpRequestOccurrence | None = None,
) -> HttpAuthorizationIntent:
    """Build one valid direct-route authorization intent."""
    actual_request = request or finalized_request()
    route = DirectResolvedRoute(destination=destination())
    request_occurrence_value = occurrence or request_occurrence()
    policy_authority = authority()
    secret_approval = (
        SecretEgressApproval(
            request=actual_request,
            route=route,
            occurrence=request_occurrence_value,
            plan_fingerprint=fingerprint("c"),
            authority=policy_authority,
        )
        if http_secret_egress_origins(actual_request)
        else None
    )
    return HttpAuthorizationIntent(
        request=actual_request,
        route=route,
        occurrence=request_occurrence_value,
        plan_fingerprint=fingerprint("c"),
        effective_policy_fingerprint=fingerprint("e"),
        secret_egress_approval=secret_approval,
    )


def multipart_body() -> tuple[MaterializedBody, MultipartBoundaryValidation]:
    """Build a valid collision-validated multipart materialization."""
    source = RepeatableByteSource(
        source_id=BodyId(value=uuid4()),
        byte_count=ByteCount(value=4),
        digest=digest("e"),
        provenance=provenance(),
    )
    boundary = MultipartBoundary(value="boundary")
    validation = MultipartBoundaryValidation(
        boundary=boundary,
        source_id=source.source_id,
        digest=source.digest,
        encoded_content_type=multipart_content_type(boundary),
    )
    return MaterializedBody(source=source, multipart=validation), validation
