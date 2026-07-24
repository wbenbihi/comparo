"""Resolve v1alpha1 Request resources into conservative body-free wire requests."""

from __future__ import annotations

import hashlib
import ipaddress
from typing import TypeGuard
from typing import cast
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
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
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import NoProxy
from comparo.core.domain.http.policies import RedirectPolicy
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.policies import TimeoutPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.request_materialization import EffectiveRequestFingerprint
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import PathSegment
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import Unavailable
from comparo.core.planning._canonical import find_resource
from comparo.core.planning._canonical import object_member
from comparo.core.planning._canonical import resource_spec
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.context import RunContext
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.models import ResolvedForWire
from comparo.core.resolution.snapshots import cell_inputs
from comparo.core.runtime.models import BlockedExecution
from comparo.core.runtime.models import CancelledExecution
from comparo.core.runtime.models import ExecutionWork
from comparo.core.runtime.models import UnavailableExecution
from comparo.core.runtime.ports import PreparedRequest
from comparo.core.runtime.ports import RequestPreparationFailure

_REQUEST_ACCESS = SensitiveValueAccess(purpose="runtime.request.materialization")
_RESOLUTION_UNAVAILABLE = DiagnosticCode(value="runtime.request.unavailable")
_RESOLUTION_ERROR = DiagnosticCode(value="runtime.request.resolution-error")
_RESOLUTION_BLOCKED = DiagnosticCode(value="runtime.request.blocked")
_SHAPE_INVALID = DiagnosticCode(value="runtime.request.shape-invalid")
_BODY_UNSUPPORTED = DiagnosticCode(value="runtime.request.body-unsupported")


class ConfigurationRequestProvider:
    """Resolve the current body-free HTTP subset from the admitted canonical project graph.

    Body serialization, authentication, redirects, cookies, and advanced transport options remain
    Step 8 adapter capabilities. Unsupported authored shapes fail explicitly instead of being
    silently ignored or inferred.
    """

    __slots__ = ("_context", "_prepared")

    def __init__(self, prepared: PreparedRun, context: RunContext) -> None:
        """Bind one admitted run and its isolated value context."""
        if type(prepared) is not PreparedRun or type(context) is not RunContext:
            raise TypeError("configuration request provider requires PreparedRun and RunContext")
        if context.run.run_id != prepared.plan.run_id:
            raise ValueError("request provider context belongs to another run")
        self._prepared = prepared
        self._context = context

    async def prepare(self, work: ExecutionWork, attempt: AttemptScope) -> PreparedRequest:
        """Resolve one Request and Environment at the exact attempt consumer scope."""
        if type(work) is not ExecutionWork or type(attempt) is not AttemptScope:
            raise TypeError("request preparation requires ExecutionWork and AttemptScope")
        if work.subject.identity.kind is not ResourceKind.REQUEST:
            return _unavailable(_SHAPE_INVALID)
        request_resource = find_resource(
            self._prepared.project,
            ResourceKind.REQUEST,
            work.subject.identity.resource_id,
            owner=self._prepared.project.project,
            field="subjects",
        )
        request_expression = object_member(resource_spec(request_resource), "request")
        if not _is_expression(request_expression):  # pragma: no cover - compiled-field invariant
            return _unavailable(_SHAPE_INVALID)
        result = await self._context.resolve_for_wire(
            request_expression,
            ResolutionTarget(
                symbol=SymbolPath(segments=("request",)),
                scope=attempt,
                cell_inputs=cell_inputs(self._prepared, work.trial.cell),
            ),
        )
        resolved = _resolved_object(result)
        if type(resolved) is RequestPreparationFailure:
            return resolved
        payload, origins = resolved
        authored_body = _object_value(payload, "body")
        if authored_body is not None and type(authored_body) is not JsonNull:
            return _unavailable(_BODY_UNSUPPORTED)
        method = _text_member(payload, "method")
        if method is None:  # pragma: no cover - compiler requires a request method
            return _unavailable(_SHAPE_INVALID)
        target = _text_member(payload, "url")
        if target is None:
            base = await self._resolve_base_url(work, attempt)
            if type(base) is RequestPreparationFailure:
                return base
            endpoint = _text_member(payload, "endpoint") or "/"
            target = _join_url(base, endpoint)
        try:
            url = _absolute_url(target, payload, request_expression.provenance, origins)
            headers = _headers(payload, request_expression.provenance, origins)
            effective = EffectiveRequest(
                environment=work.environment,
                logical_request_fingerprint=work.subject.fingerprint,
                method=HttpMethod(value=method.upper()),
                url=url,
                headers=headers,
                cookies=RequestCookies(),
                body=NoBody(),
                policies=_default_policies(),
                provenance=request_expression.provenance,
            )
            materialized_body = NoMaterializedBody()
            fingerprint = _effective_fingerprint(payload, method, target, origins)
            proof = prove_request_materialization(
                effective,
                materialized_body,
                effective_request_fingerprint=fingerprint,
            )
            return FinalizedWireRequest(
                environment=effective.environment,
                logical_request_fingerprint=effective.logical_request_fingerprint,
                method=effective.method,
                url=effective.url,
                headers=effective.headers,
                cookies=effective.cookies,
                body=materialized_body,
                materialization=proof,
                policies=effective.policies,
                provenance=effective.provenance,
            )
        except (TypeError, ValueError, UnicodeError):
            return _unavailable(_SHAPE_INVALID)

    async def _resolve_base_url(
        self,
        work: ExecutionWork,
        attempt: AttemptScope,
    ) -> str | RequestPreparationFailure:
        environment = find_resource(
            self._prepared.project,
            ResourceKind.ENVIRONMENT,
            work.environment.identity.resource_id,
            owner=self._prepared.project.project,
            field="environments",
        )
        expression = object_member(resource_spec(environment), "baseUrl")
        if not _is_expression(expression):  # pragma: no cover - compiled-field invariant
            return _unavailable(_SHAPE_INVALID)
        result = await self._context.resolve(
            expression,
            ResolutionTarget(
                symbol=SymbolPath(segments=("environment", "baseUrl")),
                scope=attempt,
                cell_inputs=cell_inputs(self._prepared, work.trial.cell),
            ),
        )
        if type(result) is not Present or is_sensitive_value(result.value):
            failure = _resolution_failure(result)
            return failure or _unavailable(_SHAPE_INVALID)
        if type(result.value) is JsonString:
            return result.value.value
        return _unavailable(_SHAPE_INVALID)

    async def close(self) -> None:
        """Release every run-scoped value cache and sensitive registry entry."""
        await self._context.close()


def _is_expression(value: object) -> TypeGuard[ValueExpression]:
    return is_value_expression(value)


def _resolved_object(
    result: object,
) -> tuple[JsonObject, tuple[TaintOriginId, ...]] | RequestPreparationFailure:
    if type(result) is not ResolvedForWire:
        return _resolution_failure(result) or _unavailable(_SHAPE_INVALID)
    value = result.result.value
    if is_sensitive_value(value):
        if value.payload_type is not JsonObject:
            return _unavailable(_SHAPE_INVALID)
        return cast(JsonObject, value.reveal(access=_REQUEST_ACCESS)), value.origins
    if type(value) is not JsonObject:
        return _unavailable(_SHAPE_INVALID)
    return value, ()


def _resolution_failure(result: object) -> RequestPreparationFailure | None:
    if type(result) in {Unavailable, Missing}:
        return _unavailable(_RESOLUTION_UNAVAILABLE)
    if type(result) is Error:
        if result.code is ResolutionErrorCode.CANCELLED:
            return RequestPreparationFailure(
                outcome=CancelledExecution(source=CancellationSource.PARENT_SCOPE)
            )
        return _unavailable(_RESOLUTION_ERROR)
    if type(result) is Blocked:
        return RequestPreparationFailure(
            outcome=BlockedExecution(stage=DiagnosticStage.RESOLUTION, code=_RESOLUTION_BLOCKED)
        )
    return None


def _unavailable(code: DiagnosticCode) -> RequestPreparationFailure:
    return RequestPreparationFailure(
        outcome=UnavailableExecution(stage=DiagnosticStage.RESOLUTION, code=code)
    )


def _object_value(value: JsonObject, name: str) -> JsonValue | None:
    return next((member.value for member in value.members if member.name == name), None)


def _text_member(value: JsonObject, name: str) -> str | None:
    member = _object_value(value, name)
    return member.value if type(member) is JsonString else None


def _join_url(base: str, endpoint: str) -> str:
    return f"{base.rstrip('/')}/{endpoint.lstrip('/')}"


def _absolute_url(
    raw: str,
    request: JsonObject,
    provenance: ValueProvenance,
    origins: tuple[TaintOriginId, ...],
) -> AbsoluteHttpUrl:
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("request URL must be absolute HTTP(S)")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("request URL cannot contain user information or a fragment")
    scheme = HttpScheme(parsed.scheme)
    path = _path(parsed.path, origins)
    url_query = tuple(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True))
    authored_query = _object_value(request, "query")
    query_items = [*url_query]
    if authored_query is not None:
        if type(authored_query) is not JsonObject:
            raise TypeError("request query must be an object")
        query_items.extend(
            (member.name, member.value.value)
            for member in authored_query.members
            if type(member.value) is JsonString
        )
        if len(query_items) != len(url_query) + len(authored_query.members):
            raise TypeError("request query values must be strings")
    return AbsoluteHttpUrl(
        scheme=scheme,
        authority=HttpAuthority(
            host=_host(parsed.hostname),
            port=HttpPort(value=parsed.port) if parsed.port is not None else None,
        ),
        path=path,
        query=QueryParameters(
            parameters=tuple(
                QueryParameter(
                    name=QueryName(value=name),
                    value=_sensitive(QueryText(value=value), origins),
                    source=QuerySource.AUTHORED,
                    provenance=provenance,
                )
                for name, value in query_items
            )
        ),
    )


def _headers(
    request: JsonObject,
    provenance: ValueProvenance,
    origins: tuple[TaintOriginId, ...],
) -> RequestHeaders:
    value = _object_value(request, "headers")
    if value is None:
        return RequestHeaders()
    if type(value) is not JsonObject:
        raise TypeError("request headers must be an object")
    fields: list[HeaderField] = []
    for member in value.members:
        if type(member.value) is not JsonString:
            raise TypeError("request header values must be strings")
        fields.append(
            HeaderField(
                name=HeaderName(value=member.name.lower()),
                value=_sensitive(HeaderText(value=member.value.value), origins),
                source=HeaderSource.AUTHORED,
                provenance=provenance,
            )
        )
    return RequestHeaders(fields=tuple(fields))


def _path(raw: str, origins: tuple[TaintOriginId, ...]) -> UrlPath:
    if not raw or raw == "/":
        return UrlPath()
    trailing = raw.endswith("/")
    pieces = raw.strip("/").split("/")
    segments: tuple[PathSegment, ...] = tuple(
        _sensitive(DecodedPathSegment(value=piece), origins) for piece in pieces
    )
    return UrlPath(segments=segments, trailing_slash=trailing)


def _host(value: str) -> HttpHost:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return DnsHost(value=value.lower())
    return Ipv4Host(value=str(parsed)) if parsed.version == 4 else Ipv6Host(value=str(parsed))


def _sensitive[ValueT](
    value: ValueT,
    origins: tuple[TaintOriginId, ...],
) -> ValueT | SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=origins) if origins else value


def _effective_fingerprint(
    payload: JsonObject,
    method: str,
    resolved_target: str,
    origins: tuple[TaintOriginId, ...],
) -> EffectiveRequestFingerprint:
    encoded = msgspec.json.encode(
        {
            "method": method.upper(),
            "resolvedTarget": resolved_target,
            "resolvedRequest": payload,
        }
    )
    fingerprint = Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(encoded).hexdigest()),
    )
    return SensitiveValue(fingerprint, origins=origins) if origins else fingerprint


def _default_policies() -> RequestPolicies:
    deadline = Duration(nanoseconds=30_000_000_000)
    return RequestPolicies(
        timeouts=TimeoutPolicy(
            connect=deadline,
            tls_handshake=deadline,
            response_headers=deadline,
            idle=deadline,
            attempt_total=deadline,
            execution_total=deadline,
        ),
        retries=RetryPolicy(
            max_attempts=PositiveCount(value=1),
            conditions=(),
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
