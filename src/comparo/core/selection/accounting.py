"""Pure cumulative accounting and cancellation inputs for selection."""

from __future__ import annotations

from typing import Literal
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.budget_contracts import BudgetStage
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.results import SelectionResult
from comparo.core.domain.selectors.results import is_selection_result

_WORK_OPERATION = "selector.evaluate.work"
_MATCH_OPERATION = "selector.evaluate.matches"
_BYTES_OPERATION = "selector.evaluate.result-bytes"


@final
class SelectionBudgets(msgspec.Struct, frozen=True, kw_only=True):
    """The exact cumulative budgets consumed by one or many selectors."""

    work: ResourceBudget
    matches: ResourceBudget
    result_bytes: ResourceBudget

    def __post_init__(self) -> None:
        """Require one correctly placed budget for every selector dimension."""
        expected = (
            (
                self.work,
                BudgetResource.SELECTION_WORK,
                Count,
                _WORK_OPERATION,
            ),
            (
                self.matches,
                BudgetResource.SELECTION_MATCHES,
                Count,
                _MATCH_OPERATION,
            ),
            (
                self.result_bytes,
                BudgetResource.SELECTED_RESULT_BYTES,
                ByteCount,
                _BYTES_OPERATION,
            ),
        )
        for budget, resource, amount_type, operation in expected:
            _require_budget(budget, resource, amount_type, operation)
        scopes = tuple((budget.scope_kind, budget.scope) for budget, *_rest in expected)
        if len(set(scopes)) != 1:
            raise ValueError("selection budgets must share one exact accounting scope")


def _require_budget(
    budget: object,
    resource: BudgetResource,
    amount_type: type[Count] | type[ByteCount],
    operation: str,
) -> None:
    if type(budget) is not ResourceBudget:
        raise TypeError("selection accounting requires ResourceBudget values")
    if budget.resource is not resource:
        raise ValueError("selection budget resource contradicts its accounting dimension")
    if budget.stage is not BudgetStage.SELECTION:
        raise ValueError("selection budgets require the selection stage")
    if budget.operation.value != operation:
        raise ValueError("selection budget operation contradicts its accounting dimension")
    if type(budget.effective) is not amount_type:
        raise TypeError("selection budget amount has the wrong unit")


@final
class SelectionCancellation(msgspec.Struct, frozen=True, kw_only=True):
    """One immutable cancellation observation at a bounded pure call boundary."""

    cancelled: bool
    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a bool state and payload-free stable code."""
        if type(self.cancelled) is not bool:
            raise TypeError("selection cancellation state must be bool")
        if type(self.code) is not DiagnosticCode:
            raise TypeError("selection cancellation requires a DiagnosticCode")


@final
class SelectionControl(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned cumulative budgets and current cancellation snapshot."""

    budgets: SelectionBudgets
    cancellation: SelectionCancellation

    def __post_init__(self) -> None:
        """Require immutable validated control inputs."""
        if type(self.budgets) is not SelectionBudgets:
            raise TypeError("selection control requires SelectionBudgets")
        if type(self.cancellation) is not SelectionCancellation:
            raise TypeError("selection control requires SelectionCancellation")


@final
class SelectionEvaluation(msgspec.Struct, frozen=True, kw_only=True):
    """One result paired with budgets ready for the next selector call."""

    result: SelectionResult
    budgets: SelectionBudgets

    def __post_init__(self) -> None:
        """Require a closed selector result and exact cumulative usage."""
        if not is_selection_result(self.result):
            raise TypeError("selection evaluation requires a closed SelectionResult")
        if type(self.budgets) is not SelectionBudgets:
            raise TypeError("selection evaluation requires SelectionBudgets")


class SelectionMeter:
    """Private per-call accumulator that exposes only immutable budget snapshots."""

    __slots__ = ("_budgets",)

    def __init__(self, budgets: SelectionBudgets) -> None:
        """Start from the caller's exact cumulative usage."""
        if type(budgets) is not SelectionBudgets:
            raise TypeError("selection meter requires SelectionBudgets")
        self._budgets = budgets

    @property
    def budgets(self) -> SelectionBudgets:
        """Return the latest immutable cumulative accounting snapshot."""
        return self._budgets

    def charge_work(self, amount: int = 1) -> BudgetExceeded | None:
        """Charge deterministic traversal or expression work."""
        return self._charge("work", Count(value=_positive_amount(amount)))

    def charge_match(self, amount: int = 1) -> BudgetExceeded | None:
        """Charge each output occurrence, including duplicates."""
        return self._charge("matches", Count(value=_positive_amount(amount)))

    def charge_result_bytes(self, amount: int) -> BudgetExceeded | None:
        """Charge deterministic selected payload bytes before appending a match."""
        return self._charge("result_bytes", ByteCount(value=_non_negative_amount(amount)))

    def _charge(
        self,
        field: Literal["work", "matches", "result_bytes"],
        amount: Count | ByteCount,
    ) -> BudgetExceeded | None:
        budget = {
            "work": self._budgets.work,
            "matches": self._budgets.matches,
            "result_bytes": self._budgets.result_bytes,
        }[field]
        current = cast("Count | ByteCount", budget.usage.consumed)
        effective = cast("Count | ByteCount", budget.effective)
        attempted_value = current.value + amount.value
        attempted = type(amount)(value=attempted_value)
        if attempted_value > effective.value:
            return BudgetExceeded(
                budget=budget,
                attempted=attempted,
                effect=BudgetExhaustionEffect.EVALUATION_INCOMPLETE,
            )
        usage = msgspec.structs.replace(budget.usage, consumed=attempted)
        updated = msgspec.structs.replace(budget, usage=usage)
        if field == "work":
            self._budgets = msgspec.structs.replace(self._budgets, work=updated)
        elif field == "matches":
            self._budgets = msgspec.structs.replace(self._budgets, matches=updated)
        else:
            self._budgets = msgspec.structs.replace(self._budgets, result_bytes=updated)
        return None


def _positive_amount(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("selection count charge must be a positive integer")
    return value


def _non_negative_amount(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("selection byte charge must be a non-negative integer")
    return value


def selection_budget_operations() -> tuple[str, str, str]:
    """Expose stable operation identities for planners and tests."""
    return _WORK_OPERATION, _MATCH_OPERATION, _BYTES_OPERATION
