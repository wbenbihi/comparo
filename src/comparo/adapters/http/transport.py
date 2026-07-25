"""Environment-isolated production HTTP/1.1 and HTTP/2 transport adapter."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Protocol
from typing import cast

import httpcore
import msgspec

from comparo.adapters.http.capture import CaptureBudgetProvider
from comparo.adapters.http.capture import ResponseBodyCapturer
from comparo.adapters.http.capture import ResponseCaptureLimits
from comparo.adapters.http.network import RoutePinnedNetworkBackend
from comparo.adapters.http.storage import BodyStoreError
from comparo.adapters.http.storage import TransientBodyStore
from comparo.adapters.http.timing import ExchangeTraceRecorder
from comparo.adapters.http.tls import TlsConfigurationError
from comparo.adapters.http.tls import TlsContextProvider
from comparo.adapters.http.wire import RenderedWireRequest
from comparo.adapters.http.wire import RequestWireRenderer
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.auth import BasicAuthentication
from comparo.core.domain.http.auth import BasicUsername
from comparo.core.domain.http.auth import BearerAuthentication
from comparo.core.domain.http.auth import NoAuthentication
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.fields import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ProxyPolicy
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import TimeoutLimit
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import UnlimitedTimeout
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import ResponseBody
from comparo.core.domain.http.response_metadata import derive_content_encodings
from comparo.core.domain.http.response_metadata import derive_media_type
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.responses import ReasonPhrase
from comparo.core.domain.http.responses import ResponseField
from comparo.core.domain.http.responses import ResponseFields
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.transport import EndpointMetadata
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import HttpProtocol
from comparo.core.domain.http.transport import TlsLayers
from comparo.core.domain.http.transport import TlsMetadata
from comparo.core.domain.http.transport import TlsNotPerformed
from comparo.core.domain.http.transport import TlsObservation
from comparo.core.domain.http.transport import TlsUnavailable
from comparo.core.domain.http.transport import TlsUnavailableReason
from comparo.core.domain.http.transport import TlsVersion
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import ClientResolvedTarget
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import HttpPort
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import Ipv4Host
from comparo.core.domain.http.url import Ipv6Host
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.runtime.ports import HttpTransportResult

_PROXY_ACCESS = SensitiveValueAccess(purpose="http.proxy.authentication")

_CONNECT_TIMEOUT = DiagnosticCode(value="http.connect-timeout")
_POOL_TIMEOUT = DiagnosticCode(value="http.connection-pool-timeout")
_CONNECT_FAILED = DiagnosticCode(value="http.connect-failed")
_PROXY_FAILED = DiagnosticCode(value="http.proxy-failed")
_TLS_FAILED = DiagnosticCode(value="http.tls-failed")
_REQUEST_WRITE_TIMEOUT = DiagnosticCode(value="http.request-write-timeout")
_REQUEST_WRITE_FAILED = DiagnosticCode(value="http.request-write-failed")
_RESPONSE_HEADERS_TIMEOUT = DiagnosticCode(value="http.response-headers-timeout")
_RESPONSE_HEADERS_FAILED = DiagnosticCode(value="http.response-headers-failed")
_PROTOCOL_FAILED = DiagnosticCode(value="http.protocol-failed")
_TRAILERS_UNAVAILABLE = DiagnosticCode(value="http.response-trailers-unavailable")
_TRANSPORT_CANCELLED = DiagnosticCode(value="http.transport-cancelled")
_TRANSPORT_FAILED = DiagnosticCode(value="http.transport-failed")


@dataclass(frozen=True, slots=True)
class ResponseClassification:
    """Environment-effective sensitivity classification for captured response data."""

    body: Sensitivity
    sensitive_headers: tuple[HeaderName, ...] = ()

    def __post_init__(self) -> None:
        """Require one closed body class and unique typed header names."""
        if type(self.body) is not Sensitivity:
            raise TypeError("response body classification must be Sensitivity")
        if type(self.sensitive_headers) is not tuple or any(
            type(item) is not HeaderName for item in self.sensitive_headers
        ):
            raise TypeError("sensitive response headers must be an immutable HeaderName tuple")
        if len(set(self.sensitive_headers)) != len(self.sensitive_headers):
            raise ValueError("sensitive response header classifications must be unique")


class HttpcoreTransportConfig(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned pool, retention, and classification controls."""

    maximum_connections: PositiveCount
    maximum_keepalive_connections: Count
    keepalive_expiry: Duration
    capture: ResponseCaptureLimits
    classification: ResponseClassification

    def __post_init__(self) -> None:
        """Require exact finite controls without imposing application-level maxima."""
        if type(self.maximum_connections) is not PositiveCount:
            raise TypeError("HTTP maximum connections must be PositiveCount")
        if type(self.maximum_keepalive_connections) is not Count:
            raise TypeError("HTTP maximum keepalive connections must be Count")
        if self.maximum_keepalive_connections.value > self.maximum_connections.value:
            raise ValueError("HTTP keepalive connections cannot exceed total connections")
        if type(self.keepalive_expiry) is not Duration:
            raise TypeError("HTTP keepalive expiry must be Duration")
        if type(self.capture) is not ResponseCaptureLimits:
            raise TypeError("HTTP capture config must be ResponseCaptureLimits")
        if type(self.classification) is not ResponseClassification:
            raise TypeError("HTTP response classification must be typed")


@dataclass(frozen=True, slots=True)
class _PoolKey:
    environment: DefinitionRef
    route: ResolvedRoute
    target_tls: TlsPolicy
    proxy: ProxyPolicy


class HttpcoreTransport:
    """Perform one authorized exchange with no implicit redirect, retry, or ambient state."""

    __slots__ = (
        "_active",
        "_body_store",
        "_capture",
        "_closed",
        "_closing",
        "_condition",
        "_config",
        "_connection_ids",
        "_next_connection_id",
        "_pool_lock",
        "_pools",
        "_renderer",
        "_tls",
    )

    def __init__(
        self,
        *,
        body_store: TransientBodyStore,
        config: HttpcoreTransportConfig,
        tls: TlsContextProvider,
        budgets: CaptureBudgetProvider,
    ) -> None:
        """Compose narrow storage, TLS, and budget ports into one run-local adapter."""
        if type(body_store) is not TransientBodyStore:
            raise TypeError("HTTP transport requires TransientBodyStore")
        if type(config) is not HttpcoreTransportConfig:
            raise TypeError("HTTP transport requires HttpcoreTransportConfig")
        if not hasattr(tls, "create"):
            raise TypeError("HTTP transport requires TlsContextProvider")
        self._body_store = body_store
        self._config = config
        self._tls = tls
        self._renderer = RequestWireRenderer(body_store)
        self._capture = ResponseBodyCapturer(
            store=body_store,
            limits=config.capture,
            budgets=budgets,
        )
        self._pools: dict[_PoolKey, httpcore.AsyncConnectionPool] = {}
        self._connection_ids: dict[tuple[DefinitionRef, int], str] = {}
        self._next_connection_id = 1
        self._pool_lock = asyncio.Lock()
        self._condition = asyncio.Condition()
        self._active = 0
        self._closing = False
        self._closed = False

    async def exchange(self, request: AuthorizedExchangeRequest) -> HttpTransportResult:
        """Execute exactly one route-pinned request and retain every available terminal fact."""
        if type(request) is not AuthorizedExchangeRequest:
            raise TypeError("HTTP transport accepts only AuthorizedExchangeRequest")
        await self._admit()
        trace = ExchangeTraceRecorder(idle_timeout=_seconds(request.request.policies.timeouts.idle))
        response_observation: ResponseObservation | None = None
        failure: TransportFailure | None = None
        try:
            wire = self._renderer.render(request.request)
            pool = await self._pool(request)
            extensions = self._extensions(request, trace)
            async with pool.stream(
                wire.method,
                _routed_url(wire, request.route),
                headers=wire.headers,
                content=wire.content,
                extensions=extensions,
            ) as response:
                response_observation, failure = await self._observe_response(
                    request,
                    response,
                    trace,
                )
        except asyncio.CancelledError as error:
            failure = _map_failure(error, trace)
        except (
            BodyStoreError,
            TlsConfigurationError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.ProxyError,
            httpcore.TimeoutException,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            failure = _map_failure(error, trace)
        finally:
            await self._release()
        timings = trace.timings(
            route=request.route,
            response=response_observation,
            failure=failure,
        )
        return HttpTransportResult(
            response=response_observation,
            failure=failure,
            timings=timings,
        )

    async def close(self) -> None:
        """Reject new exchanges, await in-flight work, and close every isolated pool."""
        async with self._condition:
            if self._closed:
                return
            if self._closing:
                await self._condition.wait_for(lambda: self._closed)
                return
            self._closing = True
            await self._condition.wait_for(lambda: self._active == 0)
        async with self._pool_lock:
            pools = tuple(self._pools.values())
            self._pools.clear()
        cleanup = asyncio.create_task(self._finish_close(pools))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _finish_close(self, pools: tuple[httpcore.AsyncConnectionPool, ...]) -> None:
        """Attempt every pool close before publishing the terminal lifecycle state."""
        results = await asyncio.gather(*(pool.aclose() for pool in pools), return_exceptions=True)
        async with self._condition:
            self._closed = True
            self._condition.notify_all()
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("HTTP connection-pool cleanup failed")

    async def _admit(self) -> None:
        async with self._condition:
            if self._closing or self._closed:
                raise RuntimeError("HTTP transport is closed")
            self._active += 1

    async def _release(self) -> None:
        async with self._condition:
            self._active -= 1
            self._condition.notify_all()

    async def _pool(
        self,
        request: AuthorizedExchangeRequest,
    ) -> httpcore.AsyncConnectionPool:
        key = _PoolKey(
            environment=request.request.environment,
            route=request.route,
            target_tls=request.request.policies.tls,
            proxy=request.request.policies.proxy,
        )
        async with self._pool_lock:
            existing = self._pools.get(key)
            if existing is not None:
                return existing
            destination = _connect_destination(request.route)
            pool = httpcore.AsyncConnectionPool(
                ssl_context=(
                    self._tls.create(request.request.policies.tls)
                    if request.request.url.scheme is HttpScheme.HTTPS
                    else None
                ),
                proxy=_proxy(request.request.policies.proxy, self._tls),
                max_connections=self._config.maximum_connections.value,
                max_keepalive_connections=self._config.maximum_keepalive_connections.value,
                keepalive_expiry=_duration_seconds(self._config.keepalive_expiry),
                http1=True,
                http2=True,
                retries=0,
                network_backend=RoutePinnedNetworkBackend(
                    destination,
                    tls_timeout=_seconds(request.request.policies.timeouts.tls_handshake),
                ),
            )
            self._pools[key] = pool
            return pool

    def _extensions(
        self,
        request: AuthorizedExchangeRequest,
        trace: ExchangeTraceRecorder,
    ) -> dict[str, object]:
        timeouts = request.request.policies.timeouts
        return {
            "timeout": {
                "pool": _seconds(timeouts.connect),
                "connect": _seconds(timeouts.connect),
                "write": _seconds(timeouts.attempt_total),
                "read": _seconds(timeouts.response_headers),
            },
            "trace": trace.trace,
            "sni_hostname": request.request.url.authority.host.value,
        }

    async def _observe_response(
        self,
        request: AuthorizedExchangeRequest,
        response: httpcore.Response,
        trace: ExchangeTraceRecorder,
    ) -> tuple[ResponseObservation, TransportFailure | None]:
        protocol = _protocol(response)
        headers = _response_fields(
            response.headers,
            request.occurrence.exchange_id.value.hex,
            self._config.classification.sensitive_headers,
        )
        media = derive_media_type(headers)
        encodings, encoding_error = derive_content_encodings(headers)
        endpoint = self._endpoint(request, response, trace)
        tls = _tls_layers(request, response, protocol)
        reason = _reason(response, protocol)
        no_body = _no_body_reason(request.request.method.value, response.status)
        body: ResponseBody
        if no_body is not None:
            body = NoResponseBody(reason=no_body)
            failure = None
        else:
            origins = (
                (
                    TaintOriginId(
                        value=f"runtime/http-response/{request.occurrence.exchange_id.value.hex}"
                    ),
                )
                if self._config.classification.body is Sensitivity.SENSITIVE
                else ()
            )
            captured = await self._capture.capture(
                response,
                request=request,
                media=media,
                encodings=encodings,
                encoding_valid=encoding_error is None,
                sensitivity=self._config.classification.body,
                origins=origins,
                first_byte=trace.mark_first_body_byte,
            )
            body = captured.body
            failure = captured.failure
        if failure is None and _announces_trailers(response.headers):
            failure = TransportFailure(
                phase=FailurePhase.PROTOCOL,
                code=_TRAILERS_UNAVAILABLE,
                retryable=False,
            )
        return (
            ResponseObservation(
                protocol=protocol,
                status=HttpStatus(value=response.status),
                reason=reason,
                informational=(),
                headers=headers,
                trailers=ResponseFields(),
                media=media,
                content_encodings=encodings,
                content_encoding_error=encoding_error,
                body=body,
                endpoint=endpoint,
                tls=tls,
            ),
            failure,
        )

    def _endpoint(
        self,
        request: AuthorizedExchangeRequest,
        response: httpcore.Response,
        trace: ExchangeTraceRecorder,
    ) -> EndpointMetadata:
        destination = _connect_destination(request.route)
        stream = response.extensions.get("network_stream")
        socket = stream.get_extra_info("socket") if stream is not None else None
        marker = id(socket if socket is not None else stream if stream is not None else response)
        key = (request.request.environment, marker)
        connection_id = self._connection_ids.get(key)
        if connection_id is None:
            connection_id = f"connection-{self._next_connection_id}"
            self._next_connection_id += 1
            self._connection_ids[key] = connection_id
        local_address, local_port = _local_endpoint(stream)
        return EndpointMetadata(
            destination=destination,
            remote_address=destination.selected_address,
            remote_port=destination.port,
            local_address=local_address,
            local_port=local_port,
            connection_id=connection_id,
            connection_reused=not trace.started("connect_tcp"),
        )


def _connect_destination(route: ResolvedRoute) -> NetworkDestination:
    return route.destination if type(route) is DirectResolvedRoute else route.proxy_destination


def _routed_url(wire: RenderedWireRequest, route: ResolvedRoute) -> httpcore.URL:
    if type(route) is not ProxyResolvedRoute or type(route.target) is not ClientResolvedTarget:
        return wire.url
    destination = route.target.destination
    return httpcore.URL(
        scheme=wire.url.scheme,
        host=destination.selected_address.value.encode("ascii"),
        port=destination.port.value,
        target=wire.url.target,
    )


def _proxy(policy: ProxyPolicy, tls: TlsContextProvider) -> httpcore.Proxy | None:
    if type(policy) is not ExplicitProxy:
        return None
    proxy_tls = policy.tls
    ssl_context = tls.create(proxy_tls.policy) if type(proxy_tls) is ProxyTlsPolicy else None
    return httpcore.Proxy(
        url=httpcore.URL(
            scheme=policy.scheme.value.encode("ascii"),
            host=policy.authority.host.value.encode("ascii"),
            port=policy.authority.port.value if policy.authority.port is not None else None,
            target=b"/",
        ),
        headers=_proxy_headers(policy),
        ssl_context=ssl_context,
    )


def _proxy_headers(proxy: ExplicitProxy) -> tuple[tuple[bytes, bytes], ...]:
    authentication = proxy.authentication
    if type(authentication) is NoAuthentication:
        return ()
    if type(authentication) is BearerAuthentication:
        token = authentication.token.reveal(access=_PROXY_ACCESS)
        return ((b"Proxy-Authorization", f"Bearer {token.value}".encode("ascii")),)
    if type(authentication) is BasicAuthentication:
        username = authentication.username
        exact_username = (
            username.reveal(access=_PROXY_ACCESS) if is_sensitive_value(username) else username
        )
        exact_username = cast(BasicUsername, exact_username)
        password = authentication.password.reveal(access=_PROXY_ACCESS)
        wire = f"{exact_username.value}:{password.value}".encode()
        return ((b"Proxy-Authorization", b"Basic " + base64.b64encode(wire)),)
    raise TypeError("unsupported proxy authentication")  # pragma: no cover


def _response_fields(
    fields: list[tuple[bytes, bytes]],
    exchange_hex: str,
    classified: tuple[HeaderName, ...],
) -> ResponseFields:
    sensitive_names = (
        MANDATORY_SENSITIVE_HEADER_NAMES
        | {"location", "set-cookie"}
        | {item.value for item in classified}
    )
    origin = TaintOriginId(value=f"runtime/http-response-field/{exchange_hex}")
    result: list[ResponseField] = []
    for raw_name, raw_value in fields:
        name = HeaderName(value=raw_name.decode("ascii").lower())
        value = HeaderText(value=raw_value.decode("latin-1"))
        result.append(
            ResponseField(
                name=name,
                value=(
                    SensitiveValue(value, origins=(origin,))
                    if name.value in sensitive_names
                    else value
                ),
            )
        )
    return ResponseFields(fields=tuple(result))


def _announces_trailers(fields: list[tuple[bytes, bytes]]) -> bool:
    """Fail closed when the dependency cannot expose explicitly announced trailers."""
    return any(name.lower() == b"trailer" and bool(value.strip()) for name, value in fields)


def _protocol(response: httpcore.Response) -> HttpProtocol:
    value = response.extensions.get("http_version")
    if not isinstance(value, bytes):
        raise TypeError("response HTTP version must be bytes")
    protocol = {
        b"HTTP/1.0": HttpProtocol.HTTP_1_0,
        b"HTTP/1.1": HttpProtocol.HTTP_1_1,
        b"HTTP/2": HttpProtocol.HTTP_2,
    }.get(value)
    if protocol is None:
        raise ValueError("response HTTP version is unsupported")
    return protocol


def _reason(response: httpcore.Response, protocol: HttpProtocol) -> ReasonPhrase | None:
    raw = response.extensions.get("reason_phrase", b"")
    if protocol is HttpProtocol.HTTP_2 or not raw:
        return None
    if not isinstance(raw, bytes):
        raise TypeError("response reason phrase must be bytes")
    return ReasonPhrase(value=raw.decode("latin-1"))


def _no_body_reason(method: str, status: int) -> NoBodyReason | None:
    if method == "HEAD":
        return NoBodyReason.HEAD_REQUEST
    if method == "CONNECT" and 200 <= status < 300:
        return NoBodyReason.CONNECT_TUNNEL
    return {
        101: NoBodyReason.SWITCHING_PROTOCOLS,
        204: NoBodyReason.STATUS_204,
        205: NoBodyReason.STATUS_205,
        304: NoBodyReason.STATUS_304,
    }.get(status)


def _local_endpoint(stream: object) -> tuple[Ipv4Host | Ipv6Host | None, HttpPort | None]:
    if stream is None or not hasattr(stream, "get_extra_info"):
        return None, None
    value = stream.get_extra_info("client_addr")
    if not isinstance(value, tuple) or len(value) < 2:
        return None, None
    try:
        parsed = ipaddress.ip_address(value[0])
        address = (
            Ipv4Host(value=parsed.compressed)
            if parsed.version == 4
            else Ipv6Host(value=parsed.compressed)
        )
        return address, HttpPort(value=value[1])
    except (TypeError, ValueError):
        return None, None


def _tls_layers(
    request: AuthorizedExchangeRequest,
    response: httpcore.Response,
    protocol: HttpProtocol,
) -> TlsLayers:
    stream = response.extensions.get("network_stream")
    ssl_object = stream.get_extra_info("ssl_object") if stream is not None else None
    if type(request.route) is DirectResolvedRoute:
        if request.route.destination.scheme is HttpScheme.HTTP:
            return TlsLayers(connect=TlsNotPerformed())
        return TlsLayers(
            connect=_tls_observation(ssl_object, protocol, request.request.policies.tls)
        )
    proxy = cast(ExplicitProxy, request.request.policies.proxy)
    proxy_policy = proxy.tls
    target_tls = request.request.url.scheme is HttpScheme.HTTPS
    connect: TlsObservation
    if request.route.proxy_destination.scheme is HttpScheme.HTTP:
        connect = TlsNotPerformed()
    elif target_tls:
        connect = TlsUnavailable(
            reason=TlsUnavailableReason.INSTRUMENTATION_UNSUPPORTED,
            applied_policy=cast(ProxyTlsPolicy, proxy_policy),
        )
    else:
        connect = _tls_observation(
            ssl_object,
            protocol,
            cast(ProxyTlsPolicy, proxy_policy),
        )
    origin = (
        _tls_observation(ssl_object, protocol, request.request.policies.tls) if target_tls else None
    )
    return TlsLayers(connect=connect, origin=origin)


def _tls_observation(
    value: object,
    protocol: HttpProtocol,
    policy: TlsPolicy | ProxyTlsPolicy,
) -> TlsMetadata | TlsUnavailable:
    if value is None or not hasattr(value, "version"):
        return TlsUnavailable(
            reason=TlsUnavailableReason.HANDSHAKE_METADATA_LOST,
            applied_policy=policy,
        )
    exact = cast(_SslObject, value)
    version = {"TLSv1.2": TlsVersion.TLS_1_2, "TLSv1.3": TlsVersion.TLS_1_3}.get(
        exact.version() or ""
    )
    cipher = exact.cipher()
    certificate = exact.getpeercert(binary_form=True)
    if version is None or not cipher or not certificate:
        return TlsUnavailable(
            reason=TlsUnavailableReason.HANDSHAKE_METADATA_LOST,
            applied_policy=policy,
        )
    return TlsMetadata(
        version=version,
        cipher_suite=cipher[0],
        alpn=protocol,
        peer_certificate_sha256=Sha256Digest(hex_value=hashlib.sha256(certificate).hexdigest()),
        session_resumed=bool(getattr(exact, "session_reused", False)),
        applied_policy=policy,
    )


class _SslObject(Protocol):
    """Subset of SSLObject/SSLSocket metadata consumed by the adapter."""

    def version(self) -> str | None:
        """Return the negotiated TLS version."""
        ...

    def cipher(self) -> tuple[str, str, int] | None:
        """Return the negotiated cipher tuple."""
        ...

    def getpeercert(self, binary_form: bool = False) -> bytes | None:
        """Return the peer certificate in DER form."""
        ...


def _map_failure(  # noqa: C901 - exhaustive mapping over dependency failure classes
    error: BaseException,
    trace: ExchangeTraceRecorder,
) -> TransportFailure:
    if isinstance(error, asyncio.CancelledError):
        return TransportFailure(
            phase=FailurePhase.CANCELLATION,
            code=_TRANSPORT_CANCELLED,
            retryable=False,
        )
    if isinstance(error, TlsConfigurationError):
        return TransportFailure(phase=FailurePhase.TLS, code=_TLS_FAILED, retryable=True)
    if isinstance(error, httpcore.ProxyError):
        return TransportFailure(phase=FailurePhase.PROXY, code=_PROXY_FAILED, retryable=True)
    if isinstance(error, httpcore.PoolTimeout):
        return TransportFailure(phase=FailurePhase.CONNECT, code=_POOL_TIMEOUT, retryable=True)
    if isinstance(error, httpcore.ConnectTimeout):
        tls = trace.started("start_tls") and not trace.completed("start_tls")
        return TransportFailure(
            phase=FailurePhase.TLS if tls else FailurePhase.CONNECT,
            code=_TLS_FAILED if tls else _CONNECT_TIMEOUT,
            retryable=True,
        )
    if isinstance(error, httpcore.ConnectError):
        tls = trace.started("start_tls") and not trace.completed("start_tls")
        return TransportFailure(
            phase=FailurePhase.TLS if tls else FailurePhase.CONNECT,
            code=_TLS_FAILED if tls else _CONNECT_FAILED,
            retryable=True,
        )
    if isinstance(error, httpcore.WriteTimeout):
        return TransportFailure(
            phase=FailurePhase.REQUEST_WRITE,
            code=_REQUEST_WRITE_TIMEOUT,
            retryable=False,
        )
    if isinstance(error, (httpcore.WriteError, BodyStoreError)):
        return TransportFailure(
            phase=FailurePhase.REQUEST_WRITE,
            code=_REQUEST_WRITE_FAILED,
            retryable=False,
        )
    if isinstance(error, httpcore.ReadTimeout):
        return TransportFailure(
            phase=FailurePhase.RESPONSE_HEADERS_TIMEOUT,
            code=_RESPONSE_HEADERS_TIMEOUT,
            retryable=True,
        )
    if isinstance(error, httpcore.ReadError):
        return TransportFailure(
            phase=FailurePhase.RESPONSE_HEADERS,
            code=_RESPONSE_HEADERS_FAILED,
            retryable=False,
        )
    if isinstance(error, httpcore.ProtocolError):
        return TransportFailure(
            phase=FailurePhase.PROTOCOL,
            code=_PROTOCOL_FAILED,
            retryable=False,
        )
    return TransportFailure(
        phase=FailurePhase.PROTOCOL,
        code=_TRANSPORT_FAILED,
        retryable=False,
    )


def _seconds(value: TimeoutLimit) -> float | None:
    return None if type(value) is UnlimitedTimeout else _duration_seconds(value)


def _duration_seconds(value: Duration) -> float:
    return value.nanoseconds / 1_000_000_000
