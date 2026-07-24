"""Property tests for exact external-analysis authority binding."""

from uuid import UUID

import pytest
from hypothesis import assume
from hypothesis import given
from hypothesis import strategies as st

from comparo.core.domain.analysis import ANALYSIS_CAPABILITY_ID
from comparo.core.domain.analysis import ANALYSIS_EXECUTE_OPERATION
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

_DESTINATIONS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789.-",
    min_size=1,
    max_size=40,
).filter(lambda value: bool(value.strip()))


def _fingerprint(character: str) -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=character * 64),
    )


def _budget(
    resource: BudgetResource,
    amount: Count | ByteCount | Duration,
    operation: str,
) -> ResourceBudget:
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
        _budget(BudgetResource.EXTERNAL_INPUT_BYTES, ByteCount(value=1), "analysis.input"),
        _budget(BudgetResource.EXTERNAL_OUTPUT_BYTES, ByteCount(value=1), "analysis.output"),
        _budget(BudgetResource.DEADLINE, Duration(nanoseconds=1), "analysis.deadline"),
        _budget(BudgetResource.EXTERNAL_WORK, Count(value=1), "analysis.work"),
    )


def _authority() -> ExecutionPolicyAuthority:
    return ExecutionPolicyAuthority(
        policy=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="local"),
                kind=ResourceKind.EXECUTION_POLICY,
                resource_id=ResourceId(value="ci"),
            ),
            fingerprint=_fingerprint("d"),
        )
    )


def _analysis(destination: str, *, sensitive: bool) -> AnalysisRequest:
    input_digest: Fingerprint | SensitiveValue[Fingerprint]
    request_fingerprint: Fingerprint | SensitiveValue[Fingerprint]
    if sensitive:
        input_digest = SensitiveValue(
            _fingerprint("a"),
            origins=(TaintOriginId(value="response/body"),),
        )
        request_fingerprint = SensitiveValue(
            _fingerprint("b"),
            origins=(TaintOriginId(value="analysis/request"),),
        )
    else:
        input_digest = _fingerprint("a")
        request_fingerprint = _fingerprint("b")
    return AnalysisRequest(
        analysis_id=AnalysisId(value=UUID(int=1)),
        provider=AnalysisProvider(
            provider="example.judge",
            provider_version="1",
            model="judge",
            model_version="1",
            destination=NamedCapabilityTarget(
                kind=CapabilityTargetKind.DESTINATION,
                identifier=destination,
            ),
        ),
        inputs=(
            AnalysisInput(
                source=BodyId(value=UUID(int=2)),
                digest=input_digest,
                bytes=ByteCount(value=1),
                sensitivity=Sensitivity.SENSITIVE if sensitive else Sensitivity.PUBLIC,
            ),
        ),
        output_schema=AnalysisSchema(
            identifier="comparo.analysis-result",
            version="1",
            fingerprint=_fingerprint("c"),
        ),
        budgets=_budgets(),
        timeout=Duration(nanoseconds=1),
        fingerprint=request_fingerprint,
    )


@given(destination=_DESTINATIONS, authorized_destination=_DESTINATIONS)
def test_authority_for_one_destination_never_authorizes_another(
    destination: str,
    authorized_destination: str,
) -> None:
    assume(destination != authorized_destination)
    analysis = _analysis(destination, sensitive=False)
    capability = CapabilityRequest(
        category=CapabilityCategory.EXTERNAL_ANALYSIS,
        capability_id=ANALYSIS_CAPABILITY_ID,
        operation=ANALYSIS_EXECUTE_OPERATION,
        target=NamedCapabilityTarget(
            kind=CapabilityTargetKind.DESTINATION,
            identifier=authorized_destination,
        ),
    )
    with pytest.raises(ValueError, match="provider destination"):
        AnalysisAuthorizationGrant(
            analysis=analysis,
            capability=CapabilityGranted(request=capability, authority=_authority()),
        )


@given(sensitive=st.booleans())
def test_request_fingerprint_sensitivity_tracks_every_input(sensitive: bool) -> None:
    analysis = _analysis("judge.example.invalid", sensitive=sensitive)
    assert isinstance(analysis.fingerprint, SensitiveValue) is sensitive
