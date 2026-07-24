"""Property tests for exact external-analysis authority binding."""

from uuid import UUID

import msgspec
import pytest
from hypothesis import assume
from hypothesis import given
from hypothesis import strategies as st

from comparo.core.domain.analysis import AnalysisAuthorizationIntent
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
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.scopes import ExternalCapabilityBudgetScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.domain.budget_origin_factories import default_budget_origin

_DESTINATIONS = st.from_regex(r"[a-z0-9][a-z0-9.-]{0,39}", fullmatch=True)


def _fingerprint(character: str) -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=character * 64),
    )


def _budget(
    resource: BudgetResource,
    amount: Count | ByteCount | Duration,
    operation: str,
    scope: ExternalCapabilityBudgetScope,
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
        scope=scope,
    )


def _budgets(scope: ExternalCapabilityBudgetScope) -> tuple[ResourceBudget, ...]:
    return (
        _budget(
            BudgetResource.EXTERNAL_INPUT_BYTES,
            ByteCount(value=1),
            "analysis.input",
            scope,
        ),
        _budget(
            BudgetResource.EXTERNAL_OUTPUT_BYTES,
            ByteCount(value=1),
            "analysis.output",
            scope,
        ),
        _budget(BudgetResource.DEADLINE, Duration(nanoseconds=1), "analysis.deadline", scope),
        _budget(BudgetResource.EXTERNAL_WORK, Count(value=1), "analysis.work", scope),
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
    analysis_id = AnalysisId(value=UUID(int=1))
    scope = ExternalCapabilityBudgetScope(
        run=RunScope(run_id=RunId(value=UUID(int=10))),
        analysis_id=analysis_id,
    )
    return AnalysisRequest(
        analysis_id=analysis_id,
        provider=AnalysisProvider(
            provider=AnalysisProviderId(value="example.judge"),
            provider_version=AnalysisContractVersion(value="1"),
            model=AnalysisModelId(value="judge"),
            model_version=AnalysisContractVersion(value="1"),
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
            identifier=AnalysisSchemaId(value="comparo.analysis-result"),
            version=AnalysisContractVersion(value="1"),
            fingerprint=_fingerprint("c"),
        ),
        budgets=_budgets(scope),
        timeout=Duration(nanoseconds=1),
        fingerprint=request_fingerprint,
    )


@given(destination=_DESTINATIONS, authorized_destination=_DESTINATIONS)
def test_authority_for_one_destination_never_authorizes_another(
    destination: str,
    authorized_destination: str,
) -> None:
    assume(destination != authorized_destination)
    analysis = _analysis(destination, sensitive=True)
    authorized_analysis = _analysis(authorized_destination, sensitive=True)
    approval = AnalysisSecretEgressApproval(
        analysis=authorized_analysis,
        authority=_authority(),
    )
    with pytest.raises(ValueError, match="not bound to this analysis intent"):
        AnalysisAuthorizationIntent(
            analysis=analysis,
            effective_policy_authority=_authority(),
            secret_egress_approval=approval,
        )


@given(sensitive=st.booleans())
def test_request_fingerprint_sensitivity_tracks_every_input(sensitive: bool) -> None:
    analysis = _analysis("judge.example.invalid", sensitive=sensitive)
    assert isinstance(analysis.fingerprint, SensitiveValue) is sensitive


@given(model_version=st.integers(min_value=2, max_value=1_000).map(str))
def test_same_destination_approval_never_authorizes_a_changed_recipe(
    model_version: str,
) -> None:
    analysis = _analysis("judge.example.invalid", sensitive=True)
    authority = _authority()
    approval = AnalysisSecretEgressApproval(analysis=analysis, authority=authority)
    changed = msgspec.structs.replace(
        analysis,
        provider=msgspec.structs.replace(
            analysis.provider,
            model_version=AnalysisContractVersion(value=model_version),
        ),
    )
    with pytest.raises(ValueError, match="not bound to this analysis intent"):
        AnalysisAuthorizationIntent(
            analysis=changed,
            effective_policy_authority=authority,
            secret_egress_approval=approval,
        )
