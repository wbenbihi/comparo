"""Shared prepared-operand terminal handling for pure diff operations."""

from __future__ import annotations

from typing import cast
from typing import final

import msgspec

from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.diffs.inputs import PreparedOperandError
from comparo.core.diffs.inputs import PreparedOperandReady
from comparo.core.diffs.inputs import PreparedOperandUnavailable
from comparo.core.diffs.inputs import prepared_result
from comparo.core.diffs.runtime import DiffHaltError
from comparo.core.diffs.value_support import reveal
from comparo.core.domain.diffs.results import DiffIncompleteCause
from comparo.core.domain.diffs.results import DiffSide
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.selectors.values import SelectedValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value


@final
class ResolvedDiffOperand(msgspec.Struct, frozen=True, kw_only=True):
    """One ready payload plus safe sensitivity dependency identity."""

    value: SelectedValue
    taint_origins: tuple[TaintOriginId, ...]


def resolve_operand(
    inputs: PreparedDiffInputs,
    expression: ValueExpression,
    side: DiffSide,
) -> ResolvedDiffOperand:
    """Resolve a prepared terminal without collapsing absence into evaluator error."""
    result = prepared_result(inputs, expression, side)
    if type(result) is PreparedOperandUnavailable:
        raise DiffHaltError(
            code=result.code,
            incomplete_cause=DiffIncompleteCause.UNAVAILABLE,
            side=side,
        )
    if type(result) is PreparedOperandError:
        raise DiffHaltError(code=result.code)
    if type(result) is not PreparedOperandReady:
        raise TypeError("unsupported prepared diff operand")
    return ResolvedDiffOperand(
        value=reveal(cast("object", result.value)),  # type: ignore[arg-type]
        taint_origins=result.value.origins if is_sensitive_value(result.value) else (),
    )


__all__ = ("ResolvedDiffOperand", "resolve_operand")
