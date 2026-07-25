"""Framework-neutral state owned by the terminal frontend."""

from comparo.tui.state.models import DEFAULT_TUI_PREFERENCES as DEFAULT_TUI_PREFERENCES
from comparo.tui.state.models import ComparePhase as ComparePhase
from comparo.tui.state.models import CompareViewState as CompareViewState
from comparo.tui.state.models import ContentPhase as ContentPhase
from comparo.tui.state.models import ExplorerViewState as ExplorerViewState
from comparo.tui.state.models import ReportViewState as ReportViewState
from comparo.tui.state.models import ThemePreference as ThemePreference
from comparo.tui.state.models import TuiPreferences as TuiPreferences

__all__ = (
    "DEFAULT_TUI_PREFERENCES",
    "ComparePhase",
    "CompareViewState",
    "ContentPhase",
    "ExplorerViewState",
    "ReportViewState",
    "ThemePreference",
    "TuiPreferences",
)
