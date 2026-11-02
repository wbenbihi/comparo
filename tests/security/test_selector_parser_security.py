"""Adversarial selector parsing remains bounded and value-safe."""

from __future__ import annotations

import pytest

from comparo.core.selection import SelectorParserLimits
from comparo.core.selection import SelectorSyntaxCode
from comparo.core.selection import SelectorSyntaxError
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath


def test_authored_secret_never_appears_in_syntax_failure() -> None:
    secret = "top-secret-token-6c49f31e"
    authored = "$['" + secret + "' invalid]"

    with pytest.raises(SelectorSyntaxError) as captured:
        compile_jsonpath(authored)

    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
    assert captured.value.args == (captured.value.code.value, captured.value.offset)


def test_flat_logical_and_negation_bombs_fail_before_python_recursion() -> None:
    limits = SelectorParserLimits(
        max_codepoints=100_000,
        max_depth=32,
        max_segments=10_000,
        max_selectors=10_000,
        max_expressions=10_000,
        max_integer_digits=256,
    )
    authored_codes = (
        ("$[?" + "!" * 5_000 + "@]", SelectorSyntaxCode.INVALID_EXPRESSION),
        ("$[?" + "@||" * 5_000 + "@]", SelectorSyntaxCode.TOO_DEEP),
    )
    for authored, expected in authored_codes:
        with pytest.raises(SelectorSyntaxError) as captured:
            compile_jsonpath(authored, limits=limits)
        assert captured.value.code is expected


def test_numeric_exponent_bomb_is_rejected_before_integer_materialization() -> None:
    with pytest.raises(SelectorSyntaxError) as captured:
        compile_jsonpath("$[?@ == 1e100000000]")
    assert captured.value.code is SelectorSyntaxCode.TOO_COMPLEX


def test_pointer_scan_is_linear_and_bounded_by_caller_policy() -> None:
    limits = SelectorParserLimits(max_codepoints=1_000)
    authored = "/" + "a" * 10_000

    with pytest.raises(SelectorSyntaxError) as captured:
        compile_json_pointer(authored, limits=limits)
    assert captured.value.code is SelectorSyntaxCode.TOO_LONG
