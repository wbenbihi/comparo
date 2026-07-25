"""Terminal-control and bidi containment for user-authored presentation text."""

from __future__ import annotations

import pytest

from comparo.tui.presentation.text import safe_terminal_text


def test_terminal_text_neutralizes_controls_bidi_and_single_line_breaks() -> None:
    value = "hello\x1b[31m\u202eworld\nnext\tcell\ud800"
    sanitized = safe_terminal_text(value)
    assert sanitized == "hello�[31m�world next cell�"
    assert "\x1b" not in sanitized
    assert "\u202e" not in sanitized


def test_terminal_text_preserves_multiline_shape_and_bounds_output() -> None:
    assert safe_terminal_text("a\nb\tc", multiline=True) == "a\nb    c"
    assert safe_terminal_text("abcdef", maximum_characters=4) == "abc…"
    assert safe_terminal_text("abcdef", maximum_characters=1) == "…"
    assert safe_terminal_text("abc", maximum_characters=3) == "abc"


def test_terminal_text_rejects_invalid_contract_values() -> None:
    with pytest.raises(TypeError, match="must be text"):
        safe_terminal_text(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        safe_terminal_text("x", maximum_characters=0)
    with pytest.raises(TypeError, match="multiline"):
        safe_terminal_text("x", multiline=1)  # type: ignore[arg-type]
