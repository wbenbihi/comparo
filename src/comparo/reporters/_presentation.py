"""Context-specific presentation escaping applied after artifact sanitization."""

from __future__ import annotations

import html
import unicodedata


def safe_plain(value: str) -> str:
    """Replace terminal controls, bidi controls, and invalid XML characters visibly."""
    if type(value) is not str:
        raise TypeError("report presentation values must be text")
    output: list[str] = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        allowed_whitespace = character in {"\t", "\n", "\r"}
        xml_allowed = (
            allowed_whitespace
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        )
        if not xml_allowed or (category in {"Cc", "Cf"} and not allowed_whitespace):
            output.append("�")
        else:
            output.append(character)
    return "".join(output)


def markdown_cell(value: object) -> str:
    """Escape one value for a fixed Markdown table cell without enabling HTML."""
    text = safe_plain(str(value)).replace("\r", " ").replace("\n", " ")
    text = html.escape(text, quote=False)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")
