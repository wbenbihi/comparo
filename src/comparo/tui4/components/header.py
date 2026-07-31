from textual.containers import Horizontal

from .logo import ComparoLogo
from .navigation import NavBar
from .navigation import NavHelper


class AppHeader(Horizontal):
    """Comparo Application Header."""

    def __init__(self) -> None:
        """Init the Header."""
        super().__init__()

    def compose(self):
        """Render."""
        yield ComparoLogo()
        yield NavBar()
        yield NavHelper()
