"""Logical, effective, finalized, and route-authorized HTTP request aggregates."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import Authentication
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import CookieApiKeyPlacement
from comparo.core.domain.http.auth import HeaderApiKeyPlacement
from comparo.core.domain.http.auth import QueryApiKeyPlacement
from comparo.core.domain.http.auth import is_authentication
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import is_request_body
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import IdempotencyKeyRetry
from comparo.core.domain.http.policies import NoMutationRetry
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.request_materialization import EffectiveRequestFingerprint
from comparo.core.domain.http.request_materialization import EffectiveRequestMaterializationIdentity
from comparo.core.domain.http.request_materialization import RequestMaterializationProof
from comparo.core.domain.http.request_materialization import describe_request_body
from comparo.core.domain.http.request_materialization import effective_request_taint_origins
from comparo.core.domain.http.request_materialization import request_envelope_taint_origins
from comparo.core.domain.http.request_materialization import validate_effective_request_fingerprint
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import BaseRelativeTarget
from comparo.core.domain.http.url import RequestTarget
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import is_sensitive_value

type RequestDefinition = DefinitionRef | InlineDefinitionOrigin


def _is_request_definition(value: object) -> TypeGuard[RequestDefinition]:
    return type(value) in {DefinitionRef, InlineDefinitionOrigin}


def _target_query(target: RequestTarget) -> QueryParameters:
    if type(target) is BaseRelativeTarget:
        return target.query
    if type(target) is AbsoluteHttpUrl:
        return target.query
    raise TypeError("request target must be base-relative or absolute")


def _validate_retry_compatibility(method: HttpMethod, policies: RequestPolicies) -> None:
    retry = policies.retries
    if retry.max_attempts.value == 1:
        return
    read_only = method.value in {"GET", "HEAD", "OPTIONS", "TRACE"}
    if read_only and type(retry.mutation_retry) is not NoMutationRetry:
        raise ValueError("read-only requests cannot declare a mutation retry strategy")
    if not read_only and type(retry.mutation_retry) is NoMutationRetry:
        raise ValueError("mutation retries require an explicit idempotency strategy")


def _validate_final_idempotency_key(request: FinalizedWireRequest) -> None:
    strategy = request.policies.retries.mutation_retry
    if type(strategy) is not IdempotencyKeyRetry:
        return
    matching = tuple(
        field for field in request.headers.fields if field.name == strategy.header_name
    )
    if len(matching) != 1:
        raise ValueError("the finalized idempotency key header must exist exactly once")
    field = matching[0]
    if not is_sensitive_value(field.value):
        raise ValueError("the finalized idempotency key header must remain sensitive")
    if field.source is not HeaderSource.AUTHORED:
        raise ValueError("the finalized idempotency key must be a stable authored header")


def _validate_multipart_materialization(request: FinalizedWireRequest) -> None:
    if type(request.body) is not MaterializedBody or request.body.multipart is None:
        return
    fields = tuple(field for field in request.headers.fields if field.name.value == "content-type")
    if len(fields) != 1:
        raise ValueError("finalized multipart requires exactly one Content-Type header")
    field = fields[0]
    if field.source is not HeaderSource.BODY or type(field.value) is not HeaderText:
        raise ValueError("finalized multipart Content-Type must be a public body-derived header")
    if field.value != request.body.multipart.encoded_content_type:
        raise ValueError("finalized multipart Content-Type does not match boundary validation")


def _validate_logical_sources(
    headers: RequestHeaders,
    query: QueryParameters,
    cookies: RequestCookies,
) -> None:
    if any(field.source is not HeaderSource.AUTHORED for field in headers.fields):
        raise ValueError("logical request headers must contain only authored fields")
    if any(parameter.source is not QuerySource.AUTHORED for parameter in query.parameters):
        raise ValueError("logical request query must contain only authored parameters")
    if any(cookie.source is not CookieSource.EXPLICIT for cookie in cookies.cookies):
        raise ValueError("logical request cookies must contain only explicit cookies")


def _validate_authentication_collisions(
    authentication: Authentication,
    headers: RequestHeaders,
    query: QueryParameters,
    cookies: RequestCookies,
) -> None:
    header_names = frozenset(field.name.value for field in headers.fields)
    query_names = frozenset(parameter.name.value for parameter in query.parameters)
    cookie_names = frozenset(cookie.name.value for cookie in cookies.cookies)
    if type(authentication) in {BearerAuthentication, BasicAuthentication}:
        if "authorization" in header_names:
            raise ValueError("typed authentication collides with an authored Authorization header")
        return
    if type(authentication) is not ApiKeyAuthentication:
        return
    placement = authentication.placement
    if type(placement) is HeaderApiKeyPlacement and placement.name.value in header_names:
        raise ValueError("API-key authentication collides with an authored header")
    if type(placement) is QueryApiKeyPlacement and placement.name.value in query_names:
        raise ValueError("API-key authentication collides with an authored query parameter")
    if type(placement) is CookieApiKeyPlacement and placement.name.value in cookie_names:
        raise ValueError("API-key authentication collides with an explicit cookie")


def _validate_effective_field_collisions(
    headers: RequestHeaders,
    query: QueryParameters,
) -> None:
    for field in headers.fields:
        same_name = tuple(candidate for candidate in headers.fields if candidate.name == field.name)
        sources = frozenset(candidate.source for candidate in same_name)
        if HeaderSource.AUTHENTICATION in sources and len(sources) > 1:
            raise ValueError("authentication-generated header collides with another header source")
        if field.name.value == "content-type" and len(same_name) > 1:
            raise ValueError("an effective request cannot carry multiple Content-Type fields")
    for parameter in query.parameters:
        same_query_name = tuple(
            candidate for candidate in query.parameters if candidate.name == parameter.name
        )
        query_sources = frozenset(candidate.source for candidate in same_query_name)
        if QuerySource.AUTHENTICATION in query_sources and len(query_sources) > 1:
            raise ValueError("authentication-generated query value collides with authored query")


def _validate_request_payload(
    headers: RequestHeaders,
    cookies: RequestCookies,
    policies: RequestPolicies,
    provenance: ValueProvenance,
) -> None:
    if type(headers) is not RequestHeaders:
        raise TypeError("request headers must be RequestHeaders")
    if type(cookies) is not RequestCookies:
        raise TypeError("request cookies must be RequestCookies")
    if type(policies) is not RequestPolicies:
        raise TypeError("request policies must be RequestPolicies")
    if type(provenance) is not ValueProvenance:
        raise TypeError("request provenance must be ValueProvenance")


@final
class LogicalRequest(msgspec.Struct, frozen=True, kw_only=True):
    """A cell-shared request after value resolution but before Environment binding."""

    definition: RequestDefinition
    method: HttpMethod
    target: RequestTarget
    headers: RequestHeaders
    cookies: RequestCookies
    body: RequestBody
    authentication: Authentication
    policies: RequestPolicies
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require valid authored components and collision-free typed authentication."""
        if not _is_request_definition(self.definition):
            raise TypeError("logical request definition must be a supported definition origin")
        if (
            type(self.definition) is DefinitionRef
            and self.definition.identity.kind is not ResourceKind.REQUEST
        ):
            raise ValueError("logical request DefinitionRef must identify a Request")
        if type(self.method) is not HttpMethod:
            raise TypeError("logical request method must be HttpMethod")
        if type(self.target) not in {BaseRelativeTarget, AbsoluteHttpUrl}:
            raise TypeError("logical request target must be a supported RequestTarget")
        if not is_authentication(self.authentication):
            raise TypeError("logical request authentication must be Authentication")
        _validate_request_payload(self.headers, self.cookies, self.policies, self.provenance)
        if not is_request_body(self.body):
            raise TypeError("logical request body must be RequestBody")
        query = _target_query(self.target)
        _validate_logical_sources(self.headers, query, self.cookies)
        _validate_authentication_collisions(self.authentication, self.headers, query, self.cookies)
        _validate_retry_compatibility(self.method, self.policies)


def _validate_effective_identity(
    environment: DefinitionRef,
    logical_request_fingerprint: Fingerprint,
    method: HttpMethod,
    url: AbsoluteHttpUrl,
) -> None:
    if type(environment) is not DefinitionRef:
        raise TypeError("effective request environment must be DefinitionRef")
    if environment.identity.kind is not ResourceKind.ENVIRONMENT:
        raise ValueError("effective request environment must identify an Environment")
    if type(logical_request_fingerprint) is not Fingerprint:
        raise TypeError("logical request fingerprint must be Fingerprint")
    if type(method) is not HttpMethod:
        raise TypeError("effective request method must be HttpMethod")
    if type(url) is not AbsoluteHttpUrl:
        raise TypeError("effective request URL must be AbsoluteHttpUrl")


@final
class EffectiveRequest(msgspec.Struct, frozen=True, kw_only=True):
    """A resolved Environment request before body materialization and route authorization."""

    environment: DefinitionRef
    logical_request_fingerprint: Fingerprint
    method: HttpMethod
    url: AbsoluteHttpUrl
    headers: RequestHeaders
    cookies: RequestCookies
    body: RequestBody
    policies: RequestPolicies
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require a complete Environment binding and collision-free effective fields."""
        _validate_effective_identity(
            self.environment, self.logical_request_fingerprint, self.method, self.url
        )
        _validate_request_payload(self.headers, self.cookies, self.policies, self.provenance)
        if not is_request_body(self.body):
            raise TypeError("effective request body must be RequestBody")
        _validate_effective_field_collisions(self.headers, self.url.query)
        _validate_retry_compatibility(self.method, self.policies)


def prove_request_materialization(
    effective_request: EffectiveRequest,
    body: FinalizedBody,
    *,
    effective_request_fingerprint: EffectiveRequestFingerprint,
) -> RequestMaterializationProof:
    """Issue a value-free structural proof after a runtime fully materializes the body."""
    if type(effective_request) is not EffectiveRequest:
        raise TypeError("request materialization requires EffectiveRequest")
    if type(body) not in {NoMaterializedBody, MaterializedBody}:
        raise TypeError("request materialization output must be a finalized body")
    required_origins = effective_request_taint_origins(
        effective_request.url,
        effective_request.headers,
        effective_request.cookies,
        effective_request.body,
    )
    validate_effective_request_fingerprint(effective_request_fingerprint, required_origins)
    digest = body.source.digest if type(body) is MaterializedBody else None
    return RequestMaterializationProof(
        identity=EffectiveRequestMaterializationIdentity(
            environment=effective_request.environment,
            logical_request_fingerprint=effective_request.logical_request_fingerprint,
            effective_request_fingerprint=effective_request_fingerprint,
        ),
        input_body=describe_request_body(effective_request.body, digest=digest),
        body=body,
    )


def _validate_materialization_binding(request: FinalizedWireRequest) -> None:
    """Bind finalized request identity and body output to one value-free proof."""
    proof = request.materialization
    if type(proof) is not RequestMaterializationProof:
        raise TypeError("finalized request materialization must be RequestMaterializationProof")
    if (
        proof.identity.environment != request.environment
        or proof.identity.logical_request_fingerprint != request.logical_request_fingerprint
    ):
        raise ValueError("finalized request is not bound to its effective request identity")
    required_origins = request_envelope_taint_origins(
        request.url,
        request.headers,
        request.cookies,
        proof.input_body.taint_origins,
    )
    validate_effective_request_fingerprint(
        proof.identity.effective_request_fingerprint,
        required_origins,
    )
    if request.body != proof.body:
        raise ValueError("finalized request body is not bound to its materialization proof")


@final
class FinalizedWireRequest(msgspec.Struct, frozen=True, kw_only=True):
    """The complete request description after authentication and body materialization.

    Transport-derived Host and framing fields are intentionally absent. A transport derives them
    from this URL and materialized body and cannot accept caller-authored substitutes.
    """

    environment: DefinitionRef
    logical_request_fingerprint: Fingerprint
    method: HttpMethod
    url: AbsoluteHttpUrl
    headers: RequestHeaders
    cookies: RequestCookies
    body: FinalizedBody
    materialization: RequestMaterializationProof
    policies: RequestPolicies
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require the exact structurally bindable request used for authorization."""
        _validate_effective_identity(
            self.environment, self.logical_request_fingerprint, self.method, self.url
        )
        _validate_request_payload(self.headers, self.cookies, self.policies, self.provenance)
        if type(self.body) not in {NoMaterializedBody, MaterializedBody}:
            raise TypeError("finalized request body must be a materialization descriptor")
        _validate_effective_field_collisions(self.headers, self.url.query)
        _validate_retry_compatibility(self.method, self.policies)
        _validate_final_idempotency_key(self)
        _validate_multipart_materialization(self)
        _validate_materialization_binding(self)
