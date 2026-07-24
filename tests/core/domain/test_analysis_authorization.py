"""Authorization and terminal-result tests for external analysis."""

from typing import cast

import pytest

from comparo.core.domain.analysis import ANALYSIS_CAPABILITY_ID
from comparo.core.domain.analysis import ANALYSIS_EXECUTE_OPERATION
from comparo.core.domain.analysis import SECRET_EGRESS_CAPABILITY_ID
from comparo.core.domain.analysis import SECRET_EGRESS_OPERATION
from comparo.core.domain.analysis import AnalysisAuthorizationGrant
from comparo.core.domain.analysis import AnalysisBudgetExhausted
from comparo.core.domain.analysis import AnalysisCancelled
from comparo.core.domain.analysis import AnalysisDenied
from comparo.core.domain.analysis import AnalysisEvidence
from comparo.core.domain.analysis import AnalysisMalformed
from comparo.core.domain.analysis import AnalysisProviderFailed
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSucceeded
from comparo.core.domain.analysis import AnalysisTimedOut
from comparo.core.domain.analysis import AnalysisVerdict
from comparo.core.domain.analysis import AuthorizedAnalysisRequest
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetExhaustionEffect
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.capabilities import CapabilityCategory
from comparo.core.domain.capabilities import CapabilityDenialReason
from comparo.core.domain.capabilities import CapabilityDenied
from comparo.core.domain.capabilities import CapabilityGranted
from comparo.core.domain.capabilities import CapabilityId
from comparo.core.domain.capabilities import CapabilityOperation
from comparo.core.domain.capabilities import CapabilityRequest
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
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
from tests.core.domain.analysis_factories import _capability
from tests.core.domain.analysis_factories import _destination
from tests.core.domain.analysis_factories import _fingerprint
from tests.core.domain.analysis_factories import _grant
from tests.core.domain.analysis_factories import _request
from tests.core.domain.analysis_factories import _sensitive_fingerprint


def test_analysis_authorization_rejects_generic_or_mismatched_capability() -> None:
    request = _request()
    exact = CapabilityGranted(request=_capability(), authority=_authority())
    with pytest.raises(TypeError, match="AnalysisRequest"):
        AnalysisAuthorizationGrant(
            analysis=cast("AnalysisRequest", object()),
            capability=exact,
        )
    with pytest.raises(TypeError, match="CapabilityGranted"):
        AnalysisAuthorizationGrant(
            analysis=request,
            capability=cast("CapabilityGranted", object()),
        )
    wrong_id = CapabilityId(namespace="example", name="external-analysis", version="1")
    wrong_operation = CapabilityOperation(value="inspect")
    cases = (
        (_capability(category=CapabilityCategory.HTTP), "grant external analysis"),
        (_capability(capability_id=wrong_id), "built-in capability identity"),
        (_capability(operation=wrong_operation), "built-in execute operation"),
        (
            CapabilityRequest(
                category=CapabilityCategory.EXTERNAL_ANALYSIS,
                capability_id=ANALYSIS_CAPABILITY_ID,
                operation=ANALYSIS_EXECUTE_OPERATION,
                target=None,
            ),
            "NamedCapabilityTarget",
        ),
        (
            _capability(
                target=NamedCapabilityTarget(
                    kind=CapabilityTargetKind.PROVIDER,
                    identifier="example",
                )
            ),
            "identify a destination",
        ),
        (_capability(target=_destination("other.invalid")), "match the provider destination"),
    )
    for capability, message in cases:
        with pytest.raises((TypeError, ValueError), match=message):
            _grant(request, capability)

    with pytest.raises(ValueError, match="secret-egress grant"):
        _grant(request, egress=False)
    public = _request(sensitive=False)
    assert _grant(public, egress=False).secret_egress is None
    with pytest.raises(ValueError, match="unnecessary secret-egress"):
        _grant(public)
    wrong_egress = CapabilityGranted(request=_capability(), authority=_authority())
    with pytest.raises(ValueError, match="grant secret egress"):
        _grant(request, egress=wrong_egress)
    other_authority_egress = CapabilityGranted(
        request=CapabilityRequest(
            category=CapabilityCategory.SECRET_EGRESS,
            capability_id=SECRET_EGRESS_CAPABILITY_ID,
            operation=SECRET_EGRESS_OPERATION,
            target=request.provider.destination,
        ),
        authority=_authority("f"),
    )
    with pytest.raises(ValueError, match="same policy authority"):
        _grant(request, egress=other_authority_egress)
    egress_cases = (
        (
            CapabilityRequest(
                category=CapabilityCategory.SECRET_EGRESS,
                capability_id=CapabilityId(namespace="example", name="secret-egress", version="1"),
                operation=SECRET_EGRESS_OPERATION,
                target=request.provider.destination,
            ),
            "built-in capability identity",
        ),
        (
            CapabilityRequest(
                category=CapabilityCategory.SECRET_EGRESS,
                capability_id=SECRET_EGRESS_CAPABILITY_ID,
                operation=CapabilityOperation(value="send"),
                target=request.provider.destination,
            ),
            "built-in egress operation",
        ),
        (
            CapabilityRequest(
                category=CapabilityCategory.SECRET_EGRESS,
                capability_id=SECRET_EGRESS_CAPABILITY_ID,
                operation=SECRET_EGRESS_OPERATION,
                target=_destination("other.invalid"),
            ),
            "provider destination",
        ),
    )
    for egress_request, message in egress_cases:
        with pytest.raises(ValueError, match=message):
            _grant(
                request,
                egress=CapabilityGranted(request=egress_request, authority=_authority()),
            )


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
    denial = CapabilityDenied(
        request=_capability(),
        reason=CapabilityDenialReason.NOT_ALLOWED,
        authority=_authority(),
    )
    request = _request()
    assert AnalysisDenied(analysis=request, decision=denial).decision is denial
    egress_denial = CapabilityDenied(
        request=CapabilityRequest(
            category=CapabilityCategory.SECRET_EGRESS,
            capability_id=SECRET_EGRESS_CAPABILITY_ID,
            operation=SECRET_EGRESS_OPERATION,
            target=request.provider.destination,
        ),
        reason=CapabilityDenialReason.NOT_ALLOWED,
        authority=_authority(),
    )
    assert AnalysisDenied(analysis=request, decision=egress_denial).decision is egress_denial
    with pytest.raises(ValueError, match="required analysis authority"):
        AnalysisDenied(analysis=_request(sensitive=False), decision=egress_denial)
    with pytest.raises(TypeError, match="AnalysisRequest"):
        AnalysisDenied(
            analysis=cast("AnalysisRequest", object()),
            decision=denial,
        )
    with pytest.raises(TypeError, match="CapabilityDenied"):
        AnalysisDenied(
            analysis=request,
            decision=cast("CapabilityDenied", object()),
        )
    with pytest.raises(ValueError, match="built-in capability identity"):
        AnalysisDenied(
            analysis=request,
            decision=CapabilityDenied(
                request=_capability(
                    capability_id=CapabilityId(
                        namespace="example",
                        name="external-analysis",
                        version="1",
                    )
                ),
                reason=CapabilityDenialReason.NOT_ALLOWED,
                authority=_authority(),
            ),
        )
    with pytest.raises(ValueError, match="match the provider destination"):
        AnalysisDenied(
            analysis=request,
            decision=CapabilityDenied(
                request=_capability(target=_destination("another.invalid")),
                reason=CapabilityDenialReason.OUTSIDE_ALLOWED_TARGETS,
                authority=_authority(),
            ),
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
