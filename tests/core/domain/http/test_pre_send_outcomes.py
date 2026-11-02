"""Route-free resolution and route-pinned authorization outcome invariants."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import MutationApproval
from comparo.core.domain.http.authorization import ProxyDnsApproval
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import HttpAttempt
from comparo.core.domain.http.observations import HttpExchange
from comparo.core.domain.http.policies import MutationApprovalRequired
from comparo.core.domain.http.policies import RetryCondition
from comparo.core.domain.http.pre_send import AuthorizedResolutionRequest
from comparo.core.domain.http.pre_send import HttpAuthorizationDenial
from comparo.core.domain.http.pre_send import HttpMutationApprovalDenial
from comparo.core.domain.http.pre_send import HttpProxyDnsApprovalDenial
from comparo.core.domain.http.pre_send import HttpResolutionDenial
from comparo.core.domain.http.pre_send import HttpResolutionFailure
from comparo.core.domain.http.pre_send import HttpResolutionGrant
from comparo.core.domain.http.pre_send import HttpResolutionIntent
from comparo.core.domain.http.pre_send import is_http_pre_send_outcome
from comparo.core.domain.http.requests import EffectiveRequest
from comparo.core.domain.http.retry_observation_validation import RetryMetadata
from comparo.core.domain.http.retry_observation_validation import RetryTrigger
from comparo.core.domain.http.timings import EnvironmentExecutionTimings
from comparo.core.domain.http.timings import HttpAttemptTimings
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.timings import TimingUnavailable
from comparo.core.domain.http.timings import TimingUnavailableReason
from comparo.core.domain.http.transition_validation import _request_route
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import definition
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.factories import proxy_request_and_route
from tests.core.domain.http.observation_factories import attempt_timings
from tests.core.domain.http.observation_factories import authorization_denial
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import failure
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import measured
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import resolution_denial
from tests.core.domain.http.observation_factories import resolution_failure
from tests.core.domain.http.observation_factories import response


def _resolution_intent() -> HttpResolutionIntent:
    return resolution_failure(
        request(),
        request_occurrence=occurrence(),
    ).request.authorization.intent


def _execution(
    effective: EffectiveRequest,
    attempts: tuple[HttpAttempt, ...],
) -> EnvironmentExecutionObservation:
    unavailable = TimingUnavailable(reason=TimingUnavailableReason.INSTRUMENTATION_UNSUPPORTED)
    return EnvironmentExecutionObservation(
        execution_id=attempts[0].pre_send.occurrence.execution_id
        if attempts[0].pre_send is not None
        else attempts[0].exchanges[0].request.occurrence.execution_id,
        environment=effective.environment,
        step_owner=None,
        attempts=attempts,
        timings=EnvironmentExecutionTimings(queue=unavailable, elapsed=unavailable),
    )


def _dns_retry_request() -> EffectiveRequest:
    effective = request(attempts=2)
    return msgspec.structs.replace(
        effective,
        policies=msgspec.structs.replace(
            effective.policies,
            retries=msgspec.structs.replace(
                effective.policies.retries,
                conditions=(RetryCondition.DNS_FAILURE,),
            ),
        ),
    )


def _successful_retry_attempt(effective: EffectiveRequest) -> HttpAttempt:
    return HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=RetryMetadata(
            trigger=RetryTrigger.DNS_FAILURE,
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        ),
        exchanges=(
            exchange(
                exchange_number=2,
                effective_request=effective,
                observed_response=response(),
            ),
        ),
        timings=attempt_timings(),
    )


def _mutation_intent(*, approved: bool = False) -> tuple[HttpAuthorizationIntent, EffectiveRequest]:
    effective = request(method="POST")
    effective = msgspec.structs.replace(
        effective,
        policies=msgspec.structs.replace(
            effective.policies,
            mutation_approval=MutationApprovalRequired(),
        ),
    )
    wire = finalized_wire_request(effective)
    route = DirectResolvedRoute(destination=destination())
    request_occurrence = occurrence()
    approval = (
        MutationApproval(
            request=wire,
            route=route,
            occurrence=request_occurrence,
            plan_fingerprint=fingerprint("f"),
            authority=authority(),
        )
        if approved
        else None
    )
    return (
        HttpAuthorizationIntent(
            request=wire,
            route=route,
            occurrence=request_occurrence,
            plan_fingerprint=fingerprint("f"),
            effective_policy_fingerprint=fingerprint("e"),
            mutation_approval=approval,
        ),
        effective,
    )


def _proxy_dns_intent(*, approved: bool = False) -> HttpAuthorizationIntent:
    wire, route = proxy_request_and_route(delegated=True)
    request_occurrence = occurrence()
    approval = (
        ProxyDnsApproval(
            request=wire,
            route=route,
            occurrence=request_occurrence,
            plan_fingerprint=fingerprint("f"),
            authority=authority(),
        )
        if approved
        else None
    )
    return HttpAuthorizationIntent(
        request=wire,
        route=route,
        occurrence=request_occurrence,
        plan_fingerprint=fingerprint("f"),
        effective_policy_fingerprint=fingerprint("e"),
        proxy_dns_approval=approval,
    )


def test_resolution_failure_has_only_exact_resolution_authority() -> None:
    """A DNS failure proves resolution authority without fabricating a route or send grant."""
    effective = request()
    outcome = resolution_failure(effective, request_occurrence=occurrence())

    assert type(outcome.request.authorization) is HttpResolutionGrant
    assert (
        outcome.request.authorization.capability_id
        != authorized_request(effective).authorization.capability_id
    )
    assert outcome.request.request.environment == effective.environment
    assert outcome.occurrence == occurrence()
    assert not hasattr(outcome.request, "route")
    assert outcome.request.authorization.capability_id == CapabilityId(
        namespace="comparo", name="http.resolve", version="1"
    )
    assert outcome.request.authorization.operation == CapabilityOperation(value="request.resolve")


def test_resolution_and_send_denials_are_distinct_route_boundaries() -> None:
    """Policy may deny resolution before routing or deny sending after a route is pinned."""
    effective = request()
    before_route = resolution_denial(effective, request_occurrence=occurrence())
    after_route = authorization_denial(effective, request_occurrence=occurrence())

    assert before_route.request.environment == effective.environment
    assert before_route.occurrence == occurrence()
    assert not hasattr(before_route, "route")
    assert after_route.request.environment == effective.environment
    assert after_route.occurrence == occurrence()
    assert type(after_route.route) is DirectResolvedRoute
    assert before_route.capability_id == CapabilityId(
        namespace="comparo", name="http.resolve", version="1"
    )
    assert after_route.capability_id == CapabilityId(namespace="comparo", name="http", version="1")
    assert all(
        is_http_pre_send_outcome(outcome)
        for outcome in (
            before_route,
            resolution_failure(effective, request_occurrence=occurrence()),
            after_route,
        )
    )
    assert not is_http_pre_send_outcome(object())


def test_resolution_intent_rejects_untyped_or_cross_environment_identity() -> None:
    intent = _resolution_intent()
    invalid: tuple[tuple[str, object], ...] = (
        ("request", object()),
        ("occurrence", object()),
        ("plan_fingerprint", object()),
        ("effective_policy_fingerprint", object()),
    )
    for field, value in invalid:
        with pytest.raises(TypeError):
            msgspec.structs.replace(intent, **{field: value})

    other_environment = definition(ResourceKind.ENVIRONMENT, "production").identity
    with pytest.raises(ValueError, match="Environment identity"):
        msgspec.structs.replace(
            intent,
            occurrence=occurrence(environment=other_environment),
        )


def test_resolution_grant_rejects_wrong_authority_and_capability_contract() -> None:
    intent = _resolution_intent()
    grant = HttpResolutionGrant(intent=intent, authority=authority())
    with pytest.raises(TypeError, match="intent"):
        msgspec.structs.replace(grant, intent=cast("HttpResolutionIntent", object()))
    with pytest.raises(ValueError, match="effective-policy fingerprint"):
        msgspec.structs.replace(grant, authority=authority("d"))
    with pytest.raises(TypeError, match="typed capability"):
        msgspec.structs.replace(grant, capability_id=cast("CapabilityId", object()))
    with pytest.raises(ValueError, match="wrong built-in capability"):
        msgspec.structs.replace(
            grant,
            operation=CapabilityOperation(value="request.execute"),
        )
    with pytest.raises(TypeError, match="requires HttpResolutionGrant"):
        AuthorizedResolutionRequest(authorization=cast("HttpResolutionGrant", object()))


def test_resolution_failure_rejects_false_phase_types_and_later_timing() -> None:
    outcome = resolution_failure(request(), request_occurrence=occurrence())
    with pytest.raises(TypeError, match="AuthorizedResolutionRequest"):
        msgspec.structs.replace(
            outcome,
            request=cast("AuthorizedResolutionRequest", object()),
        )
    with pytest.raises(TypeError, match="requires TransportFailure"):
        msgspec.structs.replace(outcome, failure=cast("TransportFailure", object()))
    with pytest.raises(ValueError, match="DNS phase"):
        msgspec.structs.replace(outcome, failure=failure(FailurePhase.CONNECT))
    with pytest.raises(TypeError, match="timings"):
        msgspec.structs.replace(
            outcome,
            timings=cast("HttpExchangeTimings", object()),
        )
    with pytest.raises(ValueError, match="after dns failure"):
        msgspec.structs.replace(
            outcome,
            timings=msgspec.structs.replace(outcome.timings, connect=measured()),
        )


@pytest.mark.parametrize("denial_kind", ["resolution", "send"])
def test_policy_denials_reject_untyped_fields_and_false_contracts(denial_kind: str) -> None:
    effective = request()
    denial: HttpResolutionDenial | HttpAuthorizationDenial = (
        resolution_denial(effective, request_occurrence=occurrence())
        if denial_kind == "resolution"
        else authorization_denial(effective, request_occurrence=occurrence())
    )
    expected_intent = "resolution denial intent" if denial_kind == "resolution" else "denial intent"
    with pytest.raises(TypeError, match=expected_intent):
        msgspec.structs.replace(denial, intent=object())
    with pytest.raises(ValueError, match="effective-policy fingerprint"):
        msgspec.structs.replace(denial, authority=authority("d"))
    with pytest.raises(TypeError, match="code"):
        msgspec.structs.replace(denial, code=object())
    with pytest.raises(TypeError, match="timings"):
        msgspec.structs.replace(denial, timings=object())
    with pytest.raises(ValueError, match="after policy failure"):
        msgspec.structs.replace(
            denial,
            timings=msgspec.structs.replace(denial.timings, dns=measured()),
        )
    with pytest.raises(ValueError, match="wrong built-in capability"):
        msgspec.structs.replace(
            denial,
            capability_id=CapabilityId(namespace="comparo", name="wrong", version="1"),
        )


def test_pre_send_attempt_rejects_untyped_dual_and_mismatched_ownership() -> None:
    effective = request()
    denied = resolution_denial(effective, request_occurrence=occurrence())
    valid = HttpAttempt(
        attempt_id=denied.occurrence.attempt_id,
        retry=None,
        exchanges=(),
        timings=attempt_timings(),
        pre_send=denied,
    )
    assert valid.pre_send is denied
    with pytest.raises(TypeError, match="typed variant"):
        msgspec.structs.replace(valid, pre_send=object())
    with pytest.raises(ValueError, match="either exchanges or one pre-send"):
        msgspec.structs.replace(
            valid,
            exchanges=(
                exchange(
                    exchange_number=1,
                    effective_request=effective,
                    observed_response=response(),
                ),
            ),
        )
    with pytest.raises(ValueError, match="does not own this attempt"):
        msgspec.structs.replace(valid, attempt_id=AttemptId(value=UUID(int=2)))


def test_dns_retry_refreshes_route_while_preserving_request_and_authority() -> None:
    effective = _dns_retry_request()
    first_outcome = resolution_failure(
        effective,
        request_occurrence=occurrence(attempt=1, exchange=1),
    )
    first = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(),
        timings=attempt_timings(),
        pre_send=first_outcome,
    )
    refreshed_route = DirectResolvedRoute(destination=destination(address="192.0.2.99"))
    refreshed = authorized_request(
        effective,
        refreshed_route,
        request_occurrence=occurrence(attempt=2, exchange=2),
    )
    observed = response()
    observed = msgspec.structs.replace(
        observed,
        endpoint=msgspec.structs.replace(
            observed.endpoint,
            destination=refreshed_route.destination,
            remote_address=refreshed_route.destination.selected_address,
            remote_port=refreshed_route.destination.port,
        ),
    )
    successful_exchange = HttpExchange(
        exchange_id=refreshed.occurrence.exchange_id,
        request=refreshed,
        response=observed,
        failure=None,
        redirect=None,
        timings=exchange_timings(),
    )
    second = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=2)),
        retry=RetryMetadata(
            trigger=RetryTrigger.DNS_FAILURE,
            backoff=Duration(nanoseconds=0),
            jitter=Duration(nanoseconds=0),
        ),
        exchanges=(successful_exchange,),
        timings=attempt_timings(),
    )
    observed_execution = _execution(effective, (first, second))

    # The first attempt has no route. The second binds a freshly resolved destination.
    assert first_outcome.request.request == refreshed.request
    assert refreshed_route.destination.selected_address.value == "192.0.2.99"
    assert observed_execution.attempts == (first, second)


def test_policy_denial_and_non_retryable_dns_cannot_produce_retry_attempts() -> None:
    effective = _dns_retry_request()
    denied = resolution_denial(
        effective,
        request_occurrence=occurrence(attempt=1, exchange=1),
    )
    denied_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(),
        timings=attempt_timings(),
        pre_send=denied,
    )
    assert denied_attempt.pre_send is denied
    assert type(denied) is HttpResolutionDenial
    with pytest.raises(ValueError, match="trigger does not match"):
        _execution(effective, (denied_attempt, _successful_retry_attempt(effective)))

    failed = resolution_failure(
        effective,
        request_occurrence=occurrence(attempt=1, exchange=1),
        retryable=False,
    )
    assert failed.failure.retryable is False
    failed_attempt = HttpAttempt(
        attempt_id=AttemptId(value=UUID(int=1)),
        retry=None,
        exchanges=(),
        timings=attempt_timings(),
        pre_send=failed,
    )
    with pytest.raises(ValueError, match="non-retryable"):
        _execution(effective, (failed_attempt, _successful_retry_attempt(effective)))


def test_pre_send_types_reject_untyped_runtime_components() -> None:
    """Keep cast-only malformed direct construction covered at every public boundary."""
    intent = _resolution_intent()
    with pytest.raises(TypeError, match="HTTP authority"):
        HttpResolutionGrant(
            intent=intent,
            authority=cast("ExecutionPolicyAuthority", object()),
        )
    with pytest.raises(TypeError, match="policy binding"):
        HttpResolutionIntent(
            request=intent.request,
            occurrence=intent.occurrence,
            plan_fingerprint=intent.plan_fingerprint,
            effective_policy_fingerprint=cast("Fingerprint", object()),
        )
    with pytest.raises(TypeError, match="resolution failure timings"):
        HttpResolutionFailure(
            request=AuthorizedResolutionRequest(
                authorization=HttpResolutionGrant(intent=intent, authority=authority())
            ),
            failure=TransportFailure(
                phase=FailurePhase.DNS,
                code=DiagnosticCode(value="http.dns-failed"),
                retryable=False,
            ),
            timings=cast("HttpExchangeTimings", object()),
        )
    with pytest.raises(TypeError, match="attempt timings"):
        HttpAttempt(
            attempt_id=AttemptId(value=UUID(int=1)),
            retry=None,
            exchanges=(),
            timings=cast("HttpAttemptTimings", object()),
            pre_send=resolution_denial(request(), request_occurrence=occurrence()),
        )


def test_required_safeguard_denials_exist_without_fabricating_positive_grants() -> None:
    mutation_intent, effective = _mutation_intent()
    proxy_intent = _proxy_dns_intent()
    mutation = HttpMutationApprovalDenial(
        intent=mutation_intent,
        authority=authority(),
        code=DiagnosticCode(value="http.mutation-denied"),
        timings=exchange_timings(failure_phase=FailurePhase.POLICY),
    )
    proxy_dns = HttpProxyDnsApprovalDenial(
        intent=proxy_intent,
        authority=authority(),
        code=DiagnosticCode(value="http.proxy-dns-denied"),
        timings=exchange_timings(failure_phase=FailurePhase.POLICY),
    )

    assert mutation.request is mutation_intent.request
    assert mutation.route is mutation_intent.route
    assert _request_route(mutation) is mutation.route
    assert mutation.occurrence is mutation_intent.occurrence
    assert proxy_dns.request is proxy_intent.request
    assert proxy_dns.route is proxy_intent.route
    assert _request_route(proxy_dns) is proxy_dns.route
    assert proxy_dns.occurrence is proxy_intent.occurrence
    assert is_http_pre_send_outcome(mutation)
    assert is_http_pre_send_outcome(proxy_dns)
    for denial in (mutation, proxy_dns):
        attempt = HttpAttempt(
            attempt_id=denial.occurrence.attempt_id,
            retry=None,
            exchanges=(),
            timings=attempt_timings(),
            pre_send=denial,
        )
        assert attempt.pre_send is denial

    with pytest.raises(ValueError, match="requires ExecutionPolicy mutation approval"):
        HttpAuthorizationGrant(intent=mutation_intent, authority=authority())
    with pytest.raises(ValueError, match="proxy-side DNS requires"):
        HttpAuthorizationGrant(intent=proxy_intent, authority=authority())
    with pytest.raises(ValueError, match="requires ExecutionPolicy mutation approval"):
        HttpAuthorizationDenial(
            intent=mutation_intent,
            authority=authority(),
            code=DiagnosticCode(value="http.send-denied"),
            timings=exchange_timings(failure_phase=FailurePhase.POLICY),
        )
    assert effective.method.value == "POST"


def test_safeguard_denials_require_one_exact_missing_safeguard() -> None:
    mutation_intent, _ = _mutation_intent()
    proxy_intent = _proxy_dns_intent()
    mutation = HttpMutationApprovalDenial(
        intent=mutation_intent,
        authority=authority(),
        code=DiagnosticCode(value="http.mutation-denied"),
        timings=exchange_timings(failure_phase=FailurePhase.POLICY),
    )
    proxy_dns = HttpProxyDnsApprovalDenial(
        intent=proxy_intent,
        authority=authority(),
        code=DiagnosticCode(value="http.proxy-dns-denied"),
        timings=exchange_timings(failure_phase=FailurePhase.POLICY),
    )
    with pytest.raises(ValueError, match="approval-gated unsafe method"):
        msgspec.structs.replace(
            mutation,
            intent=authorized_request(request()).authorization.intent,
        )
    with pytest.raises(ValueError, match="proxy-resolved target"):
        msgspec.structs.replace(
            proxy_dns,
            intent=authorized_request(request()).authorization.intent,
        )
    with pytest.raises(ValueError, match="cannot carry a positive mutation"):
        msgspec.structs.replace(mutation, intent=_mutation_intent(approved=True)[0])
    with pytest.raises(ValueError, match="cannot carry a positive proxy DNS"):
        msgspec.structs.replace(proxy_dns, intent=_proxy_dns_intent(approved=True))

    for denial in (mutation, proxy_dns):
        with pytest.raises(TypeError, match="denial intent"):
            msgspec.structs.replace(denial, intent=object())
        with pytest.raises(ValueError, match="effective-policy fingerprint"):
            msgspec.structs.replace(denial, authority=authority("d"))
        with pytest.raises(TypeError, match="denial code"):
            msgspec.structs.replace(denial, code=object())
        with pytest.raises(TypeError, match="denial timings"):
            msgspec.structs.replace(denial, timings=object())
        with pytest.raises(ValueError, match="wrong built-in capability"):
            msgspec.structs.replace(
                denial,
                capability_id=CapabilityId(namespace="comparo", name="wrong", version="1"),
            )

    approved_mutation, _ = _mutation_intent(approved=True)
    approved_proxy = _proxy_dns_intent(approved=True)
    assert (
        HttpAuthorizationGrant(intent=approved_mutation, authority=authority()).intent
        is approved_mutation
    )
    assert (
        HttpAuthorizationGrant(intent=approved_proxy, authority=authority()).intent
        is approved_proxy
    )
