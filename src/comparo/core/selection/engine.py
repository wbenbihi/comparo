"""Public pure selection boundary over one closed prepared snapshot."""

from __future__ import annotations

from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.results import SelectionCancelled
from comparo.core.selection.accounting import SelectionControl
from comparo.core.selection.accounting import SelectionEvaluation
from comparo.core.selection.accounting import SelectionMeter
from comparo.core.selection.snapshot import SelectionSnapshot
from comparo.core.selection.source_evaluator import evaluate_source


def select(
    snapshot: SelectionSnapshot,
    selector: CompiledSelector,
    control: SelectionControl,
) -> SelectionEvaluation:
    """Evaluate one selector without I/O and return cumulative budget state."""
    if type(snapshot) is not SelectionSnapshot:
        raise TypeError("selection requires a SelectionSnapshot")
    if type(selector) is not CompiledSelector:
        raise TypeError("selection requires a CompiledSelector")
    if type(control) is not SelectionControl:
        raise TypeError("selection requires a SelectionControl")
    if control.cancellation.cancelled:
        return SelectionEvaluation(
            result=SelectionCancelled(code=control.cancellation.code),
            budgets=control.budgets,
        )
    meter = SelectionMeter(control.budgets)
    return SelectionEvaluation(
        result=evaluate_source(snapshot, selector, meter),
        budgets=meter.budgets,
    )


__all__ = ("select",)
