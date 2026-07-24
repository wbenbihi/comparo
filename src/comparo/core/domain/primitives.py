"""Exact scalar primitives shared by the Comparo domain."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal
from math import gcd
from typing import final

import msgspec

_NANOSECONDS_PER_SECOND = Decimal(1_000_000_000)
_SHA256_HEX_LENGTH = 64


def _require_exact_int(value: int, *, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")


@final
class Count(msgspec.Struct, frozen=True, kw_only=True):
    """An exact non-negative item count."""

    value: int

    def __post_init__(self) -> None:
        """Reject booleans and negative counts at direct construction boundaries."""
        _require_exact_int(self.value, label="count")
        if self.value < 0:
            raise ValueError("count must be non-negative")


@final
class PositiveCount(msgspec.Struct, frozen=True, kw_only=True):
    """An exact item count that must be at least one."""

    value: int

    def __post_init__(self) -> None:
        """Enforce a strictly positive integer."""
        _require_exact_int(self.value, label="positive count")
        if self.value < 1:
            raise ValueError("positive count must be greater than zero")


@final
class ByteCount(msgspec.Struct, frozen=True, kw_only=True):
    """An exact non-negative number of bytes."""

    value: int

    def __post_init__(self) -> None:
        """Reject booleans and negative byte counts."""
        _require_exact_int(self.value, label="byte count")
        if self.value < 0:
            raise ValueError("byte count must be non-negative")


@final
class Duration(msgspec.Struct, frozen=True, kw_only=True):
    """A monotonic duration represented as an exact integer nanosecond count."""

    nanoseconds: int

    def __post_init__(self) -> None:
        """Enforce a non-negative exact nanosecond representation."""
        _require_exact_int(self.nanoseconds, label="duration")
        if self.nanoseconds < 0:
            raise ValueError("duration must be non-negative")

    @classmethod
    def from_seconds(cls, seconds: Decimal) -> Duration:
        """Construct a duration from an exactly representable finite Decimal."""
        if type(seconds) is not Decimal or not seconds.is_finite():
            raise ValueError("seconds must be a finite Decimal")
        nanoseconds = seconds * _NANOSECONDS_PER_SECOND
        integral = nanoseconds.to_integral_exact()
        if nanoseconds != integral:
            raise ValueError("seconds must resolve to whole nanoseconds")
        return cls(nanoseconds=int(integral))


@final
class Ratio(msgspec.Struct, frozen=True, kw_only=True):
    """A canonical finite non-negative rational number."""

    numerator: int
    denominator: PositiveCount

    def __post_init__(self) -> None:
        """Reject invalid or non-reduced rational representations."""
        _require_exact_int(self.numerator, label="ratio numerator")
        if self.numerator < 0:
            raise ValueError("ratio numerator must be non-negative")
        if type(self.denominator) is not PositiveCount:
            raise TypeError("ratio denominator must be a PositiveCount")
        if gcd(self.numerator, self.denominator.value) != 1:
            raise ValueError("ratio must use its canonical reduced representation")

    @classmethod
    def from_decimal(cls, value: Decimal) -> Ratio:
        """Construct the unique reduced representation of a finite Decimal."""
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError("ratio must originate from a finite Decimal")
        numerator, denominator = value.as_integer_ratio()
        return cls(
            numerator=numerator,
            denominator=PositiveCount(value=denominator),
        )


@final
class UtcInstant(msgspec.Struct, frozen=True, kw_only=True):
    """An absolute UTC instant expressed as Unix epoch nanoseconds."""

    unix_nanoseconds: int

    def __post_init__(self) -> None:
        """Require the exact integer representation."""
        _require_exact_int(self.unix_nanoseconds, label="UTC instant")

    @classmethod
    def from_datetime(cls, value: datetime) -> UtcInstant:
        """Convert an aware datetime without using floating-point timestamps."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC instant requires an aware datetime")
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = value.astimezone(UTC) - epoch
        nanoseconds = (
            delta.days * 86_400 + delta.seconds
        ) * 1_000_000_000 + delta.microseconds * 1_000
        return cls(unix_nanoseconds=nanoseconds)


@final
class Sha256Digest(msgspec.Struct, frozen=True, kw_only=True):
    """A canonical lowercase SHA-256 digest."""

    hex_value: str

    def __post_init__(self) -> None:
        """Validate the lowercase hexadecimal representation."""
        if (
            type(self.hex_value) is not str
            or len(self.hex_value) != _SHA256_HEX_LENGTH
            or self.hex_value.lower() != self.hex_value
            or any(character not in "0123456789abcdef" for character in self.hex_value)
        ):
            raise ValueError("SHA-256 digest must be 64 lowercase hexadecimal characters")


@final
class Fingerprint(msgspec.Struct, frozen=True, kw_only=True):
    """A versioned content fingerprint with an explicit algorithm identity."""

    algorithm: str
    digest: Sha256Digest

    def __post_init__(self) -> None:
        """Accept only the initial canonical fingerprint algorithm."""
        if self.algorithm != "sha256":
            raise ValueError("unsupported fingerprint algorithm")
        if type(self.digest) is not Sha256Digest:
            raise TypeError("fingerprint digest must be a Sha256Digest")
