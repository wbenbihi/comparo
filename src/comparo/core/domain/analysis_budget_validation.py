"""Exact budget validation for authorized external-analysis requests."""

from __future__ import annotations

from typing import Final
from typing import cast

from comparo.core.domain.analysis_usage import require_unused_authorization_budgets
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import AnalysisId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.scopes import ExternalCapabilityBudgetScope

_ANALYSIS_BUDGET_OPERATIONS: Final = (
    (BudgetResource.EXTERNAL_INPUT_BYTES, BudgetOperation(value="analysis.input")),
    (BudgetResource.EXTERNAL_OUTPUT_BYTES, BudgetOperation(value="analysis.output")),
    (BudgetResource.DEADLINE, BudgetOperation(value="analysis.deadline")),
    (BudgetResource.EXTERNAL_WORK, BudgetOperation(value="analysis.work")),
)


def validate_analysis_budgets(
    budgets: tuple[ResourceBudget, ...],
    *,
    analysis_id: AnalysisId,
    input_bytes: tuple[ByteCount, ...],
    timeout: Duration,
) -> None:
    """Require exact dimensions, one coordinate, and request-compatible limits."""
    by_resource = _index_analysis_budgets(budgets)
    require_unused_authorization_budgets(budgets)
    _validate_analysis_budget_coordinates(by_resource, analysis_id=analysis_id)
    _validate_analysis_budget_limits(by_resource, input_bytes=input_bytes, timeout=timeout)


def _index_analysis_budgets(
    budgets: tuple[ResourceBudget, ...],
) -> dict[BudgetResource, ResourceBudget]:
    if type(budgets) is not tuple:
        raise TypeError("analysis budgets must be an immutable tuple")
    if any(type(budget) is not ResourceBudget for budget in budgets):
        raise TypeError("analysis budgets must contain ResourceBudget values")
    if len(budgets) != len(_ANALYSIS_BUDGET_OPERATIONS):
        raise ValueError("analysis requires exact input, output, deadline, and work budgets")
    by_resource = {budget.resource: budget for budget in budgets}
    required_resources = frozenset(resource for resource, _ in _ANALYSIS_BUDGET_OPERATIONS)
    if frozenset(by_resource) != required_resources:
        raise ValueError("analysis budgets must contain each required dimension exactly once")
    return by_resource


def _validate_analysis_budget_coordinates(
    by_resource: dict[BudgetResource, ResourceBudget],
    *,
    analysis_id: AnalysisId,
) -> None:
    expected_scope: ExternalCapabilityBudgetScope | None = None
    for resource, operation in _ANALYSIS_BUDGET_OPERATIONS:
        budget = by_resource[resource]
        if budget.scope_kind is not BudgetScopeKind.EXTERNAL_CAPABILITY:
            raise ValueError("analysis budgets must use the external-capability scope")
        if budget.operation != operation:
            raise ValueError("analysis budget must use its canonical operation identity")
        scope = cast("ExternalCapabilityBudgetScope", budget.scope)
        if scope.analysis_id != analysis_id:
            raise ValueError("analysis budget scope must match the AnalysisRequest analysis_id")
        if expected_scope is None:
            expected_scope = scope
        elif scope != expected_scope:
            raise ValueError("all analysis budgets must share one identical run and analysis scope")


def _validate_analysis_budget_limits(
    by_resource: dict[BudgetResource, ResourceBudget],
    *,
    input_bytes: tuple[ByteCount, ...],
    timeout: Duration,
) -> None:
    if type(input_bytes) is not tuple or any(type(item) is not ByteCount for item in input_bytes):
        raise TypeError("analysis input extents must be an immutable tuple of ByteCount values")
    input_limit = cast("ByteCount", by_resource[BudgetResource.EXTERNAL_INPUT_BYTES].effective)
    deadline = cast("Duration", by_resource[BudgetResource.DEADLINE].effective)
    if sum(item.value for item in input_bytes) > input_limit.value:
        raise ValueError("analysis input bytes exceed the effective external input budget")
    if timeout.nanoseconds > deadline.nanoseconds:
        raise ValueError("analysis timeout exceeds the effective deadline budget")
