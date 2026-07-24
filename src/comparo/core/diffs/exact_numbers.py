"""Context-independent exact decimal arithmetic for pure diff operations."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import cast

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.value_support import require_work


def decimal_fraction(value: Decimal, meter: DiffMeter) -> Fraction:
    """Convert a finite decimal to a fraction without consulting Decimal context."""
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast("int", raw_exponent)
    require_work(meter, max(1, len(digits) + abs(exponent)))
    coefficient = _coefficient(sign, digits)
    if exponent >= 0:
        return Fraction(coefficient * (10**exponent), 1)
    return Fraction(coefficient, 10 ** (-exponent))


def sum_decimals(values: tuple[Decimal, ...], meter: DiffMeter) -> Decimal:
    """Sum finite decimals by aligned integer coefficients under explicit work bounds."""
    parts = tuple(_decimal_part(value, meter) for value in values)
    minimum_exponent = min((exponent for _coefficient_value, exponent, _digits in parts), default=0)
    total = 0
    for coefficient, exponent, digit_count in parts:
        shift = exponent - minimum_exponent
        require_work(meter, max(1, digit_count + shift))
        total += coefficient * (10**shift)
    if total == 0:
        return Decimal(0)
    sign = 1 if total < 0 else 0
    digits = _integer_digits(abs(total), meter)
    return Decimal((sign, digits, minimum_exponent))


def _decimal_part(value: Decimal, meter: DiffMeter) -> tuple[int, int, int]:
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast("int", raw_exponent)
    require_work(meter, max(1, len(digits) + abs(exponent)))
    return _coefficient(sign, digits), exponent, max(1, len(digits))


def _coefficient(sign: int, digits: tuple[int, ...]) -> int:
    value = 0
    for digit in digits:
        value = value * 10 + digit
    return -value if sign else value


def _integer_digits(value: int, meter: DiffMeter) -> tuple[int, ...]:
    """Extract arbitrary-size base-ten digits without Python's text conversion limit."""
    reversed_digits = []
    while value:
        require_work(meter, max(1, value.bit_length()))
        value, digit = divmod(value, 10)
        reversed_digits.append(digit)
    reversed_digits.reverse()
    return tuple(reversed_digits or (0,))


__all__ = ("decimal_fraction", "sum_decimals")
