"""Tests for sensitive live-value containment."""

from __future__ import annotations

import pickle
from collections.abc import Callable
from copy import copy
from copy import deepcopy

import msgspec
import pytest

from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId


def _origin(value: str = "secret/project/api-token") -> TaintOriginId:
    return TaintOriginId(value=value)


def test_taint_origin_requires_an_exact_non_empty_string() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        TaintOriginId(value="")

    with pytest.raises(ValueError, match="non-empty string"):
        TaintOriginId(value=1)  # type: ignore[arg-type]


def test_sensitive_value_requires_unique_typed_origins() -> None:
    origin = _origin()

    with pytest.raises(ValueError, match="at least one"):
        SensitiveValue("secret", origins=())

    with pytest.raises(TypeError, match="TaintOriginId"):
        SensitiveValue("secret", origins=("source",))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unique"):
        SensitiveValue("secret", origins=(origin, origin))


def test_sensitive_value_rejects_mutable_payloads_without_echoing_them() -> None:
    secret = bytearray(b"do-not-leak")

    with pytest.raises(TypeError, match="deeply immutable") as caught:
        SensitiveValue(secret, origins=(_origin(),))

    assert "do-not-leak" not in str(caught.value)


def test_sensitive_value_is_redacted_in_all_string_rendering() -> None:
    sensitive = SensitiveValue("do-not-leak", origins=(_origin(),))

    assert str(sensitive) == "<sensitive>"
    assert repr(sensitive) == "<sensitive>"
    assert f"{sensitive}" == "<sensitive>"
    assert f"{sensitive:>40}" == "<sensitive>"
    assert "do-not-leak" not in str(sensitive)


def test_reveal_requires_an_explicit_access_capability() -> None:
    sensitive = SensitiveValue("do-not-leak", origins=(_origin(),))
    access = SensitiveValueAccess(purpose="construct an HTTP request")

    assert sensitive.reveal(access=access) == "do-not-leak"
    assert access.purpose == "construct an HTTP request"
    assert sensitive.sensitivity is Sensitivity.SENSITIVE
    assert sensitive.origins == (_origin(),)

    with pytest.raises(TypeError, match="requires SensitiveValueAccess"):
        sensitive.reveal(access=object())  # type: ignore[arg-type]


def test_sensitive_values_and_access_capabilities_are_immutable() -> None:
    sensitive = SensitiveValue("token", origins=(_origin(),))
    access = SensitiveValueAccess(purpose="construct authorization header")

    with pytest.raises(AttributeError, match="immutable"):
        sensitive.__setattr__("_origins", ())
    with pytest.raises(AttributeError, match="immutable"):
        sensitive.__delattr__("_origins")
    with pytest.raises(AttributeError, match="immutable"):
        access.__setattr__("_purpose", "different purpose")
    with pytest.raises(AttributeError, match="immutable"):
        access.__delattr__("_purpose")


def test_sensitive_values_reject_pickle() -> None:
    secret = "do-not-leak"
    sensitive = SensitiveValue(secret, origins=(_origin(),))

    with pytest.raises(TypeError, match="cannot be pickled") as caught:
        pickle.dumps(sensitive)
    assert secret not in str(caught.value)


def test_sensitive_values_reject_shallow_and_deep_copy() -> None:
    secret = "do-not-leak"
    sensitive = SensitiveValue(secret, origins=(_origin(),))

    with pytest.raises(TypeError, match="cannot be copied") as shallow:
        copy(sensitive)
    with pytest.raises(TypeError, match="cannot be copied") as deep:
        deepcopy(sensitive)

    assert secret not in str(shallow.value)
    assert secret not in str(deep.value)


@pytest.mark.parametrize(
    "serialize",
    [msgspec.json.encode, msgspec.msgpack.encode, msgspec.to_builtins],
)
def test_sensitive_values_fail_closed_at_msgspec_boundaries(
    serialize: Callable[[object], object],
) -> None:
    secret = "do-not-leak"
    sensitive = SensitiveValue(secret, origins=(_origin(),))

    with pytest.raises(TypeError, match="unsupported") as caught:
        serialize(("safe", sensitive))
    assert secret not in str(caught.value)
