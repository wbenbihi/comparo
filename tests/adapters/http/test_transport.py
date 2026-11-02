"""Production HTTP transport unit contracts and failure classification tests."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import cast
from uuid import UUID

import httpcore
import msgspec
import pytest

from comparo.adapters.http.capture import DefaultCaptureBudgetProvider
from comparo.adapters.http.capture import ResponseCaptureLimits
from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import BodyStoreFailureCode
from comparo.adapters.http.storage import StoredBodyStream
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.storage import TransientBodyStoreConfig
from comparo.adapters.http.timing import ExchangeTraceRecorder
from comparo.adapters.http.tls import ExplicitTlsContextProvider
from comparo.adapters.http.tls import TlsConfigurationError
from comparo.adapters.http.tls import TlsConfigurationFailureCode
from comparo.adapters.http.tls import TlsContextProvider
from comparo.adapters.http.transport import HttpcoreTransport
from comparo.adapters.http.transport import HttpcoreTransportConfig
from comparo.adapters.http.transport import ResponseClassification
from comparo.adapters.http.transport import _announces_trailers
from comparo.adapters.http.transport import _connect_destination
from comparo.adapters.http.transport import _duration_seconds
from comparo.adapters.http.transport import _local_endpoint
from comparo.adapters.http.transport import _map_failure
from comparo.adapters.http.transport import _no_body_reason
from comparo.adapters.http.transport import _PoolKey
from comparo.adapters.http.transport import _protocol
from comparo.adapters.http.transport import _proxy
from comparo.adapters.http.transport import _proxy_headers
from comparo.adapters.http.transport import _reason
from comparo.adapters.http.transport import _response_fields
from comparo.adapters.http.transport import _routed_url
from comparo.adapters.http.transport import _seconds
from comparo.adapters.http.transport import _tls_layers
from comparo.adapters.http.transport import _tls_observation
from comparo.adapters.http.wire import RequestWireRenderer
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BasicWireEncoding
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import NoProxy
from comparo.core.domain.http.policies import ProxyTlsDisabled
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import UnlimitedTimeout
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timing_validation import validate_exchange_timing_truth
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsMetadata
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.transport import TlsUnavailable
from comparo.core.domain.http.transport import TlsUnavailableReason
from comparo.core.domain.http.transport import TlsVersion
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.identity import BodyId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import bearer_secret
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import password_secret
from tests.core.domain.http.factories import policies
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request


def _capture_limits() -> ResponseCaptureLimits:
    return ResponseCaptureLimits(
        maximum_encoded_body_bytes=ByteCount(value=1_024),
        maximum_entity_body_bytes=ByteCount(value=1_024),
        maximum_expansion_ratio=Ratio(numerator=10, denominator=PositiveCount(value=1)),
    )


def _config() -> HttpcoreTransportConfig:
    limits = _capture_limits()
    return HttpcoreTransportConfig(
        maximum_connections=PositiveCount(value=4),
        maximum_keepalive_connections=Count(value=2),
        maximum_authorization_occurrences=PositiveCount(value=100),
        keepalive_expiry=Duration(nanoseconds=1_000_000_000),
        capture=limits,
        classification=ResponseClassification(body=Sensitivity.PUBLIC),
    )


def _store(tmp_path: Path) -> TransientBodyStore:
    return TransientBodyStore(
        TransientBodyStoreConfig(
            memory_threshold=ByteCount(value=8),
            maximum_transient_bytes=ByteCount(value=4_096),
            io_chunk_size=PositiveCount(value=4),
            root=tmp_path,
        )
    )


def _transport(tmp_path: Path) -> tuple[HttpcoreTransport, TransientBodyStore]:
    store = _store(tmp_path)
    config = _config()
    return (
        HttpcoreTransport(
            body_store=store,
            config=config,
            tls=ExplicitTlsContextProvider(),
            budgets=DefaultCaptureBudgetProvider(config.capture, default_budget_origin()),
        ),
        store,
    )


class _ExtraInfoStream:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get_extra_info(self, name: str) -> object:
        return self.values.get(name)


class _TlsProvider:
    def __init__(self) -> None:
        self.policies: list[object] = []
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def create(self, policy: object) -> ssl.SSLContext:
        self.policies.append(policy)
        return self.context


class _SslObject:
    session_reused = True

    def __init__(
        self,
        *,
        version: str | None = "TLSv1.3",
        cipher: tuple[str, str, int] | None = ("TLS_AES_128_GCM_SHA256", "TLSv1.3", 128),
        certificate: bytes | None = b"certificate",
    ) -> None:
        self._version = version
        self._cipher = cipher
        self._certificate = certificate

    def version(self) -> str | None:
        return self._version

    def cipher(self) -> tuple[str, str, int] | None:
        return self._cipher

    def getpeercert(self, binary_form: bool = False) -> bytes | None:
        assert binary_form
        return self._certificate


class _Pool:
    def __init__(self, *, failure: bool = False, gate: asyncio.Event | None = None) -> None:
        self.failure = failure
        self.gate = gate
        self.closed = False

    async def aclose(self) -> None:
        if self.gate is not None:
            await self.gate.wait()
        self.closed = True
        if self.failure:
            raise RuntimeError("secret pool detail")

    def is_closed(self) -> bool:
        return self.closed


class _BodySource:
    def open_request(self, source: RepeatableByteSource) -> StoredBodyStream:
        del source
        raise AssertionError("body-free request must not open a body source")


def _proxy_policy(
    authentication: NoAuthentication | BearerAuthentication | BasicAuthentication,
    *,
    scheme: HttpScheme = HttpScheme.HTTP,
) -> ExplicitProxy:
    return ExplicitProxy(
        scheme=scheme,
        authority=HttpAuthority(
            host=DnsHost(value="proxy.example.test"),
            port=HttpPort(value=8080),
        ),
        authentication=authentication,
        tls=(
            ProxyTlsDisabled()
            if scheme is HttpScheme.HTTP
            else ProxyTlsPolicy(policy=policies().tls)
        ),
    )


def _proxy_request(
    *,
    proxy_scheme: HttpScheme = HttpScheme.HTTP,
    target_scheme: HttpScheme = HttpScheme.HTTPS,
) -> tuple[AuthorizedExchangeRequest, ProxyResolvedRoute]:
    effective = request(scheme=target_scheme)
    policy = _proxy_policy(NoAuthentication(), scheme=proxy_scheme)
    effective = msgspec.structs.replace(
        effective,
        policies=msgspec.structs.replace(effective.policies, proxy=policy),
    )
    proxy_destination = destination(
        host="proxy.example.test",
        address="192.0.2.20",
        scheme=proxy_scheme,
        port=8080,
    )
    route = ProxyResolvedRoute(
        proxy_destination=proxy_destination,
        target=ClientResolvedTarget(
            destination=destination(
                scheme=target_scheme,
                port=443 if target_scheme is HttpScheme.HTTPS else 80,
            )
        ),
    )
    return authorized_request(effective, route=route), route


def _pool_key(environment_id: str) -> _PoolKey:
    effective = request(environment_id=environment_id)
    return _PoolKey(
        environment=effective.environment,
        route=DirectResolvedRoute(destination=destination()),
        target_tls=effective.policies.tls,
        proxy=effective.policies.proxy,
    )


def test_transport_configuration_and_constructor_boundaries(tmp_path: Path) -> None:
    classification = ResponseClassification(
        body=Sensitivity.SENSITIVE,
        sensitive_headers=(HeaderName(value="x-private"),),
    )
    assert classification.body is Sensitivity.SENSITIVE
    with pytest.raises(TypeError, match="body classification"):
        ResponseClassification(body=cast("Sensitivity", object()))
    with pytest.raises(TypeError, match="immutable HeaderName"):
        ResponseClassification(
            body=Sensitivity.PUBLIC, sensitive_headers=cast("tuple[HeaderName, ...]", [])
        )
    with pytest.raises(ValueError, match="unique"):
        ResponseClassification(
            body=Sensitivity.PUBLIC,
            sensitive_headers=(HeaderName(value="x-one"), HeaderName(value="x-one")),
        )

    config = _config()
    invalid_values = (
        {"maximum_connections": cast("PositiveCount", object())},
        {"maximum_keepalive_connections": cast("Count", object())},
        {"maximum_authorization_occurrences": cast("PositiveCount", object())},
        {"keepalive_expiry": cast("Duration", object())},
        {"capture": cast("ResponseCaptureLimits", object())},
        {"classification": cast("ResponseClassification", object())},
    )
    for changes in invalid_values:
        with pytest.raises(TypeError):
            msgspec.structs.replace(config, **changes)
    with pytest.raises(ValueError, match="cannot exceed"):
        msgspec.structs.replace(config, maximum_keepalive_connections=Count(value=5))

    store = _store(tmp_path)
    budget = DefaultCaptureBudgetProvider(config.capture, default_budget_origin())
    with pytest.raises(TypeError, match="TransientBodyStore"):
        HttpcoreTransport(
            body_store=cast("TransientBodyStore", object()),
            config=config,
            tls=ExplicitTlsContextProvider(),
            budgets=budget,
        )
    with pytest.raises(TypeError, match="HttpcoreTransportConfig"):
        HttpcoreTransport(
            body_store=store,
            config=cast("HttpcoreTransportConfig", object()),
            tls=ExplicitTlsContextProvider(),
            budgets=budget,
        )
    with pytest.raises(TypeError, match="TlsContextProvider"):
        HttpcoreTransport(
            body_store=store,
            config=config,
            tls=cast("TlsContextProvider", object()),
            budgets=budget,
        )
    asyncio.run(store.close())


def test_route_proxy_and_auth_rendering_are_exact() -> None:
    direct = DirectResolvedRoute(destination=destination())
    assert _connect_destination(direct) == direct.destination
    outbound, proxy_route = _proxy_request()
    assert _connect_destination(proxy_route) == proxy_route.proxy_destination

    rendered = RequestWireRenderer(_BodySource()).render(outbound.request)
    assert _routed_url(rendered, direct) is rendered.url
    routed = _routed_url(rendered, proxy_route)
    assert routed.host == b"192.0.2.10"
    assert routed.port == 443
    assert routed.target == rendered.url.target

    provider = _TlsProvider()
    assert _proxy(NoProxy(), provider) is None
    bearer = _proxy_policy(BearerAuthentication(token=bearer_secret("token")))
    bearer_value = _proxy(bearer, provider)
    assert bearer_value is not None
    assert bearer_value.headers == [(b"Proxy-Authorization", b"Bearer token")]
    basic = _proxy_policy(
        BasicAuthentication(
            username=BasicUsername(value="user"),
            password=password_secret("password"),
            wire_encoding=BasicWireEncoding.UTF_8,
        )
    )
    assert _proxy_headers(basic) == ((b"Proxy-Authorization", b"Basic dXNlcjpwYXNzd29yZA=="),)
    assert _proxy_headers(_proxy_policy(NoAuthentication())) == ()
    secure = _proxy_policy(NoAuthentication(), scheme=HttpScheme.HTTPS)
    secure_value = _proxy(secure, provider)
    assert secure_value is not None
    assert secure_value.ssl_context is provider.context
    assert provider.policies == [cast(ProxyTlsPolicy, secure.tls).policy]


def test_response_metadata_helpers_preserve_or_reject_exact_facts() -> None:
    fields = _response_fields(
        [(b"X-Repeat", b"one"), (b"X-Repeat", b"two"), (b"Set-Cookie", b"secret")],
        "a" * 32,
        (HeaderName(value="x-repeat"),),
    )
    assert [field.name.value for field in fields.fields] == ["x-repeat", "x-repeat", "set-cookie"]
    assert all(str(field.value) == "<sensitive>" for field in fields.fields)
    assert _announces_trailers([(b"Trailer", b"Digest")])
    assert not _announces_trailers([(b"Trailer", b" "), (b"X", b"value")])

    for raw, expected in (
        (b"HTTP/1.0", HttpProtocol.HTTP_1_0),
        (b"HTTP/1.1", HttpProtocol.HTTP_1_1),
        (b"HTTP/2", HttpProtocol.HTTP_2),
    ):
        response = httpcore.Response(200, extensions={"http_version": raw})
        assert _protocol(response) is expected
    with pytest.raises(TypeError, match="version must be bytes"):
        _protocol(httpcore.Response(200))
    with pytest.raises(ValueError, match="unsupported"):
        _protocol(httpcore.Response(200, extensions={"http_version": b"HTTP/3"}))

    h1 = httpcore.Response(
        200,
        extensions={"http_version": b"HTTP/1.1", "reason_phrase": b"All Good"},
    )
    reason = _reason(h1, HttpProtocol.HTTP_1_1)
    assert reason is not None
    assert reason.value == "All Good"
    assert _reason(h1, HttpProtocol.HTTP_2) is None
    assert _reason(httpcore.Response(200), HttpProtocol.HTTP_1_1) is None
    with pytest.raises(TypeError, match="reason phrase"):
        _reason(
            httpcore.Response(200, extensions={"reason_phrase": "wrong"}),
            HttpProtocol.HTTP_1_1,
        )


@pytest.mark.parametrize(
    ("method", "status", "expected"),
    [
        ("HEAD", 200, NoBodyReason.HEAD_REQUEST),
        ("CONNECT", 200, NoBodyReason.CONNECT_TUNNEL),
        ("GET", 101, NoBodyReason.SWITCHING_PROTOCOLS),
        ("GET", 204, NoBodyReason.STATUS_204),
        ("GET", 205, NoBodyReason.STATUS_205),
        ("GET", 304, NoBodyReason.STATUS_304),
        ("GET", 200, None),
    ],
)
def test_no_body_semantics(method: str, status: int, expected: NoBodyReason | None) -> None:
    assert _no_body_reason(method, status) is expected


def test_endpoint_and_tls_helpers_are_explicit() -> None:
    assert _local_endpoint(None) == (None, None)
    assert _local_endpoint(_ExtraInfoStream({"client_addr": "wrong"})) == (None, None)
    assert _local_endpoint(_ExtraInfoStream({"client_addr": ("127.0.0.1", 1234)})) == (
        Ipv4Host(value="127.0.0.1"),
        HttpPort(value=1234),
    )
    assert _local_endpoint(_ExtraInfoStream({"client_addr": ("2001:db8::1", 443, 0, 0)})) == (
        Ipv6Host(value="2001:db8::1"),
        HttpPort(value=443),
    )
    assert _local_endpoint(_ExtraInfoStream({"client_addr": ("invalid", 80)})) == (None, None)

    policy = policies().tls
    unavailable = _tls_observation(None, HttpProtocol.HTTP_1_1, policy)
    assert type(unavailable) is TlsUnavailable
    assert unavailable.reason is TlsUnavailableReason.HANDSHAKE_METADATA_LOST
    for invalid in (
        _SslObject(version="TLSv1.1"),
        _SslObject(cipher=None),
        _SslObject(certificate=None),
    ):
        assert type(_tls_observation(invalid, HttpProtocol.HTTP_1_1, policy)) is TlsUnavailable
    observed = _tls_observation(_SslObject(), HttpProtocol.HTTP_2, policy)
    assert type(observed) is TlsMetadata
    assert observed.version is TlsVersion.TLS_1_3
    assert observed.alpn is HttpProtocol.HTTP_2
    assert observed.session_resumed

    direct_http = authorized_request(request(scheme=HttpScheme.HTTP))
    response = httpcore.Response(200, extensions={"network_stream": _ExtraInfoStream({})})
    assert (
        type(_tls_layers(direct_http, response, HttpProtocol.HTTP_1_1).connect) is TlsNotPerformed
    )

    direct_https = authorized_request(request())
    secure_response = httpcore.Response(
        200,
        extensions={"network_stream": _ExtraInfoStream({"ssl_object": _SslObject()})},
    )
    assert (
        type(_tls_layers(direct_https, secure_response, HttpProtocol.HTTP_2).connect) is TlsMetadata
    )

    proxy_http, _route = _proxy_request(proxy_scheme=HttpScheme.HTTP)
    layers = _tls_layers(proxy_http, secure_response, HttpProtocol.HTTP_2)
    assert type(layers.connect) is TlsNotPerformed
    assert type(layers.origin) is TlsMetadata

    proxy_https, _route = _proxy_request(proxy_scheme=HttpScheme.HTTPS)
    layers = _tls_layers(proxy_https, secure_response, HttpProtocol.HTTP_2)
    assert type(layers.connect) is TlsUnavailable
    assert layers.connect.reason is TlsUnavailableReason.INSTRUMENTATION_UNSUPPORTED
    assert type(layers.origin) is TlsMetadata

    proxy_clear, _route = _proxy_request(
        proxy_scheme=HttpScheme.HTTPS,
        target_scheme=HttpScheme.HTTP,
    )
    layers = _tls_layers(proxy_clear, secure_response, HttpProtocol.HTTP_1_1)
    assert type(layers.connect) is TlsMetadata
    assert layers.origin is None


@pytest.mark.parametrize(
    ("error", "phase", "code", "retryable"),
    [
        (asyncio.CancelledError(), FailurePhase.CANCELLATION, "http.transport-cancelled", False),
        (
            TlsConfigurationError(TlsConfigurationFailureCode.INVALID_MATERIAL),
            FailurePhase.TLS,
            "http.tls-failed",
            True,
        ),
        (httpcore.ProxyError("x"), FailurePhase.PROXY, "http.proxy-failed", True),
        (
            httpcore.PoolTimeout("x"),
            FailurePhase.CONNECT,
            "http.connection-pool-timeout",
            True,
        ),
        (httpcore.ConnectTimeout("x"), FailurePhase.CONNECT, "http.connect-timeout", True),
        (httpcore.ConnectError("x"), FailurePhase.CONNECT, "http.connect-failed", True),
        (
            httpcore.WriteTimeout("x"),
            FailurePhase.REQUEST_WRITE,
            "http.request-write-timeout",
            False,
        ),
        (
            httpcore.WriteError("x"),
            FailurePhase.REQUEST_WRITE,
            "http.request-write-failed",
            False,
        ),
        (
            BodyStoreError(BodyStoreFailureCode.IO_FAILED),
            FailurePhase.REQUEST_WRITE,
            "http.request-write-failed",
            False,
        ),
        (
            httpcore.ReadTimeout("x"),
            FailurePhase.RESPONSE_HEADERS_TIMEOUT,
            "http.response-headers-timeout",
            True,
        ),
        (
            httpcore.ReadError("x"),
            FailurePhase.RESPONSE_HEADERS,
            "http.response-headers-failed",
            False,
        ),
        (
            httpcore.ProtocolError("x"),
            FailurePhase.PROTOCOL,
            "http.protocol-failed",
            False,
        ),
        (OSError(), FailurePhase.PROTOCOL, "http.transport-failed", False),
    ],
)
def test_dependency_failures_map_to_stable_value_free_outcomes(
    error: BaseException,
    phase: FailurePhase,
    code: str,
    retryable: bool,
) -> None:
    failure = _map_failure(error, ExchangeTraceRecorder(idle_timeout=None))
    assert failure.phase is phase
    assert failure.code.value == code
    assert failure.retryable is retryable


def test_connect_errors_during_tls_are_classified_as_tls() -> None:
    async def scenario() -> None:
        trace = ExchangeTraceRecorder(idle_timeout=None)
        await trace.trace("connection.start_tls.started", {})
        for error in (httpcore.ConnectTimeout("x"), httpcore.ConnectError("x")):
            failure = _map_failure(error, trace)
            assert failure.phase is FailurePhase.TLS
            assert failure.code.value == "http.tls-failed"

    asyncio.run(scenario())


def test_duration_conversion_preserves_unlimited_and_fractional_values() -> None:
    assert _duration_seconds(Duration(nanoseconds=1_500_000_000)) == 1.5
    assert _seconds(Duration(nanoseconds=250_000_000)) == 0.25
    assert _seconds(UnlimitedTimeout()) is None


def test_observe_no_body_and_announced_trailer_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport, store = _transport(tmp_path)
        outbound = authorized_request(request(scheme=HttpScheme.HTTP))
        trace = ExchangeTraceRecorder(idle_timeout=None)
        response = httpcore.Response(
            204,
            headers=[(b"Trailer", b"Digest")],
            extensions={
                "http_version": b"HTTP/1.1",
                "reason_phrase": b"No Content",
                "network_stream": _ExtraInfoStream({"client_addr": ("127.0.0.1", 50123)}),
            },
        )
        observed, failure = await transport._observe_response(outbound, response, trace)
        assert type(observed) is ResponseObservation
        assert type(observed.body) is NoResponseBody
        assert observed.body.reason is NoBodyReason.STATUS_204
        assert failure is not None
        assert failure.code.value == "http.response-trailers-unavailable"
        assert observed.endpoint.local_address == Ipv4Host(value="127.0.0.1")
        await transport.close()
        await store.close()

    asyncio.run(scenario())


def test_exchange_contains_internal_errors_and_rejects_invalid_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        transport, store = _transport(tmp_path)
        with pytest.raises(TypeError, match="AuthorizedExchangeRequest"):
            await transport.exchange(cast("AuthorizedExchangeRequest", object()))

        def fail_render(self: RequestWireRenderer, value: object) -> object:
            del self, value
            raise ValueError("secret request value")

        monkeypatch.setattr(RequestWireRenderer, "render", fail_render)
        result = await transport.exchange(authorized_request(request()))
        assert result.response is None
        assert result.failure is not None
        assert result.failure.code.value == "http.transport-failed"
        await transport.close()
        with pytest.raises(RuntimeError, match="closed"):
            await transport.exchange(authorized_request(request()))
        with pytest.raises(BodyStoreError) as storage_error:
            await store.begin(
                BodyId(value=UUID(int=991)),
                maximum_body_bytes=ByteCount(value=1),
                sensitivity=Sensitivity.PUBLIC,
                origins=(),
            )
        assert storage_error.value.code is BodyStoreFailureCode.CLOSED
        await store.close()

    asyncio.run(scenario())


def test_authorization_occurrence_is_atomically_consumed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        transport, store = _transport(tmp_path)
        canary = "transport-ledger-secret-canary"
        effective = msgspec.structs.replace(
            request(),
            headers=RequestHeaders(
                fields=(
                    HeaderField(
                        name=HeaderName(value="x-api-key"),
                        value=SensitiveValue(
                            HeaderText(value=canary),
                            origins=(TaintOriginId(value="transport-ledger-test"),),
                        ),
                        source=HeaderSource.AUTHORED,
                        provenance=provenance(),
                    ),
                )
            ),
        )
        outbound = authorized_request(effective)
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def perform(
            self: HttpcoreTransport,
            request_value: AuthorizedExchangeRequest,
            trace: ExchangeTraceRecorder,
        ) -> tuple[None, TransportFailure]:
            nonlocal calls
            del self, request_value, trace
            calls += 1
            entered.set()
            await release.wait()
            return None, TransportFailure(
                phase=FailurePhase.PROTOCOL,
                code=DiagnosticCode(value="http.test-terminal"),
                retryable=False,
            )

        monkeypatch.setattr(HttpcoreTransport, "_perform_exchange", perform)
        first = asyncio.create_task(transport.exchange(outbound))
        await entered.wait()
        concurrent_reuse = await transport.exchange(outbound)
        assert concurrent_reuse.response is None
        assert concurrent_reuse.failure is not None
        assert concurrent_reuse.failure.phase is FailurePhase.POLICY
        assert concurrent_reuse.failure.code.value == (
            "http.policy-authorization-occurrence-consumed"
        )
        assert not concurrent_reuse.failure.retryable
        validate_exchange_timing_truth(
            outbound,
            concurrent_reuse.response,
            concurrent_reuse.failure,
            concurrent_reuse.timings,
        )

        release.set()
        first_result = await first
        assert first_result.failure is not None
        assert first_result.failure.code.value == "http.test-terminal"

        sequential_reuse = await transport.exchange(outbound)
        assert sequential_reuse.response is None
        assert sequential_reuse.failure == concurrent_reuse.failure
        assert calls == 1
        assert canary not in repr(transport._consumed_authorizations)

        await transport.close()
        assert not transport._consumed_authorizations
        await store.close()

    asyncio.run(scenario())


def test_authorization_ledger_binds_policy_plan_and_occurrence_with_finite_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        store = _store(tmp_path)
        config = msgspec.structs.replace(
            _config(),
            maximum_authorization_occurrences=PositiveCount(value=4),
        )
        transport = HttpcoreTransport(
            body_store=store,
            config=config,
            tls=ExplicitTlsContextProvider(),
            budgets=DefaultCaptureBudgetProvider(config.capture, default_budget_origin()),
        )
        base = authorized_request(request())
        base_intent = base.authorization.intent
        plan_bound = AuthorizedExchangeRequest(
            authorization=msgspec.structs.replace(
                base.authorization,
                intent=msgspec.structs.replace(
                    base_intent,
                    plan_fingerprint=fingerprint("b"),
                ),
            )
        )
        policy_authority = authority("c")
        policy_bound = AuthorizedExchangeRequest(
            authorization=msgspec.structs.replace(
                base.authorization,
                intent=msgspec.structs.replace(
                    base_intent,
                    effective_policy_fingerprint=fingerprint("c"),
                ),
                authority=policy_authority,
            )
        )
        calls = 0

        async def perform(
            self: HttpcoreTransport,
            request_value: AuthorizedExchangeRequest,
            trace: ExchangeTraceRecorder,
        ) -> tuple[None, TransportFailure]:
            nonlocal calls
            del self, request_value, trace
            calls += 1
            return None, TransportFailure(
                phase=FailurePhase.PROTOCOL,
                code=DiagnosticCode(value="http.test-terminal"),
                retryable=False,
            )

        monkeypatch.setattr(HttpcoreTransport, "_perform_exchange", perform)
        for outbound in (base, plan_bound, policy_bound):
            result = await transport.exchange(outbound)
            assert result.failure is not None
            assert result.failure.code.value == "http.test-terminal"
        assert calls == 3

        different_occurrence = authorized_request(
            request(),
            request_occurrence=occurrence(exchange=2),
        )
        different_result = await transport.exchange(different_occurrence)
        assert different_result.failure is not None
        assert different_result.failure.code.value == "http.test-terminal"
        assert calls == 4
        assert len(transport._consumed_authorizations) == 4

        beyond_capacity = authorized_request(
            request(),
            request_occurrence=occurrence(exchange=3),
        )
        exhausted = await transport.exchange(beyond_capacity)
        assert exhausted.response is None
        assert exhausted.failure is not None
        assert exhausted.failure.phase is FailurePhase.RESOURCE_BUDGET
        assert exhausted.failure.code.value == "http.authorization-ledger-capacity-exhausted"
        assert not exhausted.failure.retryable
        assert calls == 4

        await transport.close()
        await store.close()

    asyncio.run(scenario())


def test_exchange_converts_pre_header_cancellation_to_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        transport, store = _transport(tmp_path)

        def cancel_render(self: RequestWireRenderer, value: object) -> object:
            del self, value
            raise asyncio.CancelledError

        monkeypatch.setattr(RequestWireRenderer, "render", cancel_render)
        result = await transport.exchange(authorized_request(request()))
        assert result.response is None
        assert result.failure is not None
        assert result.failure.phase is FailurePhase.CANCELLATION
        await transport.close()
        await store.close()

    asyncio.run(scenario())


def test_close_waits_for_active_work_is_idempotent_and_attempts_every_pool(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport, store = _transport(tmp_path)
        first = _Pool()
        second = _Pool(failure=True)
        transport._pools[_pool_key("one")] = cast("httpcore.AsyncConnectionPool", first)
        transport._pools[_pool_key("two")] = cast("httpcore.AsyncConnectionPool", second)
        await transport._admit(authorized_request(request()))
        closing = asyncio.create_task(transport.close())
        await asyncio.sleep(0)
        assert not first.closed
        await transport._release()
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await closing
        assert first.is_closed()
        assert second.is_closed()
        await transport.close()
        await store.close()

    asyncio.run(scenario())


def test_cancelled_close_finishes_cleanup_before_propagating(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport, store = _transport(tmp_path)
        gate = asyncio.Event()
        pool = _Pool(gate=gate)
        transport._pools[_pool_key("cancelled")] = cast("httpcore.AsyncConnectionPool", pool)
        closing = asyncio.create_task(transport.close())
        await asyncio.sleep(0)
        follower = asyncio.create_task(transport.close())
        await asyncio.sleep(0)
        assert not follower.done()
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await follower
        assert pool.is_closed()
        await transport.close()
        await store.close()

    asyncio.run(scenario())
