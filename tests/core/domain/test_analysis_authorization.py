"""Authorization and terminal-result tests for external analysis."""

from typing import cast

import pytest

from comparo.core.domain.analysis import AnalysisAuthorizationGrant
from comparo.core.domain.analysis import AnalysisAuthorizationIntent
from comparo.core.domain.analysis import AnalysisAuthorizationRequirement
from comparo.core.domain.analysis import AnalysisBudgetExhausted
from comparo.core.domain.analysis import AnalysisCancelled
from comparo.core.domain.analysis import AnalysisDenied
from comparo.core.domain.analysis import AnalysisEvidence
from comparo.core.domain.analysis import AnalysisMalformed
from comparo.core.domain.analysis import AnalysisProviderFailed
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSecretEgressApproval
from comparo.core.domain.analysis import AnalysisSucceeded
from comparo.core.domain.analysis import AnalysisTimedOut
from comparo.core.domain.analysis import AnalysisVerdict
from comparo.core.domain.analysis import AuthorizedAnalysisRequest
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetExhaustionEffect
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.capabilities import CapabilityDenialReason
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.analysis_factories import _authority
from tests.core.domain.analysis_factories import _budget
from tests.core.domain.analysis_factories import _fingerprint
from tests.core.domain.analysis_factories import _grant
from tests.core.domain.analysis_factories import _request
from tests.core.domain.analysis_factories import _sensitive_fingerprint


def test_analysis_authorization_rejects_untyped_or_mismatched_components() -> None:
    request = _request()
    authority = _authority()
    approval = AnalysisSecretEgressApproval(analysis=request, authority=authority)
    with pytest.raises(TypeError, match="bind an AnalysisRequest"):
        AnalysisSecretEgressApproval(
            analysis=cast("AnalysisRequest", object()),
            authority=authority,
        )
    with pytest.raises(TypeError, match="exact ExecutionPolicyAuthority"):
        AnalysisSecretEgressApproval(
            analysis=request,
            authority=cast("ExecutionPolicyAuthority", object()),
        )
    with pytest.raises(ValueError, match="wrong built-in capability"):
        AnalysisSecretEgressApproval(
            analysis=request,
            authority=authority,
            operation=CapabilityOperation(value="send"),
        )
    with pytest.raises(TypeError, match="typed capability"):
        AnalysisSecretEgressApproval(
            analysis=request,
            authority=authority,
            capability_id=cast("CapabilityId", object()),
        )

    with pytest.raises(ValueError, match="secret-egress approval"):
        _grant(request, egress=False)
    public = _request(sensitive=False)
    assert _grant(public, egress=False).intent.secret_egress_approval is None
    with pytest.raises(ValueError, match="unnecessary secret-egress authority"):
        _grant(public)

    with pytest.raises(TypeError, match="authorization intent"):
        AnalysisAuthorizationIntent(
            analysis=cast("AnalysisRequest", object()),
            effective_policy_authority=authority,
        )
    with pytest.raises(TypeError, match="policy binding"):
        AnalysisAuthorizationIntent(
            analysis=request,
            effective_policy_authority=cast("ExecutionPolicyAuthority", object()),
            secret_egress_approval=approval,
        )
    with pytest.raises(TypeError, match="AnalysisSecretEgressApproval"):
        AnalysisAuthorizationIntent(
            analysis=request,
            effective_policy_authority=authority,
            secret_egress_approval=cast("AnalysisSecretEgressApproval", object()),
        )
    foreign_issuer = AnalysisSecretEgressApproval(
        analysis=request,
        authority=_authority(resource_id="other-policy"),
    )
    with pytest.raises(ValueError, match="not bound to this analysis intent"):
        AnalysisAuthorizationIntent(
            analysis=request,
            effective_policy_authority=authority,
            secret_egress_approval=foreign_issuer,
        )


def test_analysis_grant_requires_exact_policy_and_capability_contract() -> None:
    request = _request()
    authority = _authority()
    grant = _grant(request, authority=authority)
    assert grant.intent.analysis is request
    with pytest.raises(TypeError, match="grant intent"):
        AnalysisAuthorizationGrant(
            intent=cast("AnalysisAuthorizationIntent", object()),
            authority=authority,
        )
    with pytest.raises(TypeError, match="exact ExecutionPolicyAuthority"):
        AnalysisAuthorizationGrant(
            intent=grant.intent,
            authority=cast("ExecutionPolicyAuthority", object()),
        )
    with pytest.raises(ValueError, match="exact effective policy authority"):
        AnalysisAuthorizationGrant(
            intent=grant.intent,
            authority=_authority(resource_id="other-policy"),
        )
    with pytest.raises(ValueError, match="wrong built-in capability"):
        _grant(
            request,
            capability_id=CapabilityId(namespace="example", name="analysis", version="1"),
        )
    with pytest.raises(TypeError, match="typed capability"):
        _grant(request, operation=cast("CapabilityOperation", object()))


def test_authorized_analysis_requires_dedicated_grant() -> None:
    request = _request()
    grant = _grant(request)
    assert AuthorizedAnalysisRequest(grant=grant).analysis is request
    with pytest.raises(TypeError, match="AnalysisAuthorizationGrant"):
        AuthorizedAnalysisRequest(grant=cast("AnalysisAuthorizationGrant", object()))


def test_analysis_evidence_and_success_validate_all_components() -> None:
    evidence = AnalysisEvidence(
        code=DiagnosticCode(value="analysis.support"),
        digest=_sensitive_fingerprint(),
        items=Count(value=1),
    )
    assert AnalysisSucceeded(
        verdict=AnalysisVerdict.EQUIVALENT,
        score=None,
        evidence=(evidence,),
    ).evidence == (evidence,)
    with pytest.raises(TypeError, match="DiagnosticCode"):
        AnalysisEvidence(
            code=cast("DiagnosticCode", object()),
            digest=_sensitive_fingerprint(),
            items=Count(value=1),
        )
    with pytest.raises(TypeError, match="sensitive Fingerprint"):
        AnalysisEvidence(
            code=DiagnosticCode(value="analysis.support"),
            digest=cast("SensitiveValue[Fingerprint]", _fingerprint()),
            items=Count(value=1),
        )
    with pytest.raises(TypeError, match="sensitive Fingerprint"):
        AnalysisEvidence(
            code=DiagnosticCode(value="analysis.support"),
            digest=cast(
                "SensitiveValue[Fingerprint]",
                SensitiveValue(
                    Count(value=1),
                    origins=(TaintOriginId(value="wrong/type"),),
                ),
            ),
            items=Count(value=1),
        )
    with pytest.raises(TypeError, match="Count"):
        AnalysisEvidence(
            code=DiagnosticCode(value="analysis.support"),
            digest=_sensitive_fingerprint(),
            items=cast("Count", object()),
        )
    with pytest.raises(TypeError, match="AnalysisVerdict"):
        AnalysisSucceeded(
            verdict=cast("AnalysisVerdict", object()),
            score=None,
            evidence=(),
        )
    with pytest.raises(TypeError, match="Ratio"):
        AnalysisSucceeded(
            verdict=AnalysisVerdict.EQUIVALENT,
            score=cast("Ratio", object()),
            evidence=(),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        AnalysisSucceeded(
            verdict=AnalysisVerdict.EQUIVALENT,
            score=Ratio(numerator=1, denominator=PositiveCount(value=2)),
            evidence=cast("tuple[AnalysisEvidence, ...]", []),
        )
    with pytest.raises(TypeError, match="AnalysisEvidence"):
        AnalysisSucceeded(
            verdict=AnalysisVerdict.EQUIVALENT,
            score=None,
            evidence=cast("tuple[AnalysisEvidence, ...]", (object(),)),
        )


def _diagnostic(severity: DiagnosticSeverity = DiagnosticSeverity.ERROR) -> Diagnostic:
    return Diagnostic(
        code=DiagnosticCode(value="analysis.provider-failed"),
        severity=severity,
        stage=DiagnosticStage.EVALUATION,
    )


def test_analysis_terminal_results_validate_typed_causes() -> None:
    request = _request()
    denial = AnalysisDenied(
        analysis=request,
        requirement=AnalysisAuthorizationRequirement.EXECUTION,
        reason=CapabilityDenialReason.NOT_ALLOWED,
        authority=_authority(),
    )
    assert denial.analysis is request
    assert denial.requirement is AnalysisAuthorizationRequirement.EXECUTION
    egress_denial = AnalysisDenied(
        analysis=request,
        requirement=AnalysisAuthorizationRequirement.SECRET_EGRESS,
        reason=CapabilityDenialReason.NOT_ALLOWED,
        authority=_authority(),
    )
    assert egress_denial.reason is CapabilityDenialReason.NOT_ALLOWED
    with pytest.raises(ValueError, match="unnecessary secret egress"):
        AnalysisDenied(
            analysis=_request(sensitive=False),
            requirement=AnalysisAuthorizationRequirement.SECRET_EGRESS,
            reason=CapabilityDenialReason.NOT_ALLOWED,
            authority=_authority(),
        )
    with pytest.raises(TypeError, match="AnalysisRequest"):
        AnalysisDenied(
            analysis=cast("AnalysisRequest", object()),
            requirement=AnalysisAuthorizationRequirement.EXECUTION,
            reason=CapabilityDenialReason.NOT_ALLOWED,
            authority=_authority(),
        )
    with pytest.raises(TypeError, match="denial authority"):
        AnalysisDenied(
            analysis=request,
            requirement=AnalysisAuthorizationRequirement.EXECUTION,
            reason=CapabilityDenialReason.NOT_ALLOWED,
            authority=cast("ExecutionPolicyAuthority", object()),
        )
    with pytest.raises(TypeError, match="requirement"):
        AnalysisDenied(
            analysis=request,
            requirement=cast("AnalysisAuthorizationRequirement", object()),
            reason=CapabilityDenialReason.NOT_ALLOWED,
            authority=_authority(),
        )
    with pytest.raises(TypeError, match="denial reason"):
        AnalysisDenied(
            analysis=request,
            requirement=AnalysisAuthorizationRequirement.EXECUTION,
            reason=cast("CapabilityDenialReason", object()),
            authority=_authority(),
        )
    assert AnalysisTimedOut(elapsed=Duration(nanoseconds=1)).elapsed.nanoseconds == 1
    with pytest.raises(TypeError, match="Duration"):
        AnalysisTimedOut(elapsed=cast("Duration", object()))
    assert (
        AnalysisProviderFailed(diagnostic=_diagnostic()).diagnostic.severity
        is DiagnosticSeverity.ERROR
    )
    assert (
        AnalysisMalformed(diagnostic=_diagnostic()).diagnostic.stage is DiagnosticStage.EVALUATION
    )
    for result_type in (AnalysisProviderFailed, AnalysisMalformed):
        with pytest.raises(TypeError, match="Diagnostic"):
            result_type(diagnostic=cast("Diagnostic", object()))
        with pytest.raises(ValueError, match="error severity"):
            result_type(diagnostic=_diagnostic(DiagnosticSeverity.WARNING))
    assert AnalysisCancelled(source=CancellationSource.USER).source is CancellationSource.USER
    with pytest.raises(TypeError, match="CancellationSource"):
        AnalysisCancelled(source=cast("CancellationSource", object()))


def test_analysis_budget_exhaustion_requires_external_budget() -> None:
    external = BudgetExceeded(
        budget=_budget(BudgetResource.EXTERNAL_WORK, Count(value=4), "analysis.work"),
        attempted=Count(value=5),
        effect=BudgetExhaustionEffect.EXTERNAL_CAPABILITY_UNAVAILABLE,
    )
    assert AnalysisBudgetExhausted(exhaustion=external).exhaustion is external
    with pytest.raises(TypeError, match="BudgetExceeded"):
        AnalysisBudgetExhausted(exhaustion=cast("BudgetExceeded", object()))
    non_external = BudgetExceeded(
        budget=_budget(
            BudgetResource.REQUESTS,
            Count(value=4),
            "plan.requests",
            scope_kind=BudgetScopeKind.PLAN,
            stage=BudgetStage.PLANNING,
        ),
        attempted=Count(value=5),
        effect=BudgetExhaustionEffect.PLAN_REJECTED,
    )
    with pytest.raises(ValueError, match="external-capability scoped"):
        AnalysisBudgetExhausted(exhaustion=non_external)
