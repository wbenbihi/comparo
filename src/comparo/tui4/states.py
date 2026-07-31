from dataclasses import dataclass

from comparo.tui4.types import ComparoScreen


@dataclass
class AppState:
    """Main Application State."""

    active_screen: ComparoScreen = ComparoScreen.EXPLORE
    env: str | None = None
