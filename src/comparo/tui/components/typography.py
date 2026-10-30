"""Small semantic typography primitives."""

from textual.widgets import Static


class SectionTitle(Static):
    """Uppercase section heading."""

    def __init__(
        self,
        text: str,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize an uppercase section heading."""
        super().__init__(text.upper(), id=id, classes=classes)


class Caption(Static):
    """Secondary explanatory line."""


class Hint(Static):
    """Tertiary/dimmed text."""
