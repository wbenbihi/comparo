"""Cover context-independent decimal fractions and exact aligned summation."""

from __future__ import annotations

from decimal import ROUND_FLOOR
from decimal import Decimal
from decimal import Inexact
from decimal import Rounded
from decimal import localcontext
from fractions import Fraction

import pytest

from comparo.core.diffs.exact_numbers import _coefficient
from comparo.core.diffs.exact_numbers import _decimal_part
from comparo.core.diffs.exact_numbers import decimal_fraction
from comparo.core.diffs.exact_numbers import sum_decimals
from comparo.core.diffs.runtime import DiffHaltError
from tests.core.diffs.factories import meter


def test_decimal_fraction_handles_positive_and_negative_exponents_exactly() -> None:
    assert decimal_fraction(Decimal("1E+2"), meter()) == Fraction(100, 1)
    assert decimal_fraction(Decimal("-0.25"), meter()) == Fraction(-1, 4)
    assert decimal_fraction(Decimal("0"), meter()) == Fraction(0, 1)


def test_decimal_fraction_charges_before_large_coefficient_work() -> None:
    with pytest.raises(DiffHaltError) as caught:
        decimal_fraction(Decimal("123.45"), meter(work=0))
    assert caught.value.exhaustion is not None


def test_sum_decimals_has_exact_empty_and_cancelling_zero_identities() -> None:
    assert sum_decimals((), meter()) == Decimal(0)
    assert sum_decimals((Decimal("1.25"), Decimal("-1.25")), meter()) == Decimal(0)


def test_sum_decimals_aligns_exponents_and_preserves_sign_under_hostile_context() -> None:
    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_FLOOR
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert sum_decimals((Decimal("1.2"), Decimal("0.03")), meter()) == Decimal("1.23")
        assert sum_decimals((Decimal("-1.2"), Decimal("-0.03")), meter()) == Decimal("-1.23")


def test_sum_decimals_honors_work_budget_before_aligned_arithmetic() -> None:
    with pytest.raises(DiffHaltError) as caught:
        sum_decimals((Decimal("1.2"), Decimal("0.03")), meter(work=0))
    assert caught.value.exhaustion is not None


def test_exact_number_private_parts_preserve_empty_and_negative_coefficients() -> None:
    assert _coefficient(0, ()) == 0
    assert _coefficient(1, (1, 2, 3)) == -123
    assert _decimal_part(Decimal("-1.20"), meter()) == (-120, -2, 3)
