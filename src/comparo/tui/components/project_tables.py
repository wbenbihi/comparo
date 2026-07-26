"""Virtualized native tables shared by Explorer resource facets."""

from __future__ import annotations

from typing import final
from typing import override

from textual.widgets import DataTable

from comparo.application.project_workspace import ProjectResourceSummary
from comparo.tui.presentation import safe_terminal_text


@final
class ResourceOutlineTable(DataTable[str]):
    """Virtualized native table for effective or authored structural fields."""

    def __init__(
        self,
        resource: ProjectResourceSummary,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Retain the safe resource whose outline will populate this native table."""
        super().__init__(id=id, classes=classes, zebra_stripes=True, cursor_type="row")
        self._resource = resource

    @override
    def on_mount(self) -> None:
        """Populate safe structural data once after table initialization."""
        self.add_columns("FIELD", "EFFECTIVE VALUE", "KIND")
        outline = self._resource.outline
        if outline is None:
            self.add_row("spec", "Structure unavailable", "unavailable")
            return
        for field in outline.fields:
            path = ".".join(field.path) or "spec"
            self.add_row(
                safe_terminal_text(path, maximum_characters=160),
                safe_terminal_text(field.value, maximum_characters=240),
                field.category,
            )


@final
class ResourceRelationshipTable(DataTable[str]):
    """Native dependency table shared by every resource kind."""

    def __init__(
        self,
        resource: ProjectResourceSummary,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Retain the safe resource whose dependencies will populate this table."""
        super().__init__(id=id, classes=classes, zebra_stripes=True, cursor_type="row")
        self._resource = resource

    @override
    def on_mount(self) -> None:
        """Populate exact resource identities without values."""
        self.add_columns("RELATION", "RESOURCE", "AT")
        outline = self._resource.outline
        relationships = () if outline is None else outline.relationships
        if not relationships:
            self.add_row("—", "No compiled resource dependencies", "—")
            return
        for relation in relationships:
            target = relation.target
            self.add_row(
                relation.origin,
                f"{target.kind}/{target.resource_id}",
                ".".join(relation.path) or "spec",
            )


__all__ = ("ResourceOutlineTable", "ResourceRelationshipTable")
