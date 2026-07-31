from textual.binding import Binding
from textual.binding import BindingType

from .states import AppState
from .types import ComparoScreen


def get_state() -> AppState:
    """Get the default Appstate."""
    return AppState()


def get_screen_bindings() -> list[BindingType]:
    """Get the list of Screen Bindings."""
    return [
        Binding(f"{idx}", f'workspace("{val.value}")', val.value, show=False)
        for idx, val in enumerate(ComparoScreen, start=1)
    ]
