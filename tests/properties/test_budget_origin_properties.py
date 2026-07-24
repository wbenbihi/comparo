"""Generative checks for exact budget-origin authority rules."""

from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from tests.core.domain.budget_origin_factories import default_budget_origin
from tests.core.domain.budget_origin_factories import definition_budget_origin
from tests.core.domain.budget_origin_factories import invocation_budget_origin

_ORDINARY_BUDGET_KINDS = frozenset(
    {
        ResourceKind.PROJECT,
        ResourceKind.EXECUTION_PROFILE,
        ResourceKind.ASSERTION_PROFILE,
        ResourceKind.DIFF_PROFILE,
        ResourceKind.GATE_PROFILE,
        ResourceKind.REDACTION_PROFILE,
        ResourceKind.ARTIFACT_PROFILE,
    }
)
_CEILING_KINDS = frozenset({ResourceKind.SAFETY_PROFILE, ResourceKind.EXECUTION_POLICY})


@given(st.integers(min_value=1))
def test_default_set_versions_round_trip_as_canonical_positive_decimals(
    version: int,
) -> None:
    origin = default_budget_origin(version=str(version))
    assert origin.default_set.version == str(version)


@given(st.sampled_from(tuple(ResourceKind)))
def test_definition_kind_authorizes_a_ceiling_if_and_only_if_operator_owned(
    kind: ResourceKind,
) -> None:
    origin = definition_budget_origin(kind)
    if kind in _CEILING_KINDS:
        contribution = BudgetContribution(
            kind=BudgetContributionKind.CEILING,
            amount=Count(value=1),
            origin=origin,
        )
        assert contribution.origin is origin
    else:
        with pytest.raises(ValueError, match="SafetyProfile or ExecutionPolicy"):
            BudgetContribution(
                kind=BudgetContributionKind.CEILING,
                amount=Count(value=1),
                origin=origin,
            )


@given(st.sampled_from(tuple(ResourceKind)))
def test_definition_kind_authorizes_an_ordinary_request_if_and_only_if_configurable(
    kind: ResourceKind,
) -> None:
    origin = definition_budget_origin(kind)
    if kind in _ORDINARY_BUDGET_KINDS:
        contribution = BudgetContribution(
            kind=BudgetContributionKind.REQUEST,
            amount=Count(value=1),
            origin=origin,
        )
        assert contribution.origin is origin
    else:
        with pytest.raises(ValueError, match="Project, profile, or invocation"):
            BudgetContribution(
                kind=BudgetContributionKind.REQUEST,
                amount=Count(value=1),
                origin=origin,
            )


@given(st.integers(min_value=0, max_value=(1 << 128) - 1))
def test_invocation_occurrences_always_authorize_ordinary_overrides(seed: int) -> None:
    origin = invocation_budget_origin(UUID(int=seed))
    contribution = BudgetContribution(
        kind=BudgetContributionKind.REQUEST,
        amount=Count(value=1),
        origin=origin,
    )
    assert contribution.origin is origin
