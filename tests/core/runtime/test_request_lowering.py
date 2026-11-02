"""Typed lowering of resolved v1alpha1 HTTP request values."""

from __future__ import annotations

import pytest

from comparo.core.domain.http.auth import ApiKeyAuthentication
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import NoAuthentication
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

    with pytest.raises(ValueError, match="stream mode must be auto"):
        validate_stream_mode(_text("sse"))


def test_lowering_rejects_uncontained_credentials_and_supports_all_auth_forms() -> None:
    origins = (TaintOriginId(value="test/auth-branches"),)

    with pytest.raises(ValueError, match="cookie values must be sensitivity-contained"):
        lower_cookies(_object(session=_text("secret")), (), _provenance())
    assert type(lower_authentication(_object(type=_text("none")), ())) is NoAuthentication
    with pytest.raises(ValueError, match="sensitivity-contained"):
        lower_authentication(_object(bearer=_text("secret")), ())

    basic = lower_authentication(
        _object(
            basic=_object(
                username=_text("user"),
                password=_text("secret"),
            )
        ),
        origins,
    )
    assert type(basic) is BasicAuthentication
    query_key = lower_authentication(
        _object(
            type=_text("apiKey"),
            **{"in": _text("query")},
            name=_text("key"),
            value=_text("secret"),
        ),
        origins,
    )
    cookie_key = lower_authentication(
        _object(
            type=_text("apiKey"),
            **{"in": _text("cookie")},
            name=_text("key"),
            value=_text("secret"),
        ),
        origins,
    )
    assert type(query_key) is ApiKeyAuthentication
    assert type(cookie_key) is ApiKeyAuthentication

    with pytest.raises(ValueError, match="authentication type is unsupported"):
        lower_authentication(_object(type=_text("digest")), origins)
    with pytest.raises(ValueError, match="placement must be header, query, or cookie"):
        lower_authentication(
            _object(
                type=_text("apiKey"),
                **{"in": _text("body")},
                name=_text("key"),
                value=_text("secret"),
            ),
            origins,
        )
    with pytest.raises(ValueError, match="authentication type is unsupported"):
        lower_authentication(_object(custom=_text("secret")), origins)


def test_body_lowering_rejects_every_malformed_variant() -> None:
    with pytest.raises(TypeError, match="JSON body requires a JsonValue"):
        lower_body(BytesValue(value=b"raw"), _text("json"), (), multipart_boundary="boundary")
    with pytest.raises(TypeError, match="bytes body requires explicit bytes output"):
        lower_body(_text("raw"), _text("bytes"), (), multipart_boundary="boundary")
    with pytest.raises(ValueError, match="request body type is unsupported"):
        lower_body(_text("raw"), _text("xml"), (), multipart_boundary="boundary")
    with pytest.raises(ValueError, match="ordered entry requires value"):
        lower_body(
            _array(_object(name=_text("missing"))),
            _text("form"),
            (),
            multipart_boundary="boundary",
        )
    with pytest.raises(TypeError, match="headers must be an object or ordered entry array"):
        lower_headers(_text("invalid"), (), _provenance())
    with pytest.raises(TypeError, match="headers must be an object"):
        lower_headers(_array(_text("invalid")), (), _provenance())

    malformed_multipart = (
        (_object(parts=_text("invalid")), TypeError, "ordered array"),
        (
            _object(parts=_array(_object(type=_text("binary"), name=_text("part")))),
            ValueError,
            "typed file",
        ),
        (
            _object(
                parts=_array(
                    _object(
                        type=_text("file"),
                        name=_text("part"),
                        mediaType=_text("text/plain"),
                        encoding=_text("hex"),
                    )
                )
            ),
            ValueError,
            "explicitly be base64",
        ),
        (
            _object(
                parts=_array(
                    _object(
                        type=_text("file"),
                        name=_text("part"),
                        mediaType=_text("text/plain"),
                        encoding=_text("base64"),
                        value=_text("!invalid!"),
                    )
                )
            ),
            ValueError,
            "base64 is invalid",
        ),
        (
            _object(
                parts=_array(
                    _object(
                        type=_text("text"),
                        name=_text("part"),
                        mediaType=_text("text/plain; charset=utf-8"),
                        value=_text("value"),
                    )
                )
            ),
            ValueError,
            "future structured form",
        ),
    )
    for value, error, match in malformed_multipart:
        with pytest.raises(error, match=match):
            lower_body(value, _text("multipart"), (), multipart_boundary="boundary")


def test_policy_lowering_covers_defaults_variants_and_invalid_shapes() -> None:
    defaulted = lower_request_policies(None, None, None, None, None, ())
    assert defaulted.timeouts.connect == defaulted.timeouts.execution_total

    with pytest.raises(ValueError, match="explicit unit"):
        lower_request_policies(_text("1"), None, None, None, None, ())
    with pytest.raises(ValueError, match="unknown phase"):
        lower_request_policies(_object(unknown=_text("1s")), None, None, None, None, ())
    with pytest.raises(TypeError, match="conditions must be an array"):
        lower_request_policies(
            None,
            _object(maxAttempts=JsonInteger(value=2), conditions=_text("failure")),
            None,
            None,
            None,
            (),
        )

    fixed = lower_request_policies(
        None,
        _object(
            maxAttempts=JsonInteger(value=2),
            conditions=_array(_text("connect-failure")),
            backoff=_object(type=_text("fixed"), delay=_text("1ms")),
            mutation=_object(type=_text("idempotency-key"), header=_text("Idempotency-Key")),
        ),
        None,
        None,
        None,
        (),
    )
    assert fixed.retries.max_attempts.value == 2

    no_backoff = lower_request_policies(
        None,
        _object(
            maxAttempts=JsonInteger(value=1),
            conditions=_array(),
            backoff=_object(type=_text("none")),
            mutation=_object(type=_text("none")),
        ),
        None,
        None,
        None,
        (),
    )
    assert no_backoff.retries.max_attempts.value == 1
    no_mutation = lower_request_policies(
        None,
        _object(maxAttempts=JsonInteger(value=1), conditions=_array()),
        None,
        None,
        None,
        (),
    )
    assert no_mutation.retries.max_attempts.value == 1

    for field, value, match in (
        (
            "backoff",
            _object(type=_text("linear")),
            "backoff type is unsupported",
        ),
        (
            "mutation",
            _object(type=_text("unsafe")),
            "mutation retry type is unsupported",
        ),
    ):
        retry_values: dict[str, JsonValue] = {
            "maxAttempts": JsonInteger(value=1),
            "conditions": _array(),
            field: value,
        }
        with pytest.raises(ValueError, match=match):
            lower_request_policies(None, _object(**retry_values), None, None, None, ())

    with pytest.raises(TypeError, match="retry maximum must be an integer"):
        lower_request_policies(
            None,
            _object(maxAttempts=_text("1"), conditions=_array()),
            None,
            None,
            None,
            (),
        )
    with pytest.raises(TypeError, match="policy flag must be boolean"):
        lower_request_policies(
            None,
            None,
            _object(maxHops=JsonInteger(value=1), allowCrossOrigin=_text("yes")),
            None,
            None,
            (),
        )


def test_proxy_lowering_rejects_unsafe_urls_and_unsupported_authentication() -> None:
    origins = (TaintOriginId(value="test/proxy-errors"),)
    invalid_proxies = (
        (_object(url=_text("relative")), "absolute HTTP"),
        (_object(url=_text("https://user@example.test")), "only scheme and authority"),
        (
            _object(
                url=_text("https://example.test"),
                auth=_object(
                    type=_text("apiKey"),
                    **{"in": _text("header")},
                    name=_text("x-key"),
                    value=_text("secret"),
                ),
            ),
            "supports none, basic, or bearer",
        ),
    )
    for proxy, match in invalid_proxies:
        with pytest.raises(ValueError, match=match):
            lower_request_policies(None, None, None, None, proxy, origins)
