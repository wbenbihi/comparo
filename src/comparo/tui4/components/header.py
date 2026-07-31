"""Shared application header composed from logo and navigation widgets."""

from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal

from .logo import ComparoLogo
from .navigation import NavBar
from .navigation import NavHelper


class AppHeader(Horizontal):
    """Display the product identity, workspace navigation, and context."""

    @override
    def compose(self) -> ComposeResult:
        """Compose the three stable header regions."""
        yield ComparoLogo()
        yield NavBar()
        yield NavHelper()
