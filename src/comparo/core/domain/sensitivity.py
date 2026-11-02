"""Sensitivity lineage and fail-closed live-value containers."""

from __future__ import annotations

import re
from typing import Final
from typing import Never
from typing import TypeGuard
from typing import final
from typing import override

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.immutability import is_deeply_immutable_domain_value

_REDACTION_MARKER: Final = "<sensitive>"
_TAINT_ORIGIN_PATTERN: Final = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
)
_ACCESS_PURPOSE_PATTERN: Final = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


def _require_access(access: object, *, operation: str) -> None:
    if type(access) is not SensitiveValueAccess:
        msg = f"{operation} a sensitive value requires SensitiveValueAccess"
        raise TypeError(msg)


class Sensitivity(DomainStrEnum):
    """Whether a resolved value may cross a serialization boundary."""

    PUBLIC = "public"
    SENSITIVE = "sensitive"


@final
class TaintOriginId(msgspec.Struct, frozen=True, kw_only=True):
    """Stable, non-secret identity of one sensitivity origin."""

    value: str

    def __post_init__(self) -> None:
        """Reject paths, prose, controls, and payload-bearing origin identifiers."""
        if (
            type(self.value) is not str
            or len(self.value) > 255
            or _TAINT_ORIGIN_PATTERN.fullmatch(self.value) is None
        ):
            msg = "taint origin ID must be a bounded lowercase structural path"
            raise ValueError(msg)


def stable_taint_origin_union(
    *groups: tuple[TaintOriginId, ...],
) -> tuple[TaintOriginId, ...]:
    """Merge validated origin groups in first-seen order with linear deduplication."""
    seen: set[TaintOriginId] = set()
    merged: list[TaintOriginId] = []
    for group in groups:
        if type(group) is not tuple:
            raise TypeError("taint origin groups must be immutable tuples")
        for origin in group:
            if type(origin) is not TaintOriginId:
                raise TypeError("taint origin groups require exact TaintOriginId values")
            if origin not in seen:
                seen.add(origin)
                merged.append(origin)
    return tuple(merged)


@final
class SensitiveValueAccess:
    """Explicit capability required to reveal a live sensitive value.

    This capability prevents accidental access; it is not an authorization boundary against
    hostile Python code running in the same process.
    """

    __slots__ = ("_purpose",)
    _purpose: str

    def __init__(self, *, purpose: str) -> None:
        """Create an auditable reveal capability for one internal purpose."""
        if (
            type(purpose) is not str
            or len(purpose) > 127
            or _ACCESS_PURPOSE_PATTERN.fullmatch(purpose) is None
        ):
            msg = "sensitive-value access purpose must be a bounded structural token"
            raise ValueError(msg)
        object.__setattr__(self, "_purpose", purpose)

    @property
    def purpose(self) -> str:
        """Return the non-secret reason for which access was requested."""
        return self._purpose

    @override
    def __repr__(self) -> str:
        """Avoid suggesting that a capability carries secret data."""
        return f"{type(self).__name__}(purpose={self._purpose!r})"

    @override
    def __setattr__(self, name: str, value: object) -> Never:
        """Prevent mutation after the capability has been constructed."""
        del name, value
        msg = "sensitive-value access capabilities are immutable"
        raise AttributeError(msg)

    @override
    def __delattr__(self, name: str) -> Never:
        """Prevent deletion after the capability has been constructed."""
        del name
        msg = "sensitive-value access capabilities are immutable"
        raise AttributeError(msg)


@final
class SensitiveValue[SensitiveT]:
    """Live sensitive data that fails closed at ordinary serialization boundaries."""

    __slots__ = ("__value", "_origins")
    __value: SensitiveT
    _origins: tuple[TaintOriginId, ...]

    def __init__(self, value: SensitiveT, *, origins: tuple[TaintOriginId, ...]) -> None:
        """Wrap a value and its non-secret sensitivity lineage."""
        if type(origins) is not tuple or not origins:
            msg = "a sensitive value requires at least one taint origin"
            raise ValueError(msg)
        if any(type(origin) is not TaintOriginId for origin in origins):
            msg = "sensitive value origins must be TaintOriginId instances"
            raise TypeError(msg)
        if len(set(origins)) != len(origins):
            msg = "sensitive value origins must be unique"
            raise ValueError(msg)
        if not is_deeply_immutable_domain_value(value):
            msg = "sensitive value payload must be deeply immutable"
            raise TypeError(msg)
        object.__setattr__(self, "_SensitiveValue__value", value)
        object.__setattr__(self, "_origins", origins)

    @property
    def origins(self) -> tuple[TaintOriginId, ...]:
        """Return safe lineage identifiers without exposing the wrapped value."""
        return self._origins

    @property
    def sensitivity(self) -> Sensitivity:
        """Identify the wrapper as sensitive without examining its contents."""
        return Sensitivity.SENSITIVE

    @property
    def payload_type(self) -> type[SensitiveT]:
        """Expose only the runtime payload type so receiving fields can enforce their contract."""
        return type(self.__value)

    def reveal(self, *, access: SensitiveValueAccess) -> SensitiveT:
        """Reveal the live value to code holding an explicit access capability."""
        _require_access(access, operation="revealing")
        return self.__value

    @override
    def __str__(self) -> str:
        """Render a fixed marker instead of the wrapped value."""
        return _REDACTION_MARKER

    @override
    def __repr__(self) -> str:
        """Render a fixed marker instead of the wrapped value."""
        return _REDACTION_MARKER

    @override
    def __format__(self, format_spec: str) -> str:
        """Render a fixed marker for every format specification."""
        del format_spec
        return _REDACTION_MARKER

    @override
    def __setattr__(self, name: str, value: object) -> Never:
        """Prevent mutation of the wrapped value or its lineage."""
        del name, value
        msg = "sensitive values are immutable"
        raise AttributeError(msg)

    @override
    def __delattr__(self, name: str) -> Never:
        """Prevent deletion of the wrapped value or its lineage."""
        del name
        msg = "sensitive values are immutable"
        raise AttributeError(msg)

    def __copy__(self) -> Never:
        """Reject copying paths that may fall back to unsafe persistence hooks."""
        msg = "sensitive values cannot be copied"
        raise TypeError(msg)

    def __deepcopy__(self, memo: object) -> Never:
        """Reject recursive copying paths that may expose the wrapped value."""
        del memo
        msg = "sensitive values cannot be copied"
        raise TypeError(msg)

    @override
    def __reduce_ex__(self, protocol: object) -> Never:
        """Reject pickle and copy protocols that could persist the wrapped value."""
        del protocol
        msg = "sensitive values cannot be pickled"
        raise TypeError(msg)

    @override
    def __reduce__(self) -> Never:
        """Reject legacy pickle protocols that could persist the wrapped value."""
        msg = "sensitive values cannot be pickled"
        raise TypeError(msg)


def is_sensitive_value(value: object) -> TypeGuard[SensitiveValue[object]]:
    """Accept only the sealed final implementation, never a behavior-changing subclass."""
    return type(value) is SensitiveValue
