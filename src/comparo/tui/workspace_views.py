"""Workspace compositions projected from the framework-neutral coordinator."""

from __future__ import annotations

from comparo.tui.contracts import TuiInteractionLimits
from comparo.tui.controllers import TuiCoordinator
from comparo.tui.workspaces import CompareWorkspace
from comparo.tui.workspaces import CompareWorkspaceState
from comparo.tui.workspaces import ReportWorkspace
from comparo.tui.workspaces import ReportWorkspaceState


def compare_workspace_view(
    state: TuiCoordinator,
    interaction_limits: TuiInteractionLimits,
) -> CompareWorkspace:
    """Project the coordinator's Compare state into one mounted workspace composition."""
    return CompareWorkspace(
        mode=state.compare_draft.mode,
        draft=state.compare_draft,
        resources=state.project_resources,
        state=CompareWorkspaceState(state.compare_surface.value),
        plan=state.compare_plan,
        progress=state.compare_progress,
        results=state.compare_result,
        diagnostics=state.compare_diagnostics,
        interaction_limits=interaction_limits,
        id="workspace-compare",
    )


def report_workspace_view(state: TuiCoordinator) -> ReportWorkspace:
    """Project the coordinator's Report state into one mounted workspace composition."""
    return ReportWorkspace(
        state=ReportWorkspaceState(state.report_surface.value),
        listing=state.report_listing,
        result=state.report_result,
        id="workspace-report",
    )


__all__ = ("compare_workspace_view", "report_workspace_view")
