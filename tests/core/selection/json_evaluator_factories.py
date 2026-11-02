"""Budget and immutable-JSON factories shared by selector evaluation tests."""

from __future__ import annotations

from uuid import UUID

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonValue
from comparo.core.selection.accounting import SelectionBudgets
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.accounting import selection_budget_operations
from tests.core.domain.budget_origin_factories import default_budget_origin


def selection_meter(
    *,
    work: int = 100_000,
    matches: int = 100_000,
    result_bytes: int = 10_000_000,
) -> SelectionMeter:
    """Create one run-scoped meter with independently controlled dimensions."""
    work_operation, match_operation, byte_operation = selection_budget_operations()
    return SelectionMeter(
        SelectionBudgets(
            work=_budget(
                BudgetResource.SELECTION_WORK,
                work_operation,
                Count(value=work),
            ),
            matches=_budget(
                BudgetResource.SELECTION_MATCHES,
                match_operation,
                Count(value=matches),
            ),
            result_bytes=_budget(
                BudgetResource.SELECTED_RESULT_BYTES,
                byte_operation,
                ByteCount(value=result_bytes),
            ),
        )
    )


def json_object(**members: JsonValue) -> JsonObject:
    """Build a source-ordered immutable object from keyword insertion order."""
    return JsonObject(
        members=tuple(JsonObjectMember(name=name, value=value) for name, value in members.items())
    )


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
        scope=RunScope(run_id=RunId(value=UUID(int=730))),
    )
