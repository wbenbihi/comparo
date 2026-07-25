"""Tests for frontend-independent offline security diagnostics."""

from __future__ import annotations

from collections.abc import Callable

import msgspec

from comparo.application import diagnostics
from comparo.application.diagnostics import run_security_checks
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId


def test_security_checks_pass_without_publishing_the_canary() -> None:
    checks = run_security_checks()

    assert tuple(item.name for item in checks) == (
        "sensitive-rendering",
        "sensitive-serialization",
    )
    assert all(item.passed for item in checks)
    assert b"comparo-doctor-sensitive-canary" not in msgspec.json.encode(checks)


def test_serialization_probe_distinguishes_acceptance_from_rejection() -> None:
    def accepts(value: object) -> bytes:
        del value
        return b"accepted"

    def rejects(value: object) -> bytes:
        del value
        raise TypeError

    typed_accepts: Callable[[object], bytes] = accepts
    typed_rejects: Callable[[object], bytes] = rejects
    assert not diagnostics._serialization_is_rejected(typed_accepts, _sensitive())
    assert diagnostics._serialization_is_rejected(typed_rejects, _sensitive())


def _sensitive() -> SensitiveValue[str]:
    return SensitiveValue("value", origins=(TaintOriginId(value="test/doctor"),))
