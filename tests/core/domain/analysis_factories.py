"""Shared builders for external-analysis contract tests."""

from uuid import UUID

from comparo.core.domain.analysis import ANALYSIS_CAPABILITY_ID
from comparo.core.domain.analysis import ANALYSIS_EXECUTE_OPERATION
from comparo.core.domain.analysis import SECRET_EGRESS_CAPABILITY_ID
from comparo.core.domain.analysis import SECRET_EGRESS_OPERATION
from comparo.core.domain.analysis import AnalysisAuthorizationGrant
from comparo.core.domain.analysis import AnalysisInput
from comparo.core.domain.analysis import AnalysisProvider
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSchema
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.capabilities import CapabilityCategory
from comparo.core.domain.capabilities import CapabilityGranted
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import CapabilityRequest
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.budget_origin_factories import default_budget_origin


def _fingerprint(character: str = "a") -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=character * 64),
    )


def _sensitive_fingerprint(character: str = "b") -> SensitiveValue[Fingerprint]:
    return SensitiveValue(
        _fingerprint(character),
        origins=(TaintOriginId(value="analysis/input"),),
    )


def _destination(identifier: str = "judge.example.invalid") -> NamedCapabilityTarget:
    return NamedCapabilityTarget(
        kind=CapabilityTargetKind.DESTINATION,
        identifier=identifier,
    )


def _provider() -> AnalysisProvider:
    return AnalysisProvider(
        provider="example.judge",
        provider_version="1",
        model="semantic-judge",
        model_version="2026-07-24",
        destination=_destination(),
    )


def _schema() -> AnalysisSchema:
    return AnalysisSchema(
        identifier="comparo.analysis-result",
        version="1",
        fingerprint=_fingerprint("c"),
    )


def _budget(
    resource: BudgetResource,
    amount: Count | ByteCount | Duration,
    operation: str,
    *,
    scope_kind: BudgetScopeKind = BudgetScopeKind.EXTERNAL_CAPABILITY,
    stage: BudgetStage = BudgetStage.EXTERNAL_CAPABILITY,
) -> ResourceBudget:
    if type(amount) is Count:
        zero: Count | ByteCount | Duration = Count(value=0)
    elif type(amount) is ByteCount:
        zero = ByteCount(value=0)
    else:
        zero = Duration(nanoseconds=0)
    return ResourceBudget(
        resource=resource,
        scope_kind=scope_kind,
        stage=stage,
        operation=BudgetOperation(value=operation),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=amount,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
    )


def _budgets() -> tuple[ResourceBudget, ...]:
    return (
        _budget(BudgetResource.EXTERNAL_INPUT_BYTES, ByteCount(value=12), "analysis.input"),
        _budget(BudgetResource.EXTERNAL_OUTPUT_BYTES, ByteCount(value=100), "analysis.output"),
        _budget(BudgetResource.DEADLINE, Duration(nanoseconds=100), "analysis.deadline"),
        _budget(BudgetResource.EXTERNAL_WORK, Count(value=4), "analysis.work"),
    )


def _input(*, sensitive: bool = True) -> AnalysisInput:
    digest: Fingerprint | SensitiveValue[Fingerprint]
    digest = _sensitive_fingerprint() if sensitive else _fingerprint()
    return AnalysisInput(
        source=BodyId(value=UUID("10000000-0000-0000-0000-000000000001")),
        digest=digest,
        bytes=ByteCount(value=12),
        sensitivity=Sensitivity.SENSITIVE if sensitive else Sensitivity.PUBLIC,
        media_type="application/json",
    )


def _request(*, sensitive: bool = True) -> AnalysisRequest:
    fingerprint: Fingerprint | SensitiveValue[Fingerprint]
    fingerprint = _sensitive_fingerprint("d") if sensitive else _fingerprint("d")
    return AnalysisRequest(
        analysis_id=AnalysisId(value=UUID("20000000-0000-0000-0000-000000000001")),
        provider=_provider(),
        inputs=(_input(sensitive=sensitive),),
        output_schema=_schema(),
        budgets=_budgets(),
        timeout=Duration(nanoseconds=100),
        fingerprint=fingerprint,
    )


def _capability(
    *,
    category: CapabilityCategory = CapabilityCategory.EXTERNAL_ANALYSIS,
    capability_id: CapabilityId = ANALYSIS_CAPABILITY_ID,
    operation: CapabilityOperation = ANALYSIS_EXECUTE_OPERATION,
    target: NamedCapabilityTarget | None = None,
) -> CapabilityRequest:
    return CapabilityRequest(
        category=category,
        capability_id=capability_id,
        operation=operation,
        target=_destination() if target is None else target,
    )


def _authority(character: str = "e") -> ExecutionPolicyAuthority:
    return ExecutionPolicyAuthority(
        policy=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="local"),
                kind=ResourceKind.EXECUTION_POLICY,
                resource_id=ResourceId(value="ci"),
            ),
            fingerprint=_fingerprint(character),
        )
    )


def _grant(
    request: AnalysisRequest,
    capability: CapabilityRequest | None = None,
    *,
    authority: ExecutionPolicyAuthority | None = None,
    egress: CapabilityGranted | None | bool = True,
) -> AnalysisAuthorizationGrant:
    exact_authority = authority or _authority()
    external = CapabilityGranted(
        request=_capability() if capability is None else capability,
        authority=exact_authority,
    )
    secret_egress: CapabilityGranted | None
    if egress is True:
        secret_egress = CapabilityGranted(
            request=CapabilityRequest(
                category=CapabilityCategory.SECRET_EGRESS,
                capability_id=SECRET_EGRESS_CAPABILITY_ID,
                operation=SECRET_EGRESS_OPERATION,
                target=request.provider.destination,
            ),
            authority=exact_authority,
        )
    elif egress is False:
        secret_egress = None
    else:
        secret_egress = egress
    return AnalysisAuthorizationGrant(
        analysis=request,
        capability=external,
        secret_egress=secret_egress,
    )
