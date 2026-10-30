"""Comparo wordmark widget."""

from textual.widgets import Static


class ComparoLogo(Static):
    """Comparo Logo."""

    def __init__(self) -> None:
        """Initialize the text-only wordmark."""
        super().__init__("comparo")
