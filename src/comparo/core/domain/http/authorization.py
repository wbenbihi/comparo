"""Structural HTTP authorization intents and ExecutionPolicy-issued approvals."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.http.fields import is_read_only_http_method
from comparo.core.domain.http.policies import MutationApprovalRequired
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.route_authority import (
    validate_route_request as _validate_route_request,
)
from comparo.core.domain.http.secret_egress import http_secret_egress_origins
from comparo.core.domain.http.url import ProxyResolvedRoute
from comparo.core.domain.http.url import ProxyResolvedTarget
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.http.url import is_resolved_route
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.scopes import AttemptScope

_HTTP_EXECUTE_ID = CapabilityId(namespace="comparo", name="http", version="1")
_HTTP_EXECUTE_OPERATION = CapabilityOperation(value="request.execute")
_HTTP_MUTATION_ID = CapabilityId(namespace="comparo", name="http.mutation", version="1")
_HTTP_MUTATION_OPERATION = CapabilityOperation(value="request.mutate")
_PROXY_DNS_ID = CapabilityId(namespace="comparo", name="http.proxy-dns", version="1")
_PROXY_DNS_OPERATION = CapabilityOperation(value="dns.delegate")
_SECRET_EGRESS_ID = CapabilityId(namespace="comparo", name="secret-egress", version="1")
_SECRET_EGRESS_OPERATION = CapabilityOperation(value="request.egress")


def _validate_contract(
    *,
    actual_id: CapabilityId,
    actual_operation: CapabilityOperation,
    capability_id: CapabilityId,
    operation: CapabilityOperation,
) -> None:
    if type(actual_id) is not CapabilityId or type(actual_operation) is not CapabilityOperation:
        raise TypeError("HTTP authority contract identity must use typed capability values")
    if actual_id != capability_id or actual_operation != operation:
        raise ValueError("HTTP authority grant uses the wrong built-in capability contract")


def _validate_policy_authority(
    authority: ExecutionPolicyAuthority,
    effective_policy_fingerprint: Fingerprint,
) -> None:
    """Bind one capability decision to the exact effective ExecutionPolicy revision."""
    if type(authority) is not ExecutionPolicyAuthority:
        raise TypeError("HTTP authority must be exact ExecutionPolicyAuthority")
    if type(effective_policy_fingerprint) is not Fingerprint:
        raise TypeError("HTTP authority policy binding must be Fingerprint")
    if authority.policy.fingerprint != effective_policy_fingerprint:
        raise ValueError("HTTP authority does not match the effective-policy fingerprint")


@final
class HttpRequestOccurrence(msgspec.Struct, frozen=True, kw_only=True):
    """Exact outbound occurrence for which an HTTP request is authorized."""

    attempt: AttemptScope
    exchange_id: ExchangeId

    def __post_init__(self) -> None:
        """Reject untyped occurrence identities at the authorization boundary."""
        if type(self.attempt) is not AttemptScope:
            raise TypeError("HTTP occurrence attempt must be AttemptScope")
        if type(self.exchange_id) is not ExchangeId:
            raise TypeError("HTTP occurrence exchange ID must be ExchangeId")

    @property
    def execution_id(self) -> EnvironmentExecutionId:
        """Return the Environment execution ID from the exact attempt scope."""
        return self.attempt.execution.execution_id

    @property
    def attempt_id(self) -> AttemptId:
        """Return the attempt ID from the exact attempt scope."""
        return self.attempt.attempt_id


def _validate_occurrence_request(
    occurrence: HttpRequestOccurrence,
    request: FinalizedWireRequest,
) -> None:
    """Bind a full outbound occurrence scope to the request Environment identity."""
    if occurrence.attempt.execution.environment.environment != request.environment.identity:
        raise ValueError("HTTP authorization occurrence changed its Environment identity")


def _requires_mutation_approval(intent: HttpAuthorizationIntent) -> bool:
    """Return whether this exact route-pinned intent requires mutation authority."""
    return (
        not is_read_only_http_method(intent.request.method)
        and type(intent.request.policies.mutation_approval) is MutationApprovalRequired
    )


def _requires_proxy_dns_approval(intent: HttpAuthorizationIntent) -> bool:
    """Return whether this exact route delegates target resolution to its proxy."""
    return (
        type(intent.route) is ProxyResolvedRoute
        and type(intent.route.target) is ProxyResolvedTarget
    )


def _requires_secret_egress_approval(intent: HttpAuthorizationIntent) -> bool:
    """Return whether this exact request carries authorization-relevant sealed data."""
    return bool(http_secret_egress_origins(intent.request))


def _validate_required_safeguard_approvals(intent: HttpAuthorizationIntent) -> None:
    """Require every positive safeguard before issuing final HTTP send authority."""
    if _requires_mutation_approval(intent) and intent.mutation_approval is None:
        raise ValueError("unsafe HTTP method requires ExecutionPolicy mutation approval")
    if _requires_proxy_dns_approval(intent) and intent.proxy_dns_approval is None:
        raise ValueError("proxy-side DNS requires explicit ExecutionPolicy approval")
    if _requires_secret_egress_approval(intent) and intent.secret_egress_approval is None:
        raise ValueError("sensitive HTTP request requires ExecutionPolicy secret-egress approval")


@final
class MutationApproval(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy authority for one exact unsafe request intent."""

    request: FinalizedWireRequest
    route: ResolvedRoute
    occurrence: HttpRequestOccurrence
    plan_fingerprint: Fingerprint
    authority: ExecutionPolicyAuthority
    capability_id: CapabilityId = _HTTP_MUTATION_ID
    operation: CapabilityOperation = _HTTP_MUTATION_OPERATION

    def __post_init__(self) -> None:
        """Bind mutation authority structurally to request, route, and plan."""
        if type(self.request) is not FinalizedWireRequest:
            raise TypeError("mutation approval request must be FinalizedWireRequest")
        if not is_resolved_route(self.route):
            raise TypeError("mutation approval route must be ResolvedRoute")
        if type(self.occurrence) is not HttpRequestOccurrence:
            raise TypeError("mutation approval occurrence must be HttpRequestOccurrence")
        _validate_occurrence_request(self.occurrence, self.request)
        if type(self.plan_fingerprint) is not Fingerprint:
            raise TypeError("mutation approval plan binding must be Fingerprint")
        _validate_policy_authority(self.authority, self.authority.policy.fingerprint)
        _validate_route_request(self.route, self.request)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_HTTP_MUTATION_ID,
            operation=_HTTP_MUTATION_OPERATION,
        )

    @property
    def effective_policy_fingerprint(self) -> Fingerprint:
        """Return the policy revision structurally carried by the issuing authority."""
        return self.authority.policy.fingerprint


@final
class ProxyDnsApproval(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy authority to delegate DNS for one exact HTTP request intent."""

    request: FinalizedWireRequest
    route: ResolvedRoute
    occurrence: HttpRequestOccurrence
    plan_fingerprint: Fingerprint
    authority: ExecutionPolicyAuthority
    capability_id: CapabilityId = _PROXY_DNS_ID
    operation: CapabilityOperation = _PROXY_DNS_OPERATION

    def __post_init__(self) -> None:
        """Bind proxy-DNS authority structurally to request, delegated route, and plan."""
        if type(self.request) is not FinalizedWireRequest:
            raise TypeError("proxy DNS approval request must be FinalizedWireRequest")
        if not is_resolved_route(self.route):
            raise TypeError("proxy DNS approval route must be ResolvedRoute")
        if type(self.occurrence) is not HttpRequestOccurrence:
            raise TypeError("proxy DNS approval occurrence must be HttpRequestOccurrence")
        _validate_occurrence_request(self.occurrence, self.request)
        if (
            type(self.route) is not ProxyResolvedRoute
            or type(self.route.target) is not ProxyResolvedTarget
        ):
            raise ValueError("proxy DNS approval requires a proxy-resolved delegated route")
        if type(self.plan_fingerprint) is not Fingerprint:
            raise TypeError("proxy DNS approval plan binding must be Fingerprint")
        _validate_policy_authority(self.authority, self.authority.policy.fingerprint)
        _validate_route_request(self.route, self.request)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_PROXY_DNS_ID,
            operation=_PROXY_DNS_OPERATION,
        )

    @property
    def effective_policy_fingerprint(self) -> Fingerprint:
        """Return the policy revision structurally carried by the issuing authority."""
        return self.authority.policy.fingerprint


@final
class SecretEgressApproval(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy authority to disclose sealed values in one exact HTTP request."""

    request: FinalizedWireRequest
    route: ResolvedRoute
    occurrence: HttpRequestOccurrence
    plan_fingerprint: Fingerprint
    authority: ExecutionPolicyAuthority
    capability_id: CapabilityId = _SECRET_EGRESS_ID
    operation: CapabilityOperation = _SECRET_EGRESS_OPERATION

    def __post_init__(self) -> None:
        """Bind egress authority to exact request bytes, route, occurrence, plan, and policy."""
        if type(self.request) is not FinalizedWireRequest:
            raise TypeError("secret egress approval request must be FinalizedWireRequest")
        if not http_secret_egress_origins(self.request):
            raise ValueError("secret egress approval requires a sensitive HTTP request")
        if not is_resolved_route(self.route):
            raise TypeError("secret egress approval route must be ResolvedRoute")
        if type(self.occurrence) is not HttpRequestOccurrence:
            raise TypeError("secret egress approval occurrence must be HttpRequestOccurrence")
        _validate_occurrence_request(self.occurrence, self.request)
        if type(self.plan_fingerprint) is not Fingerprint:
            raise TypeError("secret egress approval plan binding must be Fingerprint")
        _validate_policy_authority(self.authority, self.authority.policy.fingerprint)
        _validate_route_request(self.route, self.request)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_SECRET_EGRESS_ID,
            operation=_SECRET_EGRESS_OPERATION,
        )

    @property
    def effective_policy_fingerprint(self) -> Fingerprint:
        """Return the exact policy revision carried by the issuing authority."""
        return self.authority.policy.fingerprint


@final
class HttpAuthorizationIntent(msgspec.Struct, frozen=True, kw_only=True):
    """Complete immutable input presented to the HTTP ExecutionPolicy authority."""

    request: FinalizedWireRequest
    route: ResolvedRoute
    occurrence: HttpRequestOccurrence
    plan_fingerprint: Fingerprint
    effective_policy_fingerprint: Fingerprint
    mutation_approval: MutationApproval | None = None
    proxy_dns_approval: ProxyDnsApproval | None = None
    secret_egress_approval: SecretEgressApproval | None = None

    def __post_init__(self) -> None:
        """Bind route mode and every separately required approval to the full request intent."""
        if type(self.request) is not FinalizedWireRequest:
            raise TypeError("HTTP authorization intent request must be FinalizedWireRequest")
        if not is_resolved_route(self.route):
            raise TypeError("HTTP authorization intent route must be ResolvedRoute")
        if type(self.occurrence) is not HttpRequestOccurrence:
            raise TypeError("HTTP authorization occurrence must be HttpRequestOccurrence")
        _validate_occurrence_request(self.occurrence, self.request)
        if type(self.plan_fingerprint) is not Fingerprint:
            raise TypeError("HTTP authorization plan binding must be Fingerprint")
        if type(self.effective_policy_fingerprint) is not Fingerprint:
            raise TypeError("effective HTTP policy binding must be Fingerprint")
        _validate_route_request(self.route, self.request)
        self._validate_mutation_approval()
        self._validate_proxy_dns_approval()
        self._validate_secret_egress_approval()

    def _validate_mutation_approval(self) -> None:
        unsafe = not is_read_only_http_method(self.request.method)
        if not unsafe and self.mutation_approval is not None:
            raise ValueError("safe HTTP methods cannot carry mutation approval")
        if self.mutation_approval is not None:
            if type(self.mutation_approval) is not MutationApproval:
                raise TypeError("mutation approval must be MutationApproval")
            if (
                self.mutation_approval.request != self.request
                or self.mutation_approval.route != self.route
                or self.mutation_approval.occurrence != self.occurrence
                or self.mutation_approval.plan_fingerprint != self.plan_fingerprint
                or self.mutation_approval.effective_policy_fingerprint
                != self.effective_policy_fingerprint
            ):
                raise ValueError("mutation approval is not bound to this HTTP intent")

    def _validate_proxy_dns_approval(self) -> None:
        if (
            self.proxy_dns_approval is not None
            and type(self.proxy_dns_approval) is not ProxyDnsApproval
        ):
            raise TypeError("proxy DNS approval must be ProxyDnsApproval")
        delegated = (
            self.route.target
            if type(self.route) is ProxyResolvedRoute
            and type(self.route.target) is ProxyResolvedTarget
            else None
        )
        if delegated is None and self.proxy_dns_approval is not None:
            raise ValueError("client-resolved routes cannot carry proxy DNS approval")
        if (
            delegated is not None
            and self.proxy_dns_approval is not None
            and (
                self.proxy_dns_approval.request != self.request
                or self.proxy_dns_approval.route != self.route
                or self.proxy_dns_approval.occurrence != self.occurrence
                or self.proxy_dns_approval.plan_fingerprint != self.plan_fingerprint
                or self.proxy_dns_approval.effective_policy_fingerprint
                != self.effective_policy_fingerprint
            )
        ):
            raise ValueError("proxy DNS approval is not bound to this HTTP intent")

    def _validate_secret_egress_approval(self) -> None:
        approval = self.secret_egress_approval
        sensitive = bool(http_secret_egress_origins(self.request))
        if not sensitive and approval is not None:
            raise ValueError("public HTTP requests cannot carry secret-egress approval")
        if approval is None:
            return
        if type(approval) is not SecretEgressApproval:
            raise TypeError("secret egress approval must be SecretEgressApproval")
        if (
            approval.request != self.request
            or approval.route != self.route
            or approval.occurrence != self.occurrence
            or approval.plan_fingerprint != self.plan_fingerprint
            or approval.effective_policy_fingerprint != self.effective_policy_fingerprint
        ):
            raise ValueError("secret egress approval is not bound to this HTTP intent")


@final
class HttpAuthorizationGrant(msgspec.Struct, frozen=True, kw_only=True):
    """Final ExecutionPolicy decision structurally associated with one complete HTTP intent."""

    intent: HttpAuthorizationIntent
    authority: ExecutionPolicyAuthority
    capability_id: CapabilityId = _HTTP_EXECUTE_ID
    operation: CapabilityOperation = _HTTP_EXECUTE_OPERATION

    def __post_init__(self) -> None:
        """Require the exact built-in ``comparo/http@1 request.execute`` capability contract."""
        if type(self.intent) is not HttpAuthorizationIntent:
            raise TypeError("HTTP authorization grant intent must be HttpAuthorizationIntent")
        _validate_required_safeguard_approvals(self.intent)
        _validate_policy_authority(self.authority, self.intent.effective_policy_fingerprint)
        approvals = (
            self.intent.mutation_approval,
            self.intent.proxy_dns_approval,
            self.intent.secret_egress_approval,
        )
        if any(
            approval is not None and approval.authority != self.authority for approval in approvals
        ):
            raise ValueError(
                "HTTP approvals and final grant must use one ExecutionPolicy authority"
            )
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_HTTP_EXECUTE_ID,
            operation=_HTTP_EXECUTE_OPERATION,
        )


@final
class AuthorizedExchangeRequest(msgspec.Struct, frozen=True, kw_only=True):
    """The only complete, route-pinned request shape an HTTP transport may accept."""

    authorization: HttpAuthorizationGrant

    def __post_init__(self) -> None:
        """Require a structurally bound final HTTP authority decision."""
        if type(self.authorization) is not HttpAuthorizationGrant:
            raise TypeError("authorized exchange requires HttpAuthorizationGrant")

    @property
    def request(self) -> FinalizedWireRequest:
        """Return the exact finalized request carried by the authorized intent."""
        return self.authorization.intent.request

    @property
    def route(self) -> ResolvedRoute:
        """Return the exact pinned route carried by the authorized intent."""
        return self.authorization.intent.route

    @property
    def occurrence(self) -> HttpRequestOccurrence:
        """Return the exact execution, attempt, and exchange authorized to send."""
        return self.authorization.intent.occurrence
