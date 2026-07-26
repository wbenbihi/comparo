"""Pure lowering from resolved request values into validated HTTP domain objects."""

from __future__ import annotations

import base64
import re
from decimal import Decimal
from typing import cast
from urllib.parse import urlsplit

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import ApiKeyCredential
from comparo.core.domain.http.auth import ApiKeyPlacement
from comparo.core.domain.http.auth import Authentication
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicPassword
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BasicWireEncoding
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import BearerToken
from comparo.core.domain.http.auth import CookieApiKeyPlacement
from comparo.core.domain.http.auth import HeaderApiKeyPlacement
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.auth import QueryApiKeyPlacement
from comparo.core.domain.http.bodies import BodyText
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import FormField
from comparo.core.domain.http.bodies import FormName
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import MultipartBoundary
from comparo.core.domain.http.bodies import MultipartDispositionFilename
from comparo.core.domain.http.bodies import MultipartDispositionName
from comparo.core.domain.http.bodies import MultipartFilePart
from comparo.core.domain.http.bodies import MultipartPart
from comparo.core.domain.http.bodies import MultipartTextPart
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import DeclaredIdempotentRetry
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import FixedBackoff
from comparo.core.domain.http.policies import IdempotencyKeyRetry
from comparo.core.domain.http.policies import NoBackoff
from comparo.core.domain.http.policies import NoMutationRetry
from comparo.core.domain.http.policies import NoProxy
from comparo.core.domain.http.policies import ProxyTlsDisabled
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.policies import RedirectPolicy
from comparo.core.domain.http.policies import RequestPolicies
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.policies import TimeoutPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.http.policies import TrustStoreRef
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import is_json_value

DEFAULT_REQUEST_TIMEOUT = Duration(nanoseconds=30_000_000_000)
_DURATION = re.compile(r"(0|[1-9][0-9]*)(?:\.([0-9]+))?(ns|us|ms|s|m|h)")


def lower_headers(
    value: ResolvedValue | None,
    origins: tuple[TaintOriginId, ...],
    provenance: ValueProvenance,
) -> RequestHeaders:
    """Lower ergonomic map or ordered-entry forms without losing duplicates."""
    entries = _named_entries(value, label="headers")
    return RequestHeaders(
        fields=tuple(
            HeaderField(
                name=HeaderName(value=name.lower()),
                value=_tainted(HeaderText(value=_string(item, label="header value")), origins),
                source=HeaderSource.AUTHORED,
                provenance=provenance,
            )
            for name, item in entries
        )
    )


def lower_query(
    value: ResolvedValue | None,
    origins: tuple[TaintOriginId, ...],
    provenance: ValueProvenance,
) -> tuple[QueryParameter, ...]:
    """Lower ordered query occurrences while preserving bare null parameters."""
    entries = _named_entries(value, label="query")
    return tuple(
        QueryParameter(
            name=QueryName(value=name),
            value=(
                BareQueryValue()
                if type(item) is JsonNull
                else _tainted(QueryText(value=_string(item, label="query value")), origins)
            ),
            source=QuerySource.AUTHORED,
            provenance=provenance,
        )
        for name, item in entries
    )


def lower_cookies(
    value: ResolvedValue | None,
    origins: tuple[TaintOriginId, ...],
    provenance: ValueProvenance,
) -> RequestCookies:
    """Lower explicit cookies while requiring compiler/provider sensitivity lineage."""
    entries = _named_entries(value, label="cookies")
    if entries and not origins:
        raise ValueError("cookie values must be sensitivity-contained")
    return RequestCookies(
        cookies=tuple(
            RequestCookie(
                name=CookieName(value=name),
                value=SensitiveValue(
                    CookieText(value=_string(item, label="cookie value")),
                    origins=origins,
                ),
                source=CookieSource.EXPLICIT,
                provenance=provenance,
            )
            for name, item in entries
        )
    )


def merge_headers(environment: RequestHeaders, request: RequestHeaders) -> RequestHeaders:
    """Let Request-authored header names replace Environment defaults by name."""
    replaced = {field.name for field in request.fields}
    return RequestHeaders(
        fields=(
            *(field for field in environment.fields if field.name not in replaced),
            *request.fields,
        )
    )


def merge_cookies(environment: RequestCookies, request: RequestCookies) -> RequestCookies:
    """Let explicit Request cookies replace Environment defaults by name."""
    replaced = {cookie.name for cookie in request.cookies}
    return RequestCookies(
        cookies=(
            *(cookie for cookie in environment.cookies if cookie.name not in replaced),
            *request.cookies,
        )
    )


def lower_authentication(
    value: ResolvedValue | None,
    origins: tuple[TaintOriginId, ...],
) -> Authentication:
    """Lower tagged or concise bearer/basic/API-key authentication forms."""
    if value is None or type(value) is JsonNull:
        return NoAuthentication()
    fields, auth_type = _authentication_fields(value)
    if auth_type == "none":
        return NoAuthentication()
    if not origins:
        raise ValueError("authentication credentials must be sensitivity-contained")
    if auth_type == "bearer":
        return _bearer_authentication(fields, origins)
    if auth_type == "basic":
        return _basic_authentication(fields, origins)
    if auth_type == "apiKey":
        return _api_key_authentication(fields, origins)
    raise ValueError("authentication type is unsupported")


def _authentication_fields(value: ResolvedValue) -> tuple[dict[str, JsonValue], str | None]:
    fields = _object_fields(value, label="authentication")
    auth_type = _optional_string(fields.get("type"))
    if auth_type is not None or len(fields) != 1:
        return fields, auth_type
    concise_name, concise_value = next(iter(fields.items()))
    if concise_name == "bearer":
        return {"type": JsonString(value="bearer"), "token": concise_value}, "bearer"
    if concise_name == "basic":
        return {
            "type": JsonString(value="basic"),
            **_object_fields(concise_value, label="basic"),
        }, "basic"
    return fields, None


def _bearer_authentication(
    fields: dict[str, JsonValue],
    origins: tuple[TaintOriginId, ...],
) -> BearerAuthentication:
    return BearerAuthentication(
        token=SensitiveValue(
            BearerToken(value=_string(fields.get("token"), label="bearer token")),
            origins=origins,
        )
    )


def _basic_authentication(
    fields: dict[str, JsonValue],
    origins: tuple[TaintOriginId, ...],
) -> BasicAuthentication:
    return BasicAuthentication(
        username=SensitiveValue(
            BasicUsername(value=_string(fields.get("username"), label="basic username")),
            origins=origins,
        ),
        password=SensitiveValue(
            BasicPassword(value=_string(fields.get("password"), label="basic password")),
            origins=origins,
        ),
        wire_encoding=BasicWireEncoding.UTF_8,
    )


def _api_key_authentication(
    fields: dict[str, JsonValue],
    origins: tuple[TaintOriginId, ...],
) -> ApiKeyAuthentication:
    placement_name = _string(fields.get("in"), label="API-key placement")
    name = _string(fields.get("name"), label="API-key name")
    placement: ApiKeyPlacement
    if placement_name == "header":
        placement = HeaderApiKeyPlacement(name=HeaderName(value=name.lower()))
    elif placement_name == "query":
        placement = QueryApiKeyPlacement(name=QueryName(value=name))
    elif placement_name == "cookie":
        placement = CookieApiKeyPlacement(name=CookieName(value=name))
    else:
        raise ValueError("API-key placement must be header, query, or cookie")
    return ApiKeyAuthentication(
        credential=SensitiveValue(
            ApiKeyCredential(value=_string(fields.get("value"), label="API-key value")),
            origins=origins,
        ),
        placement=placement,
    )


def lower_body(
    value: ResolvedValue | None,
    body_type_value: ResolvedValue | None,
    origins: tuple[TaintOriginId, ...],
    *,
    multipart_boundary: str,
) -> RequestBody:
    """Lower the explicit request-body union without inferred value conversion."""
    if value is None or type(value) is JsonNull:
        return NoBody()
    body_type = "json" if body_type_value is None else _string(body_type_value, label="body type")
    if body_type == "json":
        if not is_json_value(value):
            raise TypeError("JSON body requires a JsonValue")
        return JsonBody(value=_tainted(value, origins))
    if body_type == "text":
        return TextBody(value=_tainted(BodyText(value=_string(value, label="text body")), origins))
    if body_type == "bytes":
        if type(value) is not BytesValue:
            raise TypeError("bytes body requires explicit bytes output")
        return BytesBody(value=_tainted(value, origins))
    if body_type == "form":
        return FormBody(
            fields=tuple(
                FormField(
                    name=FormName(value=name),
                    value=_tainted(BodyText(value=_string(item, label="form value")), origins),
                )
                for name, item in _named_entries(value, label="form")
            )
        )
    if body_type == "multipart":
        return _multipart(value, origins, default_boundary=multipart_boundary)
    raise ValueError("request body type is unsupported")


def lower_request_policies(
    timeout: ResolvedValue | None,
    retries: ResolvedValue | None,
    redirects: ResolvedValue | None,
    tls: JsonValue | None,
    proxy: ResolvedValue | None,
    proxy_origins: tuple[TaintOriginId, ...],
) -> RequestPolicies:
    """Lower explicit policy objects using versioned defaults only for absent fields."""
    return RequestPolicies(
        timeouts=_timeouts(timeout),
        retries=_retries(retries),
        tls=_tls(tls),
        proxy=_proxy(proxy, proxy_origins),
        redirects=_redirects(redirects),
    )


def validate_stream_mode(value: ResolvedValue | None) -> None:
    """Validate the v1 auto-detection declaration used by the response snapshot provider."""
    if value is None:
        return
    if type(value) is JsonString:
        if value.value == "auto":
            return
        raise ValueError("stream mode must be auto")
    fields = _object_fields(value, label="stream")
    if set(fields) != {"protocol"} or (
        _string(fields["protocol"], label="stream protocol") != "auto"
    ):
        raise ValueError("stream mode must declare only protocol: auto")


def _named_entries(
    value: ResolvedValue | None,
    *,
    label: str,
) -> tuple[tuple[str, JsonValue], ...]:
    if value is None:
        return ()
    if type(value) is JsonObject:
        return tuple((member.name, member.value) for member in value.members)
    if type(value) is not JsonArray:
        raise TypeError(f"{label} must be an object or ordered entry array")
    entries: list[tuple[str, JsonValue]] = []
    for item in value.items:
        fields = _object_fields(item, label=label)
        name = _string(fields.get("name", fields.get("key")), label=f"{label} name")
        if "value" not in fields:
            raise ValueError(f"{label} ordered entry requires value")
        entries.append((name, fields["value"]))
    return tuple(entries)


def _object_fields(value: object, *, label: str) -> dict[str, JsonValue]:
    if type(value) is not JsonObject:
        raise TypeError(f"{label} must be an object")
    return {member.name: member.value for member in value.members}


def _string(value: object, *, label: str) -> str:
    if type(value) is not JsonString:
        raise TypeError(f"{label} must be explicit text")
    return value.value


def _optional_string(value: object) -> str | None:
    return value.value if type(value) is JsonString else None


def _tainted[ValueT](
    value: ValueT,
    origins: tuple[TaintOriginId, ...],
) -> ValueT | SensitiveValue[ValueT]:
    return SensitiveValue(value, origins=origins) if origins else value


def _multipart(
    value: ResolvedValue,
    origins: tuple[TaintOriginId, ...],
    *,
    default_boundary: str,
) -> MultipartBody:
    fields = _object_fields(value, label="multipart body")
    boundary = _optional_string(fields.get("boundary")) or default_boundary
    raw_parts = fields.get("parts")
    if type(raw_parts) is not JsonArray:
        raise TypeError("multipart parts must be an ordered array")
    parts: list[MultipartPart] = []
    for item in raw_parts.items:
        part = _object_fields(item, label="multipart part")
        part_type = _string(part.get("type"), label="multipart part type")
        name = MultipartDispositionName(
            value=_string(part.get("name"), label="multipart part name")
        )
        media = _media_type(part.get("mediaType"))
        if part_type == "text":
            parts.append(
                MultipartTextPart(
                    name=name,
                    value=_tainted(
                        BodyText(value=_string(part.get("value"), label="multipart text")),
                        origins,
                    ),
                    media_type=media,
                )
            )
            continue
        if part_type != "file" or media is None:
            raise ValueError("multipart part must be text or a typed file")
        encoding = _string(part.get("encoding"), label="multipart file encoding")
        if encoding != "base64":
            raise ValueError("multipart file encoding must explicitly be base64")
        try:
            content = base64.b64decode(
                _string(part.get("value"), label="multipart file value"),
                validate=True,
            )
        except ValueError as error:
            raise ValueError("multipart file base64 is invalid") from error
        filename = MultipartDispositionFilename(
            value=_string(part.get("filename"), label="multipart filename")
        )
        parts.append(
            MultipartFilePart(
                name=name,
                filename=_tainted(filename, origins),
                value=_tainted(BytesValue(value=content), origins),
                media_type=media,
            )
        )
    return MultipartBody(boundary=MultipartBoundary(value=boundary), parts=tuple(parts))


def _media_type(value: object) -> MediaType | None:
    if value is None:
        return None
    raw = _string(value, label="media type")
    if ";" in raw or raw.count("/") != 1:
        raise ValueError("media type parameters require a future structured form")
    type_name, subtype = raw.split("/", 1)
    return MediaType(type=type_name.lower(), subtype=subtype.lower())


def _duration(value: object) -> Duration:
    raw = _string(value, label="duration")
    match = _DURATION.fullmatch(raw)
    if match is None:
        raise ValueError("duration requires an explicit unit")
    whole, fraction, unit = match.groups()
    amount = Decimal(whole + ("." + fraction if fraction else ""))
    seconds = (
        amount
        * {
            "ns": Decimal("0.000000001"),
            "us": Decimal("0.000001"),
            "ms": Decimal("0.001"),
            "s": Decimal(1),
            "m": Decimal(60),
            "h": Decimal(3600),
        }[unit]
    )
    return Duration.from_seconds(seconds)


def _timeouts(value: ResolvedValue | None) -> TimeoutPolicy:
    names = {
        "connect": "connect",
        "tlsHandshake": "tls_handshake",
        "responseHeaders": "response_headers",
        "idle": "idle",
        "attemptTotal": "attempt_total",
        "executionTotal": "execution_total",
    }
    values = dict.fromkeys(names.values(), DEFAULT_REQUEST_TIMEOUT)
    if type(value) is JsonString:
        duration = _duration(value)
        values = dict.fromkeys(values, duration)
    elif value is not None:
        fields = _object_fields(value, label="timeout")
        if not set(fields) <= set(names):
            raise ValueError("timeout contains an unknown phase")
        for field, item in fields.items():
            values[names[field]] = _duration(item)
    return TimeoutPolicy(
        connect=values["connect"],
        tls_handshake=values["tls_handshake"],
        response_headers=values["response_headers"],
        idle=values["idle"],
        attempt_total=values["attempt_total"],
        execution_total=values["execution_total"],
    )


def _retries(value: ResolvedValue | None) -> RetryPolicy:
    if value is None:
        return RetryPolicy(
            max_attempts=PositiveCount(value=1),
            conditions=(),
            backoff=NoBackoff(),
            jitter=Ratio(numerator=0, denominator=PositiveCount(value=1)),
        )
    fields = _object_fields(value, label="retries")
    maximum = _integer(fields.get("maxAttempts"), label="retry maximum")
    conditions_value = fields.get("conditions")
    if type(conditions_value) is not JsonArray:
        raise TypeError("retry conditions must be an array")
    conditions = tuple(
        RetryCondition(_string(item, label="retry condition")) for item in conditions_value.items
    )
    backoff = _backoff(fields.get("backoff"))
    jitter = _ratio(fields.get("jitter"))
    mutation = _mutation_retry(fields.get("mutation"))
    return RetryPolicy(
        max_attempts=PositiveCount(value=maximum),
        conditions=conditions,
        backoff=backoff,
        jitter=jitter,
        mutation_retry=mutation,
    )


def _backoff(value: object) -> NoBackoff | FixedBackoff | ExponentialBackoff:
    if value is None:
        return NoBackoff()
    fields = _object_fields(value, label="retry backoff")
    kind = _string(fields.get("type"), label="retry backoff type")
    if kind == "none":
        return NoBackoff()
    if kind == "fixed":
        return FixedBackoff(delay=_duration(fields.get("delay")))
    if kind == "exponential":
        return ExponentialBackoff(
            initial=_duration(fields.get("initial")),
            multiplier=_ratio(fields.get("multiplier")),
            maximum=_duration(fields.get("maximum")),
        )
    raise ValueError("retry backoff type is unsupported")


def _ratio(value: object) -> Ratio:
    if value is None:
        return Ratio(numerator=0, denominator=PositiveCount(value=1))
    fields = _object_fields(value, label="ratio")
    return Ratio(
        numerator=_integer(fields.get("numerator"), label="ratio numerator"),
        denominator=PositiveCount(
            value=_integer(fields.get("denominator"), label="ratio denominator")
        ),
    )


def _mutation_retry(
    value: object,
) -> NoMutationRetry | IdempotencyKeyRetry | DeclaredIdempotentRetry:
    if value is None:
        return NoMutationRetry()
    fields = _object_fields(value, label="mutation retry")
    kind = _string(fields.get("type"), label="mutation retry type")
    if kind == "none":
        return NoMutationRetry()
    if kind == "declared-idempotent":
        return DeclaredIdempotentRetry()
    if kind == "idempotency-key":
        return IdempotencyKeyRetry(
            header_name=HeaderName(
                value=_string(fields.get("header"), label="idempotency header").lower()
            )
        )
    raise ValueError("mutation retry type is unsupported")


def _redirects(value: ResolvedValue | None) -> RedirectPolicy:
    if value is None:
        return RedirectPolicy(max_hops=Count(value=0))
    fields = _object_fields(value, label="redirects")
    return RedirectPolicy(
        max_hops=Count(value=_integer(fields.get("maxHops"), label="redirect maximum")),
        method_mode=RedirectMethodMode(
            _optional_string(fields.get("methodMode")) or RedirectMethodMode.STRICT_PRESERVE.value
        ),
        allow_cross_origin=_boolean(fields.get("allowCrossOrigin"), default=False),
        strip_sensitive_credentials=_boolean(fields.get("stripSensitiveCredentials"), default=True),
        block_https_downgrade=_boolean(fields.get("blockHttpsDowngrade"), default=True),
    )


def _tls(value: JsonValue | None) -> TlsPolicy:
    fields = {} if value is None else _object_fields(value, label="TLS")
    trust = _optional_string(fields.get("trust")) or TrustSource.OPERATING_SYSTEM.value
    trust_store = _optional_string(fields.get("customTrustStore"))
    client_identity = _optional_string(fields.get("clientIdentity"))
    return TlsPolicy(
        verify_certificates=_boolean(fields.get("verifyCertificates"), default=True),
        verify_hostname=_boolean(fields.get("verifyHostname"), default=True),
        minimum_version=TlsVersion(
            _optional_string(fields.get("minimumVersion")) or TlsVersion.TLS_1_2.value
        ),
        trust_source=TrustSource(trust),
        custom_trust_store=None if trust_store is None else TrustStoreRef(value=trust_store),
        client_identity=(
            None if client_identity is None else ClientIdentityRef(value=client_identity)
        ),
    )


def _proxy(
    value: ResolvedValue | None,
    origins: tuple[TaintOriginId, ...],
) -> NoProxy | ExplicitProxy:
    if value is None or type(value) is JsonNull:
        return NoProxy()
    fields = _object_fields(value, label="proxy")
    parsed = urlsplit(_string(fields.get("url"), label="proxy URL"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("proxy URL must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query:
        raise ValueError("proxy URL must contain only scheme and authority")
    candidate_auth = lower_authentication(fields.get("auth"), origins)
    if type(candidate_auth) not in {NoAuthentication, BasicAuthentication, BearerAuthentication}:
        raise ValueError("proxy authentication supports none, basic, or bearer")
    authentication = cast(
        "NoAuthentication | BasicAuthentication | BearerAuthentication",
        candidate_auth,
    )
    tls = (
        ProxyTlsPolicy(policy=_tls(fields.get("tls")))
        if parsed.scheme == "https"
        else ProxyTlsDisabled()
    )
    return ExplicitProxy(
        scheme=HttpScheme(parsed.scheme),
        authority=HttpAuthority(
            host=DnsHost(value=parsed.hostname.lower()),
            port=HttpPort(value=parsed.port) if parsed.port is not None else None,
        ),
        authentication=authentication,
        tls=tls,
    )


def _integer(value: object, *, label: str) -> int:
    if type(value) is not JsonInteger:
        raise TypeError(f"{label} must be an integer")
    return value.value


def _boolean(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not JsonBoolean:
        raise TypeError("policy flag must be boolean")
    return value.value


__all__ = (
    "DEFAULT_REQUEST_TIMEOUT",
    "lower_authentication",
    "lower_body",
    "lower_cookies",
    "lower_headers",
    "lower_query",
    "lower_request_policies",
    "merge_cookies",
    "merge_headers",
    "validate_stream_mode",
)
