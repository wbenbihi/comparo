"""Application-level presentation state."""

from dataclasses import dataclass

from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.tui4.types import ComparoScreen


@dataclass(frozen=True, slots=True)
class AppState:
    """State shared by the application header and workspace switcher."""

    active_screen: ComparoScreen = ComparoScreen.EXPLORE
    compare_subject: ResourceCoordinate | None = None


def default_app_state() -> AppState:
    """Create the initial application state."""
    return AppState()
