"""Pure, sensitivity-preserving HTTP redirect interpretation and request rewriting."""

from __future__ import annotations

import hashlib
import ipaddress
from typing import cast
from urllib.parse import quote
from urllib.parse import unquote
from urllib.parse import urljoin
from urllib.parse import urlsplit

import msgspec

from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.bodies import FinalizedBody
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.fields import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.request_materialization import EffectiveRequestMaterializationIdentity
from comparo.core.domain.http.request_materialization import RequestMaterializationProof
from comparo.core.domain.http.request_materialization import request_envelope_taint_origins
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpHost
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import PathSegment
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.runtime.ports import NoRedirect
from comparo.core.runtime.ports import PreparedRedirect
from comparo.core.runtime.ports import RedirectFailure
from comparo.core.runtime.ports import RedirectResolution

_LOCATION_ACCESS = SensitiveValueAccess(purpose="http.redirect.location")
_PATH_ACCESS = SensitiveValueAccess(purpose="http.redirect.base-path")
_QUERY_ACCESS = SensitiveValueAccess(purpose="http.redirect.base-query")
_FINGERPRINT_ACCESS = SensitiveValueAccess(purpose="http.redirect.fingerprint")
_INVALID_REDIRECT = DiagnosticCode(value="http.redirect-invalid")
_FOLLOWABLE = frozenset({300, 301, 302, 303, 307, 308})
_PATH_SAFE = "!$&'()*+,-.:;=@_~"
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class StandardRedirectResolver:
    """Resolve RFC references and apply the accepted Comparo redirect safeguards."""

    def resolve(
        self,
        request: AuthorizedExchangeRequest,
        response: ResponseObservation,
    ) -> RedirectResolution:
        """Return a fully rewritten request without performing resolution or network I/O."""
        if type(request) is not AuthorizedExchangeRequest:
            raise TypeError("redirect resolver requires AuthorizedExchangeRequest")
        if type(response) is not ResponseObservation:
            raise TypeError("redirect resolver requires ResponseObservation")
        if response.status.value not in _FOLLOWABLE:
            return NoRedirect()
        locations = tuple(
            field for field in response.headers.fields if field.name.value == "location"
        )
        if not locations:
            return NoRedirect()
        if len(locations) != 1:
            return _invalid()
        raw_location = locations[0].value
        try:
            resolved = _resolve_location(
                request.request.url, raw_location, request.request.provenance
            )
            if not _redirect_allowed(request.request, resolved):
                return NoRedirect()
            rewritten = _rewrite_request(
                request.request,
                response.status.value,
                raw_location,
                resolved,
            )
            transition = FollowedRedirect(
                raw_location=raw_location,
                resolved_url=resolved,
            )
            return PreparedRedirect(request=rewritten, transition=transition)
        except (UnicodeError, TypeError, ValueError):
            return _invalid()


def _invalid() -> RedirectFailure:
    return RedirectFailure(
        failure=TransportFailure(
            phase=FailurePhase.PROTOCOL,
            code=_INVALID_REDIRECT,
            retryable=False,
        )
    )


def _resolve_location(
    base: AbsoluteHttpUrl,
    raw: HeaderText | SensitiveValue[HeaderText],
    provenance: ValueProvenance,
) -> AbsoluteHttpUrl:
    exact = raw.reveal(access=_LOCATION_ACCESS) if is_sensitive_value(raw) else raw
    if type(exact) is not HeaderText:  # pragma: no cover - ResponseField invariant
        raise TypeError("redirect Location must contain HeaderText")
    reference = urlsplit(exact.value)
    combined = urljoin(_url_text(base), exact.value)
    parsed = urlsplit(combined)
    if parsed.scheme not in {HttpScheme.HTTP.value, HttpScheme.HTTPS.value}:
        raise ValueError("redirect scheme is unsupported")
    if parsed.hostname is None or parsed.username is not None or parsed.password is not None:
        raise ValueError("redirect authority is invalid")
    inherited_origins = (
        base.taint_origins
        if reference.scheme == "" and reference.netloc == "" and not reference.path.startswith("/")
        else ()
    )
    origins = tuple(
        dict.fromkeys(
            (*raw.origins, *inherited_origins) if is_sensitive_value(raw) else inherited_origins
        )
    )
    return AbsoluteHttpUrl(
        scheme=HttpScheme(parsed.scheme),
        authority=HttpAuthority(
            host=_host(parsed.hostname),
            port=HttpPort(value=parsed.port) if parsed.port is not None else None,
        ),
        path=_path(parsed.path, origins),
        query=_query(parsed.query, origins, provenance),
    )


def _url_text(url: AbsoluteHttpUrl) -> str:
    host = url.authority.host.value
    if type(url.authority.host) is Ipv6Host:
        host = f"[{host}]"
    authority = host
    if url.authority.port is not None:
        authority = f"{authority}:{url.authority.port.value}"
    path = "/" + "/".join(_segment_text(segment) for segment in url.path.segments)
    if url.path.trailing_slash and url.path.segments:
        path += "/"
    query = "&".join(_query_text(item) for item in url.query.parameters)
    suffix = f"?{query}" if query else ""
    return f"{url.scheme.value}://{authority}{path}{suffix}"


def _segment_text(segment: PathSegment) -> str:
    exact = segment.reveal(access=_PATH_ACCESS) if is_sensitive_value(segment) else segment
    if type(exact) is EncodedPathSegment:
        return exact.value
    if type(exact) is DecodedPathSegment:
        return quote(exact.value, safe=_PATH_SAFE, encoding="utf-8", errors="strict")
    raise TypeError("redirect base path contains an unsupported segment")  # pragma: no cover


def _query_text(parameter: QueryParameter) -> str:
    name = quote(parameter.name.value, safe="-._~", encoding="utf-8", errors="strict")
    value = parameter.value
    if type(value) is BareQueryValue:
        return name
    exact = value.reveal(access=_QUERY_ACCESS) if is_sensitive_value(value) else value
    if type(exact) is not QueryText:  # pragma: no cover - QueryParameter invariant
        raise TypeError("redirect base query contains an unsupported value")
    return f"{name}={quote(exact.value, safe='-._~', encoding='utf-8', errors='strict')}"


def _path(raw: str, origins: tuple[TaintOriginId, ...]) -> UrlPath:
    if not raw or raw == "/":
        return UrlPath()
    pieces = raw.strip("/").split("/")
    if any(not piece for piece in pieces):
        raise ValueError("redirect path contains an ambiguous empty segment")
    trailing = raw.endswith("/")
    segments = tuple(_path_segment(piece, origins) for piece in pieces)
    return UrlPath(segments=segments, trailing_slash=trailing)


def _path_segment(raw: str, origins: tuple[TaintOriginId, ...]) -> PathSegment:
    canonical = _canonical_segment(raw)
    value = EncodedPathSegment(value=canonical)
    return SensitiveValue(value, origins=origins) if origins else value


def _canonical_segment(raw: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character == "%":
            escape = raw[index + 1 : index + 3]
            if len(escape) != 2 or any(part not in "0123456789abcdefABCDEF" for part in escape):
                raise ValueError("redirect path contains malformed percent encoding")
            octet = int(escape, 16)
            if octet < 32 or octet == 127:
                raise ValueError("redirect path contains an encoded control")
            decoded = chr(octet)
            output.append(decoded if decoded in _UNRESERVED else f"%{octet:02X}")
            index += 3
            continue
        if ord(character) < 128 and (character in _PATH_SAFE or character.isalnum()):
            output.append(character)
        else:
            output.append(quote(character, safe="", encoding="utf-8", errors="strict"))
        index += 1
    return "".join(output)


def _query(
    raw: str,
    origins: tuple[TaintOriginId, ...],
    provenance: ValueProvenance,
) -> QueryParameters:
    if not raw:
        return QueryParameters()
    parameters: list[QueryParameter] = []
    for part in raw.split("&"):
        if not part:
            raise ValueError("redirect query contains an empty occurrence")
        name, separator, value = part.partition("=")
        decoded_name = unquote(name, encoding="utf-8", errors="strict")
        decoded_value = unquote(value, encoding="utf-8", errors="strict")
        query_value: BareQueryValue | QueryText | SensitiveValue[QueryText]
        if not separator:
            query_value = BareQueryValue()
        else:
            text = QueryText(value=decoded_value)
            query_value = SensitiveValue(text, origins=origins) if origins else text
        parameters.append(
            QueryParameter(
                name=QueryName(value=decoded_name),
                value=query_value,
                source=QuerySource.AUTHORED,
                provenance=provenance,
            )
        )
    return QueryParameters(parameters=tuple(parameters))


def _host(value: str) -> HttpHost:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return DnsHost(value=value.encode("idna").decode("ascii").lower())
    return (
        Ipv4Host(value=parsed.compressed)
        if parsed.version == 4
        else Ipv6Host(value=parsed.compressed)
    )


def _same_origin(left: AbsoluteHttpUrl, right: AbsoluteHttpUrl) -> bool:
    return (
        left.scheme is right.scheme
        and left.authority.host == right.authority.host
        and effective_port(left) == effective_port(right)
    )


def _redirect_allowed(request: FinalizedWireRequest, resolved: AbsoluteHttpUrl) -> bool:
    policy = request.policies.redirects
    cross_origin = not _same_origin(request.url, resolved)
    if cross_origin and not policy.allow_cross_origin:
        return False
    if cross_origin and request.policies.tls.client_identity is not None:
        return False
    if (
        request.url.scheme is HttpScheme.HTTPS
        and resolved.scheme is HttpScheme.HTTP
        and policy.block_https_downgrade
    ):
        return False
    return not (
        cross_origin and policy.strip_sensitive_credentials and resolved.query.taint_origins
    )


def _rewrite_request(
    request: FinalizedWireRequest,
    status: int,
    raw_location: HeaderText | SensitiveValue[HeaderText],
    resolved: AbsoluteHttpUrl,
) -> FinalizedWireRequest:
    drop_body = status == 303 and request.method.value not in {"GET", "HEAD"}
    if (
        status in {301, 302}
        and request.policies.redirects.method_mode is RedirectMethodMode.BROWSER_COMPATIBLE
        and request.method.value == "POST"
    ):
        drop_body = True
    cross_origin = not _same_origin(request.url, resolved)
    strip = cross_origin and request.policies.redirects.strip_sensitive_credentials
    fields = request.headers.fields
    body: FinalizedBody
    if drop_body:
        fields = tuple(field for field in fields if field.source is not HeaderSource.BODY)
    if strip:
        fields = tuple(
            field
            for field in fields
            if field.source is not HeaderSource.AUTHENTICATION
            and field.name.value not in MANDATORY_SENSITIVE_HEADER_NAMES
            and not is_sensitive_value(field.value)
        )
    headers = RequestHeaders(fields=fields)
    cookies = RequestCookies() if strip else request.cookies
    method = msgspec.structs.replace(request.method, value="GET") if drop_body else request.method
    fingerprint = _redirect_fingerprint(
        request,
        status,
        raw_location,
        resolved,
        headers,
        cookies,
        drop_body=drop_body,
    )
    if drop_body:
        effective = EffectiveRequest(
            environment=request.environment,
            logical_request_fingerprint=request.logical_request_fingerprint,
            method=method,
            url=resolved,
            headers=headers,
            cookies=cookies,
            body=NoBody(),
            policies=request.policies,
            provenance=request.provenance,
        )
        body = NoMaterializedBody()
        proof = prove_request_materialization(
            effective,
            body,
            effective_request_fingerprint=fingerprint,
        )
    else:
        body = request.body
        proof = RequestMaterializationProof(
            identity=EffectiveRequestMaterializationIdentity(
                environment=request.environment,
                logical_request_fingerprint=request.logical_request_fingerprint,
                effective_request_fingerprint=fingerprint,
            ),
            input_body=request.materialization.input_body,
            body=body,
        )
    return FinalizedWireRequest(
        environment=request.environment,
        logical_request_fingerprint=request.logical_request_fingerprint,
        method=method,
        url=resolved,
        headers=headers,
        cookies=cookies,
        body=body,
        materialization=proof,
        policies=request.policies,
        provenance=request.provenance,
    )


def _raw_fingerprint(
    request: FinalizedWireRequest,
) -> Fingerprint | SensitiveValue[Fingerprint]:
    return request.materialization.identity.effective_request_fingerprint


def _redirect_fingerprint(
    request: FinalizedWireRequest,
    status: int,
    raw_location: HeaderText | SensitiveValue[HeaderText],
    resolved: AbsoluteHttpUrl,
    headers: RequestHeaders,
    cookies: RequestCookies,
    *,
    drop_body: bool,
) -> Fingerprint | SensitiveValue[Fingerprint]:
    previous = _raw_fingerprint(request)
    previous_exact: Fingerprint
    if is_sensitive_value(previous):
        previous_exact = cast("SensitiveValue[Fingerprint]", previous).reveal(
            access=_FINGERPRINT_ACCESS
        )
    else:
        previous_exact = cast("Fingerprint", previous)
    location: HeaderText
    if is_sensitive_value(raw_location):
        location = cast("SensitiveValue[HeaderText]", raw_location).reveal(access=_LOCATION_ACCESS)
    else:
        location = cast("HeaderText", raw_location)
    digest = hashlib.sha256(
        b"\0".join(
            (
                previous_exact.digest.hex_value.encode("ascii"),
                str(status).encode("ascii"),
                location.value.encode("latin-1"),
                b"drop" if drop_body else b"preserve",
            )
        )
    ).hexdigest()
    value = Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=digest),
    )
    origins = request_envelope_taint_origins(
        resolved,
        headers,
        cookies,
        () if drop_body else request.materialization.input_body.taint_origins,
    )
    return SensitiveValue(value, origins=origins) if origins else value
