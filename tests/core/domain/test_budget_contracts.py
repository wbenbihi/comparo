"""Tests for the exhaustive semantic registry of budget dimensions."""

from typing import cast

import pytest

from comparo.core.domain.budget_contracts import BudgetAccountingMode
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import budget_dimension_contract
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio


def test_every_resource_has_one_complete_semantic_contract() -> None:
    contracts = {resource: budget_dimension_contract(resource) for resource in BudgetResource}
    assert frozenset(contracts) == frozenset(BudgetResource)
    for contract in contracts.values():
        assert contract.amount_type in {Count, ByteCount, Duration, Ratio}
        assert contract.placements
        assert contract.scope_kinds
        assert contract.stages
        assert frozenset(contract.exhaustion_effects_by_stage) == contract.stages
        assert type(contract.accounting) is BudgetAccountingMode
        assert contract.exhaustion_effects


def test_budget_contract_lookup_requires_a_typed_resource() -> None:
    with pytest.raises(TypeError, match="BudgetResource"):
        budget_dimension_contract(cast("BudgetResource", "requests"))
