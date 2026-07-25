"""Immutable TUI launch and comparison-draft contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.application.planning import PlanningOptions
from comparo.application.production_execution import ProductionExecutionSettings


class CompareMode(StrEnum):
    """User-facing ways to construct one shared comparison plan."""

    ASSERTIONS = "assertions"
    DIFFERENCES = "differences"
    EXECUTION_PROFILE = "execution-profile"


class ResultAccess(StrEnum):
    """Capabilities of a shared results workspace."""

    LIVE = "live"
    READ_ONLY = "read-only"


@final
class TuiLaunchRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Explicit local project, policy, and archive coordinates for one TUI process."""

    config: Path = Path("comparo.yaml")
    additional_sources: tuple[Path, ...] = ()
    policy: Path | None = None
    artifact_root: Path = Path(".comparo/artifacts")

    def __post_init__(self) -> None:
        """Reject non-path and mutable launch inputs."""
        if not isinstance(self.config, Path):
            raise TypeError("TUI project config must be a Path")
        if type(self.additional_sources) is not tuple or any(
            not isinstance(source, Path) for source in self.additional_sources
        ):
            raise TypeError("TUI additional sources must be an immutable Path tuple")
        if self.policy is not None and not isinstance(self.policy, Path):
            raise TypeError("TUI policy must be a Path or None")
        if not isinstance(self.artifact_root, Path):
            raise TypeError("TUI artifact root must be a Path")


@final
class CompareDraft(msgspec.Struct, frozen=True, kw_only=True):
    """Primitive UI selections translated by the shared application planner."""

    mode: CompareMode
    subject: str | None = None
    environment: str | None = None
    baseline: str | None = None
    candidate: str | None = None
    execution_profile: str | None = None

    def __post_init__(self) -> None:
        """Require typed mode and bounded identifier selections."""
        if type(self.mode) is not CompareMode:
            raise TypeError("comparison draft mode must be typed")
        for value in (
            self.subject,
            self.environment,
            self.baseline,
            self.candidate,
            self.execution_profile,
        ):
            if value is not None and (type(value) is not str or not value or len(value) > 63):
                raise ValueError("comparison draft selections must be bounded text or absent")

    def planning_options(self) -> PlanningOptions:
        """Translate the current mode without reconstructing planner precedence."""
        subjects = () if self.subject is None else (self.subject,)
        if self.mode is CompareMode.ASSERTIONS:
            environments = () if self.environment is None else (self.environment,)
            return PlanningOptions(subjects=subjects, environments=environments)
        if self.mode is CompareMode.DIFFERENCES:
            candidates = () if self.candidate is None else (self.candidate,)
            return PlanningOptions(
                subjects=subjects,
                baseline=self.baseline,
                candidates=candidates,
            )
        return PlanningOptions(execution_profile=self.execution_profile)


@final
class TuiExecutionControls(msgspec.Struct, frozen=True, kw_only=True):
    """Explicit execution choices whose artifact root remains launch-owned."""

    settings: ProductionExecutionSettings
    approve_mutations: bool
    tool_version: str

    def __post_init__(self) -> None:
        """Validate safe execution controls before a preparation is consumed."""
        if type(self.settings) is not ProductionExecutionSettings:
            raise TypeError("TUI execution settings must be typed")
        if type(self.approve_mutations) is not bool:
            raise TypeError("TUI mutation approval must be bool")
        if (
            type(self.tool_version) is not str
            or not self.tool_version
            or len(self.tool_version) > 64
        ):
            raise ValueError("TUI tool version must be bounded non-empty text")


__all__ = (
    "CompareDraft",
    "CompareMode",
    "ResultAccess",
    "TuiExecutionControls",
    "TuiLaunchRequest",
)
