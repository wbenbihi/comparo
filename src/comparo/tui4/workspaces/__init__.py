"""Top-level workspace registry."""

from comparo.tui4.types import ComparoScreen

from .base import Workspace
from .debug import DebugWorkspace
from .explorer import ExplorerWorkspace
from .launch import LaunchWorkspace
from .reports import ReportsWorkspace

WORKSPACES: dict[ComparoScreen, type[Workspace]] = {
    ComparoScreen.EXPLORE: ExplorerWorkspace,
    ComparoScreen.LAUNCH: LaunchWorkspace,
    ComparoScreen.REPORTS: ReportsWorkspace,
    ComparoScreen.DEBUG: DebugWorkspace,
}

__all__ = ["WORKSPACES"]
