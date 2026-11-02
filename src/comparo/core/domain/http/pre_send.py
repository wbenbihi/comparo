"""Authorized resolution failures and denied send decisions before an HTTP exchange."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.http.authorization import _HTTP_MUTATION_ID
from comparo.core.domain.http.authorization import _HTTP_MUTATION_OPERATION
from comparo.core.domain.http.authorization import _PROXY_DNS_ID
from comparo.core.domain.http.authorization import _PROXY_DNS_OPERATION
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.authorization import _requires_mutation_approval
from comparo.core.domain.http.authorization import _requires_proxy_dns_approval
from comparo.core.domain.http.authorization import _validate_contract
from comparo.core.domain.http.authorization import _validate_occurrence_request
from comparo.core.domain.http.authorization import _validate_policy_authority
from comparo.core.domain.http.authorization import _validate_required_safeguard_approvals
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.timing_validation import validate_pre_send_timing_truth
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.transport import FailurePhase
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.http.url import ResolvedRoute
from comparo.core.domain.primitives import Fingerprint

_HTTP_RESOLVE_ID = CapabilityId(namespace="comparo", name="http.resolve", version="1")
_HTTP_RESOLVE_OPERATION = CapabilityOperation(value="request.resolve")
_HTTP_EXECUTE_ID = CapabilityId(namespace="comparo", name="http", version="1")
_HTTP_EXECUTE_OPERATION = CapabilityOperation(value="request.execute")


@final
class HttpResolutionIntent(msgspec.Struct, frozen=True, kw_only=True):
    """Exact pre-route request presented for policy-authorized name resolution."""

    request: FinalizedWireRequest
    occurrence: HttpRequestOccurrence
    plan_fingerprint: Fingerprint
    effective_policy_fingerprint: Fingerprint

    def __post_init__(self) -> None:
        """Bind resolution authority to an exact request, occurrence, plan, and policy."""
        if type(self.request) is not FinalizedWireRequest:
            raise TypeError("HTTP resolution intent request must be FinalizedWireRequest")
        if type(self.occurrence) is not HttpRequestOccurrence:
            raise TypeError("HTTP resolution occurrence must be HttpRequestOccurrence")
        _validate_occurrence_request(self.occurrence, self.request)
        if type(self.plan_fingerprint) is not Fingerprint:
            raise TypeError("HTTP resolution plan binding must be Fingerprint")
        if type(self.effective_policy_fingerprint) is not Fingerprint:
            raise TypeError("HTTP resolution policy binding must be Fingerprint")


@final
class HttpResolutionGrant(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy authority to resolve one request target without authority to send it."""

    intent: HttpResolutionIntent
    authority: ExecutionPolicyAuthority
    capability_id: CapabilityId = _HTTP_RESOLVE_ID
    operation: CapabilityOperation = _HTTP_RESOLVE_OPERATION

    def __post_init__(self) -> None:
        """Require the exact built-in resolution capability and effective policy revision."""
        if type(self.intent) is not HttpResolutionIntent:
            raise TypeError("HTTP resolution grant intent must be HttpResolutionIntent")
        _validate_policy_authority(self.authority, self.intent.effective_policy_fingerprint)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_HTTP_RESOLVE_ID,
            operation=_HTTP_RESOLVE_OPERATION,
        )


@final
class AuthorizedResolutionRequest(msgspec.Struct, frozen=True, kw_only=True):
    """A pre-route request authorized only for resolution and route construction."""

    authorization: HttpResolutionGrant

    def __post_init__(self) -> None:
        """Reject send grants and untyped resolution authority at this boundary."""
        if type(self.authorization) is not HttpResolutionGrant:
            raise TypeError("authorized resolution requires HttpResolutionGrant")

    @property
    def request(self) -> FinalizedWireRequest:
        """Return the exact finalized request being resolved."""
        return self.authorization.intent.request

    @property
    def occurrence(self) -> HttpRequestOccurrence:
        """Return the exact intended outbound occurrence."""
        return self.authorization.intent.occurrence


@final
class HttpResolutionDenial(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy denial of pre-route resolution, without a resolution grant."""

    intent: HttpResolutionIntent
    authority: ExecutionPolicyAuthority
    code: DiagnosticCode
    timings: HttpExchangeTimings
    capability_id: CapabilityId = _HTTP_RESOLVE_ID
    operation: CapabilityOperation = _HTTP_RESOLVE_OPERATION

    def __post_init__(self) -> None:
        """Bind the denial to one exact resolution intent and policy revision."""
        if type(self.intent) is not HttpResolutionIntent:
            raise TypeError("HTTP resolution denial intent must be HttpResolutionIntent")
        _validate_policy_authority(self.authority, self.intent.effective_policy_fingerprint)
        if type(self.code) is not DiagnosticCode:
            raise TypeError("HTTP resolution denial code must be DiagnosticCode")
        if type(self.timings) is not HttpExchangeTimings:
            raise TypeError("HTTP resolution denial timings must be HttpExchangeTimings")
        validate_pre_send_timing_truth(FailurePhase.POLICY, self.timings)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_HTTP_RESOLVE_ID,
            operation=_HTTP_RESOLVE_OPERATION,
        )

    @property
    def request(self) -> FinalizedWireRequest:
        """Return the exact finalized request denied before resolution."""
        return self.intent.request

    @property
    def occurrence(self) -> HttpRequestOccurrence:
        """Return the exact intended outbound occurrence that was denied."""
        return self.intent.occurrence


@final
class HttpResolutionFailure(msgspec.Struct, frozen=True, kw_only=True):
    """A failed authorized name-resolution operation before any route or send grant existed."""

    request: AuthorizedResolutionRequest
    failure: TransportFailure
    timings: HttpExchangeTimings

    def __post_init__(self) -> None:
        """Require an exact DNS failure and pre-send timing cutoff."""
        if type(self.request) is not AuthorizedResolutionRequest:
            raise TypeError("resolution failure request must be AuthorizedResolutionRequest")
        if type(self.failure) is not TransportFailure:
            raise TypeError("resolution failure requires TransportFailure")
        if self.failure.phase is not FailurePhase.DNS:
            raise ValueError("resolution failure must stop in the DNS phase")
        if type(self.timings) is not HttpExchangeTimings:
            raise TypeError("resolution failure timings must be HttpExchangeTimings")
        validate_pre_send_timing_truth(FailurePhase.DNS, self.timings)

    @property
    def occurrence(self) -> HttpRequestOccurrence:
        """Return the unresolved request's exact intended occurrence."""
        return self.request.occurrence


@final
class HttpAuthorizationDenial(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy denial of an exact route-pinned send intent, without a send grant."""

    intent: HttpAuthorizationIntent
    authority: ExecutionPolicyAuthority
    code: DiagnosticCode
    timings: HttpExchangeTimings
    capability_id: CapabilityId = _HTTP_EXECUTE_ID
    operation: CapabilityOperation = _HTTP_EXECUTE_OPERATION

    def __post_init__(self) -> None:
        """Bind a value-free denial to the exact policy revision and send capability."""
        if type(self.intent) is not HttpAuthorizationIntent:
            raise TypeError("HTTP authorization denial intent must be HttpAuthorizationIntent")
        _validate_required_safeguard_approvals(self.intent)
        _validate_policy_authority(self.authority, self.intent.effective_policy_fingerprint)
        if type(self.code) is not DiagnosticCode:
            raise TypeError("HTTP authorization denial code must be DiagnosticCode")
        if type(self.timings) is not HttpExchangeTimings:
            raise TypeError("HTTP authorization denial timings must be HttpExchangeTimings")
        validate_pre_send_timing_truth(FailurePhase.POLICY, self.timings)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_HTTP_EXECUTE_ID,
            operation=_HTTP_EXECUTE_OPERATION,
        )

    @property
    def request(self) -> FinalizedWireRequest:
        """Return the exact finalized request denied before transport execution."""
        return self.intent.request

    @property
    def occurrence(self) -> HttpRequestOccurrence:
        """Return the exact intended outbound occurrence that was denied."""
        return self.intent.occurrence

    @property
    def route(self) -> ResolvedRoute:
        """Return the exact resolved route whose send authority was denied."""
        return self.intent.route


@final
class HttpMutationApprovalDenial(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy denial of a required unsafe-method mutation safeguard."""

    intent: HttpAuthorizationIntent
    authority: ExecutionPolicyAuthority
    code: DiagnosticCode
    timings: HttpExchangeTimings
    capability_id: CapabilityId = _HTTP_MUTATION_ID
    operation: CapabilityOperation = _HTTP_MUTATION_OPERATION

    def __post_init__(self) -> None:
        """Bind the negative decision to one exact missing mutation approval."""
        if type(self.intent) is not HttpAuthorizationIntent:
            raise TypeError("HTTP mutation denial intent must be HttpAuthorizationIntent")
        if not _requires_mutation_approval(self.intent):
            raise ValueError("HTTP mutation denial requires an approval-gated unsafe method")
        if self.intent.mutation_approval is not None:
            raise ValueError("HTTP mutation denial cannot carry a positive mutation approval")
        _validate_policy_authority(self.authority, self.intent.effective_policy_fingerprint)
        if type(self.code) is not DiagnosticCode:
            raise TypeError("HTTP mutation denial code must be DiagnosticCode")
        if type(self.timings) is not HttpExchangeTimings:
            raise TypeError("HTTP mutation denial timings must be HttpExchangeTimings")
        validate_pre_send_timing_truth(FailurePhase.POLICY, self.timings)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_HTTP_MUTATION_ID,
            operation=_HTTP_MUTATION_OPERATION,
        )

    @property
    def request(self) -> FinalizedWireRequest:
        """Return the exact finalized unsafe request whose safeguard was denied."""
        return self.intent.request

    @property
    def occurrence(self) -> HttpRequestOccurrence:
        """Return the exact intended outbound occurrence that was denied."""
        return self.intent.occurrence

    @property
    def route(self) -> ResolvedRoute:
        """Return the exact route pinned before mutation authority was denied."""
        return self.intent.route


@final
class HttpProxyDnsApprovalDenial(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy denial of proxy-side target resolution for one exact route."""

    intent: HttpAuthorizationIntent
    authority: ExecutionPolicyAuthority
    code: DiagnosticCode
    timings: HttpExchangeTimings
    capability_id: CapabilityId = _PROXY_DNS_ID
    operation: CapabilityOperation = _PROXY_DNS_OPERATION

    def __post_init__(self) -> None:
        """Bind the negative decision to one exact missing proxy-DNS approval."""
        if type(self.intent) is not HttpAuthorizationIntent:
            raise TypeError("HTTP proxy DNS denial intent must be HttpAuthorizationIntent")
        if not _requires_proxy_dns_approval(self.intent):
            raise ValueError("HTTP proxy DNS denial requires a proxy-resolved target")
        if self.intent.proxy_dns_approval is not None:
            raise ValueError("HTTP proxy DNS denial cannot carry a positive proxy DNS approval")
        _validate_policy_authority(self.authority, self.intent.effective_policy_fingerprint)
        if type(self.code) is not DiagnosticCode:
            raise TypeError("HTTP proxy DNS denial code must be DiagnosticCode")
        if type(self.timings) is not HttpExchangeTimings:
            raise TypeError("HTTP proxy DNS denial timings must be HttpExchangeTimings")
        validate_pre_send_timing_truth(FailurePhase.POLICY, self.timings)
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=_PROXY_DNS_ID,
            operation=_PROXY_DNS_OPERATION,
        )

    @property
    def request(self) -> FinalizedWireRequest:
        """Return the exact finalized request whose proxy resolution was denied."""
        return self.intent.request

    @property
    def occurrence(self) -> HttpRequestOccurrence:
        """Return the exact intended outbound occurrence that was denied."""
        return self.intent.occurrence

    @property
    def route(self) -> ResolvedRoute:
        """Return the exact delegated route whose DNS safeguard was denied."""
        return self.intent.route


type HttpPreSendOutcome = (
    HttpResolutionDenial
    | HttpResolutionFailure
    | HttpAuthorizationDenial
    | HttpMutationApprovalDenial
    | HttpProxyDnsApprovalDenial
)


def is_http_pre_send_outcome(value: object) -> TypeGuard[HttpPreSendOutcome]:
    """Narrow arbitrary input to the closed pre-send outcome union."""
    return type(value) in {
        HttpResolutionDenial,
        HttpResolutionFailure,
        HttpAuthorizationDenial,
        HttpMutationApprovalDenial,
        HttpProxyDnsApprovalDenial,
    }
