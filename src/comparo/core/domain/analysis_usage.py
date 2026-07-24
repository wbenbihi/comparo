"""Terminal resource-usage evidence for authorized external analysis."""

from __future__ import annotations

from fractions import Fraction
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.budgets import BudgetAmount
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration


@final
class AnalysisBudgetUsageReport(msgspec.Struct, frozen=True, kw_only=True):
    """Exact terminal snapshots for every authorized analysis budget."""

    snapshots: tuple[ResourceBudget, ...]

    def __post_init__(self) -> None:
        """Require an immutable, typed, resource-unique snapshot collection."""
        if type(self.snapshots) is not tuple:
            raise TypeError("analysis usage snapshots must be an immutable tuple")
        if any(type(snapshot) is not ResourceBudget for snapshot in self.snapshots):
            raise TypeError("analysis usage snapshots must contain ResourceBudget values")
        resources = tuple(snapshot.resource for snapshot in self.snapshots)
        if len(set(resources)) != len(resources):
            raise ValueError("analysis usage snapshots must contain each resource at most once")
        if any(_amount_value(snapshot.usage.in_use) != 0 for snapshot in self.snapshots):
            raise ValueError("terminal analysis usage cannot retain live reservations")


def require_unused_authorization_budgets(budgets: tuple[ResourceBudget, ...]) -> None:
    """Require authorization to describe limits before any runtime consumption."""
    if any(not _usage_is_zero(budget.usage) for budget in budgets):
        raise ValueError("analysis authorization budgets must start with zero usage")


def validate_analysis_usage_report(
    authorized: tuple[ResourceBudget, ...],
    report: AnalysisBudgetUsageReport,
    *,
    expected_input_bytes: ByteCount,
) -> None:
    """Bind terminal snapshots to every exact authorized contract and its initial usage."""
    if type(report) is not AnalysisBudgetUsageReport:
        raise TypeError("analysis execution usage must be an AnalysisBudgetUsageReport")
    if type(expected_input_bytes) is not ByteCount:
        raise TypeError("expected analysis input extent must be a ByteCount")
    authorized_by_resource = {budget.resource: budget for budget in authorized}
    actual_by_resource = {budget.resource: budget for budget in report.snapshots}
    if frozenset(actual_by_resource) != frozenset(authorized_by_resource):
        raise ValueError("analysis usage must report every authorized budget exactly once")
    for resource, initial in authorized_by_resource.items():
        terminal = actual_by_resource[resource]
        if not budget_contract_matches(terminal, initial):
            raise ValueError(
                "analysis usage snapshot does not match its authorized budget contract"
            )
        _require_monotonic_usage(initial.usage, terminal.usage)
    consumed_input = cast(
        "ByteCount",
        actual_by_resource[BudgetResource.EXTERNAL_INPUT_BYTES].usage.consumed,
    )
    if consumed_input != expected_input_bytes:
        raise ValueError("terminal external input usage must equal declared analysis input bytes")


def validate_success_usage(
    report: AnalysisBudgetUsageReport,
    *,
    allows_empty_output: bool,
) -> None:
    """Require minimal resource evidence for a successful external invocation."""
    work = cast("Count", analysis_usage_budget(report, BudgetResource.EXTERNAL_WORK).usage.consumed)
    if work.value == 0:
        raise ValueError("successful external analysis requires nonzero work usage")
    output = cast(
        "ByteCount",
        analysis_usage_budget(report, BudgetResource.EXTERNAL_OUTPUT_BYTES).usage.consumed,
    )
    if output.value == 0 and not allows_empty_output:
        raise ValueError("successful external analysis requires nonempty output usage")


def budget_contract_matches(actual: ResourceBudget, authorized: ResourceBudget) -> bool:
    """Compare immutable authorization inputs while allowing runtime usage to advance."""
    return (
        actual.resource is authorized.resource
        and actual.scope_kind is authorized.scope_kind
        and actual.stage is authorized.stage
        and actual.operation == authorized.operation
        and actual.requested == authorized.requested
        and actual.effective == authorized.effective
        and actual.cap == authorized.cap
        and actual.contributions == authorized.contributions
        and actual.scope == authorized.scope
    )


def analysis_usage_budget(
    report: AnalysisBudgetUsageReport,
    resource: BudgetResource,
) -> ResourceBudget:
    """Return the exact terminal snapshot for a validated required resource."""
    return next(snapshot for snapshot in report.snapshots if snapshot.resource is resource)


def _require_monotonic_usage(initial: BudgetUsage, terminal: BudgetUsage) -> None:
    if _amount_value(terminal.consumed) < _amount_value(initial.consumed):
        raise ValueError("analysis cumulative budget usage cannot decrease after authorization")
    if _amount_value(terminal.high_water) < _amount_value(initial.high_water):
        raise ValueError("analysis budget high-water usage cannot decrease after authorization")


def _usage_is_zero(usage: BudgetUsage) -> bool:
    return all(
        _amount_value(amount) == 0 for amount in (usage.consumed, usage.in_use, usage.high_water)
    )


def _amount_value(amount: BudgetAmount) -> Fraction:
    analysis_amount = cast("Count | ByteCount | Duration", amount)
    if type(analysis_amount) is Duration:
        return Fraction(analysis_amount.nanoseconds)
    return Fraction(analysis_amount.value)
