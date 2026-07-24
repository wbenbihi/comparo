"""Tests for exact scalar domain primitives."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest

from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Ratio
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.primitives import UtcInstant


@pytest.mark.parametrize("value", [True, -1])
def test_count_rejects_invalid_values(value: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        Count(value=value)


def test_count_accepts_zero() -> None:
    assert Count(value=0).value == 0


@pytest.mark.parametrize("value", [False, 0, -1])
def test_positive_count_rejects_non_positive_integers(value: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        PositiveCount(value=value)


def test_positive_count_accepts_one() -> None:
    assert PositiveCount(value=1).value == 1


@pytest.mark.parametrize("value", [True, -1])
def test_byte_count_rejects_invalid_values(value: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        ByteCount(value=value)


def test_byte_count_accepts_zero() -> None:
    assert ByteCount(value=0).value == 0


@pytest.mark.parametrize("value", [True, -1])
def test_duration_rejects_invalid_nanoseconds(value: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        Duration(nanoseconds=value)


def test_duration_converts_exact_decimal_seconds() -> None:
    assert Duration.from_seconds(Decimal("1.000000001")).nanoseconds == 1_000_000_001


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("0.0000000001"), 1])
def test_duration_rejects_non_finite_inexact_or_non_decimal_seconds(value: object) -> None:
    with pytest.raises(ValueError, match=r"finite Decimal|whole nanoseconds"):
        Duration.from_seconds(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-0.1"), 1])
def test_ratio_rejects_invalid_decimal_values(value: object) -> None:
    with pytest.raises(ValueError, match=r"finite Decimal|non-negative"):
        Ratio.from_decimal(value)  # type: ignore[arg-type]


def test_ratio_has_one_canonical_representation() -> None:
    assert Ratio.from_decimal(Decimal("1.00")) == Ratio(
        numerator=1,
        denominator=PositiveCount(value=1),
    )
    assert Ratio.from_decimal(Decimal("-0")) == Ratio(
        numerator=0,
        denominator=PositiveCount(value=1),
    )
    with pytest.raises(ValueError, match="canonical reduced"):
        Ratio(numerator=2, denominator=PositiveCount(value=2))
    with pytest.raises(TypeError, match="PositiveCount"):
        Ratio(numerator=1, denominator=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="integer"):
        Ratio(numerator=True, denominator=PositiveCount(value=1))


def test_utc_instant_rejects_boolean() -> None:
    with pytest.raises(TypeError):
        UtcInstant(unix_nanoseconds=True)


def test_utc_instant_converts_aware_datetime_exactly() -> None:
    value = datetime(1970, 1, 1, 1, 0, 0, 1, tzinfo=timezone(timedelta(hours=1)))
    assert UtcInstant.from_datetime(value).unix_nanoseconds == 1_000


def test_utc_instant_rejects_naive_datetime() -> None:
    naive = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="aware datetime"):
        UtcInstant.from_datetime(naive)


@pytest.mark.parametrize(
    "value",
    ["0" * 63, "0" * 65, "G" * 64, "z" * 64, 1],
)
def test_sha256_digest_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        Sha256Digest(hex_value=value)  # type: ignore[arg-type]


def test_fingerprint_accepts_sha256_and_rejects_other_algorithms() -> None:
    digest = Sha256Digest(hex_value="a" * 64)
    assert Fingerprint(algorithm="sha256", digest=digest).digest == digest
    with pytest.raises(ValueError, match="unsupported fingerprint"):
        Fingerprint(algorithm="sha1", digest=digest)
    with pytest.raises(TypeError):
        Fingerprint(algorithm="sha256", digest="a" * 64)  # type: ignore[arg-type]
