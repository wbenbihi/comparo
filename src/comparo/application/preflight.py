"""Offline project compilation, policy selection, and deterministic plan inspection."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from typing import final
from uuid import UUID

import msgspec

from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import PlanInspection
from comparo.application.inspection import inspect_configuration_diagnostic
from comparo.application.inspection import inspect_diagnostic
from comparo.application.inspection import inspect_plan
from comparo.application.inspection import inspect_policy_diagnostic
from comparo.application.planning import InvocationBuildError
from comparo.application.planning import PlanningOptions
from comparo.application.planning import plan_project
from comparo.configuration.loader import compile_project_path
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import planning_authority
from comparo.core.policy import trusted_local_policy
from comparo.policy import compile_policy_path


@final
class ProjectPlanRequest(msgspec.Struct, frozen=True, kw_only=True):
    """Complete offline input for one local project plan preview."""

    config: Path
    additional_sources: tuple[Path, ...]
    policy: Path | None
    options: PlanningOptions
    invocation_id: UUID
    run_id: UUID

    def __post_init__(self) -> None:
        """Require exact, immutable adapter inputs before opening any source."""
        if not isinstance(self.config, Path):
            raise TypeError("project plan config must be a Path")
        if type(self.additional_sources) is not tuple or any(
            not isinstance(source, Path) for source in self.additional_sources
        ):
            raise TypeError("project plan sources must be an immutable Path tuple")
        if self.policy is not None and not isinstance(self.policy, Path):
            raise TypeError("project plan policy must be a Path or None")
        if type(self.options) is not PlanningOptions:
            raise TypeError("project plan options must be PlanningOptions")
        if type(self.invocation_id) is not UUID or type(self.run_id) is not UUID:
            raise TypeError("project plan identifiers must be exact UUID values")


@final
class ProjectPlanResult(msgspec.Struct, frozen=True, kw_only=True):
    """Exactly one admitted inspection or one or more value-free diagnostics."""

    inspection: PlanInspection | None = None
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Keep successful and rejected outcomes structurally disjoint."""
        if self.inspection is not None and type(self.inspection) is not PlanInspection:
            raise TypeError("project plan result inspection must be PlanInspection")
        if type(self.diagnostics) is not tuple or any(
            type(item) is not ApplicationDiagnostic for item in self.diagnostics
        ):
            raise TypeError("project plan diagnostics must be immutable and typed")
        if (self.inspection is None) == bool(self.diagnostics):
            return
        raise ValueError("project plan result must contain exactly an inspection or diagnostics")


def inspect_project_plan(request: ProjectPlanRequest) -> ProjectPlanResult:
    """Compile and plan without resolving values, opening bodies, or using the network."""
    if type(request) is not ProjectPlanRequest:
        raise TypeError("project plan preflight requires ProjectPlanRequest")
    loaded = compile_project_path(
        request.config,
        additional_sources=request.additional_sources,
    )
    compilation = loaded.compilation
    if compilation.project is None:
        return ProjectPlanResult(
            diagnostics=tuple(
                inspect_configuration_diagnostic(item) for item in compilation.diagnostics
            )
        )

    policy = _load_policy(request.policy)
    if type(policy) is tuple:
        return ProjectPlanResult(
            diagnostics=tuple(inspect_policy_diagnostic(item) for item in policy)
        )

    authority = planning_authority(
        cast("CompiledExecutionPolicy", policy),
        project_namespace=compilation.project.namespace,
    )
    try:
        planned = plan_project(
            compilation.project,
            authority,
            request.options,
            invocation_id=request.invocation_id,
            run_id=request.run_id,
        )
    except InvocationBuildError as error:
        return ProjectPlanResult(
            diagnostics=(
                ApplicationDiagnostic(
                    code=f"invocation.{error.code.value}",
                    severity="error",
                    stage="invocation",
                ),
            )
        )
    if planned.prepared is None:
        return ProjectPlanResult(
            diagnostics=tuple(inspect_diagnostic(item) for item in planned.diagnostics)
        )
    return ProjectPlanResult(inspection=inspect_plan(planned.prepared))


def _load_policy(
    path: Path | None,
) -> CompiledExecutionPolicy | tuple[ConfigurationDiagnostic, ...]:
    if path is None:
        return trusted_local_policy()
    compilation = compile_policy_path(path).compilation
    if compilation.policy is not None:
        return compilation.policy
    return compilation.diagnostics


__all__ = (
    "ProjectPlanRequest",
    "ProjectPlanResult",
    "inspect_project_plan",
)
