"""Top-level workspace registry."""

from comparo.tui.types import ComparoScreen

from .base import Workspace
from .debug import DebugWorkspace
from .explorer import ExplorerWorkspace
from .launch import LaunchWorkspace
from .reports import ReportsWorkspace
from .settings import SettingsWorkspace

WORKSPACES: dict[ComparoScreen, type[Workspace]] = {
    ComparoScreen.EXPLORE: ExplorerWorkspace,
    ComparoScreen.LAUNCH: LaunchWorkspace,
    ComparoScreen.REPORTS: ReportsWorkspace,
    ComparoScreen.DEBUG: DebugWorkspace,
    ComparoScreen.SETTINGS: SettingsWorkspace,
}

__all__ = ["WORKSPACES"]
