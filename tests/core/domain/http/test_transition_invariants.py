"""Adversarial redirect and retry transition policy tests."""

from __future__ import annotations

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.bodies import BytesBody
from comparo.core.domain.http.bodies import MaterializedBody
from comparo.core.domain.http.bodies import MultipartBody
from comparo.core.domain.http.bodies import NoBody
from comparo.core.domain.http.bodies import NoMaterializedBody
from comparo.core.domain.http.bodies import RequestBody
from comparo.core.domain.http.fields import CookieName
from comparo.core.domain.http.fields import CookieSource
from comparo.core.domain.http.fields import CookieText
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
from comparo.core.domain.http.fields import RequestCookie
from comparo.core.domain.http.fields import RequestCookies
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.policies import ClientIdentityRef
from comparo.core.domain.http.policies import RedirectMethodMode
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.requests import prove_request_materialization
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.transition_validation import validate_redirect_transition
from comparo.core.domain.http.transition_validation import validate_retry_request
from comparo.core.domain.http.transition_validation import validate_stream_reconnect_request
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import TrialId
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import materialization_fingerprint
from tests.core.domain.http.factories import multipart_body
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.factories import request_occurrence
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import request


def _url(
    host: str = "api.example.test",
    *,
    scheme: HttpScheme = HttpScheme.HTTPS,
    query: QueryParameters | None = None,
) -> AbsoluteHttpUrl:
    return AbsoluteHttpUrl(
        scheme=scheme,
        authority=HttpAuthority(host=DnsHost(value=host)),
        query=QueryParameters() if query is None else query,
    )


def _authorized(
    effective: EffectiveRequest,
    *,
    attempt: int = 1,
    exchange: int = 1,
) -> AuthorizedExchangeRequest:
    return authorized_request(
        effective,
        DirectResolvedRoute(
            destination=destination(
                effective.url.authority.host.value,
                scheme=effective.url.scheme,
                port=443 if effective.url.scheme is HttpScheme.HTTPS else 80,
            )
        ),
        request_occurrence=request_occurrence(
            attempt=attempt,
            exchange=exchange,
            environment=effective.environment.identity,
        ),
    )


def _with_wire(
    authorized: AuthorizedExchangeRequest,
    *,
    authority_character: str = "e",
    plan_character: str = "f",
    retry_occurrence: bool = False,
    occurrence_execution: int | None = None,
    occurrence_attempt: int | None = None,
    occurrence_exchange: int | None = None,
    **changes: object,
) -> AuthorizedExchangeRequest:
    original_wire = authorized.request
    changed_body = changes.get("body", original_wire.body)
    effective_body: RequestBody
    if type(changed_body) is NoMaterializedBody:
        effective_body = NoBody()
    elif type(changed_body) is MaterializedBody and changed_body.multipart is None:
        effective_body = BytesBody(value=changed_body.source)
    elif type(changed_body) is MaterializedBody:
        validation = changed_body.multipart
        if validation is None:
            raise AssertionError("materialized test body branch must carry multipart validation")
        effective_body = MultipartBody(
            boundary=validation.boundary,
            parts=(),
        )
    else:
        raise TypeError("test wire body change must remain a finalized body")
    effective = msgspec.structs.replace(
        EffectiveRequest(
            environment=original_wire.environment,
            logical_request_fingerprint=original_wire.logical_request_fingerprint,
            method=original_wire.method,
            url=original_wire.url,
            headers=original_wire.headers,
            cookies=original_wire.cookies,
            body=effective_body,
            policies=original_wire.policies,
            provenance=original_wire.provenance,
        ),
        **{key: value for key, value in changes.items() if key != "body"},
        body=effective_body,
    )
    wire = msgspec.structs.replace(
        original_wire,
        **changes,
        materialization=prove_request_materialization(
            effective,
            changed_body,
            effective_request_fingerprint=materialization_fingerprint(effective),
        ),
    )
    previous = authorized.occurrence
    occurrence = request_occurrence(
        execution=(
            previous.execution_id.value.int
            if occurrence_execution is None
            else occurrence_execution
        ),
        attempt=(
            occurrence_attempt
            if occurrence_attempt is not None
            else (
                previous.attempt_id.value.int + 1
                if retry_occurrence
                else previous.attempt_id.value.int
            )
        ),
        exchange=(
            previous.exchange_id.value.int + 1
            if occurrence_exchange is None
            else occurrence_exchange
        ),
    )
    intent = msgspec.structs.replace(
        authorized.authorization.intent,
        request=wire,
        occurrence=occurrence,
        plan_fingerprint=fingerprint(plan_character),
        effective_policy_fingerprint=fingerprint(authority_character),
    )
    return AuthorizedExchangeRequest(
        authorization=HttpAuthorizationGrant(
            intent=intent,
            authority=authority(authority_character),
        )
    )


def _with_foreign_trial(
    authorized: AuthorizedExchangeRequest,
) -> AuthorizedExchangeRequest:
    occurrence = authorized.occurrence
    foreign_execution = msgspec.structs.replace(
        occurrence.attempt.execution,
        trial=msgspec.structs.replace(
            occurrence.attempt.execution.trial,
            trial_id=TrialId(value=UUID(int=999)),
        ),
    )
    intent = msgspec.structs.replace(
        authorized.authorization.intent,
        occurrence=msgspec.structs.replace(
            occurrence,
            attempt=msgspec.structs.replace(occurrence.attempt, execution=foreign_execution),
        ),
    )
    return AuthorizedExchangeRequest(
        authorization=msgspec.structs.replace(authorized.authorization, intent=intent)
    )


def _cookie() -> RequestCookie:
    return RequestCookie(
        name=CookieName(value="session"),
        value=SensitiveValue(
            CookieText(value="secret"),
            origins=(TaintOriginId(value="cookie"),),
        ),
        source=CookieSource.SESSION,
        provenance=provenance(),
    )


def _header(
    name: str,
    *,
    source: HeaderSource = HeaderSource.AUTHORED,
) -> HeaderField:
    value: HeaderText | SensitiveValue[HeaderText] = HeaderText(value="value")
    if source is HeaderSource.AUTHENTICATION or name == "authorization":
        value = SensitiveValue(
            HeaderText(value="secret"),
            origins=(TaintOriginId(value=f"header-{name}"),),
        )
    return HeaderField(
        name=HeaderName(value=name),
        value=value,
        source=source,
        provenance=provenance(),
    )


def test_303_get_preserves_while_browser_post_drops_body_and_body_headers() -> None:
    get = request(redirect_hops=1)
    before_get = _authorized(get)
    validate_redirect_transition(
        before_get,
        HttpStatus(value=303),
        before_get.request.url,
        _with_wire(before_get),
    )

    redirects = msgspec.structs.replace(
        get.policies.redirects,
        method_mode=RedirectMethodMode.BROWSER_COMPATIBLE,
    )
    post = msgspec.structs.replace(
        get,
        method=HttpMethod(value="POST"),
        policies=msgspec.structs.replace(get.policies, redirects=redirects),
        headers=RequestHeaders(fields=(_header("content-type", source=HeaderSource.BODY),)),
    )
    materialized, _ = multipart_body()
    before_post = _with_wire(
        _authorized(post),
        body=msgspec.structs.replace(materialized, multipart=None),
    )
    after_get = _authorized(
        msgspec.structs.replace(
            post,
            method=HttpMethod(value="GET"),
            headers=RequestHeaders(),
        )
    )
    validate_redirect_transition(
        before_post,
        HttpStatus(value=302),
        after_get.request.url,
        after_get,
    )

    with pytest.raises(ValueError, match="method"):
        validate_redirect_transition(
            before_post,
            HttpStatus(value=302),
            before_post.request.url,
            before_post,
        )
    wrong_body = _with_wire(after_get, body=before_post.request.body)
    with pytest.raises(ValueError, match="drop its request body"):
        validate_redirect_transition(
            before_post,
            HttpStatus(value=302),
            wrong_body.request.url,
            wrong_body,
        )


def test_strict_and_307_redirects_preserve_exact_bodies() -> None:
    effective = request(method="POST", redirect_hops=1)
    base = _authorized(effective)
    materialized, _ = multipart_body()
    before = _with_wire(base, body=msgspec.structs.replace(materialized, multipart=None))
    validate_redirect_transition(
        before,
        HttpStatus(value=307),
        before.request.url,
        _with_wire(before),
    )
    other_materialized, _ = multipart_body()
    changed = _with_wire(
        before,
        body=msgspec.structs.replace(other_materialized, multipart=None),
    )
    with pytest.raises(ValueError, match="preserve the exact request body"):
        validate_redirect_transition(before, HttpStatus(value=307), before.request.url, changed)


def test_redirect_origin_downgrade_and_resolved_location_are_enforced() -> None:
    before_effective = request(redirect_hops=1)
    before = _authorized(before_effective)
    cross = _authorized(msgspec.structs.replace(before_effective, url=_url("other.example.test")))
    with pytest.raises(ValueError, match="cross-origin"):
        validate_redirect_transition(before, HttpStatus(value=302), cross.request.url, cross)
    with pytest.raises(ValueError, match="resolved Location"):
        validate_redirect_transition(before, HttpStatus(value=302), _url(), cross)

    redirects = msgspec.structs.replace(
        before_effective.policies.redirects,
        allow_cross_origin=True,
    )
    secure = msgspec.structs.replace(
        before_effective,
        policies=msgspec.structs.replace(before_effective.policies, redirects=redirects),
    )
    insecure = msgspec.structs.replace(secure, url=_url(scheme=HttpScheme.HTTP))
    with pytest.raises(ValueError, match="downgrade"):
        validate_redirect_transition(
            _authorized(secure),
            HttpStatus(value=302),
            insecure.url,
            _authorized(insecure),
        )


def test_cross_origin_redirect_cannot_reuse_tls_client_identity() -> None:
    effective = request(redirect_hops=1)
    effective = msgspec.structs.replace(
        effective,
        policies=msgspec.structs.replace(
            effective.policies,
            redirects=msgspec.structs.replace(
                effective.policies.redirects,
                allow_cross_origin=True,
            ),
            tls=msgspec.structs.replace(
                effective.policies.tls,
                client_identity=ClientIdentityRef(value="service-mtls"),
            ),
        ),
    )
    before = _authorized(effective)
    after = _authorized(
        msgspec.structs.replace(effective, url=_url("other.example.test")),
        exchange=2,
    )
    with pytest.raises(ValueError, match="TLS client identity"):
        validate_redirect_transition(before, HttpStatus(value=302), after.request.url, after)


def test_public_location_cannot_create_sensitive_resolved_url_values() -> None:
    sensitive_query = QueryParameters(
        parameters=(
            QueryParameter(
                name=QueryName(value="token"),
                value=SensitiveValue(
                    QueryText(value="secret"),
                    origins=(TaintOriginId(value="resolved-location"),),
                ),
                source=QuerySource.AUTHORED,
                provenance=provenance(),
            ),
        )
    )
    with pytest.raises(ValueError, match="public redirect Location"):
        FollowedRedirect(
            raw_location=HeaderText(value="/next"),
            resolved_url=_url(query=sensitive_query),
        )


def test_cross_origin_redirect_strips_headers_cookies_and_query_credentials() -> None:
    base = request(redirect_hops=1)
    redirects = msgspec.structs.replace(
        base.policies.redirects,
        allow_cross_origin=True,
        strip_sensitive_credentials=True,
    )
    before_effective = msgspec.structs.replace(
        base,
        policies=msgspec.structs.replace(base.policies, redirects=redirects),
        headers=RequestHeaders(
            fields=(
                _header("authorization", source=HeaderSource.AUTHENTICATION),
                _header("x-public"),
            )
        ),
        cookies=RequestCookies(cookies=(_cookie(),)),
    )
    after_effective = msgspec.structs.replace(
        before_effective,
        url=_url("other.example.test"),
        headers=RequestHeaders(fields=(_header("x-public"),)),
        cookies=RequestCookies(),
    )
    before = _authorized(before_effective)
    after = _authorized(after_effective, exchange=2)
    validate_redirect_transition(before, HttpStatus(value=302), after.request.url, after)

    missing_public_header = _authorized(
        msgspec.structs.replace(after_effective, headers=RequestHeaders())
    )
    with pytest.raises(ValueError, match="headers"):
        validate_redirect_transition(
            before,
            HttpStatus(value=302),
            after.request.url,
            missing_public_header,
        )
    retained_cookie = _authorized(
        msgspec.structs.replace(
            after_effective,
            cookies=RequestCookies(cookies=(_cookie(),)),
        )
    )
    with pytest.raises(ValueError, match="strip request cookies"):
        validate_redirect_transition(
            before,
            HttpStatus(value=302),
            after.request.url,
            retained_cookie,
        )

    secret_query = QueryParameters(
        parameters=(
            QueryParameter(
                name=QueryName(value="token"),
                value=SensitiveValue(
                    QueryText(value="secret"),
                    origins=(TaintOriginId(value="query-token"),),
                ),
                source=QuerySource.AUTHENTICATION,
                provenance=provenance(),
            ),
        )
    )
    query_after = msgspec.structs.replace(
        after_effective,
        url=_url("other.example.test", query=secret_query),
    )
    candidate = _authorized(query_after)
    with pytest.raises(ValueError, match="credential query"):
        validate_redirect_transition(
            before,
            HttpStatus(value=302),
            candidate.request.url,
            candidate,
        )


def test_same_origin_redirect_rejects_cookie_and_identity_drift() -> None:
    effective = request(redirect_hops=1)
    before = _authorized(effective)
    changed_cookie = _authorized(
        msgspec.structs.replace(
            effective,
            cookies=RequestCookies(cookies=(_cookie(),)),
        )
    )
    with pytest.raises(ValueError, match="cookies changed"):
        validate_redirect_transition(
            before,
            HttpStatus(value=302),
            changed_cookie.request.url,
            changed_cookie,
        )

    other_environment = msgspec.structs.replace(
        effective,
        environment=definition(ResourceKind.ENVIRONMENT, "other"),
    )
    changed_environment = _authorized(other_environment)
    with pytest.raises(ValueError, match="Environment identity"):
        validate_redirect_transition(
            before,
            HttpStatus(value=302),
            changed_environment.request.url,
            changed_environment,
        )

    cases = (
        ("logical-request", {"logical_request_fingerprint": fingerprint("b")}),
        (
            "request policies",
            {
                "policies": msgspec.structs.replace(
                    effective.policies,
                    redirects=msgspec.structs.replace(
                        effective.policies.redirects,
                        max_hops=msgspec.structs.replace(
                            effective.policies.redirects.max_hops,
                            value=2,
                        ),
                    ),
                )
            },
        ),
        (
            "provenance",
            {
                "provenance": ValueProvenance(
                    entries=(ProvenanceEntry(kind=ProvenanceKind.INVOCATION),)
                )
            },
        ),
    )
    for message, changes in cases:
        changed = _with_wire(before, **changes)
        with pytest.raises(ValueError, match=message):
            validate_redirect_transition(
                before,
                HttpStatus(value=302),
                changed.request.url,
                changed,
            )

    changed_plan = _with_wire(before, plan_character="b")
    with pytest.raises(ValueError, match="run-plan"):
        validate_redirect_transition(
            before,
            HttpStatus(value=302),
            changed_plan.request.url,
            changed_plan,
        )
    changed_authority = _with_wire(before, authority_character="b")
    with pytest.raises(ValueError, match="policy revision"):
        validate_redirect_transition(
            before,
            HttpStatus(value=302),
            changed_authority.request.url,
            changed_authority,
        )


def test_retry_requires_exact_request_route_and_authority() -> None:
    original = _authorized(request(attempts=2))
    validate_retry_request(original, _with_wire(original, retry_occurrence=True))
    retry_drift = _with_wire(
        original,
        retry_occurrence=True,
        policies=msgspec.structs.replace(
            original.request.policies,
            retries=msgspec.structs.replace(
                original.request.policies.retries,
                max_attempts=msgspec.structs.replace(
                    original.request.policies.retries.max_attempts,
                    value=3,
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="retry policy"):
        validate_retry_request(original, retry_drift)
    redirect_drift = _with_wire(
        original,
        retry_occurrence=True,
        policies=msgspec.structs.replace(
            original.request.policies,
            redirects=msgspec.structs.replace(
                original.request.policies.redirects,
                max_hops=msgspec.structs.replace(
                    original.request.policies.redirects.max_hops,
                    value=1,
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="redirect policy"):
        validate_retry_request(original, redirect_drift)
    with pytest.raises(ValueError, match="exact finalized"):
        validate_retry_request(
            original,
            _with_wire(
                original,
                retry_occurrence=True,
                logical_request_fingerprint=fingerprint("b"),
            ),
        )
    with pytest.raises(ValueError, match="same plan"):
        validate_retry_request(
            original,
            _with_wire(original, plan_character="b", retry_occurrence=True),
        )


def test_transition_authorization_cannot_be_replayed() -> None:
    original = _authorized(request(attempts=2, redirect_hops=1))
    with pytest.raises(ValueError, match="fresh exchange authorization"):
        validate_redirect_transition(
            original,
            HttpStatus(value=307),
            original.request.url,
            original,
        )
    with pytest.raises(ValueError, match="fresh attempt authorization"):
        validate_retry_request(original, original)

    with pytest.raises(ValueError, match="execution or attempt occurrence"):
        validate_redirect_transition(
            original,
            HttpStatus(value=307),
            original.request.url,
            _with_wire(original, occurrence_execution=2),
        )
    with pytest.raises(ValueError, match="execution or attempt occurrence"):
        validate_redirect_transition(
            original,
            HttpStatus(value=307),
            original.request.url,
            _with_wire(original, occurrence_attempt=2),
        )
    with pytest.raises(ValueError, match="execution or attempt occurrence"):
        validate_redirect_transition(
            original,
            HttpStatus(value=307),
            original.request.url,
            _with_foreign_trial(_with_wire(original)),
        )
    with pytest.raises(ValueError, match="execution occurrence"):
        validate_retry_request(
            original,
            _with_wire(original, retry_occurrence=True, occurrence_execution=2),
        )
    with pytest.raises(ValueError, match="fresh exchange authorization"):
        validate_retry_request(
            original,
            _with_wire(original, retry_occurrence=True, occurrence_exchange=1),
        )
    with pytest.raises(ValueError, match="execution occurrence"):
        validate_retry_request(
            original,
            _with_foreign_trial(_with_wire(original, retry_occurrence=True)),
        )


def test_stream_reconnect_requires_fresh_occurrence_authorization() -> None:
    original = _authorized(request())
    reconnected = _with_wire(original, retry_occurrence=True)
    validate_stream_reconnect_request(original, reconnected)
    with pytest.raises(ValueError, match="fresh attempt authorization"):
        validate_stream_reconnect_request(original, original)
    with pytest.raises(ValueError, match="execution occurrence"):
        validate_stream_reconnect_request(
            original,
            _with_wire(original, retry_occurrence=True, occurrence_execution=2),
        )
    with pytest.raises(ValueError, match="fresh exchange authorization"):
        validate_stream_reconnect_request(
            original,
            _with_wire(original, retry_occurrence=True, occurrence_exchange=1),
        )
    with pytest.raises(ValueError, match="execution occurrence"):
        validate_stream_reconnect_request(
            original,
            _with_foreign_trial(_with_wire(original, retry_occurrence=True)),
        )
