"""Tests for sensitive live-value containment."""

from __future__ import annotations

import pickle
from collections.abc import Callable
from copy import copy
from copy import deepcopy
from decimal import Decimal
from enum import Enum
from enum import StrEnum
from typing import cast

import msgspec
import pytest

from comparo.core.domain.primitives import Duration
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union


class _ExampleEnum(StrEnum):
    """String Enum rejected because arbitrary Enum instances are not allowlisted."""

    VALUE = "value"


def _mutable_payload() -> list[str]:
    return ["mutable"]


class _MutableEnum(Enum):
    """An Enum whose value would otherwise smuggle a mutable object."""

    VALUE = _mutable_payload()


class _FrozenPayload(msgspec.Struct, frozen=True):
    """Deeply immutable Struct payload."""

    value: str


class _FrozenWithMutablePayload(msgspec.Struct, frozen=True):
    """Shallowly frozen Struct whose nested list must still be rejected."""

    values: list[str]


class _MutablePayload(msgspec.Struct):
    """Mutable Struct payload that must never enter SensitiveValue."""

    value: str


def _origin(value: str = "secret/project/api-token") -> TaintOriginId:
    return TaintOriginId(value=value)


def test_taint_origin_union_is_stable_unique_and_linear_set_backed() -> None:
    first = _origin("secret/project/first")
    second = _origin("secret/project/second")
    assert stable_taint_origin_union((first, second), (second, first)) == (first, second)
    sensitive = SensitiveValue("token", origins=(first,))
    assert is_sensitive_value(sensitive)
    assert not is_sensitive_value("token")
    with pytest.raises(TypeError, match="immutable tuples"):
        stable_taint_origin_union(cast("tuple[TaintOriginId, ...]", [first]))
    with pytest.raises(TypeError, match="exact TaintOriginId"):
        stable_taint_origin_union((cast("TaintOriginId", object()),))


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "Secret/project/token",
        "secret//token",
        "secret token",
        "api-key=do-not-store",
        "secret/token\nforged",
        "/absolute/path",
        "a" * 256,
        1,
    ],
)
def test_taint_origin_requires_a_bounded_structural_path(invalid: object) -> None:
    with pytest.raises(ValueError, match="bounded lowercase structural path"):
        TaintOriginId(value=invalid)  # type: ignore[arg-type]


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
    access = SensitiveValueAccess(purpose="http.request.materialize")

    assert sensitive.reveal(access=access) == "do-not-leak"
    assert access.purpose == "http.request.materialize"
    assert sensitive.sensitivity is Sensitivity.SENSITIVE
    assert sensitive.payload_type is str
    assert sensitive.origins == (_origin(),)

    with pytest.raises(TypeError, match="requires SensitiveValueAccess"):
        sensitive.reveal(access=object())  # type: ignore[arg-type]


def test_access_capability_validates_purpose_and_has_safe_repr() -> None:
    access = SensitiveValueAccess(purpose="http.bytes.authorize")
    assert repr(access) == "SensitiveValueAccess(purpose='http.bytes.authorize')"
    with pytest.raises(ValueError, match="structural token"):
        SensitiveValueAccess(purpose="")
    with pytest.raises(ValueError, match="structural token"):
        SensitiveValueAccess(purpose=1)  # type: ignore[arg-type]
    for unsafe in ("token=secret", "purpose\nforged", "human readable prose", "a" * 128):
        with pytest.raises(ValueError, match="structural token"):
            SensitiveValueAccess(purpose=unsafe)


def test_deep_immutability_accepts_only_closed_immutable_graphs() -> None:
    origin = (_origin(),)
    accepted: tuple[object, ...] = (
        None,
        Decimal("1.25"),
        ("a", 1, Decimal("2")),
        Duration(nanoseconds=1),
    )
    for value in accepted:
        assert SensitiveValue(value, origins=origin).payload_type is type(value)

    rejected: tuple[object, ...] = (
        Decimal("NaN"),
        _ExampleEnum.VALUE,
        _MutableEnum.VALUE,
        ("safe", []),
        _MutablePayload("value"),
        _FrozenWithMutablePayload(["value"]),
        _FrozenPayload("value"),
    )
    for value in rejected:
        with pytest.raises(TypeError, match="deeply immutable"):
            SensitiveValue(value, origins=origin)


def test_sensitive_values_and_access_capabilities_are_immutable() -> None:
    sensitive = SensitiveValue("token", origins=(_origin(),))
    access = SensitiveValueAccess(purpose="http.authorization.header")

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
    with pytest.raises(TypeError, match="cannot be pickled"):
        sensitive.__reduce__()


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
