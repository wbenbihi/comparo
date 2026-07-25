"""The four persistent thin Comparo workspace compositions."""

from comparo.tui.workspaces.compare import CompareWorkspace as CompareWorkspace
from comparo.tui.workspaces.compare import CompareWorkspaceState as CompareWorkspaceState
from comparo.tui.workspaces.explorer import ExplorerWorkspace as ExplorerWorkspace
from comparo.tui.workspaces.report import ReportWorkspace as ReportWorkspace
from comparo.tui.workspaces.report import ReportWorkspaceState as ReportWorkspaceState
from comparo.tui.workspaces.settings import SettingsWorkspace as SettingsWorkspace

__all__ = (
    "CompareWorkspace",
    "CompareWorkspaceState",
    "ExplorerWorkspace",
    "ReportWorkspace",
    "ReportWorkspaceState",
    "SettingsWorkspace",
)
