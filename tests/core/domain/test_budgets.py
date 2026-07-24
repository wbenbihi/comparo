"""Tests for finite, provenance-rich resource budget contracts."""

from decimal import Decimal
from uuid import UUID

import pytest

from comparo.core.domain.budgets import BudgetContribution
from comparo.core.domain.budgets import BudgetContributionKind
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.budgets import BudgetExhaustionEffect
from comparo.core.domain.budgets import BudgetOperation
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.budgets import BudgetScopeKind
from comparo.core.domain.budgets import BudgetStage
from comparo.core.domain.budgets import BudgetUsage
from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.scopes import RunScope

_REQUESTED = Count(value=10)
_EFFECTIVE = Count(value=8)
_CAP = Count(value=8)


def _origin(kind: ProvenanceKind = ProvenanceKind.VERSIONED_DEFAULT) -> ProvenanceEntry:
    return ProvenanceEntry(kind=kind)


def _contribution(
    amount: Count | ByteCount | Duration | Ratio,
    kind: BudgetContributionKind = BudgetContributionKind.DEFAULT,
    origin: ProvenanceKind = ProvenanceKind.VERSIONED_DEFAULT,
) -> BudgetContribution:
    return BudgetContribution(kind=kind, amount=amount, provenance=_origin(origin))


def _usage(amount: Count | ByteCount | Duration | Ratio) -> BudgetUsage:
    if type(amount) is Count:
        zero: Count | ByteCount | Duration | Ratio = Count(value=0)
    elif type(amount) is ByteCount:
        zero = ByteCount(value=0)
    elif type(amount) is Duration:
        zero = Duration(nanoseconds=0)
    else:
        zero = Ratio.from_decimal(Decimal(0))
    return BudgetUsage(consumed=zero, in_use=zero, high_water=zero)


def _budget(
    *,
    requested: Count = _REQUESTED,
    effective: Count = _EFFECTIVE,
    cap: Count | None = _CAP,
    contributions: tuple[BudgetContribution, ...] | None = None,
    usage: BudgetUsage | None = None,
    scope: RunScope | None = None,
) -> ResourceBudget:
    if contributions is None:
        contributions = (
            _contribution(Count(value=5)),
            _contribution(
                requested,
                BudgetContributionKind.REQUEST,
                ProvenanceKind.INVOCATION,
            ),
            _contribution(
                Count(value=8),
                BudgetContributionKind.CEILING,
                ProvenanceKind.EXECUTION_POLICY,
            ),
        )
    return ResourceBudget(
        resource=BudgetResource.REQUESTS,
        scope_kind=BudgetScopeKind.RUN,
        stage=BudgetStage.PLANNING,
        operation=BudgetOperation(value="expand.requests"),
        requested=requested,
        effective=effective,
        cap=cap,
        contributions=contributions,
        usage=usage or _usage(requested),
        scope=scope,
    )


def test_budget_has_no_hard_coded_project_limit() -> None:
    large = Count(value=10**30)
    budget = _budget(
        requested=large,
        effective=large,
        cap=None,
        contributions=(_contribution(large),),
    )
    assert budget.effective == large
    assert budget.cap is None


def test_budget_retains_default_override_ceiling_usage_and_operation() -> None:
    run = RunScope(run_id=RunId(value=UUID(int=1)))
    budget = _budget(scope=run)
    assert budget.requested == Count(value=10)
    assert budget.effective == Count(value=8)
    assert budget.cap == Count(value=8)
    assert tuple(item.kind for item in budget.contributions) == (
        BudgetContributionKind.DEFAULT,
        BudgetContributionKind.REQUEST,
        BudgetContributionKind.CEILING,
    )
    assert budget.stage is BudgetStage.PLANNING
    assert budget.operation.value == "expand.requests"
    assert budget.scope == run


def test_multiple_ceilings_use_the_exact_lowest_value() -> None:
    requested = Ratio.from_decimal(Decimal("4"))
    low = Ratio.from_decimal(Decimal("1.5"))
    high = Ratio.from_decimal(Decimal("3"))
    middle = Ratio.from_decimal(Decimal("2.5"))
    contributions = (
        _contribution(requested),
        _contribution(high, BudgetContributionKind.CEILING, ProvenanceKind.SAFETY_PROFILE),
        _contribution(low, BudgetContributionKind.CEILING, ProvenanceKind.EXECUTION_POLICY),
        _contribution(middle, BudgetContributionKind.CEILING, ProvenanceKind.SAFETY_PROFILE),
    )
    budget = ResourceBudget(
        resource=BudgetResource.DECOMPRESSION_RATIO,
        scope_kind=BudgetScopeKind.BODY,
        stage=BudgetStage.DECODING,
        operation=BudgetOperation(value="decode.entity-body"),
        requested=requested,
        effective=low,
        cap=low,
        contributions=contributions,
        usage=_usage(requested),
    )
    assert budget.cap == low


def test_duration_budgets_compare_exact_nanoseconds() -> None:
    requested = Duration(nanoseconds=20)
    cap = Duration(nanoseconds=10)
    budget = ResourceBudget(
        resource=BudgetResource.DEADLINE,
        scope_kind=BudgetScopeKind.ATTEMPT,
        stage=BudgetStage.HTTP,
        operation=BudgetOperation(value="send.request"),
        requested=requested,
        effective=cap,
        cap=cap,
        contributions=(
            _contribution(requested),
            _contribution(cap, BudgetContributionKind.CEILING, ProvenanceKind.EXECUTION_POLICY),
        ),
        usage=_usage(requested),
    )
    assert type(budget.effective) is Duration
    assert budget.effective.nanoseconds == 10


def test_budget_operation_and_contribution_validate_components() -> None:
    with pytest.raises(ValueError, match="lowercase dotted token"):
        BudgetOperation(value="expand requests")
    with pytest.raises(TypeError, match="contribution kind"):
        BudgetContribution(
            kind="default",  # type: ignore[arg-type]
            amount=Count(value=1),
            provenance=_origin(),
        )
    with pytest.raises(TypeError, match="supported exact amount"):
        BudgetContribution(
            kind=BudgetContributionKind.DEFAULT,
            amount=1,  # type: ignore[arg-type]
            provenance=_origin(),
        )
    with pytest.raises(TypeError, match="provenance"):
        BudgetContribution(
            kind=BudgetContributionKind.DEFAULT,
            amount=Count(value=1),
            provenance="default",  # type: ignore[arg-type]
        )


def test_budget_usage_is_typed_and_unit_consistent() -> None:
    usage = BudgetUsage(
        consumed=ByteCount(value=5),
        in_use=ByteCount(value=2),
        high_water=ByteCount(value=4),
    )
    assert usage.high_water == ByteCount(value=4)
    with pytest.raises(TypeError, match="supported exact amounts"):
        BudgetUsage(
            consumed=1,  # type: ignore[arg-type]
            in_use=Count(value=0),
            high_water=Count(value=0),
        )
    with pytest.raises(TypeError, match="one exact unit"):
        BudgetUsage(
            consumed=Count(value=1),
            in_use=ByteCount(value=0),
            high_water=Count(value=1),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("resource", "requests"),
        ("scope_kind", "run"),
        ("stage", "planning"),
        ("operation", "expand.requests"),
        ("usage", "zero"),
        ("scope", "run"),
    ],
)
def test_budget_rejects_untyped_identity_fields(field: str, replacement: object) -> None:
    values: dict[str, object] = {
        "resource": BudgetResource.REQUESTS,
        "scope_kind": BudgetScopeKind.RUN,
        "stage": BudgetStage.PLANNING,
        "operation": BudgetOperation(value="expand.requests"),
        "requested": Count(value=1),
        "effective": Count(value=1),
        "cap": None,
        "contributions": (_contribution(Count(value=1)),),
        "usage": _usage(Count(value=1)),
        "scope": None,
    }
    values[field] = replacement
    with pytest.raises(TypeError):
        ResourceBudget(**values)  # type: ignore[arg-type]


def test_budget_rejects_invalid_amount_and_contribution_shapes() -> None:
    with pytest.raises(TypeError, match="requested and effective"):
        _budget(
            requested=1,  # type: ignore[arg-type]
            effective=Count(value=1),
            cap=None,
            contributions=(_contribution(Count(value=1)),),
        )
    with pytest.raises(TypeError, match="budget cap"):
        _budget(cap=8)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="immutable tuple"):
        _budget(contributions=[_contribution(Count(value=10))])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must not be empty"):
        _budget(contributions=())
    with pytest.raises(TypeError, match="BudgetContribution"):
        _budget(contributions=("default",))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="one exact unit"):
        _budget(
            cap=None,
            effective=Count(value=10),
            contributions=(_contribution(ByteCount(value=10)),),
        )


def test_budget_recomputes_every_derivation_invariant() -> None:
    ceiling_only = (
        _contribution(
            Count(value=8),
            BudgetContributionKind.CEILING,
            ProvenanceKind.EXECUTION_POLICY,
        ),
    )
    with pytest.raises(ValueError, match="default or requested"):
        _budget(contributions=ceiling_only)
    with pytest.raises(ValueError, match="begin with a versioned default"):
        _budget(
            contributions=(
                _contribution(
                    Count(value=10),
                    BudgetContributionKind.REQUEST,
                    ProvenanceKind.PROJECT,
                ),
            ),
            effective=Count(value=10),
            cap=None,
        )
    with pytest.raises(ValueError, match="only one default"):
        _budget(
            contributions=(
                _contribution(Count(value=5)),
                _contribution(Count(value=10)),
            ),
            effective=Count(value=10),
            cap=None,
        )
    with pytest.raises(ValueError, match="last ordinary contribution"):
        _budget(
            contributions=(_contribution(Count(value=5)),),
            effective=Count(value=10),
            cap=None,
        )
    with pytest.raises(ValueError, match="lowest ceiling"):
        _budget(cap=Count(value=7))
    with pytest.raises(ValueError, match="constrained by its cap"):
        _budget(effective=Count(value=7))


def test_budget_exhaustion_is_a_typed_non_success_outcome() -> None:
    budget = _budget()
    exceeded = BudgetExceeded(
        budget=budget,
        attempted=Count(value=9),
        effect=BudgetExhaustionEffect.PLAN_REJECTED,
    )
    assert exceeded.budget.stage is BudgetStage.PLANNING
    assert exceeded.effect is BudgetExhaustionEffect.PLAN_REJECTED
    with pytest.raises(TypeError, match="ResourceBudget"):
        BudgetExceeded(
            budget="budget",  # type: ignore[arg-type]
            attempted=Count(value=9),
            effect=BudgetExhaustionEffect.PLAN_REJECTED,
        )
    with pytest.raises(TypeError, match="supported exact amount"):
        BudgetExceeded(
            budget=budget,
            attempted=9,  # type: ignore[arg-type]
            effect=BudgetExhaustionEffect.PLAN_REJECTED,
        )
    with pytest.raises(TypeError, match="effect"):
        BudgetExceeded(
            budget=budget,
            attempted=Count(value=9),
            effect="plan-rejected",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="one exact unit"):
        BudgetExceeded(
            budget=budget,
            attempted=ByteCount(value=9),
            effect=BudgetExhaustionEffect.PLAN_REJECTED,
        )
    with pytest.raises(ValueError, match="must exceed"):
        BudgetExceeded(
            budget=budget,
            attempted=Count(value=8),
            effect=BudgetExhaustionEffect.PLAN_REJECTED,
        )


def test_resource_budgets_are_immutable() -> None:
    budget = _budget()
    with pytest.raises(AttributeError):
        budget.cap = None  # type: ignore[misc]
