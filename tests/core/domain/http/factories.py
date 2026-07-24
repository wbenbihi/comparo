"""Small deterministic factories shared by HTTP domain tests."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import msgspec

from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.http.auth import ApiKeyCredential
from comparo.core.domain.http.auth import BasicPassword
from comparo.core.domain.http.auth import BearerToken
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import MaterializedBody
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
from comparo.core.domain.http.policies import RedirectPolicy
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.policies import TimeoutPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import LogicalRequest
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
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
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


def digest(character: str = "a") -> Sha256Digest:
    return Sha256Digest(hex_value=character * 64)


def fingerprint(character: str = "a") -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=digest(character))


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
    return FinalizedWireRequest(
        environment=definition(ResourceKind.ENVIRONMENT, "staging"),
        logical_request_fingerprint=fingerprint("a"),
        method=HttpMethod(value=method),
        url=url
        or AbsoluteHttpUrl(
            scheme=HttpScheme.HTTPS,
            authority=HttpAuthority(host=DnsHost(value="api.example.test")),
        ),
        headers=headers or RequestHeaders(),
        cookies=cookies or RequestCookies(),
        body=body or NoMaterializedBody(),
        policies=request_policies or policies(),
        provenance=provenance(),
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
    )
    request = finalized_request(
        request_policies=msgspec.structs.replace(policies(), proxy=proxy_policy)
    )
    return request, ProxyResolvedRoute(proxy_destination=proxy_destination, target=target)


def direct_intent(*, request: FinalizedWireRequest | None = None) -> HttpAuthorizationIntent:
    """Build one valid direct-route authorization intent."""
    actual_request = request or finalized_request()
    return HttpAuthorizationIntent(
        request=actual_request,
        route=DirectResolvedRoute(destination=destination()),
        plan_fingerprint=fingerprint("c"),
        effective_policy_fingerprint=fingerprint("e"),
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
