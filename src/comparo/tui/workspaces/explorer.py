"""Thin Explorer composition over safe project inspection components."""

from __future__ import annotations

from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical

from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.tui.components import DiagnosticsList
from comparo.tui.components import ProjectResourceDetail
from comparo.tui.components import ProjectResourceList
from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode


@final
class ExplorerWorkspace(Vertical):
    """Compose project loading, diagnostics, resource list, and resource detail."""

    def __init__(
        self,
        result: ProjectWorkspaceResult | None,
        *,
        id: str | None = None,
        classes: str | None = "workspace",
    ) -> None:
        """Initialize Explorer from a loading, failed, or successful result."""
        super().__init__(id=id, classes=classes)
        self._result = result

    @override
    def compose(self) -> ComposeResult:
        """Select one shared state without implementing project-loading behavior."""
        if self._result is None:
            yield StatePanel(
                mode=StatePanelMode.LOADING,
                message="Loading project…",
            )
            return
        snapshot = self._result.snapshot
        if snapshot is None:
            yield StatePanel(
                DiagnosticsList(self._result.diagnostics),
                mode=StatePanelMode.ERROR,
                message="The project could not be loaded.",
            )
            return
        resources = (snapshot.project, *snapshot.resources)
        layout = Horizontal(
            ProjectResourceList(resources, classes="explorer-resource-list"),
            ProjectResourceDetail(snapshot.project, classes="explorer-resource-detail"),
            classes="explorer-layout",
        )
        yield StatePanel(layout, mode=StatePanelMode.CONTENT)

    @on(ProjectResourceList.ResourceSelected)
    def show_selected_resource(self, message: ProjectResourceList.ResourceSelected) -> None:
        """Route resource selection to the shared detail component."""
        self.query_one(ProjectResourceDetail).show_resource(message.resource)

    def show_loading(self) -> None:
        """Keep this workspace mounted while replacing its internal state with loading."""
        self._result = None
        if self.is_mounted:
            self.refresh(recompose=True)

    def show_result(self, result: ProjectWorkspaceResult) -> None:
        """Keep this workspace mounted while presenting a new safe project result."""
        if type(result) is not ProjectWorkspaceResult:
            raise TypeError("Explorer workspace requires ProjectWorkspaceResult")
        if self._result is result:
            return
        self._result = result
        if self.is_mounted:
            self.refresh(recompose=True)

    def restore_resource(self, entity: str | None) -> None:
        """Restore the exact resource represented by a retained local route."""
        snapshot = None if self._result is None else self._result.snapshot
        if snapshot is None or not self.is_mounted:
            return
        resources = (snapshot.project, *snapshot.resources)
        resource = snapshot.project
        if entity is not None:
            kind, separator, resource_id = entity.partition("/")
            if separator:
                selected = next(
                    (
                        item
                        for item in resources
                        if item.coordinate.kind == kind
                        and item.coordinate.resource_id == resource_id
                    ),
                    None,
                )
                if selected is not None:
                    resource = selected
        self.query_one(ProjectResourceList).show_resource(resource)
        self.query_one(ProjectResourceDetail).show_resource(resource)


__all__ = ("ExplorerWorkspace",)
