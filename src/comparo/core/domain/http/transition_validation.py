"""Cross-exchange redirect and retry transition truth validation."""

from __future__ import annotations

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.fields import MANDATORY_SENSITIVE_HEADER_NAMES
from comparo.core.domain.http.fields import MANDATORY_SENSITIVE_QUERY_NAMES
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.pre_send import AuthorizedResolutionRequest
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpProxyDnsApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.sensitivity import is_sensitive_value


def _same_origin(left: AbsoluteHttpUrl, right: AbsoluteHttpUrl) -> bool:
    return (
        left.scheme is right.scheme
        and left.authority.host == right.authority.host
        and effective_port(left) == effective_port(right)
    )


def _redirect_drops_body(
    status: HttpStatus,
    request: AuthorizedExchangeRequest,
) -> bool:
    method = request.request.method.value
    if status.value == 303:
        return method not in {"GET", "HEAD"}
    return (
        status.value in {301, 302}
        and request.request.policies.redirects.method_mode is RedirectMethodMode.BROWSER_COMPATIBLE
        and method == "POST"
    )


def _headers_after_body_decision(
    request: AuthorizedExchangeRequest,
    *,
    drop_body: bool,
    strip_credentials: bool,
) -> RequestHeaders:
    fields = request.request.headers.fields
    if drop_body:
        fields = tuple(field for field in fields if field.source is not HeaderSource.BODY)
    if strip_credentials:
        fields = tuple(
            field
            for field in fields
            if field.source is not HeaderSource.AUTHENTICATION
            and field.name.value not in MANDATORY_SENSITIVE_HEADER_NAMES
            and not is_sensitive_value(field.value)
        )
    return RequestHeaders(fields=fields)


def _validate_redirect_origin(
    before: AuthorizedExchangeRequest,
    after: AuthorizedExchangeRequest,
) -> tuple[bool, bool]:
    """Enforce cross-origin and downgrade policy, returning origin and stripping decisions."""
    policy = before.request.policies.redirects
    cross_origin = not _same_origin(before.request.url, after.request.url)
    if cross_origin and not policy.allow_cross_origin:
        raise ValueError("cross-origin redirect is forbidden by the effective redirect policy")
    if cross_origin and before.request.policies.tls.client_identity is not None:
        raise ValueError(
            "cross-origin redirect cannot reuse a TLS client identity without destination approval"
        )
    if (
        before.request.url.scheme is HttpScheme.HTTPS
        and after.request.url.scheme is HttpScheme.HTTP
        and policy.block_https_downgrade
    ):
        raise ValueError("HTTPS redirect downgrade is forbidden by the effective policy")
    return cross_origin, cross_origin and policy.strip_sensitive_credentials


def _validate_redirect_payload(
    previous: AuthorizedExchangeRequest,
    status: HttpStatus,
    following: AuthorizedExchangeRequest,
) -> bool:
    """Enforce the status/mode-specific method and body transition."""
    before = previous.request
    after = following.request
    drop_body = _redirect_drops_body(status, previous)
    expected_method = "GET" if drop_body else before.method.value
    if after.method.value != expected_method:
        raise ValueError("redirect request method does not match the effective rewrite mode")
    if drop_body and type(after.body) is not NoMaterializedBody:
        raise ValueError("a redirect rewritten to GET must drop its request body")
    if not drop_body and after.body != before.body:
        raise ValueError("this redirect status must preserve the exact request body")
    return drop_body


def _validate_redirect_credentials(
    previous: AuthorizedExchangeRequest,
    following: AuthorizedExchangeRequest,
    *,
    drop_body: bool,
    strip: bool,
) -> None:
    """Enforce exact header/cookie/query credential retention or stripping."""
    before = previous.request
    after = following.request
    if after.headers != _headers_after_body_decision(
        previous,
        drop_body=drop_body,
        strip_credentials=strip,
    ):
        raise ValueError("redirect request headers do not match rewrite and credential policy")
    if not strip:
        if after.cookies != before.cookies:
            raise ValueError("redirect request cookies changed without credential stripping")
        return
    if after.cookies.cookies:
        raise ValueError("cross-origin redirect must strip request cookies")
    if any(
        parameter.source is QuerySource.AUTHENTICATION
        or parameter.name.value.casefold() in MANDATORY_SENSITIVE_QUERY_NAMES
        or is_sensitive_value(parameter.value)
        for parameter in after.url.query.parameters
    ):
        raise ValueError("cross-origin redirect must strip credential query parameters")


def _validate_redirect_identity(
    previous: AuthorizedExchangeRequest,
    following: AuthorizedExchangeRequest,
) -> None:
    """Enforce stable execution identity and fresh authorization by one policy revision."""
    before = previous.request
    after = following.request
    if after.environment != before.environment:
        raise ValueError("redirect request changed its Environment identity")
    if after.logical_request_fingerprint != before.logical_request_fingerprint:
        raise ValueError("redirect request changed its logical-request fingerprint")
    if after.policies != before.policies:
        raise ValueError("redirect request changed its effective request policies")
    if after.provenance != before.provenance:
        raise ValueError("redirect request changed its provenance")
    before_intent = previous.authorization.intent
    after_intent = following.authorization.intent
    before_occurrence = before_intent.occurrence
    after_occurrence = after_intent.occurrence
    if after_occurrence.attempt != before_occurrence.attempt:
        raise ValueError("redirect request changed its execution or attempt occurrence")
    if after_occurrence.exchange_id == before_occurrence.exchange_id:
        raise ValueError("redirect request requires a fresh exchange authorization")
    if after_intent.plan_fingerprint != before_intent.plan_fingerprint:
        raise ValueError("redirect request changed its run-plan fingerprint")
    if (
        after_intent.effective_policy_fingerprint != before_intent.effective_policy_fingerprint
        or following.authorization.authority != previous.authorization.authority
    ):
        raise ValueError("redirect request must be re-authorized by the same policy revision")


def validate_redirect_transition(
    previous: AuthorizedExchangeRequest,
    status: HttpStatus,
    resolved_url: AbsoluteHttpUrl,
    following: AuthorizedExchangeRequest,
) -> None:
    """Validate one exact ADR-0011 redirect rewrite and credential decision."""
    after = following.request
    if after.url != resolved_url:
        raise ValueError("redirect request URL must equal the retained resolved Location")
    _, strip = _validate_redirect_origin(previous, following)
    drop_body = _validate_redirect_payload(previous, status, following)
    _validate_redirect_credentials(previous, following, drop_body=drop_body, strip=strip)
    _validate_redirect_identity(previous, following)


type HttpAttemptRequest = (
    AuthorizedExchangeRequest
    | AuthorizedResolutionRequest
    | HttpResolutionDenial
    | HttpAuthorizationDenial
    | HttpMutationApprovalDenial
    | HttpProxyDnsApprovalDenial
)


def _request_route(request: HttpAttemptRequest) -> ResolvedRoute | None:
    if type(request) is AuthorizedExchangeRequest:
        return request.route
    if type(request) is HttpAuthorizationDenial:
        return request.route
    if type(request) is HttpMutationApprovalDenial:
        return request.route
    if type(request) is HttpProxyDnsApprovalDenial:
        return request.route
    return None


def _request_authority(
    request: HttpAttemptRequest,
) -> tuple[Fingerprint, Fingerprint, object]:
    if type(request) is AuthorizedExchangeRequest or type(request) is AuthorizedResolutionRequest:
        intent = request.authorization.intent
        authority = request.authorization.authority
    else:
        intent = request.intent
        authority = request.authority
    return intent.plan_fingerprint, intent.effective_policy_fingerprint, authority


def _validate_resolution_mode(
    previous: HttpAttemptRequest,
    following: HttpAttemptRequest,
    *,
    transition: str,
) -> None:
    before_route = _request_route(previous)
    after_route = _request_route(following)
    if before_route is None or after_route is None:
        return
    if type(after_route) is not type(before_route):
        raise ValueError(f"{transition} cannot change direct or proxy route mode")
    if (
        type(after_route) is ProxyResolvedRoute
        and type(before_route) is ProxyResolvedRoute
        and type(after_route.target) is not type(before_route.target)
    ):
        raise ValueError(f"{transition} cannot change proxy target-resolution ownership")


def validate_retry_request(
    original: HttpAttemptRequest,
    retried: HttpAttemptRequest,
) -> None:
    """Require exact request/policy continuity while permitting fresh DNS route results."""
    if retried.request.policies.retries != original.request.policies.retries:
        raise ValueError("all retry attempts must retain one effective retry policy")
    if retried.request.policies.redirects != original.request.policies.redirects:
        raise ValueError("all exchanges must retain one effective redirect policy")
    if retried.request != original.request:
        raise ValueError("retry attempt must replay the exact finalized initial request")
    _validate_resolution_mode(original, retried, transition="retry attempt")
    original_occurrence = original.occurrence
    retried_occurrence = retried.occurrence
    if (
        retried_occurrence.attempt.execution != original_occurrence.attempt.execution
        or retried_occurrence.attempt.step_execution_id
        != original_occurrence.attempt.step_execution_id
    ):
        raise ValueError("retry attempt changed its execution occurrence")
    if retried_occurrence.attempt_id == original_occurrence.attempt_id:
        raise ValueError("retry attempt requires a fresh attempt authorization")
    if retried_occurrence.exchange_id == original_occurrence.exchange_id:
        raise ValueError("retry attempt requires a fresh exchange authorization")
    if _request_authority(retried) != _request_authority(original):
        raise ValueError("retry attempt must use the same plan and policy authority revision")


def validate_stream_reconnect_request(
    previous: HttpAttemptRequest,
    reconnected: HttpAttemptRequest,
) -> None:
    """Require a clean reconnect to repeat and freshly authorize one exact wire request.

    Step 1 models no reconnect-specific mutation, including SSE ``Last-Event-ID``. DNS results
    may be refreshed, but direct/proxy routing and target-resolution ownership cannot change.
    Future reconnect mutations require a typed validated variant rather than weaker equality.
    """
    _validate_resolution_mode(previous, reconnected, transition="stream reconnect")
    if reconnected.request != previous.request:
        raise ValueError(
            "stream reconnect must repeat the exact finalized wire request; "
            "reconnect mutations are not modeled"
        )
    before_occurrence = previous.occurrence
    after_occurrence = reconnected.occurrence
    if (
        after_occurrence.attempt.execution != before_occurrence.attempt.execution
        or after_occurrence.attempt.step_execution_id != before_occurrence.attempt.step_execution_id
    ):
        raise ValueError("stream reconnect changed its execution occurrence")
    if after_occurrence.attempt_id == before_occurrence.attempt_id:
        raise ValueError("stream reconnect requires a fresh attempt authorization")
    if after_occurrence.exchange_id == before_occurrence.exchange_id:
        raise ValueError("stream reconnect requires a fresh exchange authorization")
    if _request_authority(reconnected) != _request_authority(previous):
        raise ValueError(
            "stream reconnect must retain the run plan and ExecutionPolicy authority revision"
        )
