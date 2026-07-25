"""Immutable TUI launch and comparison-draft contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import final

import msgspec

from comparo.application.planning import BudgetOverride
from comparo.application.planning import MatrixOverride
from comparo.application.planning import PlanningOptions
from comparo.application.planning import ValueResourceOverride
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


class CompareDraftValidationCode(StrEnum):
    """Closed value-free errors produced by bounded Compare input parsing."""

    INVALID_SELECTION = "invalid-selection"
    INVALID_MATRIX = "invalid-matrix"
    INVALID_LIMIT = "invalid-limit"
    INVALID_BUDGET = "invalid-budget"
    INVALID_VALUE_RESOURCE = "invalid-value-resource"
    INVALID_COMBINATION = "invalid-combination"


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
    subjects: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    baseline: str | None = None
    candidates: tuple[str, ...] = ()
    execution_profile: str | None = None
    matrices: tuple[MatrixOverride, ...] = ()
    trials: int | None = None
    maximum_attempts: int | None = None
    fail_fast: bool | None = None
    budgets: tuple[BudgetOverride, ...] = ()
    value_resources: tuple[ValueResourceOverride, ...] = ()
    approve_mutations: bool = False
    validation_code: CompareDraftValidationCode | None = None

    def __post_init__(self) -> None:
        """Require typed mode and bounded identifier selections."""
        if type(self.mode) is not CompareMode:
            raise TypeError("comparison draft mode must be typed")
        if type(self.subjects) is not tuple or type(self.environments) is not tuple:
            raise TypeError("comparison draft selections must be immutable tuples")
        if type(self.candidates) is not tuple:
            raise TypeError("comparison draft selections must be immutable tuples")
        for selection in (*self.subjects, *self.environments, *self.candidates):
            _validate_selection(selection)
        for optional_selection in (self.baseline, self.execution_profile):
            if optional_selection is not None and (
                type(optional_selection) is not str
                or not optional_selection
                or len(optional_selection) > 63
            ):
                raise ValueError("comparison draft selections must be bounded text or absent")
        if type(self.approve_mutations) is not bool:
            raise TypeError("comparison draft mutation approval must be bool")
        if self.validation_code is not None and type(self.validation_code) is not (
            CompareDraftValidationCode
        ):
            raise TypeError("comparison draft validation code must be typed or absent")
        if self.validation_code is None:
            self.planning_options()

    def planning_options(self) -> PlanningOptions:
        """Translate the current mode without reconstructing planner precedence."""
        if self.validation_code is not None:
            raise ValueError(f"comparison draft input {self.validation_code.value}")
        if self.mode is CompareMode.ASSERTIONS:
            return PlanningOptions(
                subjects=self.subjects,
                environments=self.environments,
                matrices=self.matrices,
                trials=self.trials,
                maximum_attempts=self.maximum_attempts,
                fail_fast=self.fail_fast,
                budgets=self.budgets,
                value_resources=self.value_resources,
            )
        if self.mode is CompareMode.DIFFERENCES:
            return PlanningOptions(
                subjects=self.subjects,
                baseline=self.baseline,
                candidates=self.candidates,
                matrices=self.matrices,
                trials=self.trials,
                maximum_attempts=self.maximum_attempts,
                fail_fast=self.fail_fast,
                budgets=self.budgets,
                value_resources=self.value_resources,
            )
        return PlanningOptions(
            execution_profile=self.execution_profile,
            subjects=self.subjects,
            environments=self.environments,
            baseline=self.baseline,
            candidates=self.candidates,
            matrices=self.matrices,
            trials=self.trials,
            maximum_attempts=self.maximum_attempts,
            fail_fast=self.fail_fast,
            budgets=self.budgets,
            value_resources=self.value_resources,
        )


def _validate_selection(value: object) -> None:
    if type(value) is not str or not value or len(value) > 63:
        raise ValueError("comparison draft selections must be bounded text")


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
    "CompareDraftValidationCode",
    "CompareMode",
    "ResultAccess",
    "TuiExecutionControls",
    "TuiLaunchRequest",
)
