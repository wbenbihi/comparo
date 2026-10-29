"""Top-level workspace registry."""

from comparo.tui4.types import ComparoScreen

from .base import Workspace
from .debug import DebugWorkspace
from .explorer import ExplorerWorkspace
from .launch import LaunchWorkspace

WORKSPACES: dict[ComparoScreen, type[Workspace]] = {
    ComparoScreen.EXPLORE: ExplorerWorkspace,
    ComparoScreen.LAUNCH: LaunchWorkspace,
    ComparoScreen.DEBUG: DebugWorkspace,
}

__all__ = ["WORKSPACES"]
