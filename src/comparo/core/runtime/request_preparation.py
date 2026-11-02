"""Resolve v1alpha1 Request and Environment inputs into finalized wire requests."""

from __future__ import annotations

import hashlib
import ipaddress
from typing import Protocol
from typing import TypeGuard
from typing import cast
from urllib.parse import parse_qsl
from urllib.parse import urlsplit
from uuid import uuid5

import msgspec

from comparo.core.configuration.canonical import CompiledResource
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.http.auth import Authentication
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.request_materialization import EffectiveRequestFingerprint
from comparo.core.domain.http.request_materialization import effective_request_taint_origins
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
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Fingerprint
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
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import is_json_value
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
from comparo.core.runtime.request_lowering import DEFAULT_REQUEST_TIMEOUT
from comparo.core.runtime.request_lowering import lower_authentication
from comparo.core.runtime.request_lowering import lower_body
from comparo.core.runtime.request_lowering import lower_cookies
from comparo.core.runtime.request_lowering import lower_headers
from comparo.core.runtime.request_lowering import lower_query
from comparo.core.runtime.request_lowering import lower_request_policies
from comparo.core.runtime.request_lowering import merge_cookies
from comparo.core.runtime.request_lowering import merge_headers
from comparo.core.runtime.request_lowering import validate_stream_mode

_REQUEST_ACCESS = SensitiveValueAccess(purpose="runtime.request.materialization")
_RESOLUTION_UNAVAILABLE = DiagnosticCode(value="runtime.request.unavailable")
_RESOLUTION_ERROR = DiagnosticCode(value="runtime.request.resolution-error")
_RESOLUTION_BLOCKED = DiagnosticCode(value="runtime.request.blocked")
_SHAPE_INVALID = DiagnosticCode(value="runtime.request.shape-invalid")
_MATERIALIZATION_FAILED = DiagnosticCode(value="runtime.request.materialization-failed")
_SENSITIVE_TARGET = DiagnosticCode(value="runtime.request.sensitive-target")

type _ResolvedFields = dict[str, ResolvedValue]
type _FieldOrigins = dict[str, tuple[TaintOriginId, ...]]


class RequestFinalizer(Protocol):
    """Finalize one resolved request behind an adapter-owned body store."""

    async def finalize(
        self,
        request: EffectiveRequest,
        authentication: Authentication,
        *,
        body_id: BodyId,
    ) -> FinalizedWireRequest:
        """Apply authentication and materialize one retry-safe body."""
        ...


class ConfigurationRequestProvider:
    """Resolve admitted configuration through typed lowering and injected finalization."""

    __slots__ = ("_context", "_finalizer", "_prepared")

    def __init__(
        self,
        prepared: PreparedRun,
        context: RunContext,
        finalizer: RequestFinalizer | None = None,
    ) -> None:
        """Bind one admitted run, isolated value context, and optional real finalizer."""
        if type(prepared) is not PreparedRun or type(context) is not RunContext:
            raise TypeError("configuration request provider requires PreparedRun and RunContext")
        if context.run.run_id != prepared.plan.run_id:
            raise ValueError("request provider context belongs to another run")
        if finalizer is not None and not hasattr(finalizer, "finalize"):
            raise TypeError("request provider finalizer must implement finalize")
        self._prepared = prepared
        self._context = context
        self._finalizer = finalizer or _NoBodyRequestFinalizer()

    async def prepare(self, work: ExecutionWork, attempt: AttemptScope) -> PreparedRequest:
        """Resolve, lower, and finalize one exact Environment attempt."""
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
        expression = object_member(resource_spec(request_resource), "request")
        if type(expression) is not ObjectExpr:  # pragma: no cover - compiler invariant
            return _unavailable(_SHAPE_INVALID)
        expression = effective_request_expression(self._prepared, work, expression)
        target = ResolutionTarget(
            symbol=SymbolPath(segments=("request",)),
            scope=attempt,
            cell_inputs=cell_inputs(self._prepared, work.trial.cell),
        )
        resolved = await _resolve_fields(self._context, expression, target)
        if type(resolved) is RequestPreparationFailure:
            return resolved
        request_values, request_origins = resolved
        environment = _environment_resource(self._prepared, work)
        resolved_environment = await _resolve_environment(
            self._context,
            self._prepared,
            environment,
            work,
            attempt,
        )
        if type(resolved_environment) is RequestPreparationFailure:
            return resolved_environment
        environment_values, environment_origins, tls, base_url = resolved_environment
        routing = _routing(request_values, request_origins, base_url)
        if type(routing) is RequestPreparationFailure:
            return routing
        method, raw_target = routing
        try:
            return await self._finalize(
                work,
                attempt,
                expression.provenance,
                method,
                raw_target,
                base_url,
                request_values,
                request_origins,
                environment_values,
                environment_origins,
                tls,
            )
        except (TypeError, ValueError, UnicodeError):
            return _unavailable(_SHAPE_INVALID)
        except Exception:  # noqa: BLE001 - adapter errors must not expose input-bearing details
            return _unavailable(_MATERIALIZATION_FAILED)

    async def _finalize(
        self,
        work: ExecutionWork,
        attempt: AttemptScope,
        provenance: ValueProvenance,
        method: str,
        raw_target: str,
        base_url: str,
        request: _ResolvedFields,
        request_origins: _FieldOrigins,
        environment: _ResolvedFields,
        environment_origins: _FieldOrigins,
        tls: JsonValue | None,
    ) -> FinalizedWireRequest:
        """Construct validated effective fields before adapter-owned finalization."""
        query = lower_query(
            request.get("query"),
            request_origins.get("query", ()),
            provenance,
        )
        url = _absolute_url(raw_target, query, provenance)
        same_origin = _same_origin(base_url, raw_target)
        request_headers = lower_headers(
            request.get("headers"), request_origins.get("headers", ()), provenance
        )
        request_cookies = lower_cookies(
            request.get("cookies"), request_origins.get("cookies", ()), provenance
        )
        environment_headers = (
            lower_headers(
                environment.get("headers"),
                environment_origins.get("headers", ()),
                provenance,
            )
            if same_origin
            else type(request_headers)()
        )
        environment_cookies = (
            lower_cookies(
                environment.get("cookies"),
                environment_origins.get("cookies", ()),
                provenance,
            )
            if same_origin
            else type(request_cookies)()
        )
        auth_value, auth_origins = _effective_authentication(
            request,
            request_origins,
            environment,
            environment_origins,
            same_origin=same_origin,
        )
        authentication = lower_authentication(auth_value, auth_origins)
        timeout = request.get("timeout", environment.get("timeout"))
        proxy = environment.get("proxy")
        policies = lower_request_policies(
            timeout,
            request.get("retries"),
            request.get("redirects"),
            tls,
            proxy,
            environment_origins.get("proxy", ()),
        )
        body = lower_body(
            request.get("body"),
            request.get("bodyType"),
            request_origins.get("body", ()),
            multipart_boundary=f"comparo-{work.trial.cell.cell_id.value.hex}",
        )
        validate_stream_mode(request.get("stream"))
        effective = EffectiveRequest(
            environment=work.environment,
            logical_request_fingerprint=work.subject.fingerprint,
            method=HttpMethod(value=method.upper()),
            url=url,
            headers=merge_headers(environment_headers, request_headers),
            cookies=merge_cookies(environment_cookies, request_cookies),
            body=body,
            policies=policies,
            provenance=provenance,
        )
        return await self._finalizer.finalize(
            effective,
            authentication,
            body_id=BodyId(value=uuid5(attempt.attempt_id.value, "request-body")),
        )

    async def close(self) -> None:
        """Release no resources because the injected context/finalizer remain caller-owned."""


class _NoBodyRequestFinalizer:
    """Conservative fallback used by core-only tests and fake-runtime compositions."""

    async def finalize(
        self,
        request: EffectiveRequest,
        authentication: Authentication,
        *,
        body_id: BodyId,
    ) -> FinalizedWireRequest:
        del body_id
        if type(request.body) is not NoBody or type(authentication) is not NoAuthentication:
            raise ValueError("body or authentication requires an injected request finalizer")
        body = NoMaterializedBody()
        fingerprint = _opaque_effective_fingerprint(request)
        proof = prove_request_materialization(
            request,
            body,
            effective_request_fingerprint=fingerprint,
        )
        return FinalizedWireRequest(
            environment=request.environment,
            logical_request_fingerprint=request.logical_request_fingerprint,
            method=request.method,
            url=request.url,
            headers=request.headers,
            cookies=request.cookies,
            body=body,
            materialization=proof,
            policies=request.policies,
            provenance=request.provenance,
        )


def effective_request_expression(
    prepared: PreparedRun,
    work: ExecutionWork,
    authored: ObjectExpr,
) -> ObjectExpr:
    """Overlay the run-owned typed Request edit without mutating the compiled project."""
    subject = next(item for item in prepared.effective.subjects if item.definition == work.subject)
    edit = subject.request_edit
    if edit is None:
        return authored
    replacements = dict(edit.field_values())
    retained = tuple(
        ObjectExpressionMember(
            name=member.name,
            expression=replacements.pop(member.name, member.expression),
        )
        for member in authored.members
    )
    appended = tuple(
        ObjectExpressionMember(name=name, expression=expression)
        for name, expression in edit.field_values()
        if name in replacements
    )
    return ObjectExpr(members=(*retained, *appended), provenance=authored.provenance)


async def _resolve_fields(
    context: RunContext,
    expression: ObjectExpr,
    target: ResolutionTarget,
) -> tuple[_ResolvedFields, _FieldOrigins] | RequestPreparationFailure:
    """Resolve top-level fields independently so routing and credentials retain lineage."""
    values: _ResolvedFields = {}
    origins: _FieldOrigins = {}
    for member in expression.members:
        result = await context.resolve_for_wire(
            member.expression,
            ResolutionTarget(
                symbol=SymbolPath(segments=(*target.symbol.segments, member.name)),
                scope=target.scope,
                namespace=target.namespace,
                cell_inputs=target.cell_inputs,
            ),
        )
        if type(result) is Missing:
            continue
        resolved = _resolved_value(result)
        if type(resolved) is RequestPreparationFailure:
            return resolved
        values[member.name], origins[member.name] = resolved
    return values, origins


async def _resolve_environment(
    context: RunContext,
    prepared: PreparedRun,
    environment: CompiledResource,
    work: ExecutionWork,
    attempt: AttemptScope,
) -> tuple[_ResolvedFields, _FieldOrigins, JsonValue | None, str] | RequestPreparationFailure:
    """Resolve one Environment's dynamic overlays and inert TLS declaration."""
    spec = resource_spec(environment)
    base_expression = object_member(spec, "baseUrl")
    if not _is_expression(base_expression):  # pragma: no cover - compiler invariant
        return _unavailable(_SHAPE_INVALID)
    expressions = tuple(
        ObjectExpressionMember(name=name, expression=value)
        for name in ("headers", "cookies", "auth", "timeout", "proxy")
        if _is_expression(value := object_member(spec, name))
    )
    synthetic = ObjectExpr(members=expressions, provenance=base_expression.provenance)
    resolved = await _resolve_fields(
        context,
        synthetic,
        ResolutionTarget(
            symbol=SymbolPath(segments=("environment",)),
            scope=attempt,
            cell_inputs=cell_inputs(prepared, work.trial.cell),
        ),
    )
    if type(resolved) is RequestPreparationFailure:
        return resolved
    base_url = await _resolve_base_url(context, prepared, environment, work, attempt)
    if type(base_url) is RequestPreparationFailure:
        return base_url
    tls_value = object_member(spec, "tls")
    if tls_value is not None and not is_json_value(tls_value):
        return _unavailable(_SHAPE_INVALID)
    values, origins = resolved
    return values, origins, tls_value, base_url


async def _resolve_base_url(
    context: RunContext,
    prepared: PreparedRun,
    environment: CompiledResource,
    work: ExecutionWork,
    attempt: AttemptScope,
) -> str | RequestPreparationFailure:
    expression = object_member(resource_spec(environment), "baseUrl")
    if not _is_expression(expression):  # pragma: no cover - compiler invariant
        return _unavailable(_SHAPE_INVALID)
    result = await context.resolve(
        expression,
        ResolutionTarget(
            symbol=SymbolPath(segments=("environment", "baseUrl")),
            scope=attempt,
            cell_inputs=cell_inputs(prepared, work.trial.cell),
        ),
    )
    if type(result) is not Present or is_sensitive_value(result.value):
        return _resolution_failure(result) or _unavailable(_SHAPE_INVALID)
    return result.value.value if type(result.value) is JsonString else _unavailable(_SHAPE_INVALID)


def _environment_resource(prepared: PreparedRun, work: ExecutionWork) -> CompiledResource:
    return find_resource(
        prepared.project,
        ResourceKind.ENVIRONMENT,
        work.environment.identity.resource_id,
        owner=prepared.project.project,
        field="environments",
    )


def _resolved_value(
    result: object,
) -> tuple[ResolvedValue, tuple[TaintOriginId, ...]] | RequestPreparationFailure:
    """Reveal one already-registered value only within short-lived request lowering."""
    if type(result) is not ResolvedForWire:
        return _resolution_failure(result) or _unavailable(_SHAPE_INVALID)
    value = result.result.value
    if is_sensitive_value(value):
        revealed = cast("ResolvedValue", value.reveal(access=_REQUEST_ACCESS))
        return revealed, value.origins
    return cast("ResolvedValue", value), ()


def _routing(
    values: _ResolvedFields,
    origins: _FieldOrigins,
    base_url: str,
) -> tuple[str, str] | RequestPreparationFailure:
    method = _text(values.get("method"))
    if method is None:
        return _unavailable(_SHAPE_INVALID)
    if origins.get("method"):
        return _unavailable(_SENSITIVE_TARGET)
    explicit = _text(values.get("url"))
    field = "url"
    if explicit is None:
        explicit = _join_url(base_url, _text(values.get("endpoint")) or "/")
        field = "endpoint"
    if origins.get(field):
        return _unavailable(_SENSITIVE_TARGET)
    return method, explicit


def _effective_authentication(
    request: _ResolvedFields,
    request_origins: _FieldOrigins,
    environment: _ResolvedFields,
    environment_origins: _FieldOrigins,
    *,
    same_origin: bool,
) -> tuple[ResolvedValue | None, tuple[TaintOriginId, ...]]:
    if "auth" in request:
        return request["auth"], request_origins.get("auth", ())
    if same_origin and "auth" in environment:
        return environment["auth"], environment_origins.get("auth", ())
    return None, ()


def _absolute_url(
    raw: str,
    authored_query: tuple[QueryParameter, ...],
    provenance: ValueProvenance,
) -> AbsoluteHttpUrl:
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("request URL must be absolute HTTP(S)")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("request URL cannot contain user information or a fragment")
    embedded_query = tuple(
        QueryParameter(
            name=QueryName(value=name),
            value=QueryText(value=value),
            source=QuerySource.AUTHORED,
            provenance=provenance,
        )
        for name, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    )
    return AbsoluteHttpUrl(
        scheme=HttpScheme(parsed.scheme),
        authority=HttpAuthority(
            host=_host(parsed.hostname),
            port=HttpPort(value=parsed.port) if parsed.port is not None else None,
        ),
        path=_path(parsed.path, ()),
        query=QueryParameters(parameters=(*embedded_query, *authored_query)),
    )


def _same_origin(base: str, target: str) -> bool:
    base_url = urlsplit(base)
    target_url = urlsplit(target)
    return (
        base_url.scheme.lower(),
        (base_url.hostname or "").lower(),
        base_url.port or _default_port(base_url.scheme),
    ) == (
        target_url.scheme.lower(),
        (target_url.hostname or "").lower(),
        target_url.port or _default_port(target_url.scheme),
    )


def _default_port(scheme: str) -> int | None:
    return {"http": 80, "https": 443}.get(scheme.lower())


def _join_url(base: str, endpoint: str) -> str:
    parsed = urlsplit(base)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Environment base URL cannot contain query, fragment, or userinfo")
    prefix = parsed.path.rstrip("/")
    suffix = endpoint if endpoint.startswith("/") else "/" + endpoint
    path = prefix + suffix
    host = parsed.hostname or ""
    authority = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        authority += f":{parsed.port}"
    return f"{parsed.scheme}://{authority}{path}"


def _path(raw: str, origins: tuple[TaintOriginId, ...]) -> UrlPath:
    if not raw or raw == "/":
        return UrlPath()
    trailing = raw.endswith("/")
    segments: tuple[PathSegment, ...] = tuple(
        _sensitive(DecodedPathSegment(value=piece), origins) for piece in raw.strip("/").split("/")
    )
    return UrlPath(segments=segments, trailing_slash=trailing)


def _host(value: str) -> HttpHost:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return DnsHost(value=value.lower())
    return Ipv4Host(value=str(parsed)) if parsed.version == 4 else Ipv6Host(value=str(parsed))


def _text(value: ResolvedValue | None) -> str | None:
    return value.value if type(value) is JsonString else None


def _sensitive[ValueT](
    value: ValueT,
    origins: tuple[TaintOriginId, ...],
) -> ValueT | SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=origins) if origins else value


def _opaque_effective_fingerprint(request: EffectiveRequest) -> EffectiveRequestFingerprint:
    """Create a test/fake identity without opening any sensitivity-contained field value."""
    structural = msgspec.json.encode(
        {
            "logical": request.logical_request_fingerprint.digest.hex_value,
            "method": request.method.value,
            "scheme": request.url.scheme.value,
            "host": request.url.authority.host.value,
            "port": None
            if request.url.authority.port is None
            else request.url.authority.port.value,
            "headers": [field.name.value for field in request.headers.fields],
            "cookies": [cookie.name.value for cookie in request.cookies.cookies],
        },
        order="deterministic",
    )
    value = Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(structural).hexdigest()),
    )
    origins = effective_request_taint_origins(
        request.url,
        request.headers,
        request.cookies,
        request.body,
    )
    return SensitiveValue(value, origins=origins) if origins else value


def _is_expression(value: object) -> TypeGuard[ValueExpression]:
    return is_value_expression(value)


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


__all__ = (
    "DEFAULT_REQUEST_TIMEOUT",
    "ConfigurationRequestProvider",
    "RequestFinalizer",
)
