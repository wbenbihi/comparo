"""Shared Textual components used across all four Comparo workspaces."""

from comparo.tui.components.base import COMPONENT_STYLESHEET as COMPONENT_STYLESHEET
from comparo.tui.components.base import ResponsiveWidget as ResponsiveWidget
from comparo.tui.components.base import StatePanel as StatePanel
from comparo.tui.components.base import StatePanelMode as StatePanelMode
from comparo.tui.components.compare_input import FailFastChoice as FailFastChoice
from comparo.tui.components.compare_options import CompareAdvancedOptions as CompareAdvancedOptions
from comparo.tui.components.execution import CompareDraftForm as CompareDraftForm
from comparo.tui.components.execution import CompareModeSelector as CompareModeSelector
from comparo.tui.components.execution import ExecutionProgress as ExecutionProgress
from comparo.tui.components.execution import PlanPreview as PlanPreview
from comparo.tui.components.project import ProjectResourceDetail as ProjectResourceDetail
from comparo.tui.components.project import ProjectResourceItem as ProjectResourceItem
from comparo.tui.components.project import ProjectResourceList as ProjectResourceList
from comparo.tui.components.reports import ReportList as ReportList
from comparo.tui.components.reports import ReportListItem as ReportListItem
from comparo.tui.components.results import ResultDetail as ResultDetail
from comparo.tui.components.results import ResultIndex as ResultIndex
from comparo.tui.components.results import ResultIndexKind as ResultIndexKind
from comparo.tui.components.results import ResultIndexSelector as ResultIndexSelector
from comparo.tui.components.results import ResultSummary as ResultSummary
from comparo.tui.components.results import ResultsWorkspaceView as ResultsWorkspaceView
from comparo.tui.components.settings import TuiOnlySettingsPanel as TuiOnlySettingsPanel
from comparo.tui.components.status import DiagnosticsList as DiagnosticsList
from comparo.tui.components.status import StatusBadge as StatusBadge
from comparo.tui.components.status import status_for_outcome as status_for_outcome

__all__ = (
    "COMPONENT_STYLESHEET",
    "CompareAdvancedOptions",
    "CompareDraftForm",
    "CompareModeSelector",
    "DiagnosticsList",
    "ExecutionProgress",
    "FailFastChoice",
    "PlanPreview",
    "ProjectResourceDetail",
    "ProjectResourceItem",
    "ProjectResourceList",
    "ReportList",
    "ReportListItem",
    "ResponsiveWidget",
    "ResultDetail",
    "ResultIndex",
    "ResultIndexKind",
    "ResultIndexSelector",
    "ResultSummary",
    "ResultsWorkspaceView",
    "StatePanel",
    "StatePanelMode",
    "StatusBadge",
    "TuiOnlySettingsPanel",
    "status_for_outcome",
)
