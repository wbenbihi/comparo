"""Exact immutable values and exhaustive value-resolution outcomes."""

from __future__ import annotations

from decimal import Decimal
from typing import TypeGuard
from typing import cast
from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.provenance import ResolutionStepOutcome
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_references import ValueNamespace


def _require_exact_bool(value: object) -> None:
    if type(value) is not bool:
        msg = "JSON boolean must be a bool"
        raise TypeError(msg)


def _require_resolution_provenance(
    value: object,
    *,
    outcome: str,
    terminal: ResolutionStepOutcome,
) -> None:
    if type(value) is not ResolutionProvenance:
        msg = f"{outcome} provenance must be ResolutionProvenance"
        raise TypeError(msg)
    if value.terminal_outcome is not terminal:
        msg = f"{outcome} provenance must end with a {terminal.value} resolution step"
        raise ValueError(msg)


def _canonical_decimal(value: Decimal) -> Decimal:
    sign, digits, exponent = value.as_tuple()
    canonical_exponent = cast("int", exponent)
    if not any(digits):
        return Decimal(0)
    canonical_digits = digits
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

_EXACT_VALUE_TYPES: frozenset[type[object]] = frozenset(
    {
        JsonNull,
        JsonBoolean,
        JsonInteger,
        JsonDecimal,
        JsonString,
        JsonArray,
        JsonObject,
        BytesValue,
    }
)


def is_exact_value(value: object) -> TypeGuard[ExactValue]:
    """Narrow arbitrary input to the closed immutable value model."""
    return type(value) in _EXACT_VALUE_TYPES


def is_exact_value_type(value: type[object]) -> bool:
    """Check a sealed payload's runtime type without revealing its value."""
    return value in _EXACT_VALUE_TYPES


type ResolvedValue = ExactValue | Duration | ByteCount
_RESOLVED_EXTRA_VALUE_TYPES: frozenset[type[object]] = frozenset(
    (cast("type[object]", Duration), cast("type[object]", ByteCount))
)


def is_resolved_value(value: object) -> TypeGuard[ResolvedValue]:
    """Narrow to the explicit result types produced by the conversion vocabulary."""
    return is_exact_value(value) or type(value) in _RESOLVED_EXTRA_VALUE_TYPES


def is_resolved_value_type(value: type[object]) -> bool:
    """Check a sealed resolved payload type without revealing its contents."""
    return is_exact_value_type(value) or value in _RESOLVED_EXTRA_VALUE_TYPES


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


class UnavailableReason(DomainStrEnum):
    """Expected absence categories on which an explicit fallback may advance."""

    NOT_DEFINED = "not-defined"
    NOT_FOUND = "not-found"
    NOT_PROVIDED = "not-provided"


class ResolutionErrorCode(DomainStrEnum):
    """Stable failure categories that stop value resolution."""

    INVALID_SOURCE = "invalid-source"
    CONVERSION_FAILED = "conversion-failed"
    PROVIDER_FAILED = "provider-failed"
    CYCLE = "cycle"
    BUDGET_EXCEEDED = "budget-exceeded"
    CANCELLED = "cancelled"


class ResolutionBlockCode(DomainStrEnum):
    """Stable policy-denial categories that stop value resolution."""

    CAPABILITY_DENIED = "capability-denied"
    SOURCE_DENIED = "source-denied"
    PATH_DENIED = "path-denied"


@final
class Present[ValueT: ResolvedValue](msgspec.Struct, frozen=True, kw_only=True):
    """A resolved value, including explicit null, false, zero, or empty values."""

    value: ValueT | SensitiveValue[ValueT]
    provenance: ResolutionProvenance

    def __post_init__(self) -> None:
        """Require one closed immutable value and typed non-secret provenance."""
        if is_sensitive_value(self.value):
            if not is_resolved_value_type(self.value.payload_type):
                raise TypeError("present sensitive payload must be a closed resolved value")
        elif not is_resolved_value(self.value):
            raise TypeError("present payload must be a closed resolved value")
        _require_resolution_provenance(
            self.provenance,
            outcome="present value",
            terminal=ResolutionStepOutcome.PRESENT,
        )
        terminal_namespace = self.provenance.steps[-1].source.namespace
        if (
            self.provenance.namespace is ValueNamespace.SECRETS
            or terminal_namespace is ValueNamespace.SECRETS
        ) and not is_sensitive_value(self.value):
            raise ValueError("secret-namespace present values must be SensitiveValue")

    @property
    def sensitivity(self) -> Sensitivity:
        """Derive sensitivity from containment so raw secrets cannot be mislabeled public."""
        if is_sensitive_value(self.value):
            return Sensitivity.SENSITIVE
        return Sensitivity.PUBLIC

    @property
    def taint_origins(self) -> tuple[TaintOriginId, ...]:
        """Return sensitivity lineage without revealing the resolved value."""
        if is_sensitive_value(self.value):
            return self.value.origins
        return ()


@final
class Unavailable(msgspec.Struct, frozen=True, kw_only=True):
    """An expected source absence on which an explicit fallback may advance."""

    reason: UnavailableReason
    provenance: ResolutionProvenance

    def __post_init__(self) -> None:
        """Require a typed reason and non-secret provenance."""
        if type(self.reason) is not UnavailableReason:
            msg = "unavailable reason must be an UnavailableReason"
            raise TypeError(msg)
        _require_resolution_provenance(
            self.provenance,
            outcome="unavailable",
            terminal=ResolutionStepOutcome.UNAVAILABLE,
        )


@final
class Missing(msgspec.Struct, frozen=True, kw_only=True):
    """Intentional omission produced only by an optional value expression."""

    provenance: ResolutionProvenance

    def __post_init__(self) -> None:
        """Require non-secret provenance for the intentional omission."""
        _require_resolution_provenance(
            self.provenance,
            outcome="missing",
            terminal=ResolutionStepOutcome.MISSING,
        )


@final
class Error(msgspec.Struct, frozen=True, kw_only=True):
    """A value-free resolution failure that must stop fallback evaluation."""

    code: ResolutionErrorCode
    provenance: ResolutionProvenance

    def __post_init__(self) -> None:
        """Require a typed failure code and non-secret provenance."""
        if type(self.code) is not ResolutionErrorCode:
            msg = "resolution error code must be a ResolutionErrorCode"
            raise TypeError(msg)
        _require_resolution_provenance(
            self.provenance,
            outcome="resolution error",
            terminal=ResolutionStepOutcome.ERROR,
        )


@final
class Blocked(msgspec.Struct, frozen=True, kw_only=True):
    """A value-free policy denial that must stop fallback evaluation."""

    code: ResolutionBlockCode
    provenance: ResolutionProvenance

    def __post_init__(self) -> None:
        """Require a typed policy code and non-secret provenance."""
        if type(self.code) is not ResolutionBlockCode:
            msg = "resolution block code must be a ResolutionBlockCode"
            raise TypeError(msg)
        _require_resolution_provenance(
            self.provenance,
            outcome="resolution block",
            terminal=ResolutionStepOutcome.BLOCKED,
        )


type ResolutionResult[ValueT] = Present[ValueT] | Unavailable | Missing | Error | Blocked
