"""Authorized external-analysis contracts kept outside pure diff evaluation."""

from __future__ import annotations

from typing import Final
from typing import Protocol
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.analysis_budget_validation import validate_analysis_budgets
from comparo.core.domain.analysis_results import AnalysisBudgetExhausted as AnalysisBudgetExhausted
from comparo.core.domain.analysis_results import AnalysisCancelled as AnalysisCancelled
from comparo.core.domain.analysis_results import AnalysisEvidence as AnalysisEvidence
from comparo.core.domain.analysis_results import AnalysisExecutionResult
from comparo.core.domain.analysis_results import AnalysisMalformed as AnalysisMalformed
from comparo.core.domain.analysis_results import AnalysisProviderFailed as AnalysisProviderFailed
from comparo.core.domain.analysis_results import AnalysisSucceeded as AnalysisSucceeded
from comparo.core.domain.analysis_results import AnalysisTimedOut as AnalysisTimedOut
from comparo.core.domain.analysis_results import AnalysisVerdict as AnalysisVerdict
from comparo.core.domain.analysis_types import AnalysisContractVersion
from comparo.core.domain.analysis_types import AnalysisModelId
from comparo.core.domain.analysis_types import AnalysisProviderId
from comparo.core.domain.analysis_types import AnalysisSchemaId
from comparo.core.domain.analysis_usage import (
    AnalysisBudgetUsageReport as AnalysisBudgetUsageReport,
)
from comparo.core.domain.analysis_usage import analysis_usage_budget
from comparo.core.domain.analysis_usage import budget_contract_matches
from comparo.core.domain.analysis_usage import validate_analysis_usage_report
from comparo.core.domain.analysis_usage import validate_success_usage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.capabilities import CapabilityDenialReason
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import is_sensitive_value

ANALYSIS_CAPABILITY_ID: Final = CapabilityId(
    namespace="comparo.core",
    name="external-analysis",
    version="1",
)
ANALYSIS_EXECUTE_OPERATION: Final = CapabilityOperation(value="execute")
SECRET_EGRESS_CAPABILITY_ID: Final = CapabilityId(
    namespace="comparo.core",
    name="secret-egress",
    version="1",
)
SECRET_EGRESS_OPERATION: Final = CapabilityOperation(value="egress")


@final
class AnalysisProvider(msgspec.Struct, frozen=True, kw_only=True):
    """A versioned provider/model and its declared data destination."""

    provider: AnalysisProviderId
    provider_version: AnalysisContractVersion
    model: AnalysisModelId
    model_version: AnalysisContractVersion
    destination: NamedCapabilityTarget

    def __post_init__(self) -> None:
        """Require explicit stable identities and a destination target."""
        if type(self.provider) is not AnalysisProviderId:
            raise TypeError("analysis provider must be an AnalysisProviderId")
        if type(self.provider_version) is not AnalysisContractVersion:
            raise TypeError("analysis provider version must be an AnalysisContractVersion")
        if type(self.model) is not AnalysisModelId:
            raise TypeError("analysis model must be an AnalysisModelId")
        if type(self.model_version) is not AnalysisContractVersion:
            raise TypeError("analysis model version must be an AnalysisContractVersion")
        if type(self.destination) is not NamedCapabilityTarget:
            raise TypeError("analysis destination must be a NamedCapabilityTarget")
        if self.destination.kind is not CapabilityTargetKind.DESTINATION:
            raise ValueError("analysis destination must use the destination target kind")


@final
class AnalysisSchema(msgspec.Struct, frozen=True, kw_only=True):
    """The exact structured-output contract requested from an analysis provider."""

    identifier: AnalysisSchemaId
    version: AnalysisContractVersion
    fingerprint: Fingerprint
    allows_empty_output: bool = False

    def __post_init__(self) -> None:
        """Require a stable identity, explicit version, and content fingerprint."""
        if type(self.identifier) is not AnalysisSchemaId:
            raise TypeError("analysis schema identifier must be an AnalysisSchemaId")
        if type(self.version) is not AnalysisContractVersion:
            raise TypeError("analysis schema version must be an AnalysisContractVersion")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("analysis schema fingerprint must be a Fingerprint")
        if type(self.allows_empty_output) is not bool:
            raise TypeError("analysis schema empty-output permission must be bool")


type AnalysisInputSource = BodyId | ViewId | FactId


@final
class AnalysisInput(msgspec.Struct, frozen=True, kw_only=True):
    """A bounded data descriptor; payload access remains an authorized runtime concern."""

    source: AnalysisInputSource
    digest: Fingerprint | SensitiveValue[Fingerprint]
    bytes: ByteCount
    sensitivity: Sensitivity
    media_type: MediaType | None = None

    def __post_init__(self) -> None:
        """Require typed source identity and non-payload metadata."""
        if type(self.source) not in {BodyId, ViewId, FactId}:
            raise TypeError("analysis input source must be BodyId, ViewId, or FactId")
        if type(self.digest) is not Fingerprint and not (
            is_sensitive_value(self.digest) and self.digest.payload_type is Fingerprint
        ):
            raise TypeError("analysis input digest must contain a Fingerprint")
        if type(self.bytes) is not ByteCount:
            raise TypeError("analysis input bytes must be a ByteCount")
        if type(self.sensitivity) is not Sensitivity:
            raise TypeError("analysis input sensitivity must be a Sensitivity")
        digest_is_sensitive = is_sensitive_value(self.digest)
        if digest_is_sensitive != (self.sensitivity is Sensitivity.SENSITIVE):
            raise ValueError("analysis input digest containment must match input sensitivity")
        if self.media_type is not None and type(self.media_type) is not MediaType:
            raise TypeError("analysis input media type must be absent or MediaType")


@final
class AnalysisRequest(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable analysis recipe awaiting an operator-owned authorization grant."""

    analysis_id: AnalysisId
    provider: AnalysisProvider
    inputs: tuple[AnalysisInput, ...]
    output_schema: AnalysisSchema
    budgets: tuple[ResourceBudget, ...]
    timeout: Duration
    fingerprint: Fingerprint | SensitiveValue[Fingerprint]

    def __post_init__(self) -> None:
        """Require exact model, inputs, output contract, and finite operational bounds."""
        if type(self.analysis_id) is not AnalysisId:
            raise TypeError("analysis request requires an AnalysisId")
        if type(self.provider) is not AnalysisProvider:
            raise TypeError("analysis request requires an AnalysisProvider")
        _validate_inputs(self.inputs)
        if type(self.output_schema) is not AnalysisSchema:
            raise TypeError("analysis request requires an AnalysisSchema")
        if type(self.timeout) is not Duration:
            raise TypeError("analysis timeout must be a Duration")
        validate_analysis_budgets(
            self.budgets,
            analysis_id=self.analysis_id,
            input_bytes=tuple(item.bytes for item in self.inputs),
            timeout=self.timeout,
        )
        _validate_request_fingerprint(self.fingerprint, inputs=self.inputs)


@final
class AnalysisSecretEgressApproval(msgspec.Struct, frozen=True, kw_only=True):
    """ExecutionPolicy authority to disclose sensitive inputs for one exact analysis."""

    analysis: AnalysisRequest
    authority: ExecutionPolicyAuthority
    capability_id: CapabilityId = SECRET_EGRESS_CAPABILITY_ID
    operation: CapabilityOperation = SECRET_EGRESS_OPERATION

    def __post_init__(self) -> None:
        """Bind the secret-egress approval to the full immutable request and policy revision."""
        if type(self.analysis) is not AnalysisRequest:
            raise TypeError("secret-egress approval must bind an AnalysisRequest")
        if type(self.authority) is not ExecutionPolicyAuthority:
            raise TypeError("analysis authority must be exact ExecutionPolicyAuthority")
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=SECRET_EGRESS_CAPABILITY_ID,
            operation=SECRET_EGRESS_OPERATION,
        )


@final
class AnalysisAuthorizationIntent(msgspec.Struct, frozen=True, kw_only=True):
    """Complete immutable input presented to the external-analysis authority."""

    analysis: AnalysisRequest
    effective_policy_authority: ExecutionPolicyAuthority
    secret_egress_approval: AnalysisSecretEgressApproval | None = None

    def __post_init__(self) -> None:
        """Bind any sensitive-data approval to this exact request and policy revision."""
        if type(self.analysis) is not AnalysisRequest:
            raise TypeError("analysis authorization intent must bind an AnalysisRequest")
        if type(self.effective_policy_authority) is not ExecutionPolicyAuthority:
            raise TypeError(
                "effective analysis policy binding must be exact ExecutionPolicyAuthority"
            )
        sensitive = any(item.sensitivity is Sensitivity.SENSITIVE for item in self.analysis.inputs)
        if sensitive and self.secret_egress_approval is None:
            raise ValueError("sensitive analysis requires an exact secret-egress approval")
        if not sensitive and self.secret_egress_approval is not None:
            raise ValueError("public analysis must not carry unnecessary secret-egress authority")
        if self.secret_egress_approval is not None:
            if type(self.secret_egress_approval) is not AnalysisSecretEgressApproval:
                raise TypeError("secret-egress approval must be AnalysisSecretEgressApproval")
            if (
                self.secret_egress_approval.analysis != self.analysis
                or self.secret_egress_approval.authority != self.effective_policy_authority
            ):
                raise ValueError("secret-egress approval is not bound to this analysis intent")


@final
class AnalysisAuthorizationGrant(msgspec.Struct, frozen=True, kw_only=True):
    """Final ExecutionPolicy decision bound to one complete analysis intent."""

    intent: AnalysisAuthorizationIntent
    authority: ExecutionPolicyAuthority
    capability_id: CapabilityId = ANALYSIS_CAPABILITY_ID
    operation: CapabilityOperation = ANALYSIS_EXECUTE_OPERATION

    def __post_init__(self) -> None:
        """Require the exact built-in contract, policy revision, and approval issuer."""
        if type(self.intent) is not AnalysisAuthorizationIntent:
            raise TypeError(
                "analysis authorization grant intent must be AnalysisAuthorizationIntent"
            )
        if type(self.authority) is not ExecutionPolicyAuthority:
            raise TypeError("analysis authority must be exact ExecutionPolicyAuthority")
        if self.authority != self.intent.effective_policy_authority:
            raise ValueError("analysis grant authority is not the exact effective policy authority")
        _validate_contract(
            actual_id=self.capability_id,
            actual_operation=self.operation,
            capability_id=ANALYSIS_CAPABILITY_ID,
            operation=ANALYSIS_EXECUTE_OPERATION,
        )


@final
class AuthorizedAnalysisRequest(msgspec.Struct, frozen=True, kw_only=True):
    """The only request type accepted by an effectful analysis executor."""

    grant: AnalysisAuthorizationGrant

    def __post_init__(self) -> None:
        """Reject generic or unvalidated authority decisions at the executor boundary."""
        if type(self.grant) is not AnalysisAuthorizationGrant:
            raise TypeError("authorized analysis requires an AnalysisAuthorizationGrant")

    @property
    def analysis(self) -> AnalysisRequest:
        """Return the exact immutable recipe nested in the authorization grant."""
        return self.grant.intent.analysis


@final
class AnalysisExecutionFact(msgspec.Struct, frozen=True, kw_only=True):
    """One terminal external-analysis result bound to its exact authorized request."""

    request: AuthorizedAnalysisRequest
    result: AnalysisExecutionResult
    usage: AnalysisBudgetUsageReport

    def __post_init__(self) -> None:
        """Reject untyped results and budget evidence from another analysis contract."""
        if type(self.request) is not AuthorizedAnalysisRequest:
            raise TypeError("analysis execution fact requires an AuthorizedAnalysisRequest")
        if type(self.result) not in {
            AnalysisSucceeded,
            AnalysisTimedOut,
            AnalysisProviderFailed,
            AnalysisMalformed,
            AnalysisCancelled,
            AnalysisBudgetExhausted,
        }:
            raise TypeError("analysis execution fact requires a typed terminal result")
        analysis = self.request.analysis
        validate_analysis_usage_report(
            analysis.budgets,
            self.usage,
            expected_input_bytes=ByteCount(
                value=sum(item.bytes.value for item in analysis.inputs),
            ),
        )
        if type(self.result) is AnalysisSucceeded:
            validate_success_usage(
                self.usage,
                allows_empty_output=analysis.output_schema.allows_empty_output,
            )
        if (
            type(self.result) is AnalysisTimedOut
            and self.result.elapsed.nanoseconds < self.request.analysis.timeout.nanoseconds
        ):
            raise ValueError("analysis timeout evidence must reach the authorized timeout")
        if type(self.result) is AnalysisTimedOut:
            deadline = analysis_usage_budget(self.usage, BudgetResource.DEADLINE)
            elapsed = cast("Duration", deadline.usage.consumed)
            if elapsed.nanoseconds < self.result.elapsed.nanoseconds:
                raise ValueError("analysis timeout must be backed by terminal deadline usage")
        if type(self.result) is AnalysisBudgetExhausted:
            _validate_exhausted_budget(self.request.analysis, self.result.exhaustion, self.usage)


def validate_analysis_execution(
    request: AuthorizedAnalysisRequest,
    fact: AnalysisExecutionFact,
) -> None:
    """Require an executor result to belong to the exact request passed to that call."""
    if type(request) is not AuthorizedAnalysisRequest:
        raise TypeError("analysis execution validation requires an AuthorizedAnalysisRequest")
    if type(fact) is not AnalysisExecutionFact:
        raise TypeError("analysis execution validation requires an AnalysisExecutionFact")
    if fact.request != request:
        raise ValueError("analysis execution fact belongs to a different authorized request")


def _validate_exhausted_budget(
    analysis: AnalysisRequest,
    exhaustion: BudgetExceeded,
    usage: AnalysisBudgetUsageReport,
) -> None:
    exhausted = exhaustion.budget
    if not any(budget_contract_matches(exhausted, item) for item in analysis.budgets):
        raise ValueError("analysis exhaustion is not bound to an authorized request budget")
    if exhausted != analysis_usage_budget(usage, exhausted.resource):
        raise ValueError("analysis exhaustion budget must equal its terminal usage snapshot")


def _validate_inputs(inputs: tuple[AnalysisInput, ...]) -> None:
    if type(inputs) is not tuple:
        raise TypeError("analysis inputs must be an immutable tuple")
    if not inputs:
        raise ValueError("analysis request requires at least one input")
    if any(type(item) is not AnalysisInput for item in inputs):
        raise TypeError("analysis inputs must contain AnalysisInput values")


def _validate_contract(
    *,
    actual_id: CapabilityId,
    actual_operation: CapabilityOperation,
    capability_id: CapabilityId,
    operation: CapabilityOperation,
) -> None:
    if type(actual_id) is not CapabilityId or type(actual_operation) is not CapabilityOperation:
        raise TypeError("analysis authority contract identity must use typed capability values")
    if actual_id != capability_id or actual_operation != operation:
        raise ValueError("analysis authority grant uses the wrong built-in capability contract")


def _validate_request_fingerprint(
    fingerprint: Fingerprint | SensitiveValue[Fingerprint],
    *,
    inputs: tuple[AnalysisInput, ...],
) -> None:
    if type(fingerprint) is not Fingerprint and not (
        is_sensitive_value(fingerprint) and fingerprint.payload_type is Fingerprint
    ):
        raise TypeError("analysis fingerprint must contain a Fingerprint")
    contains_sensitive_input = any(item.sensitivity is Sensitivity.SENSITIVE for item in inputs)
    if is_sensitive_value(fingerprint) != contains_sensitive_input:
        raise ValueError("analysis fingerprint containment must match input sensitivity")


class AnalysisAuthorizationRequirement(DomainStrEnum):
    """The exact external-analysis safeguard refused by policy."""

    EXECUTION = "execution"
    SECRET_EGRESS = "secret-egress"  # noqa: S105


@final
class AnalysisDenied(msgspec.Struct, frozen=True, kw_only=True):
    """A typed policy denial bound to one complete immutable analysis request."""

    analysis: AnalysisRequest
    requirement: AnalysisAuthorizationRequirement
    reason: CapabilityDenialReason
    authority: ExecutionPolicyAuthority

    def __post_init__(self) -> None:
        """Bind the denied safeguard, request, and exact effective policy revision."""
        if type(self.analysis) is not AnalysisRequest:
            raise TypeError("analysis denial must bind an AnalysisRequest")
        if type(self.requirement) is not AnalysisAuthorizationRequirement:
            raise TypeError("analysis denial requirement must be AnalysisAuthorizationRequirement")
        if type(self.reason) is not CapabilityDenialReason:
            raise TypeError("analysis denial reason must be CapabilityDenialReason")
        if type(self.authority) is not ExecutionPolicyAuthority:
            raise TypeError("analysis denial authority must be exact ExecutionPolicyAuthority")
        sensitive = any(item.sensitivity is Sensitivity.SENSITIVE for item in self.analysis.inputs)
        if self.requirement is AnalysisAuthorizationRequirement.SECRET_EGRESS and not sensitive:
            raise ValueError("public analysis cannot be denied for unnecessary secret egress")


type AnalysisResult = AnalysisDenied | AnalysisExecutionFact


class AnalysisExecutor(Protocol):
    """Narrow runtime port for an authorized, bounded external analysis."""

    def execute(self, request: AuthorizedAnalysisRequest) -> AnalysisExecutionFact:
        """Execute one request and return a terminal fact carrying that exact authority."""
        ...
