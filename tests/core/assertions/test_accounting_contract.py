"""Assertion budget validation and atomic cumulative accounting."""

# mypy: disable-error-code="arg-type,redundant-cast,type-var,union-attr"

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.assertions.accounting import AssertionBudgets
from comparo.core.assertions.accounting import AssertionMeter
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.primitives import ByteCount
from tests.core.assertions.factories import control


def test_assertion_meter_validation_and_atomic_charges() -> None:
    budgets = control(work=1, facts=1, evidence=1, evidence_bytes=10_000).budgets
    meter = AssertionMeter(budgets)
    assert meter.charge_work() is None
    assert meter.charge_work() is not None
    assert meter.charge_fact() is None
    assert meter.charge_fact() is not None
    before = meter.budgets
    assert meter.charge_evidence(10_001) is not None
    assert meter.budgets == before
    assert meter.charge_evidence(1) is None
    assert meter.charge_evidence(1) is not None
    with pytest.raises(ValueError, match="count charge must be positive"):
        meter.charge_work(0)
    with pytest.raises(ValueError, match="non-negative integer"):
        meter.charge_evidence(-1)
    with pytest.raises(TypeError):
        AssertionMeter(cast("AssertionBudgets", object()))


def test_assertion_budget_contract_rejects_every_mismatch() -> None:
    good = control().budgets
    with pytest.raises(TypeError):
        AssertionBudgets(
            work=cast("object", object()),
            facts=good.facts,
            evidence_examples=good.evidence_examples,
            evidence_bytes=good.evidence_bytes,
        )
    with pytest.raises(ValueError, match="resource or stage"):
        msgspec.structs.replace(
            good,
            work=msgspec.structs.replace(good.work, resource=BudgetResource.FACTS),
        )
    with pytest.raises(ValueError, match="operation"):
        msgspec.structs.replace(
            good,
            work=msgspec.structs.replace(
                good.work,
                operation=msgspec.structs.replace(good.work.operation, value="wrong"),
            ),
        )
    with pytest.raises(TypeError):
        msgspec.structs.replace(
            good,
            work=msgspec.structs.replace(
                good.work,
                effective=ByteCount(value=1),
            ),
        )
    corrupted = msgspec.structs.replace(good.work)
    msgspec.structs.force_setattr(corrupted, "effective", ByteCount(value=1))
    with pytest.raises(TypeError, match="wrong unit"):
        msgspec.structs.replace(good, work=corrupted)
    other_scope = msgspec.structs.replace(
        good.facts,
        scope=msgspec.structs.replace(
            good.facts.scope,
            run_id=msgspec.structs.replace(good.facts.scope.run_id, value=UUID(int=3)),
        ),
    )
    with pytest.raises(ValueError, match="share one exact scope"):
        msgspec.structs.replace(good, facts=other_scope)
