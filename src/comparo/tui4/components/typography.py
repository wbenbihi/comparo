from typing import Any

from textual.widgets import Static


class SectionTitle(Static):
    """Uppercase section heading."""

    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(text.upper(), **kwargs)


class Caption(Static):
    """Secondary explanatory line."""


class Hint(Static):
    """Tertiary/dimmed text."""
