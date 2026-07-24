"""Tests for the authorized external-analysis seam."""

from uuid import UUID

import pytest

from comparo.core.domain.analysis import AnalysisCancelled
from comparo.core.domain.analysis import AnalysisDenied
from comparo.core.domain.analysis import AnalysisInput
from comparo.core.domain.analysis import AnalysisProvider
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSucceeded
from comparo.core.domain.analysis import AnalysisVerdict
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.capabilities import CapabilityCategory
from comparo.core.domain.capabilities import CapabilityDenialReason
from comparo.core.domain.capabilities import CapabilityDenied
from comparo.core.domain.capabilities import CapabilityGranted
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import CapabilityRequest
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.identity import BodyId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId


def _fingerprint(character: str = "a") -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=character * 64),
    )


def _provider() -> AnalysisProvider:
    return AnalysisProvider(
        provider="openai.api",
        provider_version="1",
        model="judge",
        model_version="2026-07-01",
        destination=NamedCapabilityTarget(
            kind=CapabilityTargetKind.DESTINATION,
            identifier="api.example.invalid",
        ),
    )


def _capability_request() -> CapabilityRequest:
    provider = _provider()
    return CapabilityRequest(
        category=CapabilityCategory.EXTERNAL_ANALYSIS,
        capability_id=CapabilityId(namespace="comparo", name="external-analysis", version="1"),
        operation=CapabilityOperation(value="judge"),
        target=provider.destination,
    )


def _grant() -> CapabilityGranted:
    return CapabilityGranted(
        request=_capability_request(),
        authority=(ProvenanceEntry(kind=ProvenanceKind.EXECUTION_POLICY),),
    )


def _budget() -> ResourceBudget:
    provenance = ProvenanceEntry(kind=ProvenanceKind.EXECUTION_POLICY)
    amount = Count(value=10)
    contribution = BudgetContribution(
        kind=BudgetContributionKind.DEFAULT,
        amount=amount,
        provenance=provenance,
    )
    return ResourceBudget(
        resource=BudgetResource.EXTERNAL_WORK,
        scope_kind=BudgetScopeKind.EXTERNAL_CAPABILITY,
        stage=BudgetStage.EXTERNAL_CAPABILITY,
        operation=BudgetOperation(value="analysis.work"),
        requested=amount,
        effective=amount,
        cap=None,
        contributions=(contribution,),
        usage=BudgetUsage(
            consumed=Count(value=0),
            in_use=Count(value=0),
            high_water=Count(value=0),
        ),
    )


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        analysis_id=AnalysisId(value=UUID("12345678-1234-5678-1234-567812345678")),
        provider=_provider(),
        inputs=(
            AnalysisInput(
                source=BodyId(value=UUID("22345678-1234-5678-1234-567812345678")),
                digest=SensitiveValue(
                    _fingerprint("b"),
                    origins=(TaintOriginId(value="response/body"),),
                ),
                bytes=ByteCount(value=100),
                sensitivity=Sensitivity.SENSITIVE,
                media_type="application/json",
            ),
        ),
        authorization=_grant(),
        budgets=(_budget(),),
        timeout=Duration(nanoseconds=1_000_000_000),
        fingerprint=_fingerprint("c"),
    )


def test_analysis_request_binds_inputs_authority_budgets_and_destination() -> None:
    request = _request()
    assert request.authorization.request.target == request.provider.destination
    with pytest.raises(ValueError, match="at least one input"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=request.provider,
            inputs=(),
            authorization=request.authorization,
            budgets=request.budgets,
            timeout=request.timeout,
            fingerprint=request.fingerprint,
        )
    with pytest.raises(ValueError, match="finite external-capability budgets"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=request.provider,
            inputs=request.inputs,
            authorization=request.authorization,
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


def test_analysis_request_rejects_wrong_authority_category_or_destination() -> None:
    request = _request()
    wrong_request = CapabilityRequest(
        category=CapabilityCategory.HTTP,
        capability_id=request.authorization.request.capability_id,
        operation=request.authorization.request.operation,
        target=request.provider.destination,
    )
    wrong_grant = CapabilityGranted(
        request=wrong_request,
        authority=request.authorization.authority,
    )
    with pytest.raises(ValueError, match="grant external analysis"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=request.provider,
            inputs=request.inputs,
            authorization=wrong_grant,
            budgets=request.budgets,
            timeout=request.timeout,
            fingerprint=request.fingerprint,
        )
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
    with pytest.raises(ValueError, match="target must match"):
        AnalysisRequest(
            analysis_id=request.analysis_id,
            provider=other_provider,
            inputs=request.inputs,
            authorization=request.authorization,
            budgets=request.budgets,
            timeout=request.timeout,
            fingerprint=request.fingerprint,
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
    denial = CapabilityDenied(
        request=_capability_request(),
        reason=CapabilityDenialReason.NOT_ALLOWED,
        authority=(ProvenanceEntry(kind=ProvenanceKind.EXECUTION_POLICY),),
    )
    assert AnalysisDenied(decision=denial).decision.reason is CapabilityDenialReason.NOT_ALLOWED
    assert AnalysisCancelled(source=CancellationSource.USER).source is CancellationSource.USER
