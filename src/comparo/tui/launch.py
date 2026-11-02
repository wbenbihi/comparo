"""Safe process bootstrap for the main Comparo terminal interface."""

from collections.abc import Callable
from typing import Protocol

from comparo.tui.app import ComparoApp
from comparo.tui.contracts import TuiLaunchRequest


class _RunnableTui(Protocol):
    """Launch-time surface implemented by the terminal application."""

    def run(self) -> object:
        """Run until the user or application exits."""
        ...


type TuiAppFactory = Callable[[TuiLaunchRequest], _RunnableTui]


def build_tui_app(request: TuiLaunchRequest) -> ComparoApp:
    """Compose the main terminal app without loading a project or executing HTTP."""
    if type(request) is not TuiLaunchRequest:
        raise TypeError("TUI launch requires a typed request")
    return ComparoApp(request)


def run_tui(request: TuiLaunchRequest, *, app_factory: TuiAppFactory = build_tui_app) -> int:
    """Contain bootstrap failures and return only stable process exit codes."""
    try:
        app = app_factory(request)
        result = app.run(mouse=app.preferences.mouse) if isinstance(app, ComparoApp) else app.run()
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001 - never expose bootstrap exception text
        return 4
    if result is None:
        return 0
    if type(result) is not int or not 0 <= result <= 255:
        return 4
    return result
