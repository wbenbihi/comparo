"""Framework-neutral TUI effect and lifecycle coordination."""

from comparo.tui.controllers.availability import available_commands as available_commands
from comparo.tui.controllers.coordinator import CompareSurface as CompareSurface
from comparo.tui.controllers.coordinator import ReportSurface as ReportSurface
from comparo.tui.controllers.coordinator import TuiCoordinator as TuiCoordinator
from comparo.tui.controllers.coordinator import TuiOperationSlot as TuiOperationSlot

__all__ = (
    "CompareSurface",
    "ReportSurface",
    "TuiCoordinator",
    "TuiOperationSlot",
    "available_commands",
)
