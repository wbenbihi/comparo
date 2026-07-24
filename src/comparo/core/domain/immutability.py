"""Cycle-free runtime contract for field-owned immutable domain payloads."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import msgspec

from comparo.core.domain.enums import DomainStrEnum

type ImmutableDomainValue = (
    None
    | str
    | bytes
    | bool
    | int
    | Decimal
    | UUID
    | DomainStrEnum
    | msgspec.Struct
    | tuple[ImmutableDomainValue, ...]
    | frozenset[ImmutableDomainValue]
)


def is_deeply_immutable_domain_value(value: object) -> bool:
    """Validate a closed domain-owned immutable graph without trusting static types."""
    if value is None or type(value) in {str, bytes, bool, int, UUID}:
        return True
    if type(value) is Decimal:
        return value.is_finite()
    if isinstance(value, DomainStrEnum):
        return type(value).__module__.startswith("comparo.core.domain")
    if type(value) is tuple or type(value) is frozenset:
        return all(is_deeply_immutable_domain_value(item) for item in value)
    if isinstance(value, msgspec.Struct):
        value_type = type(value)
        config = value_type.__struct_config__
        if (
            not value_type.__module__.startswith("comparo.core.domain")
            or not getattr(value_type, "__final__", False)
            or config.dict
            or not config.frozen
        ):
            return False
        return all(
            is_deeply_immutable_domain_value(getattr(value, field.name))
            for field in msgspec.structs.fields(value)
        )
    return False
