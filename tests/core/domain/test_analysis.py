"""Tests for the authorized external-analysis seam."""

from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.analysis import AnalysisAuthorizationGrant
from comparo.core.domain.analysis import AnalysisAuthorizationIntent
from comparo.core.domain.analysis import AnalysisAuthorizationRequirement
from comparo.core.domain.analysis import AnalysisCancelled
from comparo.core.domain.analysis import AnalysisDenied
from comparo.core.domain.analysis import AnalysisInput
from comparo.core.domain.analysis import AnalysisProvider
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSchema
from comparo.core.domain.analysis import AnalysisSecretEgressApproval
from comparo.core.domain.analysis import AnalysisSucceeded
from comparo.core.domain.analysis import AnalysisVerdict
from comparo.core.domain.analysis import AuthorizedAnalysisRequest
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
from comparo.core.domain.capabilities import CapabilityDenialReason
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
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
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


def _provider() -> AnalysisProvider:
    return AnalysisProvider(
        provider=AnalysisProviderId(value="openai.api"),
        provider_version=AnalysisContractVersion(value="1"),
        model=AnalysisModelId(value="judge"),
        model_version=AnalysisContractVersion(value="2026-07-01"),
        destination=NamedCapabilityTarget(
            kind=CapabilityTargetKind.DESTINATION,
            identifier="api.example.invalid",
        ),
    )


def _budget(
    resource: BudgetResource,
    amount: Count | ByteCount | Duration,
    operation: str,
    *,
    scope: ExternalCapabilityBudgetScope,
) -> ResourceBudget:
    contribution = BudgetContribution(
        kind=BudgetContributionKind.DEFAULT,
        amount=amount,
        origin=default_budget_origin(),
    )
    if type(amount) is Count:
        zero: Count | ByteCount | Duration = Count(value=0)
    elif type(amount) is ByteCount:
        zero = ByteCount(value=0)
    else:
        zero = Duration(nanoseconds=0)
    return ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.EXTERNAL_CAPABILITY,
        stage=BudgetStage.EXTERNAL_CAPABILITY,
        operation=BudgetOperation(value=operation),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(contribution,),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=scope,
    )


def _budgets(analysis_id: AnalysisId) -> tuple[ResourceBudget, ...]:
    scope = ExternalCapabilityBudgetScope(
        run=RunScope(run_id=RunId(value=UUID(int=10))),
        analysis_id=analysis_id,
    )
    return (
        _budget(
            BudgetResource.EXTERNAL_INPUT_BYTES,
            ByteCount(value=100),
            "analysis.input",
            scope=scope,
        ),
        _budget(
            BudgetResource.EXTERNAL_OUTPUT_BYTES,
            ByteCount(value=1_000),
            "analysis.output",
            scope=scope,
        ),
        _budget(
            BudgetResource.DEADLINE,
            Duration(nanoseconds=1_000_000_000),
            "analysis.deadline",
            scope=scope,
        ),
        _budget(BudgetResource.EXTERNAL_WORK, Count(value=10), "analysis.work", scope=scope),
    )


def _authority() -> ExecutionPolicyAuthority:
    return ExecutionPolicyAuthority(
        policy=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="local"),
                kind=ResourceKind.EXECUTION_POLICY,
                resource_id=ResourceId(value="ci"),
            ),
            fingerprint=_fingerprint("f"),
        )
    )


def _request() -> AnalysisRequest:
    input_value = AnalysisInput(
        source=BodyId(value=UUID("22345678-1234-5678-1234-567812345678")),
        digest=SensitiveValue(
            _fingerprint("b"),
            origins=(TaintOriginId(value="response/body"),),
        ),
        bytes=ByteCount(value=100),
        sensitivity=Sensitivity.SENSITIVE,
        media_type=MediaType(type="application", subtype="json"),
    )
    analysis_id = AnalysisId(value=UUID("12345678-1234-5678-1234-567812345678"))
    return AnalysisRequest(
        analysis_id=analysis_id,
        provider=_provider(),
        inputs=(input_value,),
        output_schema=AnalysisSchema(
            identifier=AnalysisSchemaId(value="comparo.judge-result"),
            version=AnalysisContractVersion(value="1"),
            fingerprint=_fingerprint("e"),
        ),
        budgets=_budgets(analysis_id),
        timeout=Duration(nanoseconds=1_000_000_000),
        fingerprint=SensitiveValue(
            _fingerprint("c"),
            origins=(TaintOriginId(value="analysis/request"),),
        ),
    )


def _authorization(analysis: AnalysisRequest) -> AnalysisAuthorizationGrant:
    authority = _authority()
    return AnalysisAuthorizationGrant(
        intent=AnalysisAuthorizationIntent(
            analysis=analysis,
            effective_policy_authority=authority,
            secret_egress_approval=AnalysisSecretEgressApproval(
                analysis=analysis,
                authority=authority,
            ),
        ),
        authority=authority,
    )


def test_analysis_request_binds_inputs_authority_budgets_and_destination() -> None:
    request = _request()
    authorized = AuthorizedAnalysisRequest(grant=_authorization(request))
    assert authorized.analysis is request
    assert authorized.grant.intent.analysis.provider.destination == request.provider.destination
    with pytest.raises(ValueError, match="at least one input"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=request.provider,
            inputs=(),
            output_schema=request.output_schema,
            budgets=request.budgets,
            timeout=request.timeout,
            fingerprint=request.fingerprint,
        )
    with pytest.raises(ValueError, match="exact input, output, deadline, and work"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=request.provider,
            inputs=request.inputs,
            output_schema=request.output_schema,
            budgets=(),
            timeout=request.timeout,
            fingerprint=request.fingerprint,
        )


def test_analysis_input_digest_containment_matches_payload_sensitivity() -> None:
    with pytest.raises(ValueError, match="must match input sensitivity"):
        AnalysisInput(
            source=BodyId(value=UUID("32345678-1234-5678-1234-567812345678")),
            digest=_fingerprint("d"),
            bytes=ByteCount(value=1),
            sensitivity=Sensitivity.SENSITIVE,
        )


def test_analysis_authority_cannot_be_replayed_for_a_changed_request() -> None:
    request = _request()
    other_provider = AnalysisProvider(
        provider=request.provider.provider,
        provider_version=request.provider.provider_version,
        model=request.provider.model,
        model_version=request.provider.model_version,
        destination=NamedCapabilityTarget(
            kind=CapabilityTargetKind.DESTINATION,
            identifier="other.example.invalid",
        ),
    )
    changed = msgspec.structs.replace(request, provider=other_provider)
    original = _authorization(request)
    with pytest.raises(ValueError, match="not bound to this analysis intent"):
        AnalysisAuthorizationIntent(
            analysis=changed,
            effective_policy_authority=original.intent.effective_policy_authority,
            secret_egress_approval=original.intent.secret_egress_approval,
        )


def test_success_score_is_exact_and_normalized() -> None:
    success = AnalysisSucceeded(
        verdict=AnalysisVerdict.EQUIVALENT,
        score=Ratio(numerator=3, denominator=PositiveCount(value=4)),
        evidence=(),
    )
    assert success.score is not None
    assert success.score.numerator == 3
    with pytest.raises(ValueError, match="between zero and one"):
        AnalysisSucceeded(
            verdict=AnalysisVerdict.DRIFTED,
            score=Ratio(numerator=2, denominator=PositiveCount(value=1)),
            evidence=(),
        )


def test_denial_and_cancellation_are_distinct_terminal_results() -> None:
    denial = AnalysisDenied(
        analysis=_request(),
        requirement=AnalysisAuthorizationRequirement.EXECUTION,
        reason=CapabilityDenialReason.NOT_ALLOWED,
        authority=_authority(),
    )
    assert denial.reason is CapabilityDenialReason.NOT_ALLOWED
    assert AnalysisCancelled(source=CancellationSource.USER).source is CancellationSource.USER
