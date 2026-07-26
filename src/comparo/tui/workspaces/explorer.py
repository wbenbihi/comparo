"""Screenshot-led Explorer workspace composed from native Textual controls."""

from __future__ import annotations

from typing import ClassVar
from typing import final
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input
from textual.widgets import Label
from textual.widgets import Static
from textual.widgets import Tab
from textual.widgets import Tabs

from comparo.application.project_workspace import ProjectResourceSummary
from comparo.application.project_workspace import ProjectWorkspaceResult
from comparo.tui.bindings import textual_bindings
from comparo.tui.commands import CommandScope
from comparo.tui.components import DiagnosticsList
from comparo.tui.components import ProjectResourceDetail
from comparo.tui.components import ProjectResourceList
from comparo.tui.components import StatePanel
from comparo.tui.components import StatePanelMode
from comparo.tui.presentation import safe_terminal_text

_FILTERS = (
    ("All", "all"),
    ("Request", "request"),
    ("Schema", "schema"),
    ("Matrix", "matrix"),
    ("Profile", "profile"),
    ("Env", "env"),
)


@final
class ExplorerWorkspace(Vertical):
    """Own catalog filtering and selected-resource inspection, but no loading effects."""

    BINDINGS: ClassVar[list[BindingType]] = textual_bindings(CommandScope.EXPLORER)

    @final
    class UseInCompareRequested(Message):
        """A safe resource identity should seed the Compare target flow."""

        def __init__(self, resource: ProjectResourceSummary) -> None:
            """Retain the safe resource selected for Compare."""
            super().__init__()
            self.resource = resource

    def __init__(
        self,
        result: ProjectWorkspaceResult | None,
        *,
        id: str | None = None,
        classes: str | None = "workspace",
    ) -> None:
        """Initialize from a loading, failed, or successful inspection result."""
        super().__init__(id=id, classes=classes)
        self.can_focus = True
        self._result = result

    @override
    def compose(self) -> ComposeResult:
        """Compose a dense catalog and inspector with one authoritative selection."""
        if self._result is None:
            yield StatePanel(mode=StatePanelMode.LOADING, message="Loading project…")
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
        with Vertical(classes="explorer-page"):
            with (
                Horizontal(classes="workspace-heading"),
                Vertical(classes="workspace-heading-copy"),
            ):
                yield Label("Explorer", classes="workspace-title")
                yield Static(
                    safe_terminal_text(
                        f"{snapshot.project.name or snapshot.project.coordinate.resource_id}"
                        f"  ·  {len(snapshot.resources)} resources"
                    ),
                    classes="workspace-subtitle",
                    markup=False,
                )
            with Horizontal(classes="explorer-layout"):
                with Vertical(classes="explorer-catalog-region"):
                    yield Input(
                        placeholder="Search resources  /",
                        id="explorer-search",
                    )
                    yield Tabs(
                        *(Tab(label, id=f"explorer-filter-{value}") for label, value in _FILTERS),
                        active="explorer-filter-all",
                        id="explorer-filters",
                    )
                    yield ProjectResourceList(resources, classes="explorer-resource-list")
                yield ProjectResourceDetail(
                    snapshot.project,
                    classes="explorer-resource-detail",
                )

    @on(Input.Changed, "#explorer-search")
    def filter_resources(self, event: Input.Changed) -> None:
        """Apply search on every edit while retaining native Tree navigation."""
        self.query_one(ProjectResourceList).filter(event.value)

    @on(Tabs.TabActivated, "#explorer-filters")
    def filter_resource_kind(self, event: Tabs.TabActivated) -> None:
        """Apply the selected product-facing catalog filter."""
        tab_id = event.tab.id or ""
        if tab_id.startswith("explorer-filter-"):
            self.query_one(ProjectResourceList).filter_kind(tab_id.removeprefix("explorer-filter-"))

    @on(ProjectResourceList.ResourceHighlighted)
    def show_highlighted_resource(
        self,
        message: ProjectResourceList.ResourceHighlighted,
    ) -> None:
        """Update the inspector as the cursor moves, without requiring Enter."""
        self.query_one(ProjectResourceDetail).show_resource(message.resource)

    @on(ProjectResourceList.ResourceSelected)
    def show_selected_resource(self, message: ProjectResourceList.ResourceSelected) -> None:
        """Retain compatibility with explicit activation and local navigation history."""
        self.query_one(ProjectResourceDetail).show_resource(message.resource)

    @on(ProjectResourceDetail.UseInCompare)
    def request_use_in_compare(self, message: ProjectResourceDetail.UseInCompare) -> None:
        """Forward pointer activation as one workspace-level semantic intent."""
        self.post_message(self.UseInCompareRequested(message.resource))

    def selected_resource(self) -> ProjectResourceSummary | None:
        """Return the exact resource under the catalog cursor."""
        if not self.is_mounted or not self.query(ProjectResourceList):
            return None
        return self.query_one(ProjectResourceList).selected_resource

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
