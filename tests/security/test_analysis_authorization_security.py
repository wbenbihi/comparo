"""Security regressions for the external-analysis authorization boundary."""

from collections.abc import Callable
from typing import cast
from typing import get_type_hints
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.analysis import ANALYSIS_CAPABILITY_ID
from comparo.core.domain.analysis import ANALYSIS_EXECUTE_OPERATION
from comparo.core.domain.analysis import AnalysisAuthorizationGrant
from comparo.core.domain.analysis import AnalysisAuthorizationIntent
from comparo.core.domain.analysis import AnalysisExecutor
from comparo.core.domain.analysis import AnalysisInput
from comparo.core.domain.analysis import AnalysisProvider
from comparo.core.domain.analysis import AnalysisRequest
from comparo.core.domain.analysis import AnalysisSchema
from comparo.core.domain.analysis import AnalysisSecretEgressApproval
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


def _fingerprint(character: str) -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=character * 64),
    )


def _analysis() -> AnalysisRequest:
    analysis_id = AnalysisId(value=UUID(int=1))
    scope = ExternalCapabilityBudgetScope(
        run=RunScope(run_id=RunId(value=UUID(int=10))),
        analysis_id=analysis_id,
    )

    def budget(
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
            scope=scope,
        )

    destination = NamedCapabilityTarget(
        kind=CapabilityTargetKind.DESTINATION,
        identifier="judge.example.invalid",
    )
    return AnalysisRequest(
        analysis_id=analysis_id,
        provider=AnalysisProvider(
            provider=AnalysisProviderId(value="example.judge"),
            provider_version=AnalysisContractVersion(value="1"),
            model=AnalysisModelId(value="judge"),
            model_version=AnalysisContractVersion(value="1"),
            destination=destination,
        ),
        inputs=(
            AnalysisInput(
                source=BodyId(value=UUID(int=2)),
                digest=SensitiveValue(
                    _fingerprint("a"),
                    origins=(TaintOriginId(value="response/body"),),
                ),
                bytes=ByteCount(value=12),
                sensitivity=Sensitivity.SENSITIVE,
            ),
        ),
        output_schema=AnalysisSchema(
            identifier=AnalysisSchemaId(value="comparo.analysis-result"),
            version=AnalysisContractVersion(value="1"),
            fingerprint=_fingerprint("b"),
        ),
        budgets=(
            budget(BudgetResource.EXTERNAL_INPUT_BYTES, ByteCount(value=12), "analysis.input"),
            budget(BudgetResource.EXTERNAL_OUTPUT_BYTES, ByteCount(value=100), "analysis.output"),
            budget(BudgetResource.DEADLINE, Duration(nanoseconds=1), "analysis.deadline"),
            budget(BudgetResource.EXTERNAL_WORK, Count(value=1), "analysis.work"),
        ),
        timeout=Duration(nanoseconds=1),
        fingerprint=SensitiveValue(
            _fingerprint("c"),
            origins=(TaintOriginId(value="analysis/request"),),
        ),
    )


def _grant(analysis: AnalysisRequest) -> AnalysisAuthorizationGrant:
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


def _authority(*, resource_id: str = "ci") -> ExecutionPolicyAuthority:
    return ExecutionPolicyAuthority(
        policy=DefinitionRef(
            identity=ResourceIdentity(
                namespace=NamespaceId(value="local"),
                kind=ResourceKind.EXECUTION_POLICY,
                resource_id=ResourceId(value=resource_id),
            ),
            fingerprint=_fingerprint("d"),
        )
    )


def test_generic_capability_grant_cannot_cross_executor_boundary() -> None:
    analysis = _analysis()
    capability = CapabilityRequest(
        category=CapabilityCategory.EXTERNAL_ANALYSIS,
        capability_id=ANALYSIS_CAPABILITY_ID,
        operation=ANALYSIS_EXECUTE_OPERATION,
        target=analysis.provider.destination,
    )
    generic = CapabilityGranted(
        request=capability,
        authority=_authority(),
    )
    with pytest.raises(TypeError, match="AnalysisAuthorizationGrant"):
        AuthorizedAnalysisRequest(grant=cast("AnalysisAuthorizationGrant", generic))

    hints = get_type_hints(AnalysisExecutor.execute)
    assert hints["request"] is AuthorizedAnalysisRequest


@pytest.mark.parametrize(
    "serialize",
    [msgspec.json.encode, msgspec.msgpack.encode, msgspec.to_builtins],
)
def test_authorized_sensitive_analysis_fails_closed_at_serialization(
    serialize: Callable[[object], object],
) -> None:
    analysis = _analysis()
    authorized = AuthorizedAnalysisRequest(grant=_grant(analysis))
    sensitive_digest = "a" * 64
    with pytest.raises(TypeError, match="unsupported") as caught:
        serialize(authorized)
    assert sensitive_digest not in str(caught.value)
    assert sensitive_digest not in repr(authorized)


def test_grant_owns_the_exact_immutable_recipe_authorized_by_policy() -> None:
    analysis = _analysis()
    grant = _grant(analysis)
    authorized = AuthorizedAnalysisRequest(grant=grant)
    assert authorized.analysis is analysis
    assert authorized.analysis.provider.model == AnalysisModelId(value="judge")
    with pytest.raises(AttributeError):
        grant.intent = grant.intent  # type: ignore[misc]


def test_same_destination_approval_cannot_be_replayed_for_another_request() -> None:
    analysis = _analysis()
    authority = _authority()
    approval = AnalysisSecretEgressApproval(analysis=analysis, authority=authority)
    changed_provider = msgspec.structs.replace(
        analysis.provider,
        model_version=AnalysisContractVersion(value="2"),
    )
    changed = msgspec.structs.replace(analysis, provider=changed_provider)
    assert changed.provider.destination == analysis.provider.destination
    with pytest.raises(ValueError, match="not bound to this analysis intent"):
        AnalysisAuthorizationIntent(
            analysis=changed,
            effective_policy_authority=authority,
            secret_egress_approval=approval,
        )


def test_same_hash_foreign_policy_cannot_issue_the_final_grant() -> None:
    analysis = _analysis()
    grant = _grant(analysis)
    foreign_authority = _authority(resource_id="other-policy")
    assert foreign_authority.policy.fingerprint == grant.authority.policy.fingerprint
    assert foreign_authority != grant.authority
    with pytest.raises(ValueError, match="exact effective policy authority"):
        AnalysisAuthorizationGrant(intent=grant.intent, authority=foreign_authority)


def test_sensitive_analysis_and_all_budgets_are_bound_to_exact_coordinates() -> None:
    analysis = _analysis()
    authority = _authority()
    with pytest.raises(ValueError, match="secret-egress approval"):
        AnalysisAuthorizationIntent(
            analysis=analysis,
            effective_policy_authority=authority,
        )

    first_scope = cast("ExternalCapabilityBudgetScope", analysis.budgets[0].scope)
    foreign_analysis_scope = ExternalCapabilityBudgetScope(
        run=first_scope.run,
        analysis_id=AnalysisId(value=UUID(int=99)),
    )
    foreign_budget = msgspec.structs.replace(
        analysis.budgets[0],
        scope=foreign_analysis_scope,
    )
    with pytest.raises(ValueError, match="match the AnalysisRequest analysis_id"):
        msgspec.structs.replace(
            analysis,
            budgets=(foreign_budget, *analysis.budgets[1:]),
        )

    foreign_run_scope = ExternalCapabilityBudgetScope(
        run=RunScope(run_id=RunId(value=UUID(int=98))),
        analysis_id=analysis.analysis_id,
    )
    mixed_budget = msgspec.structs.replace(analysis.budgets[1], scope=foreign_run_scope)
    with pytest.raises(ValueError, match="one identical run and analysis scope"):
        msgspec.structs.replace(
            analysis,
            budgets=(analysis.budgets[0], mixed_budget, *analysis.budgets[2:]),
        )
