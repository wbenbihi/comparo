"""Reusable Explorer resource list and detail components."""

from __future__ import annotations

from typing import final

from textual.message import Message
from textual.widgets import Label
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import Static

from comparo.application.project_workspace import ProjectResourceSummary
from comparo.tui.presentation import safe_terminal_text


@final
class ProjectResourceItem(ListItem):
    """One typed project-resource row retaining its safe application model."""

    def __init__(self, resource: ProjectResourceSummary) -> None:
        """Initialize the row from one safe resource summary."""
        self.resource = resource
        name = resource.name or resource.coordinate.resource_id
        label = safe_terminal_text(
            f"{resource.coordinate.kind}  {name}",
            maximum_characters=256,
        )
        super().__init__(Label(label, markup=False))


@final
class ProjectResourceList(ListView):
    """Shared navigable list for every compiled project-resource kind."""

    @final
    class ResourceSelected(Message):
        """A user selected one project resource for inspection."""

        def __init__(self, resource: ProjectResourceSummary) -> None:
            """Retain the selected safe resource summary."""
            super().__init__()
            self.resource = resource

    def __init__(
        self,
        resources: tuple[ProjectResourceSummary, ...],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize rows from immutable application summaries."""
        super().__init__(
            *(ProjectResourceItem(resource) for resource in resources),
            id=id,
            classes=classes,
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Translate framework selection into a typed component message."""
        if type(event.item) is ProjectResourceItem:
            self.post_message(self.ResourceSelected(event.item.resource))


@final
class ProjectResourceDetail(Static):
    """One source-aware resource detail with no configuration interpretation."""

    def __init__(
        self,
        resource: ProjectResourceSummary | None = None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize detail with an optional selected resource."""
        super().__init__("", id=id, classes=classes, markup=False)
        self.show_resource(resource)

    def show_resource(self, resource: ProjectResourceSummary | None) -> None:
        """Display only application-projected identity, metadata, and provenance."""
        if resource is None:
            self.update("Select a project resource to inspect it.")
            return
        coordinate = resource.coordinate
        tags = ", ".join(resource.tags) if resource.tags else "—"
        description = resource.description or "—"
        lines = (
            resource.name or coordinate.resource_id,
            f"{coordinate.kind}/{coordinate.resource_id}",
            f"Namespace: {coordinate.namespace}",
            f"Description: {description}",
            f"Tags: {tags}",
            (
                f"Source: {resource.source.source}:{resource.source.line}:"
                f"{resource.source.column} · document {resource.source.document}"
            ),
        )
        self.update(
            "\n".join(
                safe_terminal_text(item, maximum_characters=1024, multiline=False) for item in lines
            )
        )


__all__ = (
    "ProjectResourceDetail",
    "ProjectResourceItem",
    "ProjectResourceList",
)
