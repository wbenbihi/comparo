"""Safe process bootstrap for Comparo's Textual application."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from comparo.tui.app import ComparoApp
from comparo.tui.backend import ProductionTuiBackend
from comparo.tui.backend import default_tui_execution_controls
from comparo.tui.contracts import TuiLaunchRequest
from comparo.tui.state import DEFAULT_TUI_PREFERENCES
from comparo.tui.state import TuiPreferences


class _RunnableTui(Protocol):
    """Small launch-time surface implemented by the Textual application."""

    def run(self, *, mouse: bool) -> object:
        """Run until the user or application exits."""
        ...


type TuiAppFactory = Callable[[TuiLaunchRequest, TuiPreferences], _RunnableTui]


def build_tui_app(
    request: TuiLaunchRequest,
    preferences: TuiPreferences = DEFAULT_TUI_PREFERENCES,
) -> ComparoApp:
    """Compose the production backend and presentation shell without global state."""
    if type(request) is not TuiLaunchRequest or type(preferences) is not TuiPreferences:
        raise TypeError("TUI launch requires typed request and preferences")
    return ComparoApp(
        ProductionTuiBackend(request),
        default_tui_execution_controls(request.limits.execution_settings),
        preferences=preferences,
        interaction_limits=request.limits.interaction,
    )


def run_tui(
    request: TuiLaunchRequest,
    *,
    preferences: TuiPreferences = DEFAULT_TUI_PREFERENCES,
    app_factory: TuiAppFactory = build_tui_app,
) -> int:
    """Run the terminal app and collapse bootstrap failures to stable process codes."""
    try:
        app = app_factory(request, preferences)
        result: object = app.run(mouse=preferences.mouse)
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001 - process boundary never renders exception text
        return 4
    if result is None:
        return 0
    if type(result) is not int or result < 0 or result > 255:
        return 4
    return result


__all__ = ("TuiAppFactory", "build_tui_app", "run_tui")
