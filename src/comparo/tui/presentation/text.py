"""Central containment of untrusted text before terminal presentation."""

from __future__ import annotations

import unicodedata

_DIRECTIONAL_CONTROLS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)


def safe_terminal_text(
    value: str,
    *,
    maximum_characters: int = 4096,
    multiline: bool = False,
) -> str:
    """Bound text and neutralize terminal, control, and bidirectional directives."""
    if type(value) is not str:
        raise TypeError("terminal text must be text")
    if type(maximum_characters) is not int or maximum_characters < 1:
        raise ValueError("terminal text limit must be a positive exact integer")
    if type(multiline) is not bool:
        raise TypeError("terminal multiline selection must be bool")
    sanitized = "".join(_safe_character(character, multiline=multiline) for character in value)
    if len(sanitized) <= maximum_characters:
        return sanitized
    if maximum_characters == 1:
        return "…"
    return f"{sanitized[: maximum_characters - 1]}…"


def _safe_character(character: str, *, multiline: bool) -> str:
    if character == "\n":
        return character if multiline else " "
    if character == "\t":
        return "    " if multiline else " "
    if character in _DIRECTIONAL_CONTROLS:
        return "�"
    category = unicodedata.category(character)
    if category in {"Cc", "Cs"}:
        return "�"
    return character


__all__ = ("safe_terminal_text",)
