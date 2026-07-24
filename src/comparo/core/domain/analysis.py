"""Authorized external-analysis contracts kept outside pure diff evaluation."""

from __future__ import annotations

import re
from typing import Final
from typing import Protocol
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.analysis_results import AnalysisBudgetExhausted as AnalysisBudgetExhausted
from comparo.core.domain.analysis_results import AnalysisCancelled as AnalysisCancelled
from comparo.core.domain.analysis_results import AnalysisEvidence as AnalysisEvidence
from comparo.core.domain.analysis_results import AnalysisExecutionResult
from comparo.core.domain.analysis_results import AnalysisMalformed as AnalysisMalformed
from comparo.core.domain.analysis_results import AnalysisProviderFailed as AnalysisProviderFailed
from comparo.core.domain.analysis_results import AnalysisSucceeded as AnalysisSucceeded
from comparo.core.domain.analysis_results import AnalysisTimedOut as AnalysisTimedOut
from comparo.core.domain.analysis_results import AnalysisVerdict as AnalysisVerdict
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.capabilities import CapabilityCategory
from comparo.core.domain.capabilities import CapabilityDenied
from comparo.core.domain.capabilities import CapabilityGranted
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import CapabilityRequest
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import ViewId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity

_PROVIDER_TOKEN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")

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

_ANALYSIS_BUDGET_OPERATIONS: Final = {
    BudgetResource.EXTERNAL_INPUT_BYTES: BudgetOperation(value="analysis.input"),
    BudgetResource.EXTERNAL_OUTPUT_BYTES: BudgetOperation(value="analysis.output"),
    BudgetResource.DEADLINE: BudgetOperation(value="analysis.deadline"),
    BudgetResource.EXTERNAL_WORK: BudgetOperation(value="analysis.work"),
}


@final
class AnalysisProvider(msgspec.Struct, frozen=True, kw_only=True):
    """A versioned provider/model and its declared data destination."""

    provider: str
    provider_version: str
    model: str
    model_version: str
    destination: NamedCapabilityTarget

    def __post_init__(self) -> None:
        """Require explicit stable identities and a destination target."""
        if type(self.provider) is not str or _PROVIDER_TOKEN.fullmatch(self.provider) is None:
            raise ValueError("analysis provider must be a lowercase namespaced token")
        for label, value in (
            ("provider version", self.provider_version),
            ("model", self.model),
            ("model version", self.model_version),
        ):
            if type(value) is not str or not value.strip():
                raise ValueError(f"analysis {label} must be non-blank text")
        if type(self.destination) is not NamedCapabilityTarget:
            raise TypeError("analysis destination must be a NamedCapabilityTarget")
        if self.destination.kind is not CapabilityTargetKind.DESTINATION:
            raise ValueError("analysis destination must use the destination target kind")


@final
class AnalysisSchema(msgspec.Struct, frozen=True, kw_only=True):
    """The exact structured-output contract requested from an analysis provider."""

    identifier: str
    version: str
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        """Require a stable identity, explicit version, and content fingerprint."""
        if type(self.identifier) is not str or _PROVIDER_TOKEN.fullmatch(self.identifier) is None:
            raise ValueError("analysis schema identifier must be a lowercase namespaced token")
        if type(self.version) is not str or not self.version.strip():
            raise ValueError("analysis schema version must be non-blank text")
        if type(self.fingerprint) is not Fingerprint:
            raise TypeError("analysis schema fingerprint must be a Fingerprint")


type AnalysisInputSource = BodyId | ViewId | FactId


@final
class AnalysisInput(msgspec.Struct, frozen=True, kw_only=True):
    """A bounded data descriptor; payload access remains an authorized runtime concern."""

    source: AnalysisInputSource
    digest: Fingerprint | SensitiveValue[Fingerprint]
    bytes: ByteCount
    sensitivity: Sensitivity
    media_type: str | None = None

    def __post_init__(self) -> None:
        """Require typed source identity and non-payload metadata."""
        if type(self.source) not in {BodyId, ViewId, FactId}:
            raise TypeError("analysis input source must be BodyId, ViewId, or FactId")
        if type(self.digest) is not Fingerprint and not (
            isinstance(self.digest, SensitiveValue) and self.digest.payload_type is Fingerprint
        ):
            raise TypeError("analysis input digest must contain a Fingerprint")
        if type(self.bytes) is not ByteCount:
            raise TypeError("analysis input bytes must be a ByteCount")
        if type(self.sensitivity) is not Sensitivity:
            raise TypeError("analysis input sensitivity must be a Sensitivity")
        digest_is_sensitive = isinstance(self.digest, SensitiveValue)
        if digest_is_sensitive != (self.sensitivity is Sensitivity.SENSITIVE):
            raise ValueError("analysis input digest containment must match input sensitivity")
        if self.media_type is not None and (
            type(self.media_type) is not str or not self.media_type.strip()
        ):
            raise ValueError("analysis input media type must be absent or non-blank text")


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
        _validate_budgets(self.budgets, inputs=self.inputs, timeout=self.timeout)
        _validate_request_fingerprint(self.fingerprint, inputs=self.inputs)


@final
class AnalysisAuthorizationGrant(msgspec.Struct, frozen=True, kw_only=True):
    """Operator authority bound to one exact immutable analysis recipe.

    The complete request is nested in the grant instead of re-stating only its destination. This
    prevents an authorized destination from being reused with a different model, input set,
    output schema, timeout, or budget set.
    """

    analysis: AnalysisRequest
    capability: CapabilityGranted
    secret_egress: CapabilityGranted | None = None

    def __post_init__(self) -> None:
        """Require the built-in operation and externally owned authority for this recipe."""
        if type(self.analysis) is not AnalysisRequest:
            raise TypeError("analysis authorization must bind an AnalysisRequest")
        if type(self.capability) is not CapabilityGranted:
            raise TypeError("analysis authorization requires a final CapabilityGranted")
        _validate_analysis_capability(
            self.capability.request,
            destination=self.analysis.provider.destination,
        )
        contains_sensitive_input = any(
            item.sensitivity is Sensitivity.SENSITIVE for item in self.analysis.inputs
        )
        if contains_sensitive_input:
            if type(self.secret_egress) is not CapabilityGranted:
                raise ValueError("sensitive analysis requires an exact secret-egress grant")
            _validate_secret_egress_capability(
                self.secret_egress.request,
                destination=self.analysis.provider.destination,
            )
            if self.secret_egress.authority != self.capability.authority:
                raise ValueError("analysis capability grants must use the same policy authority")
        elif self.secret_egress is not None:
            raise ValueError("public analysis must not carry unnecessary secret-egress authority")


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
        return self.grant.analysis


def _validate_inputs(inputs: tuple[AnalysisInput, ...]) -> None:
    if type(inputs) is not tuple:
        raise TypeError("analysis inputs must be an immutable tuple")
    if not inputs:
        raise ValueError("analysis request requires at least one input")
    if any(type(item) is not AnalysisInput for item in inputs):
        raise TypeError("analysis inputs must contain AnalysisInput values")


def _validate_analysis_capability(
    capability: CapabilityRequest,
    *,
    destination: NamedCapabilityTarget | None,
) -> None:
    if capability.category is not CapabilityCategory.EXTERNAL_ANALYSIS:
        raise ValueError("analysis authorization must grant external analysis")
    if capability.capability_id != ANALYSIS_CAPABILITY_ID:
        raise ValueError("analysis authorization must use the built-in capability identity")
    if capability.operation != ANALYSIS_EXECUTE_OPERATION:
        raise ValueError("analysis authorization must use the built-in execute operation")
    if type(capability.target) is not NamedCapabilityTarget:
        raise TypeError("analysis authorization target must be a NamedCapabilityTarget")
    if capability.target.kind is not CapabilityTargetKind.DESTINATION:
        raise ValueError("analysis authorization target must identify a destination")
    if destination is not None and capability.target != destination:
        raise ValueError("analysis authorization target must match the provider destination")


def _validate_secret_egress_capability(
    capability: CapabilityRequest,
    *,
    destination: NamedCapabilityTarget,
) -> None:
    if capability.category is not CapabilityCategory.SECRET_EGRESS:
        raise ValueError("sensitive analysis authorization must grant secret egress")
    if capability.capability_id != SECRET_EGRESS_CAPABILITY_ID:
        raise ValueError("secret-egress authorization must use the built-in capability identity")
    if capability.operation != SECRET_EGRESS_OPERATION:
        raise ValueError("secret-egress authorization must use the built-in egress operation")
    if capability.target != destination:
        raise ValueError("secret-egress authorization target must match the provider destination")


def _validate_budgets(
    budgets: tuple[ResourceBudget, ...],
    *,
    inputs: tuple[AnalysisInput, ...],
    timeout: Duration,
) -> None:
    by_resource = _index_analysis_budgets(budgets)
    _validate_analysis_budget_coordinates(by_resource)
    _validate_analysis_budget_limits(by_resource, inputs=inputs, timeout=timeout)


def _index_analysis_budgets(
    budgets: tuple[ResourceBudget, ...],
) -> dict[BudgetResource, ResourceBudget]:
    if type(budgets) is not tuple:
        raise TypeError("analysis budgets must be an immutable tuple")
    if any(type(budget) is not ResourceBudget for budget in budgets):
        raise TypeError("analysis budgets must contain ResourceBudget values")
    if len(budgets) != len(_ANALYSIS_BUDGET_OPERATIONS):
        raise ValueError("analysis requires exact input, output, deadline, and work budgets")
    by_resource = {budget.resource: budget for budget in budgets}
    if frozenset(by_resource) != frozenset(_ANALYSIS_BUDGET_OPERATIONS):
        raise ValueError("analysis budgets must contain each required dimension exactly once")
    return by_resource


def _validate_analysis_budget_coordinates(
    by_resource: dict[BudgetResource, ResourceBudget],
) -> None:
    for resource, operation in _ANALYSIS_BUDGET_OPERATIONS.items():
        budget = by_resource[resource]
        if budget.scope_kind is not BudgetScopeKind.EXTERNAL_CAPABILITY:
            raise ValueError("analysis budgets must use the external-capability scope")
        if budget.operation != operation:
            raise ValueError("analysis budget must use its canonical operation identity")


def _validate_analysis_budget_limits(
    by_resource: dict[BudgetResource, ResourceBudget],
    *,
    inputs: tuple[AnalysisInput, ...],
    timeout: Duration,
) -> None:
    input_limit = cast("ByteCount", by_resource[BudgetResource.EXTERNAL_INPUT_BYTES].effective)
    deadline = cast("Duration", by_resource[BudgetResource.DEADLINE].effective)
    if sum(item.bytes.value for item in inputs) > input_limit.value:
        raise ValueError("analysis input bytes exceed the effective external input budget")
    if timeout.nanoseconds > deadline.nanoseconds:
        raise ValueError("analysis timeout exceeds the effective deadline budget")


def _validate_request_fingerprint(
    fingerprint: Fingerprint | SensitiveValue[Fingerprint],
    *,
    inputs: tuple[AnalysisInput, ...],
) -> None:
    if type(fingerprint) is not Fingerprint and not (
        isinstance(fingerprint, SensitiveValue) and fingerprint.payload_type is Fingerprint
    ):
        raise TypeError("analysis fingerprint must contain a Fingerprint")
    contains_sensitive_input = any(item.sensitivity is Sensitivity.SENSITIVE for item in inputs)
    if isinstance(fingerprint, SensitiveValue) != contains_sensitive_input:
        raise ValueError("analysis fingerprint containment must match input sensitivity")


@final
class AnalysisDenied(msgspec.Struct, frozen=True, kw_only=True):
    """External analysis was denied before provider invocation."""

    analysis: AnalysisRequest
    decision: CapabilityDenied

    def __post_init__(self) -> None:
        """Bind the denial to the exact recipe and its requested destination."""
        if type(self.analysis) is not AnalysisRequest:
            raise TypeError("analysis denial must bind an AnalysisRequest")
        if type(self.decision) is not CapabilityDenied:
            raise TypeError("analysis denial requires a CapabilityDenied")
        if self.decision.request.category is CapabilityCategory.EXTERNAL_ANALYSIS:
            _validate_analysis_capability(
                self.decision.request,
                destination=self.analysis.provider.destination,
            )
            return
        contains_sensitive_input = any(
            item.sensitivity is Sensitivity.SENSITIVE for item in self.analysis.inputs
        )
        if (
            self.decision.request.category is CapabilityCategory.SECRET_EGRESS
            and contains_sensitive_input
        ):
            _validate_secret_egress_capability(
                self.decision.request,
                destination=self.analysis.provider.destination,
            )
            return
        raise ValueError("analysis denial must refuse required analysis authority")


type AnalysisResult = AnalysisDenied | AnalysisExecutionResult


class AnalysisExecutor(Protocol):
    """Narrow runtime port for an authorized, bounded external analysis."""

    def execute(self, request: AuthorizedAnalysisRequest) -> AnalysisExecutionResult:
        """Execute one already authorized request and return a typed terminal result."""
        ...
