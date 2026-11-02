"""Property checks for selector canonicalization and parser totality."""

from __future__ import annotations

import json
from typing import Literal

from hypothesis import given
from hypothesis import settings
from hypothesis import strategies as st

from comparo.core.selection import SelectorSyntaxError
from comparo.core.selection import compile_json_pointer
from comparo.core.selection import compile_jsonpath

_SURROGATE_CATEGORY: tuple[Literal["Cs"], ...] = ("Cs",)
_UNICODE_SCALARS = st.characters(blacklist_categories=_SURROGATE_CATEGORY)


@given(st.lists(st.text(_UNICODE_SCALARS, max_size=30), max_size=20))
def test_encoded_pointer_tokens_compile_without_loss(tokens: list[str]) -> None:
    pointer = "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)

    assert compile_json_pointer(pointer).pointer.value == pointer


@given(st.text(_UNICODE_SCALARS, max_size=100))
def test_arbitrary_member_names_have_idempotent_canonical_identity(name: str) -> None:
    authored = "$[" + json.dumps(name, ensure_ascii=False) + "]"
    first = compile_jsonpath(authored)
    second = compile_jsonpath(first.canonical)

    assert first.ast == second.ast
    assert first.canonical == second.canonical
    assert first.fingerprint == second.fingerprint


@given(st.text(_UNICODE_SCALARS, max_size=500))
@settings(max_examples=300)
def test_parser_is_total_for_bounded_unicode_text(authored: str) -> None:
    failure: SelectorSyntaxError | None = None
    compiled = None
    try:
        compiled = compile_jsonpath(authored)
    except SelectorSyntaxError as captured:
        failure = captured
    if failure is not None:
        assert failure.offset <= len(authored)
        assert failure.args == (failure.code.value, failure.offset)
    else:
        assert compiled is not None
        assert compiled.canonical.startswith("$")
