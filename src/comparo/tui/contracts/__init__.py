"""Typed frontend inputs, intents, and injected service ports."""

from comparo.tui.contracts.launch import DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS_V1
from comparo.tui.contracts.launch import (
    DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1 as DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1,
)
from comparo.tui.contracts.launch import TuiLaunchLimits as TuiLaunchLimits
from comparo.tui.contracts.launch import (
    tui_artifact_read_limits_defaults_v1 as tui_artifact_read_limits_defaults_v1,
)
from comparo.tui.contracts.launch import (
    tui_launch_limits_defaults_v1 as tui_launch_limits_defaults_v1,
)
from comparo.tui.contracts.models import CompareDraft as CompareDraft
from comparo.tui.contracts.models import CompareDraftValidationCode as CompareDraftValidationCode
from comparo.tui.contracts.models import CompareMode as CompareMode
from comparo.tui.contracts.models import ResultAccess as ResultAccess
from comparo.tui.contracts.models import TuiExecutionControls as TuiExecutionControls
from comparo.tui.contracts.models import TuiLaunchRequest as TuiLaunchRequest
from comparo.tui.contracts.services import TuiBackend as TuiBackend
from comparo.tui.contracts.services import TuiBackendError as TuiBackendError
from comparo.tui.contracts.services import TuiBackendErrorCode as TuiBackendErrorCode

__all__ = (
    "DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS_V1",
    "DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1",
    "CompareDraft",
    "CompareDraftValidationCode",
    "CompareMode",
    "ResultAccess",
    "TuiBackend",
    "TuiBackendError",
    "TuiBackendErrorCode",
    "TuiExecutionControls",
    "TuiLaunchLimits",
    "TuiLaunchRequest",
    "tui_artifact_read_limits_defaults_v1",
    "tui_launch_limits_defaults_v1",
)
