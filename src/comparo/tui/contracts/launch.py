"""Caller-owned operational limits for one terminal-interface process."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.application import DEFAULT_POLICY_LOAD_LIMITS
from comparo.application import DEFAULT_PROJECT_LOAD_LIMITS
from comparo.application import ArtifactReadLimits
from comparo.application import PolicyLoadLimits
from comparo.application import ProductionExecutionSettings
from comparo.application import ProjectLoadLimits
from comparo.application import ReportArchiveLimits
from comparo.application import persisted_production_execution_defaults_v1

DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS_V1 = 8
DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1 = 4096


def tui_artifact_read_limits_defaults_v1() -> ArtifactReadLimits:
    """Return the versioned, caller-overridable TUI artifact read defaults."""
    return ArtifactReadLimits(
        maximum_blobs=4096,
        maximum_blob_bytes=64 * 1024 * 1024,
        maximum_total_blob_bytes=512 * 1024 * 1024,
        maximum_manifest_bytes=64 * 1024 * 1024,
        maximum_report_bytes=64 * 1024 * 1024,
        maximum_redaction_patterns=4096,
        maximum_redaction_pattern_bytes=1024 * 1024,
        maximum_redaction_value_bytes=256 * 1024,
        maximum_redaction_value_nodes=4096,
    )


@final
class TuiLaunchLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Immutable caller choices for every bounded TUI backend operation."""

    project_load: ProjectLoadLimits
    policy_load: PolicyLoadLimits
    execution_settings: ProductionExecutionSettings
    report_archive: ReportArchiveLimits
    maximum_outstanding_preparations: int

    def __post_init__(self) -> None:
        """Reject untyped or unbounded launch inputs before acquiring capabilities."""
        if type(self.project_load) is not ProjectLoadLimits:
            raise TypeError("TUI project load limits must be ProjectLoadLimits")
        if type(self.policy_load) is not PolicyLoadLimits:
            raise TypeError("TUI policy load limits must be PolicyLoadLimits")
        if type(self.execution_settings) is not ProductionExecutionSettings:
            raise TypeError("TUI execution settings must be ProductionExecutionSettings")
        if type(self.report_archive) is not ReportArchiveLimits:
            raise TypeError("TUI report limits must be ReportArchiveLimits")
        if (
            type(self.maximum_outstanding_preparations) is not int
            or self.maximum_outstanding_preparations < 1
        ):
            raise ValueError("TUI outstanding preparation limit must be a positive integer")


def tui_launch_limits_defaults_v1() -> TuiLaunchLimits:
    """Return fresh versioned defaults that callers may replace without hidden maxima."""
    return TuiLaunchLimits(
        project_load=DEFAULT_PROJECT_LOAD_LIMITS,
        policy_load=DEFAULT_POLICY_LOAD_LIMITS,
        execution_settings=persisted_production_execution_defaults_v1(),
        report_archive=ReportArchiveLimits(
            maximum_entries=DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1,
            artifact=tui_artifact_read_limits_defaults_v1(),
        ),
        maximum_outstanding_preparations=(DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS_V1),
    )


__all__ = (
    "DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS_V1",
    "DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1",
    "TuiLaunchLimits",
    "tui_artifact_read_limits_defaults_v1",
    "tui_launch_limits_defaults_v1",
)
