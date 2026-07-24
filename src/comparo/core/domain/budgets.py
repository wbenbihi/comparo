"""Finite typed resource budgets with complete derivation and usage evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType
from typing import Final
from typing import final

import msgspec

from comparo.core.domain.budget_contracts import BudgetAccountingMode
from comparo.core.domain.budget_contracts import BudgetExhaustionEffect as BudgetExhaustionEffect
from comparo.core.domain.budget_contracts import BudgetResource as BudgetResource
from comparo.core.domain.budget_contracts import BudgetScopeKind as BudgetScopeKind
from comparo.core.domain.budget_contracts import BudgetStage as BudgetStage
from comparo.core.domain.budget_contracts import budget_dimension_contract
from comparo.core.domain.budget_origins import BudgetOrigin
from comparo.core.domain.budget_origins import DefaultBudgetOrigin
from comparo.core.domain.budget_origins import DefinitionBudgetOrigin
from comparo.core.domain.budget_origins import InvocationBudgetOrigin
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope

_OPERATION_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")

type BudgetAmount = Count | ByteCount | Duration | Ratio


type RuntimeScopeType = (
    type[RunScope]
    | type[CellScope]
    | type[TrialScope]
    | type[EnvironmentScope]
    | type[ExecutionScope]
    | type[StepScope]
    | type[AttemptScope]
)

_RUNTIME_SCOPE_TYPES: Final[Mapping[BudgetScopeKind, RuntimeScopeType]] = MappingProxyType(
    {
        BudgetScopeKind.RUN: RunScope,
        BudgetScopeKind.CELL: CellScope,
        BudgetScopeKind.TRIAL: TrialScope,
        BudgetScopeKind.ENVIRONMENT: EnvironmentScope,
        BudgetScopeKind.EXECUTION: ExecutionScope,
        BudgetScopeKind.STEP: StepScope,
        BudgetScopeKind.ATTEMPT: AttemptScope,
    }
)


@final
class BudgetOperation(msgspec.Struct, frozen=True, kw_only=True):
    """A stable operation identity at the exact enforcement point."""

    value: str

    def __post_init__(self) -> None:
        """Require a deterministic, machine-readable operation name."""
        if type(self.value) is not str or _OPERATION_PATTERN.fullmatch(self.value) is None:
            raise ValueError("budget operation must be a lowercase dotted token")


class BudgetContributionKind(StrEnum):
    """How one configuration origin participates in budget derivation."""

    DEFAULT = "default"
    REQUEST = "request"
    CEILING = "ceiling"


@final
class BudgetContribution(msgspec.Struct, frozen=True, kw_only=True):
    """One value-free provenance entry paired with its exact budget amount."""

    kind: BudgetContributionKind
    amount: BudgetAmount
    origin: BudgetOrigin

    def __post_init__(self) -> None:
        """Require typed contribution components."""
        if type(self.kind) is not BudgetContributionKind:
            raise TypeError("budget contribution kind must be a BudgetContributionKind")
        if not _is_budget_amount(self.amount):
            raise TypeError("budget contribution amount must be a supported exact amount")
        _validate_budget_origin(self.kind, self.origin)


def _validate_budget_origin(kind: BudgetContributionKind, origin: BudgetOrigin) -> None:
    """Bind each derivation role to an exact authorized origin type and resource kind."""
    if type(origin) not in {DefaultBudgetOrigin, DefinitionBudgetOrigin, InvocationBudgetOrigin}:
        raise TypeError("budget contribution origin must be an exact BudgetOrigin")
    if kind is BudgetContributionKind.DEFAULT:
        if type(origin) is not DefaultBudgetOrigin:
            raise ValueError("default budget contribution requires a versioned default-set origin")
        return
    if kind is BudgetContributionKind.CEILING:
        if type(origin) is not DefinitionBudgetOrigin or origin.definition.identity.kind not in {
            ResourceKind.SAFETY_PROFILE,
            ResourceKind.EXECUTION_POLICY,
        }:
            raise ValueError("budget ceiling requires an exact SafetyProfile or ExecutionPolicy")
        return
    if type(origin) is InvocationBudgetOrigin:
        return
    ordinary_kinds = {
        ResourceKind.PROJECT,
        ResourceKind.EXECUTION_PROFILE,
        ResourceKind.ASSERTION_PROFILE,
        ResourceKind.DIFF_PROFILE,
        ResourceKind.GATE_PROFILE,
        ResourceKind.REDACTION_PROFILE,
        ResourceKind.ARTIFACT_PROFILE,
    }
    if (
        type(origin) is not DefinitionBudgetOrigin
        or origin.definition.identity.kind not in ordinary_kinds
    ):
        raise ValueError(
            "ordinary budget contribution requires an exact Project, profile, or invocation"
        )


@final
class BudgetUsage(msgspec.Struct, frozen=True, kw_only=True):
    """Actual cumulative use, live reservation, and simultaneous high-water mark."""

    consumed: BudgetAmount
    in_use: BudgetAmount
    high_water: BudgetAmount

    def __post_init__(self) -> None:
        """Require one exact unit type for all usage measurements."""
        for amount in (self.consumed, self.in_use, self.high_water):
            if not _is_budget_amount(amount):
                raise TypeError("budget usage values must be supported exact amounts")
        _require_same_amount_type(self.consumed, self.in_use, self.high_water)


@final
class ResourceBudget(msgspec.Struct, frozen=True, kw_only=True):
    """One finite effective budget with its full request and ceiling derivation."""

    resource: BudgetResource
    scope_kind: BudgetScopeKind
    stage: BudgetStage
    operation: BudgetOperation
    requested: BudgetAmount
    effective: BudgetAmount
    cap: BudgetAmount | None
    contributions: tuple[BudgetContribution, ...]
    usage: BudgetUsage
    scope: RuntimeScope | None = None

    def __post_init__(self) -> None:
        """Validate units, derivation evidence, and effective minimum semantics."""
        _validate_budget_identity(self)
        _validate_budget_derivation(self)


def _validate_budget_identity(budget: ResourceBudget) -> None:
    """Validate the typed resource, enforcement point, usage, and runtime scope."""
    if type(budget.resource) is not BudgetResource:
        raise TypeError("budget resource must be a BudgetResource")
    if type(budget.scope_kind) is not BudgetScopeKind:
        raise TypeError("budget scope kind must be a BudgetScopeKind")
    if type(budget.stage) is not BudgetStage:
        raise TypeError("budget stage must be a BudgetStage")
    if type(budget.operation) is not BudgetOperation:
        raise TypeError("budget operation must be a BudgetOperation")
    if type(budget.usage) is not BudgetUsage:
        raise TypeError("budget usage must be a BudgetUsage")
    contract = budget_dimension_contract(budget.resource)
    if (budget.scope_kind, budget.stage) not in contract.placements:
        raise ValueError("budget resource does not support the declared scope/stage placement")
    if budget.scope is not None and type(budget.scope) not in {
        RunScope,
        CellScope,
        TrialScope,
        EnvironmentScope,
        ExecutionScope,
        StepScope,
        AttemptScope,
    }:
        raise TypeError("budget scope must be a supported RuntimeScope")
    _validate_budget_scope(budget.scope_kind, budget.scope)


def _validate_budget_scope(
    scope_kind: BudgetScopeKind,
    scope: RuntimeScope | None,
) -> None:
    """Require a concrete runtime coordinate exactly when its kind is addressable."""
    expected_scope = _RUNTIME_SCOPE_TYPES.get(scope_kind)

    if expected_scope is None:
        if scope is not None:
            raise ValueError("non-runtime budget scope kind cannot carry a RuntimeScope")
        return
    if scope is None:
        raise ValueError("runtime-addressable budget scope kind requires a concrete scope")
    if type(scope) is not expected_scope:
        raise ValueError("budget scope kind must match the concrete RuntimeScope")


def _validate_budget_derivation(budget: ResourceBudget) -> None:
    """Validate exact units and recompute requested, cap, and effective values."""
    _validate_budget_inputs(budget)
    ordinary = tuple(
        item for item in budget.contributions if item.kind is not BudgetContributionKind.CEILING
    )
    ceilings = tuple(
        item for item in budget.contributions if item.kind is BudgetContributionKind.CEILING
    )
    _validate_ordinary_contributions(budget.requested, ordinary)
    expected_cap = _minimum_amount(tuple(item.amount for item in ceilings))
    if budget.cap != expected_cap:
        raise ValueError("budget cap must equal the lowest ceiling contribution")
    expected_effective = (
        budget.requested
        if expected_cap is None or _amount_less_equal(budget.requested, expected_cap)
        else expected_cap
    )
    if budget.effective != expected_effective:
        raise ValueError("effective budget must equal the requested value constrained by its cap")


def _validate_budget_inputs(budget: ResourceBudget) -> None:
    """Validate the closed amount union, immutable evidence, and consistent units."""
    if not _is_budget_amount(budget.requested) or not _is_budget_amount(budget.effective):
        raise TypeError("requested and effective budgets must be supported exact amounts")
    if budget.cap is not None and not _is_budget_amount(budget.cap):
        raise TypeError("budget cap must be a supported exact amount")
    if type(budget.contributions) is not tuple:
        raise TypeError("budget contributions must be an immutable tuple")
    if not budget.contributions:
        raise ValueError("budget contributions must not be empty")
    if any(type(item) is not BudgetContribution for item in budget.contributions):
        raise TypeError("budget contributions must contain BudgetContribution values")

    amounts = tuple(item.amount for item in budget.contributions)
    optional_cap: tuple[BudgetAmount, ...] = () if budget.cap is None else (budget.cap,)
    _require_same_amount_type(
        budget.requested,
        budget.effective,
        budget.usage.consumed,
        budget.usage.in_use,
        budget.usage.high_water,
        *optional_cap,
        *amounts,
    )
    contract = budget_dimension_contract(budget.resource)
    if type(budget.requested) is not contract.amount_type:
        raise TypeError("budget resource requires its contract-defined amount type")
    if not _amount_less_equal(budget.usage.in_use, budget.usage.high_water):
        raise ValueError("budget in-use amount must not exceed its high-water mark")
    if not _amount_less_equal(budget.usage.high_water, budget.effective):
        raise ValueError("budget high-water mark must not exceed the effective limit")
    if contract.accounting is BudgetAccountingMode.CUMULATIVE and not _amount_less_equal(
        budget.usage.consumed, budget.effective
    ):
        raise ValueError("cumulative budget consumption must not exceed the effective limit")


def _validate_ordinary_contributions(
    requested: BudgetAmount,
    ordinary: tuple[BudgetContribution, ...],
) -> None:
    """Require one initial default followed by zero or more ordinary overrides."""
    if not ordinary:
        raise ValueError("budget derivation requires a default or requested value")
    if ordinary[0].kind is not BudgetContributionKind.DEFAULT:
        raise ValueError("budget derivation must begin with a versioned default")
    if any(item.kind is BudgetContributionKind.DEFAULT for item in ordinary[1:]):
        raise ValueError("budget derivation may contain only one default")
    if ordinary[-1].amount != requested:
        raise ValueError("requested budget must equal the last ordinary contribution")


@final
class BudgetExceeded(msgspec.Struct, frozen=True, kw_only=True):
    """A typed exhaustion outcome that can never be mistaken for drift or success."""

    budget: ResourceBudget
    attempted: BudgetAmount
    effect: BudgetExhaustionEffect

    def __post_init__(self) -> None:
        """Require a same-unit amount that strictly exceeds the effective budget."""
        if type(self.budget) is not ResourceBudget:
            raise TypeError("budget exhaustion requires a ResourceBudget")
        if not _is_budget_amount(self.attempted):
            raise TypeError("attempted budget amount must be a supported exact amount")
        if type(self.effect) is not BudgetExhaustionEffect:
            raise TypeError("budget exhaustion effect must be a BudgetExhaustionEffect")
        contract = budget_dimension_contract(self.budget.resource)
        effects = contract.exhaustion_effects_by_stage[self.budget.stage]
        if self.effect not in effects:
            raise ValueError("budget exhaustion effect is not valid for this resource")
        _require_same_amount_type(self.budget.effective, self.attempted)
        if _amount_less_equal(self.attempted, self.budget.effective):
            raise ValueError("attempted amount must exceed the effective budget")


def _is_budget_amount(amount: object) -> bool:
    """Return whether an object is one of the closed exact budget amount types."""
    return type(amount) in {Count, ByteCount, Duration, Ratio}


def _amount_value(amount: BudgetAmount) -> Fraction:
    """Return an exact rational scalar without floating-point conversion."""
    if type(amount) is Duration:
        return Fraction(amount.nanoseconds)
    if type(amount) is Ratio:
        return Fraction(amount.numerator, amount.denominator.value)
    return Fraction(amount.value)


def _amount_less_equal(left: BudgetAmount, right: BudgetAmount) -> bool:
    """Compare two same-unit amounts without floating-point conversion."""
    _require_same_amount_type(left, right)
    return _amount_value(left) <= _amount_value(right)


def _minimum_amount(amounts: tuple[BudgetAmount, ...]) -> BudgetAmount | None:
    """Return the exact minimum of a same-unit tuple, or no cap."""
    if not amounts:
        return None
    result = amounts[0]
    for amount in amounts[1:]:
        if _amount_less_equal(amount, result):
            result = amount
    return result


def _require_same_amount_type(first: BudgetAmount, *remaining: BudgetAmount) -> None:
    """Reject attempts to compare or aggregate unlike budget units."""
    if any(type(amount) is not type(first) for amount in remaining):
        raise TypeError("budget amounts must use one exact unit type")
