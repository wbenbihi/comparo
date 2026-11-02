"""Cross-object HTTP response, capture, endpoint, and TLS truth validation."""

from __future__ import annotations

from typing import TypeGuard

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.policies import ExplicitProxy
from comparo.core.domain.http.policies import ProxyTlsPolicy
from comparo.core.domain.http.policies import TlsPolicy
from comparo.core.domain.http.policies import TlsVersion as PolicyTlsVersion
from comparo.core.domain.http.response_bodies import BodyCaptureState
from comparo.core.domain.http.response_bodies import BodyReadFailedCapture
from comparo.core.domain.http.response_bodies import BodyReadFailureKind
from comparo.core.domain.http.response_bodies import BudgetExceededCapture
from comparo.core.domain.http.response_bodies import CancelledCapture
from comparo.core.domain.http.response_bodies import ContentDecodingFailedCapture
from comparo.core.domain.http.response_bodies import NoBodyReason
from comparo.core.domain.http.response_bodies import NoResponseBody
from comparo.core.domain.http.response_bodies import RemoteTruncatedCapture
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.response_bodies import StorageFailedCapture
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TlsMetadata
from comparo.core.domain.http.transport import TlsObservation
from comparo.core.domain.http.transport import TlsUnavailable
from comparo.core.domain.http.transport import TlsVersion as ObservedTlsVersion
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import NetworkDestination
from comparo.core.domain.http.url import ProxyResolvedRoute


def _expected_no_body_reasons(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation,
) -> frozenset[NoBodyReason]:
    reasons: set[NoBodyReason] = set()
    method = request.request.method.value
    if method == "HEAD":
        reasons.add(NoBodyReason.HEAD_REQUEST)
    if method == "CONNECT" and 200 <= response.status.value < 300:
        reasons.add(NoBodyReason.CONNECT_TUNNEL)
    status_reason = {
        101: NoBodyReason.SWITCHING_PROTOCOLS,
        204: NoBodyReason.STATUS_204,
        205: NoBodyReason.STATUS_205,
        304: NoBodyReason.STATUS_304,
    }.get(response.status.value)
    if status_reason is not None:
        reasons.add(status_reason)
    return frozenset(reasons)


def _required_capture_failure_phase(capture: BodyCaptureState | None) -> FailurePhase | None:
    if type(capture) is BodyReadFailedCapture:
        return {
            BodyReadFailureKind.READ_FAILURE: FailurePhase.BODY_READ,
            BodyReadFailureKind.IDLE_TIMEOUT: FailurePhase.IDLE_TIMEOUT,
        }[capture.kind]
    if type(capture) is RemoteTruncatedCapture:
        return FailurePhase.PROTOCOL
    if type(capture) is BudgetExceededCapture:
        return FailurePhase.RESOURCE_BUDGET
    if type(capture) is StorageFailedCapture:
        return FailurePhase.STORAGE
    if type(capture) is CancelledCapture:
        return FailurePhase.CANCELLATION
    if type(capture) is ContentDecodingFailedCapture:
        return FailurePhase.CONTENT_DECODING
    return None


def _validate_body_and_capture(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation,
    failure: TransportFailure | None,
) -> None:
    expected = _expected_no_body_reasons(request, response)
    if type(response.body) is NoResponseBody:
        if response.body.reason not in expected:
            raise ValueError("response NoBody reason does not match request and status semantics")
    elif expected and (failure is None or failure.phase is not FailurePhase.PROTOCOL):
        raise ValueError("body-forbidden response bytes require an explicit protocol failure")

    capture = response.body.capture if type(response.body) is ResponseEntityBody else None
    required_phase = _required_capture_failure_phase(capture)
    if required_phase is not None and (failure is None or failure.phase is not required_phase):
        raise ValueError(
            "operationally incomplete body capture requires its matching failure phase"
        )
    if failure is None:
        return
    expected_capture = {
        FailurePhase.BODY_READ: BodyReadFailedCapture,
        FailurePhase.IDLE_TIMEOUT: BodyReadFailedCapture,
        FailurePhase.CONTENT_DECODING: ContentDecodingFailedCapture,
        FailurePhase.RESOURCE_BUDGET: BudgetExceededCapture,
        FailurePhase.STORAGE: StorageFailedCapture,
        FailurePhase.CANCELLATION: CancelledCapture,
    }.get(failure.phase)
    if expected_capture is not None and type(capture) is not expected_capture:
        raise ValueError("post-header failure phase requires its matching body capture state")


def _connect_destination(request: AuthorizedExchangeRequest) -> NetworkDestination:
    if type(request.route) is DirectResolvedRoute:
        return request.route.destination
    return request.route.proxy_destination


def _require_applied_policy(
    layer: TlsMetadata | TlsUnavailable,
    expected: TlsPolicy | ProxyTlsPolicy,
    *,
    role: str,
) -> None:
    if layer.applied_policy != expected:
        raise ValueError(f"observed {role} TLS policy evidence does not match its authority")


def _is_established_tls(
    layer: TlsObservation,
) -> TypeGuard[TlsMetadata | TlsUnavailable]:
    return type(layer) in {TlsMetadata, TlsUnavailable}


def _minimum_tls_version(policy: TlsPolicy | ProxyTlsPolicy) -> PolicyTlsVersion:
    if type(policy) is ProxyTlsPolicy:
        return policy.policy.minimum_version
    return policy.minimum_version


def _validate_direct_tls(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation,
) -> tuple[TlsObservation, TlsPolicy]:
    tls = response.tls
    if tls.origin is not None:
        raise ValueError("a direct route cannot carry a separate origin TLS layer")
    if _is_established_tls(tls.connect):
        _require_applied_policy(
            tls.connect,
            request.request.policies.tls,
            role="target",
        )
    return tls.connect, request.request.policies.tls


def _validate_proxy_connect_tls(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation,
    proxy: ExplicitProxy,
) -> ProxyTlsPolicy | None:
    if type(request.route) is not ProxyResolvedRoute:  # pragma: no cover - authorization invariant
        raise TypeError("proxy TLS validation requires a proxy resolved route")
    if request.route.proxy_destination.scheme is HttpScheme.HTTP:
        return None
    if type(proxy.tls) is not ProxyTlsPolicy:  # pragma: no cover - ExplicitProxy invariant
        raise ValueError("an HTTPS proxy route requires independent proxy TLS authority")
    if _is_established_tls(response.tls.connect):  # pragma: no branch - HTTPS endpoint invariant
        _require_applied_policy(response.tls.connect, proxy.tls, role="proxy")
    return proxy.tls


def _validate_proxy_tls(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation,
) -> tuple[TlsObservation, TlsPolicy | ProxyTlsPolicy]:
    proxy = request.request.policies.proxy
    if type(proxy) is not ExplicitProxy:  # pragma: no cover - authorization invariant
        raise ValueError("a proxy route requires an explicit proxy policy")
    proxy_tls = _validate_proxy_connect_tls(request, response, proxy)
    if request.request.url.scheme is HttpScheme.HTTPS:
        if response.tls.origin is None:
            raise ValueError("an HTTPS proxy target requires a separate origin TLS layer")
        _require_applied_policy(
            response.tls.origin,
            request.request.policies.tls,
            role="target",
        )
        return response.tls.origin, request.request.policies.tls
    if response.tls.origin is not None:
        raise ValueError("a clear-text proxy target cannot carry an origin TLS layer")
    if proxy_tls is not None:
        return response.tls.connect, proxy_tls
    return response.tls.connect, request.request.policies.tls


def _validate_tls(request: AuthorizedExchangeRequest, response: ResponseObservation) -> None:
    response_layer: TlsObservation
    response_policy: TlsPolicy | ProxyTlsPolicy
    if type(request.route) is DirectResolvedRoute:
        response_layer, response_policy = _validate_direct_tls(request, response)
    else:
        response_layer, response_policy = _validate_proxy_tls(request, response)
    if type(response_layer) is TlsMetadata:
        if response_layer.alpn is not response.protocol:
            raise ValueError("response-serving TLS ALPN and observed HTTP protocol must agree")
        if (
            _minimum_tls_version(response_policy) is PolicyTlsVersion.TLS_1_3
            and response_layer.version is ObservedTlsVersion.TLS_1_2
        ):
            raise ValueError("negotiated TLS version is below the authorized minimum")


def validate_exchange_semantics(
    request: AuthorizedExchangeRequest,
    response: ResponseObservation | None,
    failure: TransportFailure | None,
) -> None:
    """Validate every cross-object fact available once response headers exist."""
    if response is None:
        return
    _validate_body_and_capture(request, response, failure)
    if response.endpoint.destination != _connect_destination(request):
        raise ValueError("response endpoint must match the authorized connect destination")
    _validate_tls(request, response)
