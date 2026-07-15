"""Tests for the ${...} interpolation grammar."""

import pytest

from comparo.core.interpolation import Context
from comparo.core.interpolation import InterpolationError
from comparo.core.interpolation import interpolate
from comparo.core.provenance import Origin

CTX = Context(variables={"LOCALE": "en-US", "PORT": "8080"}, secret_names=frozenset({"TOKEN"}))


def test_plain_literal() -> None:
    result = interpolate("hello", CTX)
    assert result.value == "hello"
    assert result.origin is Origin.LITERAL


def test_variable() -> None:
    result = interpolate("${LOCALE}", CTX)
    assert result.value == "en-US"
    assert result.origin is Origin.VARIABLE


def test_secret_is_masked_and_tainted() -> None:
    result = interpolate("${TOKEN}", CTX)
    assert result.value == CTX.mask
    assert result.origin is Origin.SECRET


def test_secret_priority_over_variable() -> None:
    ctx = Context(variables={"X": "plain"}, secret_names=frozenset({"X"}))
    result = interpolate("${X}", ctx)
    assert result.value == ctx.mask
    assert result.origin is Origin.SECRET


def test_default_used_when_unset() -> None:
    assert interpolate("${MISSING | fallback}", CTX).value == "fallback"


def test_optional_unset_is_none() -> None:
    assert interpolate("${MISSING?}", CTX).value is None


def test_required_unset_raises() -> None:
    with pytest.raises(InterpolationError):
        interpolate("${MISSING}", CTX)


def test_int_cast() -> None:
    assert interpolate("${PORT:int}", CTX).value == 8080


def test_substring_with_secret_taints_whole() -> None:
    result = interpolate("Bearer ${TOKEN}", CTX)
    assert result.value == f"Bearer {CTX.mask}"
    assert result.origin is Origin.SECRET
