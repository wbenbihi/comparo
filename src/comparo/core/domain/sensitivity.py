"""Sensitivity lineage and fail-closed live-value containers."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Final
from typing import Never
from typing import final
from typing import override

import msgspec

_REDACTION_MARKER: Final = "<sensitive>"


def _require_access(access: object, *, operation: str) -> None:
    if type(access) is not SensitiveValueAccess:
        msg = f"{operation} a sensitive value requires SensitiveValueAccess"
        raise TypeError(msg)


def _is_deeply_immutable(value: object) -> bool:
    if value is None or type(value) in {str, bytes, bool, int}:
        return True
    if type(value) is Decimal:
        return value.is_finite()
    if isinstance(value, StrEnum):
        return False
    if type(value) is tuple:
        return all(_is_deeply_immutable(item) for item in value)
    if isinstance(value, msgspec.Struct):
        config = type(value).__struct_config__
        if config.dict or not config.frozen:
            return False
        return all(
            _is_deeply_immutable(getattr(value, field.name))
            for field in msgspec.structs.fields(value)
        )
    return False


class Sensitivity(StrEnum):
    """Whether a resolved value may cross a serialization boundary."""

    PUBLIC = "public"
    SENSITIVE = "sensitive"


@final
class TaintOriginId(msgspec.Struct, frozen=True, kw_only=True):
    """Stable, non-secret identity of one sensitivity origin."""

    value: str

    def __post_init__(self) -> None:
        """Reject empty or incorrectly typed origin identifiers."""
        if type(self.value) is not str or not self.value:
            msg = "taint origin ID must be a non-empty string"
            raise ValueError(msg)


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
        if type(purpose) is not str or not purpose:
            msg = "sensitive-value access purpose must be a non-empty string"
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
        if not _is_deeply_immutable(value):
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
