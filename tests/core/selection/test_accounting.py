"""Exercise cumulative pure selector accounting and cancellation controls."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.selectors.results import SelectionUnsupported
from comparo.core.selection import SelectionBudgets
from comparo.core.selection import SelectionCancellation
from comparo.core.selection import SelectionControl
from comparo.core.selection import SelectionEvaluation
from comparo.core.selection import selection_budget_operations
from comparo.core.selection.accounting import SelectionMeter
from tests.core.domain.budget_origin_factories import default_budget_origin


def _budget(
    resource: BudgetResource,
    operation: str,
    limit: Count | ByteCount,
) -> ResourceBudget:
    zero = type(limit)(value=0)
    return ResourceBudget(
        resource=resource,
        scope_kind=BudgetScopeKind.RUN,
        stage=BudgetStage.SELECTION,
        operation=BudgetOperation(value=operation),
        requested=limit,
        effective=limit,
        cap=None,
        contributions=(
            BudgetContribution(
                kind=BudgetContributionKind.DEFAULT,
                amount=limit,
                origin=default_budget_origin(),
            ),
        ),
        usage=BudgetUsage(consumed=zero, in_use=zero, high_water=zero),
        scope=RunScope(run_id=RunId(value=UUID(int=710))),
    )


def _budgets(limit: int = 3) -> SelectionBudgets:
    work, matches, result_bytes = selection_budget_operations()
    return SelectionBudgets(
        work=_budget(BudgetResource.SELECTION_WORK, work, Count(value=limit)),
        matches=_budget(BudgetResource.SELECTION_MATCHES, matches, Count(value=limit)),
        result_bytes=_budget(
            BudgetResource.SELECTED_RESULT_BYTES,
            result_bytes,
            ByteCount(value=limit),
        ),
    )


def test_meter_returns_updated_usage_and_keeps_failed_charge_atomic() -> None:
    meter = SelectionMeter(_budgets())

    assert meter.charge_work(2) is None
    assert meter.charge_match() is None
    assert meter.charge_result_bytes(3) is None
    before_failure = meter.budgets
    exhaustion = meter.charge_work(2)

    assert exhaustion is not None
    assert exhaustion.attempted == Count(value=4)
    assert meter.budgets == before_failure
    assert meter.budgets.work.usage.consumed == Count(value=2)
    assert meter.budgets.matches.usage.consumed == Count(value=1)
    assert meter.budgets.result_bytes.usage.consumed == ByteCount(value=3)


def test_budget_set_rejects_wrong_resource_operation_and_scope() -> None:
    budgets = _budgets()
    with pytest.raises(ValueError, match="operation"):
        SelectionBudgets(
            work=msgspec.structs.replace(
                budgets.work,
                operation=BudgetOperation(value="selector.wrong"),
            ),
            matches=budgets.matches,
            result_bytes=budgets.result_bytes,
        )
    with pytest.raises(ValueError, match="resource"):
        SelectionBudgets(
            work=msgspec.structs.replace(
                budgets.work,
                resource=BudgetResource.SELECTION_MATCHES,
            ),
            matches=budgets.matches,
            result_bytes=budgets.result_bytes,
        )
    other_scope = RunScope(run_id=RunId(value=UUID(int=711)))
    with pytest.raises(ValueError, match="one exact"):
        SelectionBudgets(
            work=budgets.work,
            matches=msgspec.structs.replace(budgets.matches, scope=other_scope),
            result_bytes=budgets.result_bytes,
        )


def test_control_and_evaluation_retain_pure_boundary_state() -> None:
    cancellation = SelectionCancellation(
        cancelled=False,
        code=DiagnosticCode(value="selection.cancelled"),
    )
    control = SelectionControl(budgets=_budgets(), cancellation=cancellation)
    result = SelectionUnsupported(code=DiagnosticCode(value="selection.unsupported"))
    evaluation = SelectionEvaluation(result=result, budgets=control.budgets)

    assert evaluation.result is result
    assert evaluation.budgets == control.budgets
    with pytest.raises(TypeError, match="closed SelectionResult"):
        SelectionEvaluation(
            result=cast("SelectionUnsupported", "bad"),
            budgets=control.budgets,
        )


@pytest.mark.parametrize("amount", [0, -1])
def test_count_charges_must_advance_work(amount: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        SelectionMeter(_budgets()).charge_work(amount)


def test_byte_charge_rejects_negative_amount() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        SelectionMeter(_budgets()).charge_result_bytes(-1)
