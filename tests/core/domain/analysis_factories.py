"""Shared builders for external-analysis contract tests."""

from uuid import UUID

import msgspec

from comparo.core.domain.analysis import ANALYSIS_CAPABILITY_ID
from comparo.core.domain.analysis import ANALYSIS_EXECUTE_OPERATION
from comparo.core.domain.analysis import AnalysisAuthorizationGrant
from comparo.core.domain.analysis import AnalysisAuthorizationIntent
from comparo.core.domain.analysis import AnalysisBudgetUsageReport
from comparo.core.domain.analysis import AnalysisInput
from comparo.core.domain.analysis import AnalysisProvider
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSchema
from comparo.core.domain.analysis import AnalysisSecretEgressApproval
from comparo.core.domain.analysis_types import AnalysisContractVersion
from comparo.core.domain.analysis_types import AnalysisModelId
from comparo.core.domain.analysis_types import AnalysisProviderId
from comparo.core.domain.analysis_types import AnalysisSchemaId
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.capabilities import CapabilityCategory
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import CapabilityRequest
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.http.media_types import MediaType
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import BudgetScope
from comparo.core.domain.scopes import ExternalCapabilityBudgetScope
from comparo.core.domain.scopes import RunScope
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
        provider=AnalysisProviderId(value="example.judge"),
        provider_version=AnalysisContractVersion(value="1"),
        model=AnalysisModelId(value="semantic-judge"),
        model_version=AnalysisContractVersion(value="2026-07-24"),
        destination=_destination(),
    )


def _schema() -> AnalysisSchema:
    return AnalysisSchema(
        identifier=AnalysisSchemaId(value="comparo.analysis-result"),
        version=AnalysisContractVersion(value="1"),
        fingerprint=_fingerprint("c"),
    )


def _budget(
    resource: BudgetResource,
    amount: Count | ByteCount | Duration,
    operation: str,
    *,
    scope_kind: BudgetScopeKind = BudgetScopeKind.EXTERNAL_CAPABILITY,
    stage: BudgetStage = BudgetStage.EXTERNAL_CAPABILITY,
    scope: BudgetScope | None = None,
) -> ResourceBudget:
    if type(amount) is Count:
        zero: Count | ByteCount | Duration = Count(value=0)
    elif type(amount) is ByteCount:
        zero = ByteCount(value=0)
    else:
        zero = Duration(nanoseconds=0)
    exact_scope = (
        _analysis_scope()
        if scope is None and scope_kind is BudgetScopeKind.EXTERNAL_CAPABILITY
        else scope
    )
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
        scope=exact_scope,
    )


def _analysis_id(integer: int = 1) -> AnalysisId:
    return AnalysisId(value=UUID(int=integer))


def _run_scope(integer: int = 10) -> RunScope:
    return RunScope(run_id=RunId(value=UUID(int=integer)))


def _analysis_scope(
    *,
    analysis_id: AnalysisId | None = None,
    run: RunScope | None = None,
) -> ExternalCapabilityBudgetScope:
    return ExternalCapabilityBudgetScope(
        run=_run_scope() if run is None else run,
        analysis_id=_analysis_id() if analysis_id is None else analysis_id,
    )


def _budgets(
    *,
    analysis_id: AnalysisId | None = None,
    run: RunScope | None = None,
) -> tuple[ResourceBudget, ...]:
    scope = _analysis_scope(analysis_id=analysis_id, run=run)
    return (
        _budget(
            BudgetResource.EXTERNAL_INPUT_BYTES,
            ByteCount(value=12),
            "analysis.input",
            scope=scope,
        ),
        _budget(
            BudgetResource.EXTERNAL_OUTPUT_BYTES,
            ByteCount(value=100),
            "analysis.output",
            scope=scope,
        ),
        _budget(
            BudgetResource.DEADLINE,
            Duration(nanoseconds=100),
            "analysis.deadline",
            scope=scope,
        ),
        _budget(BudgetResource.EXTERNAL_WORK, Count(value=4), "analysis.work", scope=scope),
    )


def _input(*, sensitive: bool = True) -> AnalysisInput:
    digest: Fingerprint | SensitiveValue[Fingerprint]
    digest = _sensitive_fingerprint() if sensitive else _fingerprint()
    return AnalysisInput(
        source=BodyId(value=UUID("10000000-0000-0000-0000-000000000001")),
        digest=digest,
        bytes=ByteCount(value=12),
        sensitivity=Sensitivity.SENSITIVE if sensitive else Sensitivity.PUBLIC,
        media_type=MediaType(type="application", subtype="json"),
    )


def _request(*, sensitive: bool = True) -> AnalysisRequest:
    fingerprint: Fingerprint | SensitiveValue[Fingerprint]
    fingerprint = _sensitive_fingerprint("d") if sensitive else _fingerprint("d")
    analysis_id = _analysis_id()
    return AnalysisRequest(
        analysis_id=analysis_id,
        provider=_provider(),
        inputs=(_input(sensitive=sensitive),),
        output_schema=_schema(),
        budgets=_budgets(analysis_id=analysis_id),
        timeout=Duration(nanoseconds=100),
        fingerprint=fingerprint,
    )


def _usage_report(
    request: AnalysisRequest,
    *,
    input_bytes: int = 12,
    output_bytes: int = 10,
    deadline_nanoseconds: int = 50,
    work: int = 2,
) -> AnalysisBudgetUsageReport:
    """Build exact terminal snapshots for the canonical analysis test budgets."""
    return AnalysisBudgetUsageReport(
        snapshots=(
            msgspec.structs.replace(
                request.budgets[0],
                usage=BudgetUsage(
                    consumed=ByteCount(value=input_bytes),
                    in_use=ByteCount(value=0),
                    high_water=ByteCount(value=input_bytes),
                ),
            ),
            msgspec.structs.replace(
                request.budgets[1],
                usage=BudgetUsage(
                    consumed=ByteCount(value=output_bytes),
                    in_use=ByteCount(value=0),
                    high_water=ByteCount(value=output_bytes),
                ),
            ),
            msgspec.structs.replace(
                request.budgets[2],
                usage=BudgetUsage(
                    consumed=Duration(nanoseconds=deadline_nanoseconds),
                    in_use=Duration(nanoseconds=0),
                    high_water=Duration(nanoseconds=deadline_nanoseconds),
                ),
            ),
            msgspec.structs.replace(
                request.budgets[3],
                usage=BudgetUsage(
                    consumed=Count(value=work),
                    in_use=Count(value=0),
                    high_water=Count(value=work),
                ),
            ),
        )
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


def _authority(
    character: str = "e",
    *,
    resource_id: str = "ci",
) -> ExecutionPolicyAuthority:
    return ExecutionPolicyAuthority(
        policy=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="local"),
                kind=ResourceKind.EXECUTION_POLICY,
                resource_id=ResourceId(value=resource_id),
            ),
            fingerprint=_fingerprint(character),
        )
    )


def _grant(
    request: AnalysisRequest,
    *,
    authority: ExecutionPolicyAuthority | None = None,
    egress: AnalysisSecretEgressApproval | None | bool = True,
    capability_id: CapabilityId = ANALYSIS_CAPABILITY_ID,
    operation: CapabilityOperation = ANALYSIS_EXECUTE_OPERATION,
) -> AnalysisAuthorizationGrant:
    exact_authority = authority or _authority()
    secret_egress: AnalysisSecretEgressApproval | None
    if egress is True:
        secret_egress = AnalysisSecretEgressApproval(
            analysis=request,
            authority=exact_authority,
        )
    elif egress is False:
        secret_egress = None
    else:
        secret_egress = egress
    return AnalysisAuthorizationGrant(
        intent=AnalysisAuthorizationIntent(
            analysis=request,
            effective_policy_authority=exact_authority,
            secret_egress_approval=secret_egress,
        ),
        authority=exact_authority,
        capability_id=capability_id,
        operation=operation,
    )
