"""Typed lowering of resolved v1alpha1 HTTP request values."""

from __future__ import annotations

import pytest

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import FormBody
from comparo.core.domain.http.bodies import JsonBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import TextBody
from comparo.core.domain.http.policies import DeclaredIdempotentRetry
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ExponentialBackoff
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.policies import TlsVersion
from comparo.core.domain.http.policies import TrustSource
from comparo.core.domain.primitives import Duration
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.runtime.request_lowering import lower_authentication
from comparo.core.runtime.request_lowering import lower_body
from comparo.core.runtime.request_lowering import lower_cookies
from comparo.core.runtime.request_lowering import lower_headers
from comparo.core.runtime.request_lowering import lower_query
from comparo.core.runtime.request_lowering import lower_request_policies
from comparo.core.runtime.request_lowering import validate_stream_mode


def _object(**values: JsonValue) -> JsonObject:
    return JsonObject(
        members=tuple(JsonObjectMember(name=name, value=value) for name, value in values.items())
    )


def _array(*values: JsonValue) -> JsonArray:
    return JsonArray(items=values)


def _text(value: str) -> JsonString:
    return JsonString(value=value)


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def test_ordered_headers_query_and_sensitive_cookies_preserve_occurrences() -> None:
    provenance = _provenance()
    origin = (TaintOriginId(value="test/credential"),)
    headers = lower_headers(
        _array(
            _object(name=_text("X-Test"), value=_text("one")),
            _object(name=_text("X-Test"), value=_text("two")),
        ),
        (),
        provenance,
    )
    query = lower_query(
        _array(
            _object(name=_text("tag"), value=_text("one")),
            _object(name=_text("tag"), value=JsonNull()),
        ),
        (),
        provenance,
    )
    cookies = lower_cookies(_object(session=_text("secret")), origin, provenance)

    assert [field.name.value for field in headers.fields] == ["x-test", "x-test"]
    assert [item.name.value for item in query] == ["tag", "tag"]
    assert is_sensitive_value(cookies.cookies[0].value)


def test_authentication_supports_bearer_and_explicit_api_key_placement() -> None:
    origins = (TaintOriginId(value="test/auth"),)
    bearer = lower_authentication(_object(bearer=_text("token")), origins)
    api_key = lower_authentication(
        _object(
            type=_text("apiKey"),
            **{"in": _text("header")},
            name=_text("X-Api-Key"),
            value=_text("credential"),
        ),
        origins,
    )

    assert type(bearer) is BearerAuthentication
    assert type(api_key) is ApiKeyAuthentication
    assert is_sensitive_value(api_key.credential)


def test_every_request_body_variant_lowers_without_inference() -> None:
    origin = (TaintOriginId(value="test/body"),)
    json_body = lower_body(
        _object(message=_text("hello")),
        None,
        origin,
        multipart_boundary="comparo-test",
    )
    text_body = lower_body(
        _text("hello"),
        _text("text"),
        (),
        multipart_boundary="comparo-test",
    )
    bytes_body = lower_body(
        BytesValue(value=b"hello"),
        _text("bytes"),
        (),
        multipart_boundary="comparo-test",
    )
    form_body = lower_body(
        _object(name=_text("comparo")),
        _text("form"),
        (),
        multipart_boundary="comparo-test",
    )
    multipart_body = lower_body(
        _object(
            parts=_array(
                _object(type=_text("text"), name=_text("note"), value=_text("hello")),
                _object(
                    type=_text("file"),
                    name=_text("data"),
                    filename=_text("data.txt"),
                    mediaType=_text("text/plain"),
                    encoding=_text("base64"),
                    value=_text("aGVsbG8="),
                ),
            )
        ),
        _text("multipart"),
        (),
        multipart_boundary="comparo-test",
    )

    assert type(json_body) is JsonBody
    assert is_sensitive_value(json_body.value)
    assert type(text_body) is TextBody
    assert type(bytes_body) is BytesBody
    assert type(form_body) is FormBody
    assert type(multipart_body) is MultipartBody
    assert len(multipart_body.parts) == 2


def test_timeout_retry_redirect_tls_and_proxy_policies_lower_together() -> None:
    origins = (TaintOriginId(value="test/proxy"),)
    policies = lower_request_policies(
        _object(
            connect=_text("250ms"),
            attemptTotal=_text("2s"),
            executionTotal=_text("4s"),
        ),
        _object(
            maxAttempts=JsonInteger(value=3),
            conditions=_array(_text("connect-failure"), _text("status-5xx")),
            backoff=_object(
                type=_text("exponential"),
                initial=_text("10ms"),
                multiplier=_object(
                    numerator=JsonInteger(value=2),
                    denominator=JsonInteger(value=1),
                ),
                maximum=_text("1s"),
            ),
            jitter=_object(
                numerator=JsonInteger(value=1),
                denominator=JsonInteger(value=10),
            ),
            mutation=_object(type=_text("declared-idempotent")),
        ),
        _object(
            maxHops=JsonInteger(value=4),
            methodMode=_text("browser-compatible"),
            allowCrossOrigin=JsonBoolean(value=True),
            stripSensitiveCredentials=JsonBoolean(value=True),
            blockHttpsDowngrade=JsonBoolean(value=True),
        ),
        _object(
            verifyCertificates=JsonBoolean(value=True),
            verifyHostname=JsonBoolean(value=True),
            minimumVersion=_text("tls-1.3"),
            trust=_text("custom"),
            customTrustStore=_text("internal-ca"),
            clientIdentity=_text("client-one"),
        ),
        _object(
            url=_text("https://proxy.example:8443"),
            auth=_object(bearer=_text("proxy-token")),
            tls=_object(minimumVersion=_text("tls-1.2")),
        ),
        origins,
    )

    assert type(policies.timeouts.connect) is Duration
    assert policies.timeouts.connect.nanoseconds == 250_000_000
    assert policies.retries.max_attempts.value == 3
    assert type(policies.retries.backoff) is ExponentialBackoff
    assert type(policies.retries.mutation_retry) is DeclaredIdempotentRetry
    assert policies.redirects.method_mode is RedirectMethodMode.BROWSER_COMPATIBLE
    assert policies.tls.minimum_version is TlsVersion.TLS_1_3
    assert policies.tls.trust_source is TrustSource.CUSTOM
    assert policies.tls.custom_trust_store is not None
    assert policies.tls.client_identity is not None
    assert type(policies.proxy) is ExplicitProxy
    assert type(policies.proxy.tls) is ProxyTlsPolicy


def test_stream_mode_is_explicitly_limited_to_v1_auto_detection() -> None:
    validate_stream_mode(None)
    validate_stream_mode(_text("auto"))
    validate_stream_mode(_object(protocol=_text("auto")))

    with pytest.raises(ValueError, match="stream mode must declare only protocol: auto"):
        validate_stream_mode(_object(protocol=_text("sse")))
