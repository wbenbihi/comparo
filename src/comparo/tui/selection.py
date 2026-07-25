"""Mounted-widget restoration for immutable local navigation frames."""

from __future__ import annotations

from typing import cast

from textual.dom import DOMNode

from comparo.application.results import ResultEntity
from comparo.application.results import ResultReference
from comparo.tui.components import ResultsWorkspaceView
from comparo.tui.navigation import NavigationFrame
from comparo.tui.navigation import WorkspaceId
from comparo.tui.workspaces import ExplorerWorkspace


def restore_frame(root: DOMNode, frame: NavigationFrame) -> None:
    """Restore the safe entity selection retained by one mounted local frame."""
    workspace = frame.route.workspace
    if workspace is WorkspaceId.EXPLORER:
        root.query_one("#workspace-explorer", ExplorerWorkspace).restore_resource(
            frame.route.entity if frame.route.view == "resource" else None
        )
        return
    if workspace not in {WorkspaceId.COMPARE, WorkspaceId.REPORT}:
        return
    views = root.query(f"#workspace-{workspace.value} ResultsWorkspaceView")
    if not len(views):
        return
    result_view = cast("ResultsWorkspaceView", views.first())
    reference = (
        result_reference_from_route(frame.route.entity)
        if frame.route.view == "result-detail"
        else None
    )
    if reference is None:
        result_view.clear_reference()
    else:
        result_view.follow_reference(reference)


def result_reference_from_route(entity: str | None) -> ResultReference | None:
    """Recover one typed result reference from a bounded route identity."""
    if entity is None:
        return None
    raw_kind, separator, result_id = entity.partition(":")
    if not separator:
        return None
    try:
        return ResultReference(entity=ResultEntity(raw_kind), result_id=result_id)
    except (TypeError, ValueError):
        return None


__all__ = ("restore_frame", "result_reference_from_route")
