"""Pure cumulative resource accounting for assertion evaluation."""

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
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count

_WORK_OPERATION = "assertion.evaluate.work"
_FACT_OPERATION = "assertion.evaluate.facts"
_EVIDENCE_OPERATION = "assertion.evidence.examples"
_EVIDENCE_BYTES_OPERATION = "assertion.evidence.bytes"


@final
class AssertionBudgets(msgspec.Struct, frozen=True, kw_only=True):
    """Cumulative evaluation and independently truncatable evidence budgets."""

    work: ResourceBudget
    facts: ResourceBudget
    evidence_examples: ResourceBudget
    evidence_bytes: ResourceBudget

    def __post_init__(self) -> None:
        """Require exact resources, stages, units, operations, and scope."""
        expected = (
            (
                self.work,
                BudgetResource.TRANSFORM_WORK,
                BudgetStage.EVALUATION,
                Count,
                _WORK_OPERATION,
            ),
            (self.facts, BudgetResource.FACTS, BudgetStage.EVALUATION, Count, _FACT_OPERATION),
            (
                self.evidence_examples,
                BudgetResource.EVIDENCE_EXAMPLES,
                BudgetStage.EVIDENCE,
                Count,
                _EVIDENCE_OPERATION,
            ),
            (
                self.evidence_bytes,
                BudgetResource.EVIDENCE_BYTES,
                BudgetStage.EVIDENCE,
                ByteCount,
                _EVIDENCE_BYTES_OPERATION,
            ),
        )
        for budget, resource, stage, amount_type, operation in expected:
            if type(budget) is not ResourceBudget:
                raise TypeError("assertion accounting requires ResourceBudget values")
            if budget.resource is not resource or budget.stage is not stage:
                raise ValueError("assertion budget resource or stage is incorrect")
            if budget.operation.value != operation:
                raise ValueError("assertion budget operation is incorrect")
            if type(budget.effective) is not amount_type:
                raise TypeError("assertion budget has the wrong unit")
        scopes = tuple((item.scope_kind, item.scope) for item, *_rest in expected)
        if len(set(scopes)) != 1:
            raise ValueError("assertion budgets must share one exact scope")


class AssertionMeter:
    """Private atomic accumulator for one pure assertion call."""

    __slots__ = ("_budgets",)

    def __init__(self, budgets: AssertionBudgets) -> None:
        """Start from caller-owned cumulative usage."""
        if type(budgets) is not AssertionBudgets:
            raise TypeError("assertion meter requires AssertionBudgets")
        self._budgets = budgets

    @property
    def budgets(self) -> AssertionBudgets:
        """Return the latest immutable cumulative snapshot."""
        return self._budgets

    def charge_work(self, amount: int = 1) -> BudgetExceeded | None:
        """Charge deterministic predicate work before evaluation."""
        return self._charge("work", Count(value=_positive(amount)))

    def charge_fact(self, amount: int = 1) -> BudgetExceeded | None:
        """Charge concrete facts before constructing them."""
        return self._charge("facts", Count(value=_positive(amount)))

    def preflight_fact(self, amount: int = 1) -> BudgetExceeded | None:
        """Check fact capacity before predicate work without consuming the reservation."""
        return self._would_exceed("facts", Count(value=_positive(amount)))

    def charge_evidence(self, size: int) -> BudgetExceeded | None:
        """Atomically reserve one retained example and its bounded byte estimate."""
        if type(size) is not int or size < 0:
            raise ValueError("evidence size must be a non-negative integer")
        before = self._budgets
        examples = self._charge("evidence_examples", Count(value=1))
        if examples is not None:
            return examples
        sized = self._charge("evidence_bytes", ByteCount(value=size))
        if sized is not None:
            self._budgets = before
            return sized
        return None

    def _charge(
        self,
        field: Literal["work", "facts", "evidence_examples", "evidence_bytes"],
        amount: Count | ByteCount,
    ) -> BudgetExceeded | None:
        budget = {
            "work": self._budgets.work,
            "facts": self._budgets.facts,
            "evidence_examples": self._budgets.evidence_examples,
            "evidence_bytes": self._budgets.evidence_bytes,
        }[field]
        current = cast("Count | ByteCount", budget.usage.consumed)
        effective = cast("Count | ByteCount", budget.effective)
        attempted = type(amount)(value=current.value + amount.value)
        if attempted.value > effective.value:
            return BudgetExceeded(
                budget=budget,
                attempted=attempted,
                effect=(
                    BudgetExhaustionEffect.EVIDENCE_TRUNCATED
                    if budget.stage is BudgetStage.EVIDENCE
                    else BudgetExhaustionEffect.EVALUATION_INCOMPLETE
                ),
            )
        usage = msgspec.structs.replace(budget.usage, consumed=attempted)
        updated = msgspec.structs.replace(budget, usage=usage)
        self._budgets = msgspec.structs.replace(self._budgets, **{field: updated})
        return None

    def _would_exceed(
        self,
        field: Literal["work", "facts", "evidence_examples", "evidence_bytes"],
        amount: Count | ByteCount,
    ) -> BudgetExceeded | None:
        budget = {
            "work": self._budgets.work,
            "facts": self._budgets.facts,
            "evidence_examples": self._budgets.evidence_examples,
            "evidence_bytes": self._budgets.evidence_bytes,
        }[field]
        current = cast("Count | ByteCount", budget.usage.consumed)
        effective = cast("Count | ByteCount", budget.effective)
        attempted = type(amount)(value=current.value + amount.value)
        if attempted.value <= effective.value:
            return None
        return BudgetExceeded(
            budget=budget,
            attempted=attempted,
            effect=(
                BudgetExhaustionEffect.EVIDENCE_TRUNCATED
                if budget.stage is BudgetStage.EVIDENCE
                else BudgetExhaustionEffect.EVALUATION_INCOMPLETE
            ),
        )


def assertion_budget_operations() -> tuple[str, str, str, str]:
    """Return stable planner-visible operation identities."""
    return _WORK_OPERATION, _FACT_OPERATION, _EVIDENCE_OPERATION, _EVIDENCE_BYTES_OPERATION


def _positive(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("assertion count charge must be positive")
    return value
