"""Shared semantic activation for resource references and consumer tables."""

from typing import ClassVar
from typing import override

from textual.binding import BindingType
from textual.message import Message
from textual.widget import Widget

from comparo.application.project_outline import ResourceIdentityCoordinate
from comparo.tui.bindings import resource_link_bindings
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table
from comparo.tui.models.resource_links import ResourceDependency


class OpenResource(Message):
    """A mounted resource reference was explicitly activated."""

    def __init__(self, control: Widget, target: ResourceIdentityCoordinate) -> None:
        """Keep the typed target separate from all displayed labels."""
        super().__init__()
        self._control = control
        self.target = target

    @property
    @override
    def control(self) -> Widget:
        """Return the mounted link that requested navigation."""
        return self._control


class ResourceLinksTable(Table[str]):
    """Consumer/dependency rows opening their typed resource on activation."""

    BINDINGS: ClassVar[list[BindingType]] = resource_link_bindings()

    def __init__(
        self,
        edges: tuple[ResourceDependency, ...],
        *,
        directions: bool = False,
        classes: str | None = None,
    ) -> None:
        """Build stable rows using the standard record-table mechanics."""
        self._targets = {edge.key: edge.target for edge in edges}
        columns: tuple[Column, ...] = (
            Column("kind", "Kind", width=18),
            Column("resource", "Resource" if directions else "Consumer"),
        )
        if directions:
            columns = (Column("direction", "Direction", width=11), *columns)
        super().__init__(
            columns=columns,
            records=tuple(
                Record(
                    edge.key,
                    (edge.direction, edge.target_kind, edge.target_id)
                    if directions
                    else (edge.target_kind, edge.target_id),
                )
                for edge in edges
            ),
            classes=classes,
        )

    def on_table_row_selected(self, event: Table.RowSelected[str]) -> None:
        """Use the same semantic command for native mouse and Enter activation."""
        if event.table is self:
            event.stop()
            target = self._targets.get(event.row)
            if target is not None:
                self.post_message(OpenResource(self, target))
