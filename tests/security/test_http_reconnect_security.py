"""Adversarial reconnect-chain, authorization, and framing regressions."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.http.attempt_transitions import StreamReconnectTransition
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.authorization import ProxyDnsApproval
from comparo.core.domain.http.fields import HeaderText
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import FollowedRedirect
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.response_bodies import ResponseEntityBody
from comparo.core.domain.http.streams import SegmentEndReason
from comparo.core.domain.http.streams import StreamCompleteness
from comparo.core.domain.http.streams import StreamExtent
from comparo.core.domain.http.streams import StreamKind
from comparo.core.domain.http.streams import StreamObservation
from comparo.core.domain.http.streams import StreamTerminationReason
from comparo.core.domain.http.transition_validation import validate_stream_reconnect_request
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.http.views import ViewKind
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import proxy_request_and_route
from tests.core.domain.http.factories import request_occurrence
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response
from tests.core.domain.http.stream_factories import records_handle
from tests.core.domain.http.stream_factories import segment
from tests.core.domain.http.stream_factories import stream_execution
from tests.core.domain.http.stream_factories import stream_timings
from tests.core.domain.http.stream_factories import view_handle


def _authorized(
    wire_request: FinalizedWireRequest,
    route: ResolvedRoute,
    *,
    plan_character: str = "f",
    policy_character: str = "e",
    approve_proxy_dns: bool = False,
    occurrence: HttpRequestOccurrence | None = None,
) -> AuthorizedExchangeRequest:
    plan = fingerprint(plan_character)
    policy_authority = authority(policy_character)
    actual_occurrence = occurrence or request_occurrence(
        environment=wire_request.environment.identity
    )
    proxy_dns = (
        ProxyDnsApproval(
            request=wire_request,
            route=route,
            occurrence=actual_occurrence,
            plan_fingerprint=plan,
            authority=policy_authority,
        )
        if approve_proxy_dns
        else None
    )
    intent = HttpAuthorizationIntent(
        request=wire_request,
        route=route,
        occurrence=actual_occurrence,
        plan_fingerprint=plan,
        effective_policy_fingerprint=fingerprint(policy_character),
        proxy_dns_approval=proxy_dns,
    )
    return AuthorizedExchangeRequest(
        authorization=HttpAuthorizationGrant(
            intent=intent,
            authority=policy_authority,
        )
    )


def _authorized_like(
    original: AuthorizedExchangeRequest,
    *,
    wire_request: FinalizedWireRequest | None = None,
    route: ResolvedRoute | None = None,
    plan_character: str = "f",
    policy_character: str = "e",
    approve_proxy_dns: bool = False,
) -> AuthorizedExchangeRequest:
    previous = original.occurrence
    return _authorized(
        wire_request or original.request,
        route or original.route,
        plan_character=plan_character,
        policy_character=policy_character,
        approve_proxy_dns=approve_proxy_dns,
        occurrence=request_occurrence(
            execution=previous.execution_id.value.int,
            attempt=previous.attempt_id.value.int + 1,
            exchange=previous.exchange_id.value.int + 1,
            environment=(wire_request or original.request).environment.identity,
        ),
    )


def _changed_wire_request(
    original: FinalizedWireRequest,
    change: Literal["fingerprint", "url", "method", "request-policy"],
) -> FinalizedWireRequest:
    changes: dict[str, object]
    if change == "fingerprint":
        changes = {"logical_request_fingerprint": fingerprint("b")}
    elif change == "url":
        changes = {
            "url": msgspec.structs.replace(
                original.url,
                path=UrlPath(segments=(DecodedPathSegment(value="changed"),)),
            )
        }
    elif change == "method":
        changes = {"method": HttpMethod(value="OPTIONS")}
    else:
        changes = {
            "policies": msgspec.structs.replace(
                original.policies,
                redirects=msgspec.structs.replace(
                    original.policies.redirects,
                    allow_cross_origin=True,
                ),
            )
        }
    materialization = original.materialization
    if change == "fingerprint":
        materialization = msgspec.structs.replace(
            materialization,
            identity=msgspec.structs.replace(
                materialization.identity,
                logical_request_fingerprint=fingerprint("b"),
            ),
        )
    return msgspec.structs.replace(
        original,
        **changes,
        materialization=materialization,
    )


def _three_connection_owner() -> EnvironmentExecutionObservation:
    """Extend the deterministic two-connection execution with one valid reconnect."""
    owner = stream_execution(ViewKind.SSE)
    effective = request()
    previous_response = owner.attempts[-1].exchanges[-1].response
    assert previous_response is not None
    third_exchange = exchange(
        exchange_number=13,
        execution_number=900,
        attempt_number=3,
        effective_request=effective,
        observed_response=previous_response,
    )
    assert third_exchange.response is not None
    assert type(third_exchange.response.body) is ResponseEntityBody
    assert third_exchange.response.body.handle is not None
    third_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=3)),
        retry=None,
        exchanges=(third_exchange,),
        timings=attempt_timings(),
        stream_reconnect=StreamReconnectTransition(
            previous_attempt_id=owner.attempts[-1].attempt_id,
            previous_exchange_id=owner.attempts[-1].exchanges[-1].exchange_id,
            previous_stream_view_id=owner.views[-1].view_id,
        ),
    )
    return msgspec.structs.replace(
        owner,
        attempts=(*owner.attempts, third_attempt),
        views=(
            *owner.views,
            view_handle(
                ViewKind.SSE,
                source=third_exchange.response.body.handle,
                view_number=103,
            ),
        ),
    )


@pytest.mark.parametrize("change", ["fingerprint", "url", "method", "request-policy"])
def test_reconnect_rejects_every_unmodeled_wire_request_change(
    change: Literal["fingerprint", "url", "method", "request-policy"],
) -> None:
    owner = stream_execution(ViewKind.SSE)
    first, reconnect = owner.attempts
    previous = first.exchanges[-1].request
    changed = _authorized_like(
        previous,
        wire_request=_changed_wire_request(previous.request, change),
    )
    changed_exchange = msgspec.structs.replace(reconnect.exchanges[0], request=changed)

    with pytest.raises(ValueError, match="exact finalized wire request"):
        msgspec.structs.replace(
            owner,
            attempts=(
                first,
                msgspec.structs.replace(reconnect, exchanges=(changed_exchange,)),
            ),
        )


@pytest.mark.parametrize("change", ["plan", "policy-authority"])
def test_reconnect_rejects_plan_or_policy_authority_drift(
    change: Literal["plan", "policy-authority"],
) -> None:
    owner = stream_execution(ViewKind.SSE)
    first, reconnect = owner.attempts
    previous = first.exchanges[-1].request
    changed = _authorized_like(
        previous,
        plan_character="b" if change == "plan" else "f",
        policy_character="d" if change == "policy-authority" else "e",
    )
    changed_exchange = msgspec.structs.replace(reconnect.exchanges[0], request=changed)

    with pytest.raises(ValueError, match="run plan and ExecutionPolicy authority revision"):
        msgspec.structs.replace(
            owner,
            attempts=(
                first,
                msgspec.structs.replace(reconnect, exchanges=(changed_exchange,)),
            ),
        )


def test_reconnect_allows_fresh_dns_resolution_but_not_new_proxy_dns_ownership() -> None:
    previous = stream_execution(ViewKind.SSE).attempts[0].exchanges[0].request
    refreshed = _authorized_like(
        previous,
        route=DirectResolvedRoute(destination=destination("api.example.test", "198.51.100.8")),
    )
    validate_stream_reconnect_request(previous, refreshed)

    proxy_wire, client_route = proxy_request_and_route(delegated=False)
    client_resolved = _authorized(proxy_wire, client_route)
    delegated_route = msgspec.structs.replace(
        client_route,
        target=ProxyResolvedTarget(
            scheme=proxy_wire.url.scheme,
            authority=proxy_wire.url.authority,
        ),
    )
    proxy_resolved = _authorized_like(
        client_resolved,
        route=delegated_route,
        approve_proxy_dns=True,
    )
    with pytest.raises(ValueError, match="proxy target-resolution ownership"):
        validate_stream_reconnect_request(client_resolved, proxy_resolved)


def test_reconnect_cannot_switch_between_direct_and_proxy_routes() -> None:
    direct = stream_execution(ViewKind.SSE).attempts[0].exchanges[0].request
    proxy_wire, proxy_route = proxy_request_and_route(delegated=False)
    proxy = _authorized_like(direct, wire_request=proxy_wire, route=proxy_route)
    direct_after_proxy = _authorized_like(
        proxy,
        wire_request=direct.request,
        route=direct.route,
    )

    for previous, reconnected in ((direct, proxy), (proxy, direct_after_proxy)):
        with pytest.raises(ValueError, match="direct or proxy route mode"):
            validate_stream_reconnect_request(previous, reconnected)


def test_reconnect_attempt_may_follow_valid_internal_redirects() -> None:
    effective = request(redirect_hops=1)
    target_url = msgspec.structs.replace(
        effective.url,
        path=UrlPath(segments=(DecodedPathSegment(value="redirected"),)),
    )
    redirected = msgspec.structs.replace(effective, url=target_url)
    initial_stream = exchange(
        exchange_number=41,
        execution_number=900,
        attempt_number=1,
        effective_request=effective,
        observed_response=response(),
    )
    redirect = exchange(
        exchange_number=42,
        execution_number=900,
        attempt_number=2,
        effective_request=effective,
        observed_response=response(status=302),
        redirect=FollowedRedirect(
            raw_location=HeaderText(value="/redirected"),
            resolved_url=target_url,
        ),
    )
    terminal = exchange(
        exchange_number=43,
        execution_number=900,
        attempt_number=2,
        effective_request=redirected,
        observed_response=response(),
    )
    template = stream_execution(ViewKind.SSE)
    assert initial_stream.response is not None
    assert type(initial_stream.response.body) is ResponseEntityBody
    assert initial_stream.response.body.handle is not None
    initial_view = view_handle(
        ViewKind.SSE,
        source=initial_stream.response.body.handle,
        view_number=401,
    )
    observed = msgspec.structs.replace(
        template,
        environment=effective.environment,
        attempts=(
            HttpAttempt(
                attempt_id=AttemptId(value=template.attempts[0].attempt_id.value),
                retry=None,
                exchanges=(initial_stream,),
                timings=attempt_timings(),
            ),
            HttpAttempt(
                attempt_id=AttemptId(value=template.attempts[1].attempt_id.value),
                retry=None,
                exchanges=(redirect, terminal),
                timings=attempt_timings(),
                stream_reconnect=StreamReconnectTransition(
                    previous_attempt_id=template.attempts[0].attempt_id,
                    previous_exchange_id=ExchangeId(value=initial_stream.exchange_id.value),
                    previous_stream_view_id=initial_view.view_id,
                ),
            ),
        ),
        views=(initial_view,),
    )
    assert observed.attempts[1].exchanges[-1].request.request.url == target_url


def test_stream_observation_rejects_both_one_segment_chain_subsets() -> None:
    owner = stream_execution(ViewKind.SSE)
    records = records_handle(StreamKind.SSE, records=1, connections=2)
    extent = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
    )
    with pytest.raises(ValueError, match="every stream owner attempt"):
        StreamObservation(
            records=records,
            segments=(first,),
            captured_bytes=ByteCount(value=4),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    reconnect_only = msgspec.structs.replace(
        first,
        attempt_id=owner.attempts[1].attempt_id,
        exchange_id=owner.attempts[1].exchanges[-1].exchange_id,
        view=owner.views[1],
    )
    with pytest.raises(ValueError, match="every stream owner attempt"):
        StreamObservation(
            records=records,
            segments=(reconnect_only,),
            captured_bytes=ByteCount(value=4),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )


def test_longer_reconnect_chains_are_contiguous_and_complete() -> None:
    owner = _three_connection_owner()
    extent = StreamExtent(
        completeness=StreamCompleteness.FULL,
        termination=StreamTerminationReason.REMOTE_EOF,
    )
    first = segment(
        index=0,
        first=0,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
    )
    second = segment(
        index=1,
        first=1,
        count=1,
        end_reason=SegmentEndReason.RECONNECT,
    )
    two_records = msgspec.structs.replace(
        records_handle(StreamKind.SSE, records=2, connections=2),
        owner=owner,
    )
    with pytest.raises(ValueError, match="every stream owner attempt"):
        StreamObservation(
            records=two_records,
            segments=(first, second),
            captured_bytes=ByteCount(value=8),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    skipped_second = msgspec.structs.replace(
        second,
        attempt_id=owner.attempts[2].attempt_id,
        exchange_id=owner.attempts[2].exchanges[-1].exchange_id,
        view=owner.views[2],
    )
    with pytest.raises(ValueError, match="every stream owner attempt"):
        StreamObservation(
            records=two_records,
            segments=(first, skipped_second),
            captured_bytes=ByteCount(value=8),
            extent=extent,
            trailing_incomplete=(),
            timings=stream_timings(),
        )

    third = msgspec.structs.replace(
        segment(
            index=1,
            first=2,
            count=1,
            byte_start=8,
            byte_end=12,
        ),
        index=Count(value=2),
        attempt_id=owner.attempts[2].attempt_id,
        exchange_id=owner.attempts[2].exchanges[-1].exchange_id,
        view=owner.views[2],
    )
    complete = StreamObservation(
        records=msgspec.structs.replace(two_records, record_count=Count(value=3)),
        segments=(
            first,
            second,
            third,
        ),
        captured_bytes=ByteCount(value=12),
        extent=extent,
        trailing_incomplete=(),
        timings=stream_timings(),
    )
    assert len(complete.segments) == 3


def test_framing_counts_reject_unrepresentable_handles_and_segments() -> None:
    with pytest.raises(ValueError, match="SSE streams cannot contain JSON record errors"):
        records_handle(StreamKind.SSE, records=0, errors=1)
    with pytest.raises(ValueError, match="SSE streams cannot contain JSON record errors"):
        segment(index=0, first=0, count=0, errors=1)

    for kind in (StreamKind.NDJSON, StreamKind.JSON_SEQUENCE):
        with pytest.raises(ValueError, match="JSON streams cannot contain SSE control items"):
            records_handle(kind, records=0, controls=1)
        with pytest.raises(ValueError, match="JSON streams cannot contain SSE control items"):
            segment(index=0, first=0, count=0, controls=1, kind=kind)

    with pytest.raises(ValueError, match="stream counts require"):
        msgspec.structs.replace(
            segment(index=0, first=0, count=0),
            view=view_handle(ViewKind.JSON),
        )
