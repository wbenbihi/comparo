"""Offline security diagnostics independent from every presentation frontend."""

from __future__ import annotations

from collections.abc import Callable
from typing import final

import msgspec

from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId

_DOCTOR_ORIGIN = TaintOriginId(value="application/doctor")


@final
class SecurityCheck(msgspec.Struct, frozen=True, kw_only=True):
    """One stable offline security invariant and its observed state."""

    name: str
    passed: bool


def run_security_checks() -> tuple[SecurityCheck, ...]:
    """Exercise live-value containment without publishing the canary."""
    canary = "comparo-doctor-sensitive-canary"
    sensitive = SensitiveValue(canary, origins=(_DOCTOR_ORIGIN,))
    rendered = (str(sensitive), repr(sensitive), f"{sensitive}")
    encoders: tuple[Callable[[object], bytes], ...] = (
        msgspec.json.encode,
        msgspec.msgpack.encode,
    )
    return (
        SecurityCheck(
            name="sensitive-rendering",
            passed=all(canary not in value for value in rendered),
        ),
        SecurityCheck(
            name="sensitive-serialization",
            passed=all(_serialization_is_rejected(encode, sensitive) for encode in encoders),
        ),
    )


def _serialization_is_rejected(
    encode: Callable[[object], bytes],
    sensitive: SensitiveValue[str],
) -> bool:
    try:
        encode(sensitive)
    except TypeError:
        return True
    return False


__all__ = ("SecurityCheck", "run_security_checks")
