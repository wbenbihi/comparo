"""Adversarial redirect and proxy-DNS authorization security regressions."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import ProxyDnsApproval
from comparo.core.domain.http.fields import BareQueryValue
from comparo.core.domain.http.fields import HeaderField
from comparo.core.domain.http.fields import HeaderName
from comparo.core.domain.http.fields import HeaderSource
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HeaderValue
from comparo.core.domain.http.fields import QueryName
from comparo.core.domain.http.fields import QueryParameter
from comparo.core.domain.http.fields import QueryParameters
from comparo.core.domain.http.fields import QuerySource
from comparo.core.domain.http.fields import QueryText
from comparo.core.domain.http.fields import RequestHeaders
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.responses import HttpStatus
from comparo.core.domain.http.transition_validation import validate_redirect_transition
from comparo.core.domain.http.url import AbsoluteHttpUrl
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import EncodedPathSegment
from comparo.core.domain.http.url import HttpAuthority
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import provenance
from tests.core.domain.http.factories import proxy_request_and_route
from tests.core.domain.http.factories import request_occurrence
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import request


def test_cross_origin_redirect_rejects_custom_authored_sensitive_fields() -> None:
    base = request(redirect_hops=1)
    redirects = msgspec.structs.replace(
        base.policies.redirects,
        allow_cross_origin=True,
        strip_sensitive_credentials=True,
    )
    secret_header = HeaderField(
        name=HeaderName(value="x-private-context"),
        value=SensitiveValue(
            HeaderText(value="private"),
            origins=(TaintOriginId(value="custom-header"),),
        ),
        source=HeaderSource.AUTHORED,
        provenance=provenance(),
    )
    before_effective = msgspec.structs.replace(
        base,
        policies=msgspec.structs.replace(base.policies, redirects=redirects),
        headers=RequestHeaders(fields=(secret_header,)),
    )
    destination_url = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="other.example.test")),
    )
    before = authorized_request(
        before_effective,
        DirectResolvedRoute(destination=destination()),
    )
    retained_header_effective = msgspec.structs.replace(
        before_effective,
        url=destination_url,
    )
    retained_header = authorized_request(
        retained_header_effective,
        DirectResolvedRoute(destination=destination("other.example.test")),
    )
    with pytest.raises(ValueError, match="headers"):
        validate_redirect_transition(
            before,
            HttpStatus(value=307),
            destination_url,
            retained_header,
        )

    secret_query = QueryParameters(
        parameters=(
            QueryParameter(
                name=QueryName(value="custom_context"),
                value=SensitiveValue(
                    QueryText(value="private"),
                    origins=(TaintOriginId(value="custom-query"),),
                ),
                source=QuerySource.AUTHORED,
                provenance=provenance(),
            ),
        )
    )
    query_url = msgspec.structs.replace(destination_url, query=secret_query)
    retained_query = authorized_request(
        msgspec.structs.replace(
            before_effective,
            url=query_url,
            headers=RequestHeaders(),
        ),
        DirectResolvedRoute(destination=destination("other.example.test")),
    )
    with pytest.raises(ValueError, match="credential query"):
        validate_redirect_transition(
            before,
            HttpStatus(value=307),
            query_url,
            retained_query,
        )


def test_proxy_dns_approval_cannot_replay_across_requests_or_plans() -> None:
    approved_request, approved_route = proxy_request_and_route(delegated=True)
    approved_plan = fingerprint("a")
    occurrence = request_occurrence()
    approval = ProxyDnsApproval(
        request=approved_request,
        route=approved_route,
        occurrence=occurrence,
        plan_fingerprint=approved_plan,
        authority=authority("b"),
    )

    changed_materialization = msgspec.structs.replace(
        approved_request.materialization,
        identity=msgspec.structs.replace(
            approved_request.materialization.identity,
            logical_request_fingerprint=fingerprint("c"),
        ),
    )
    changed_request = msgspec.structs.replace(
        approved_request,
        logical_request_fingerprint=fingerprint("c"),
        materialization=changed_materialization,
    )
    with pytest.raises(ValueError, match="not bound to this HTTP intent"):
        HttpAuthorizationIntent(
            request=changed_request,
            route=approved_route,
            occurrence=occurrence,
            plan_fingerprint=approved_plan,
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=approval,
        )

    with pytest.raises(ValueError, match="not bound to this HTTP intent"):
        HttpAuthorizationIntent(
            request=approved_request,
            route=approved_route,
            occurrence=occurrence,
            plan_fingerprint=fingerprint("d"),
            effective_policy_fingerprint=fingerprint("b"),
            proxy_dns_approval=approval,
        )


def test_sensitive_redirect_location_must_taint_every_nonempty_resolved_value() -> None:
    origin = TaintOriginId(value="redirect-location")
    raw_location = SensitiveValue(
        HeaderText(value="/hook-secret?token=query-secret"),
        origins=(origin,),
    )
    path_value = SensitiveValue(
        DecodedPathSegment(value="hook-secret"),
        origins=(origin,),
    )
    query_value = SensitiveValue(
        QueryText(value="query-secret"),
        origins=(origin,),
    )
    query = QueryParameters(
        parameters=(
            QueryParameter(
                name=QueryName(value="context"),
                value=query_value,
                source=QuerySource.AUTHORED,
                provenance=provenance(),
            ),
        )
    )
    url = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="api.example.test")),
        path=UrlPath(segments=(path_value,)),
        query=query,
    )
    redirect = FollowedRedirect(raw_location=raw_location, resolved_url=url)
    assert url.path.sensitivity is Sensitivity.SENSITIVE
    assert query.sensitivity is Sensitivity.SENSITIVE
    assert url.sensitivity is Sensitivity.SENSITIVE
    assert url.taint_origins == (origin,)
    rendered = repr(redirect)
    assert "hook-secret" not in rendered
    assert "query-secret" not in rendered
    with pytest.raises(TypeError) as captured:
        msgspec.json.encode(redirect)
    assert "hook-secret" not in str(captured.value)
    assert "query-secret" not in str(captured.value)

    public_path = msgspec.structs.replace(
        url,
        path=UrlPath(segments=(DecodedPathSegment(value="hook-secret"),)),
    )
    with pytest.raises(ValueError, match="path values must remain sealed"):
        FollowedRedirect(raw_location=raw_location, resolved_url=public_path)
    public_query = msgspec.structs.replace(
        url,
        query=QueryParameters(
            parameters=(
                msgspec.structs.replace(query.parameters[0], value=QueryText(value="public")),
            )
        ),
    )
    with pytest.raises(ValueError, match="query values must remain sealed"):
        FollowedRedirect(raw_location=raw_location, resolved_url=public_query)
    wrong_origin = msgspec.structs.replace(
        url,
        path=UrlPath(
            segments=(
                SensitiveValue(
                    DecodedPathSegment(value="hook-secret"),
                    origins=(TaintOriginId(value="different-origin"),),
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="path lineage"):
        FollowedRedirect(raw_location=raw_location, resolved_url=wrong_origin)

    with pytest.raises(ValueError, match="path values must remain sealed"):
        FollowedRedirect(
            raw_location=raw_location,
            resolved_url=msgspec.structs.replace(
                url,
                path=UrlPath(segments=(EncodedPathSegment(value="encoded"),)),
            ),
        )
    bare_query = QueryParameters(
        parameters=(
            QueryParameter(
                name=QueryName(value="flag"),
                value=BareQueryValue(),
                source=QuerySource.AUTHORED,
                provenance=provenance(),
            ),
        )
    )
    empty_public_path = UrlPath(
        segments=(DecodedPathSegment(value=""),),
        trailing_slash=True,
    )
    bare_url = msgspec.structs.replace(url, path=empty_public_path, query=bare_query)
    assert (
        FollowedRedirect(raw_location=raw_location, resolved_url=bare_url).resolved_url is bare_url
    )
    assert empty_public_path.taint_origins == ()
    empty_query_url = msgspec.structs.replace(
        bare_url,
        query=QueryParameters(
            parameters=(
                QueryParameter(
                    name=QueryName(value="empty"),
                    value=QueryText(value=""),
                    source=QuerySource.AUTHORED,
                    provenance=provenance(),
                ),
            )
        ),
    )
    assert (
        FollowedRedirect(
            raw_location=raw_location,
            resolved_url=empty_query_url,
        ).resolved_url
        is empty_query_url
    )

    wrong_query_origin = msgspec.structs.replace(
        url,
        query=QueryParameters(
            parameters=(
                msgspec.structs.replace(
                    query.parameters[0],
                    value=SensitiveValue(
                        QueryText(value="query-secret"),
                        origins=(TaintOriginId(value="different-origin"),),
                    ),
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="query lineage"):
        FollowedRedirect(raw_location=raw_location, resolved_url=wrong_query_origin)


def test_public_urls_derive_public_sensitivity_and_sensitive_location_payload_is_typed() -> None:
    public = AbsoluteHttpUrl(
        scheme=HttpScheme.HTTPS,
        authority=HttpAuthority(host=DnsHost(value="api.example.test")),
    )
    assert public.path.sensitivity is Sensitivity.PUBLIC
    assert public.query.sensitivity is Sensitivity.PUBLIC
    assert public.sensitivity is Sensitivity.PUBLIC
    with pytest.raises(TypeError, match="payload must be HeaderText"):
        FollowedRedirect(
            raw_location=cast(
                "HeaderValue",
                SensitiveValue(
                    QueryText(value="wrong"),
                    origins=(TaintOriginId(value="wrong-location"),),
                ),
            ),
            resolved_url=public,
        )
