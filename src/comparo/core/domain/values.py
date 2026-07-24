"""Exact immutable values and exhaustive value-resolution outcomes."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import TypeGuard
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId


def _require_exact_bool(value: object) -> None:
    if type(value) is not bool:
        msg = "JSON boolean must be a bool"
        raise TypeError(msg)


def _require_value_provenance(value: object, *, outcome: str) -> None:
    if type(value) is not ValueProvenance:
        msg = f"{outcome} provenance must be ValueProvenance"
        raise TypeError(msg)


def _canonical_decimal(value: Decimal) -> Decimal:
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        msg = "only finite Decimal values can be canonicalized"
        raise ValueError(msg)
    if not any(digits):
        return Decimal(0)
    canonical_digits = digits
    canonical_exponent = exponent
    while canonical_exponent < 0 and canonical_digits[-1] == 0:
        canonical_digits = canonical_digits[:-1]
        canonical_exponent += 1
    return Decimal((sign, canonical_digits, canonical_exponent))


@final
class JsonNull(msgspec.Struct, frozen=True, kw_only=True):
    """The JSON null value, distinct from absence and intentional omission."""

    def __post_init__(self) -> None:
        """Provide a uniform direct-construction validation boundary."""


@final
class JsonBoolean(msgspec.Struct, frozen=True, kw_only=True):
    """A JSON boolean that cannot be confused with Python's integer subtype."""

    value: bool

    def __post_init__(self) -> None:
        """Require the exact bool type at direct construction boundaries."""
        _require_exact_bool(self.value)


@final
class JsonInteger(msgspec.Struct, frozen=True, kw_only=True):
    """An arbitrary-precision JSON integer."""

    value: int

    def __post_init__(self) -> None:
        """Require an exact integer and reject bool explicitly."""
        if type(self.value) is not int:
            msg = "JSON integer must be an int and cannot be a bool"
            raise TypeError(msg)


@final
class JsonDecimal(msgspec.Struct, frozen=True, kw_only=True):
    """An arbitrary-precision finite non-integral JSON number representation."""

    value: Decimal

    def __post_init__(self) -> None:
        """Reject float, infinity, and NaN at direct construction boundaries."""
        if type(self.value) is not Decimal:
            msg = "JSON decimal must be a Decimal"
            raise TypeError(msg)
        if not self.value.is_finite():
            msg = "JSON decimal must be finite"
            raise ValueError(msg)
        canonical = _canonical_decimal(self.value)
        if self.value.as_tuple() != canonical.as_tuple():
            msg = "JSON decimal must use its canonical coefficient and exponent"
            raise ValueError(msg)
        canonical_exponent = cast("int", canonical.as_tuple().exponent)
        if canonical_exponent >= 0:
            msg = "integral JSON numbers must use JsonInteger"
            raise ValueError(msg)


@final
class JsonString(msgspec.Struct, frozen=True, kw_only=True):
    """An exact Unicode JSON string."""

    value: str

    def __post_init__(self) -> None:
        """Require the exact string type at direct construction boundaries."""
        if type(self.value) is not str:
            msg = "JSON string must be a str"
            raise TypeError(msg)


@final
class JsonArray(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable, ordered JSON array."""

    items: tuple[JsonValue, ...]

    def __post_init__(self) -> None:
        """Reject mutable containers and non-JSON elements."""
        if type(self.items) is not tuple:
            msg = "JSON array items must be an immutable tuple"
            raise TypeError(msg)
        if any(not is_json_value(item) for item in self.items):
            msg = "JSON array items must be exact JSON values"
            raise TypeError(msg)


@final
class JsonObjectMember(msgspec.Struct, frozen=True, kw_only=True):
    """One source-ordered JSON object member."""

    name: str
    value: JsonValue

    def __post_init__(self) -> None:
        """Require an exact string key and exact JSON value."""
        if type(self.name) is not str:
            msg = "JSON object member name must be a str"
            raise TypeError(msg)
        if not is_json_value(self.value):
            msg = "JSON object member value must be an exact JSON value"
            raise TypeError(msg)


@final
class JsonObject(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable JSON object retaining source member order."""

    members: tuple[JsonObjectMember, ...]

    def __post_init__(self) -> None:
        """Require typed members and reject invalid duplicate JSON names."""
        if type(self.members) is not tuple:
            msg = "JSON object members must be an immutable tuple"
            raise TypeError(msg)
        if any(type(member) is not JsonObjectMember for member in self.members):
            msg = "JSON object members must be JsonObjectMember values"
            raise TypeError(msg)
        names = tuple(member.name for member in self.members)
        if len(set(names)) != len(names):
            msg = "JSON object member names must be unique"
            raise ValueError(msg)


type JsonValue = (
    JsonNull | JsonBoolean | JsonInteger | JsonDecimal | JsonString | JsonArray | JsonObject
)


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Narrow an arbitrary value to the exact immutable JSON model."""
    return type(value) in {
        JsonNull,
        JsonBoolean,
        JsonInteger,
        JsonDecimal,
        JsonString,
        JsonArray,
        JsonObject,
    }


@final
class BytesValue(msgspec.Struct, frozen=True, kw_only=True):
    """Exact opaque bytes, deliberately outside the JSON value union."""

    value: bytes

    def __post_init__(self) -> None:
        """Require immutable exact bytes rather than text or bytearray."""
        if type(self.value) is not bytes:
            msg = "bytes value must be bytes"
            raise TypeError(msg)


type ExactValue = JsonValue | BytesValue


def json_number(value: int | Decimal) -> JsonInteger | JsonDecimal:
    """Build the one canonical exact representation of a JSON number."""
    if type(value) is int:
        return JsonInteger(value=value)
    if type(value) is not Decimal:
        msg = "JSON number must be an int or Decimal and cannot be a bool"
        raise TypeError(msg)
    if not value.is_finite():
        msg = "JSON number must be finite"
        raise ValueError(msg)
    canonical = _canonical_decimal(value)
    canonical_exponent = cast("int", canonical.as_tuple().exponent)
    if canonical_exponent >= 0:
        return JsonInteger(value=int(canonical))
    return JsonDecimal(value=canonical)


class UnavailableReason(StrEnum):
    """Expected absence categories on which an explicit fallback may advance."""

    NOT_DEFINED = "not-defined"
    NOT_FOUND = "not-found"
    NOT_PROVIDED = "not-provided"


class ResolutionErrorCode(StrEnum):
    """Stable failure categories that stop value resolution."""

    INVALID_SOURCE = "invalid-source"
    CONVERSION_FAILED = "conversion-failed"
    PROVIDER_FAILED = "provider-failed"
    CYCLE = "cycle"
    BUDGET_EXCEEDED = "budget-exceeded"


class ResolutionBlockCode(StrEnum):
    """Stable policy-denial categories that stop value resolution."""

    CAPABILITY_DENIED = "capability-denied"
    SOURCE_DENIED = "source-denied"
    PATH_DENIED = "path-denied"


@final
class Present[ValueT](msgspec.Struct, frozen=True, kw_only=True):
    """A resolved value, including explicit null, false, zero, or empty values."""

    value: ValueT | SensitiveValue[ValueT]
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require typed non-secret provenance for direct construction."""
        _require_value_provenance(self.provenance, outcome="present value")

    @property
    def sensitivity(self) -> Sensitivity:
        """Derive sensitivity from containment so raw secrets cannot be mislabeled public."""
        if isinstance(self.value, SensitiveValue):
            return Sensitivity.SENSITIVE
        return Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Return sensitivity lineage without revealing the resolved value."""
        if isinstance(self.value, SensitiveValue):
            return self.value.origins
        return ()


@final
class Unavailable(msgspec.Struct, frozen=True, kw_only=True):
    """An expected source absence on which an explicit fallback may advance."""

    reason: UnavailableReason
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require a typed reason and non-secret provenance."""
        if type(self.reason) is not UnavailableReason:
            msg = "unavailable reason must be an UnavailableReason"
            raise TypeError(msg)
        _require_value_provenance(self.provenance, outcome="unavailable")


@final
class Missing(msgspec.Struct, frozen=True, kw_only=True):
    """Intentional omission produced only by an optional value expression."""

    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require non-secret provenance for the intentional omission."""
        _require_value_provenance(self.provenance, outcome="missing")


@final
class Error(msgspec.Struct, frozen=True, kw_only=True):
    """A deterministic resolution failure that must stop fallback evaluation."""

    code: ResolutionErrorCode
    message: str
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require a typed failure code, safe message, and provenance."""
        if type(self.code) is not ResolutionErrorCode:
            msg = "resolution error code must be a ResolutionErrorCode"
            raise TypeError(msg)
        if type(self.message) is not str or not self.message:
            msg = "resolution error message must be non-empty text"
            raise ValueError(msg)
        _require_value_provenance(self.provenance, outcome="resolution error")


@final
class Blocked(msgspec.Struct, frozen=True, kw_only=True):
    """A policy denial that must stop fallback evaluation."""

    code: ResolutionBlockCode
    message: str
    provenance: ValueProvenance

    def __post_init__(self) -> None:
        """Require a typed policy code, safe message, and provenance."""
        if type(self.code) is not ResolutionBlockCode:
            msg = "resolution block code must be a ResolutionBlockCode"
            raise TypeError(msg)
        if type(self.message) is not str or not self.message:
            msg = "resolution block message must be non-empty text"
            raise ValueError(msg)
        _require_value_provenance(self.provenance, outcome="resolution block")


type ResolutionResult[ValueT] = Present[ValueT] | Unavailable | Missing | Error | Blocked
