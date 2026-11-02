"""Deterministic fact identities for pure evaluator calls."""

from __future__ import annotations

import re
from uuid import uuid5

from comparo.core.domain.identity import FactId
from comparo.core.domain.identity import RuleApplicationId

_COMPONENT_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


def derive_fact_id(
    application_id: RuleApplicationId,
    *,
    engine: str,
    role: str,
    ordinal: int = 0,
) -> FactId:
    """Derive a stable UUID from an application and structural fact coordinate.

    The application UUID is used as the UUIDv5 namespace. No payload value, secret,
    selector rendering, or process-global state participates in the identity.
    """
    if type(application_id) is not RuleApplicationId:
        raise TypeError("fact identity derivation requires a RuleApplicationId")
    for label, value in (("engine", engine), ("role", role)):
        if type(value) is not str or _COMPONENT_PATTERN.fullmatch(value) is None:
            raise ValueError(f"fact {label} must be a lowercase structural token")
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("fact ordinal must be a non-negative integer")
    coordinate = f"comparo.core/{engine}/{role}/{ordinal}"
    return FactId(value=uuid5(application_id.value, coordinate))


__all__ = ("derive_fact_id",)
