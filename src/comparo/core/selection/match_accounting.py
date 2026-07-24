"""Pre-append accounting for selected values outside JSON-path evaluation."""

from __future__ import annotations

from typing import cast

from comparo.core.domain.selectors.results import SelectionBudgetExceeded
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.value_size import selected_value_size

_ACCESS = SensitiveValueAccess(purpose="selector.accounting")


def account_match_before_append(
    match: SelectionMatch,
    meter: SelectionMeter,
) -> SelectionBudgetExceeded | None:
    """Charge one match and its exact payload size before a caller retains it."""
    exhaustion = meter.charge_match()
    if exhaustion is not None:
        return SelectionBudgetExceeded(exhaustion=exhaustion)
    value = match.value.reveal(access=_ACCESS) if is_sensitive_value(match.value) else match.value
    sized = selected_value_size(cast(SelectedValue, value), meter)
    if type(sized) is SelectionBudgetExceeded:
        return sized
    exhaustion = meter.charge_result_bytes(sized.value)
    return SelectionBudgetExceeded(exhaustion=exhaustion) if exhaustion is not None else None


def charge_work(
    meter: SelectionMeter,
    amount: int = 1,
) -> SelectionBudgetExceeded | None:
    """Charge positive selection work and adapt exhaustion to a selector result."""
    exhaustion = meter.charge_work(amount)
    return SelectionBudgetExceeded(exhaustion=exhaustion) if exhaustion is not None else None


__all__ = ("account_match_before_append", "charge_work")
