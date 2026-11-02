"""Cumulative logical and evidence accounting for pure diff evaluation."""

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
from comparo.core.selection.accounting import SelectionControl

_OPERATIONS = (
    "diff.evaluate.derived-values",
    "diff.evaluate.derived-bytes",
    "diff.evaluate.transform-work",
    "diff.evaluate.pairs",
    "diff.evaluate.facts",
    "diff.evidence.examples",
    "diff.evidence.bytes",
)


@final
class DiffBudgets(msgspec.Struct, frozen=True, kw_only=True):
    """Every independently bounded diff resource dimension."""

    derived_values: ResourceBudget
    derived_bytes: ResourceBudget
    transform_work: ResourceBudget
    pairs: ResourceBudget
    facts: ResourceBudget
    evidence_examples: ResourceBudget
    evidence_bytes: ResourceBudget

    def __post_init__(self) -> None:
        """Require exact resources, stages, units, operations, and scopes."""
        expected = (
            (self.derived_values, BudgetResource.DERIVED_VALUES, BudgetStage.EVALUATION, Count),
            (self.derived_bytes, BudgetResource.DERIVED_BYTES, BudgetStage.EVALUATION, ByteCount),
            (self.transform_work, BudgetResource.TRANSFORM_WORK, BudgetStage.EVALUATION, Count),
            (self.pairs, BudgetResource.PAIRS, BudgetStage.EVALUATION, Count),
            (self.facts, BudgetResource.FACTS, BudgetStage.EVALUATION, Count),
            (
                self.evidence_examples,
                BudgetResource.EVIDENCE_EXAMPLES,
                BudgetStage.EVIDENCE,
                Count,
            ),
            (
                self.evidence_bytes,
                BudgetResource.EVIDENCE_BYTES,
                BudgetStage.EVIDENCE,
                ByteCount,
            ),
        )
        for index, (budget, resource, stage, amount_type) in enumerate(expected):
            if type(budget) is not ResourceBudget:
                raise TypeError("diff accounting requires ResourceBudget values")
            if budget.resource is not resource or budget.stage is not stage:
                raise ValueError("diff budget resource or stage is incorrect")
            if budget.operation.value != _OPERATIONS[index]:
                raise ValueError("diff budget operation is incorrect")
            if type(budget.effective) is not amount_type:
                raise TypeError("diff budget has the wrong unit")
        scopes = tuple((budget.scope_kind, budget.scope) for budget, *_rest in expected)
        if len(set(scopes)) != 1:
            raise ValueError("diff budgets must share one exact scope")


@final
class DiffCancellation(msgspec.Struct, frozen=True, kw_only=True):
    """Immutable cancellation observation at the pure call boundary."""

    cancelled: bool
    code: DiagnosticCode

    def __post_init__(self) -> None:
        """Require a typed immutable cancellation observation."""
        if type(self.cancelled) is not bool:
            raise TypeError("diff cancellation state must be bool")
        if type(self.code) is not DiagnosticCode:
            raise TypeError("diff cancellation requires a DiagnosticCode")


@final
class DiffControl(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned independent side selection and evaluation controls."""

    baseline_selection: SelectionControl
    candidate_selection: SelectionControl
    budgets: DiffBudgets
    cancellation: DiffCancellation

    def __post_init__(self) -> None:
        """Require independent typed controls and evaluation budgets."""
        if type(self.baseline_selection) is not SelectionControl:
            raise TypeError("diff baseline selection control must be SelectionControl")
        if type(self.candidate_selection) is not SelectionControl:
            raise TypeError("diff candidate selection control must be SelectionControl")
        if type(self.budgets) is not DiffBudgets:
            raise TypeError("diff control requires DiffBudgets")
        if type(self.cancellation) is not DiffCancellation:
            raise TypeError("diff control requires DiffCancellation")


class DiffMeter:
    """Private atomic accumulator; failed charges never mutate usage."""

    __slots__ = ("_budgets",)

    def __init__(self, budgets: DiffBudgets) -> None:
        self._budgets = budgets

    @property
    def budgets(self) -> DiffBudgets:
        return self._budgets

    def charge_work(self, amount: int = 1) -> BudgetExceeded | None:
        return self._charge("transform_work", Count(value=_positive(amount)))

    def charge_derived(self, size: int) -> BudgetExceeded | None:
        if type(size) is not int or size < 0:
            raise ValueError("derived value size must be non-negative")
        before = self._budgets
        if (failure := self._charge("derived_values", Count(value=1))) is not None:
            return failure
        if (failure := self._charge("derived_bytes", ByteCount(value=size))) is not None:
            self._budgets = before
            return failure
        return None

    def derived_exhaustion(self, size: int) -> BudgetExceeded | None:
        """Preview atomic derived-value capacity before allocating a large result."""
        before = self._budgets
        failure = self.charge_derived(size)
        self._budgets = before
        return failure

    def charge_pair(self) -> BudgetExceeded | None:
        return self._charge("pairs", Count(value=1))

    def charge_fact(self) -> BudgetExceeded | None:
        return self._charge("facts", Count(value=1))

    def fact_exhaustion(self) -> BudgetExceeded | None:
        """Preview a fact charge so capacity can be reserved before comparison work."""
        before = self._budgets
        failure = self.charge_fact()
        self._budgets = before
        return failure

    def charge_evidence(self, size: int) -> BudgetExceeded | None:
        if type(size) is not int or size < 0:
            raise ValueError("evidence size must be non-negative")
        before = self._budgets
        if (failure := self._charge("evidence_examples", Count(value=1))) is not None:
            return failure
        if (failure := self._charge("evidence_bytes", ByteCount(value=size))) is not None:
            self._budgets = before
            return failure
        return None

    def evidence_capacity(self) -> tuple[int, int]:
        """Return remaining example and byte capacity without mutating usage."""
        examples = self._budgets.evidence_examples
        byte_budget = self._budgets.evidence_bytes
        example_usage = cast("Count", examples.usage.consumed)
        byte_usage = cast("ByteCount", byte_budget.usage.consumed)
        return (
            cast("Count", examples.effective).value - example_usage.value,
            cast("ByteCount", byte_budget.effective).value - byte_usage.value,
        )

    def evidence_exhaustion(self, size: int) -> BudgetExceeded | None:
        """Preview the exact first evidence charge failure without mutation."""
        before = self._budgets
        failure = self.charge_evidence(size)
        self._budgets = before
        return failure

    def _charge(
        self,
        field: Literal[
            "derived_values",
            "derived_bytes",
            "transform_work",
            "pairs",
            "facts",
            "evidence_examples",
            "evidence_bytes",
        ],
        amount: Count | ByteCount,
    ) -> BudgetExceeded | None:
        budget = {
            "derived_values": self._budgets.derived_values,
            "derived_bytes": self._budgets.derived_bytes,
            "transform_work": self._budgets.transform_work,
            "pairs": self._budgets.pairs,
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
        self._budgets = msgspec.structs.replace(
            self._budgets,
            **{field: msgspec.structs.replace(budget, usage=usage)},
        )
        return None


def diff_budget_operations() -> tuple[str, ...]:
    """Expose stable operations for defaults, planners, and tests."""
    return _OPERATIONS


def _positive(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("diff count charge must be positive")
    return value


__all__ = (
    "DiffBudgets",
    "DiffCancellation",
    "DiffControl",
    "diff_budget_operations",
)
