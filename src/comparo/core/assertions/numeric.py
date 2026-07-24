"""Ambient-context-independent exact numeric assertion mechanics."""

from __future__ import annotations

from fractions import Fraction

from comparo.core.domain.assertions.evidence import ApproximateEvidence
from comparo.core.domain.assertions.operators import ApproximateOperator
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger


def approximate_result(
    actual: object,
    operands: tuple[object, ...],
    operator: ApproximateOperator,
) -> tuple[bool, ApproximateEvidence]:
    """Return exact truth and rational evidence without consulting Decimal context."""
    if not _numeric(actual) or not _numeric(operands[0]):
        raise TypeError("approximate comparison requires numeric values")
    expected = _fraction(operands[0])
    index = 1
    absolute = Fraction(0)
    relative = Fraction(0)
    if operator.absolute_tolerance is not None:
        absolute = _fraction(operands[index])
        index += 1
    if operator.relative_tolerance is not None:
        relative = _fraction(operands[index])
    if absolute < 0 or relative < 0:
        raise ValueError("approximate tolerances cannot be negative")
    actual_number = _fraction(actual)
    tolerance = max(absolute, relative * max(abs(actual_number), abs(expected)))
    delta = abs(actual_number - expected)
    return delta <= tolerance, ApproximateEvidence(
        delta=_ratio(delta),
        effective_tolerance=_ratio(tolerance),
    )


def _numeric(value: object) -> bool:
    return type(value) in {JsonInteger, JsonDecimal}


def _fraction(value: object) -> Fraction:
    if type(value) is JsonInteger:
        return Fraction(value.value)
    if type(value) is JsonDecimal:
        numerator, denominator = value.value.as_integer_ratio()
        return Fraction(numerator, denominator)
    raise TypeError("value is not an exact assertion number")


def _ratio(value: Fraction) -> Ratio:
    return Ratio(
        numerator=value.numerator,
        denominator=PositiveCount(value=value.denominator),
    )
