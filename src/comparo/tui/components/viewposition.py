"""Value-free cursor, focus and scroll bookmarks for recomposed inspectors."""

from dataclasses import dataclass

from textual.widget import Widget

from comparo.tui.components.table import Table
from comparo.tui.components.treemenu import TreeMenu
from comparo.tui.models.navigation import TreePosition


@dataclass(frozen=True)
class WidgetPosition:
    """One structural widget location and its presentation state."""

    path: tuple[int, ...]
    widget_type: str
    scroll_x: float
    scroll_y: float
    focused: bool
    row: str | None = None
    tree: TreePosition[str] | None = None


def capture_positions(root: Widget) -> tuple[WidgetPosition, ...]:
    """Retain no widgets, displayed values or resolved inspection products."""
    result: list[WidgetPosition] = []

    def visit(widget: Widget, path: tuple[int, ...]) -> None:
        row = widget.cursor_row_id if isinstance(widget, Table) else None
        tree = widget.capture_position() if isinstance(widget, TreeMenu) else None
        if widget.has_focus or widget.scroll_x or widget.scroll_y or row is not None or tree:
            result.append(
                WidgetPosition(
                    path,
                    type(widget).__name__,
                    widget.scroll_x,
                    widget.scroll_y,
                    widget.has_focus,
                    row,
                    tree,
                )
            )
        for index, child in enumerate(widget.children):
            visit(child, (*path, index))

    visit(root, ())
    return tuple(result)


def restore_positions(root: Widget, positions: tuple[WidgetPosition, ...]) -> None:
    """Restore surviving controls, tolerating changed structure after catalog reload."""
    for position in positions:
        widget = root
        for index in position.path:
            if index >= len(widget.children):
                break
            widget = widget.children[index]
        else:
            if type(widget).__name__ != position.widget_type:
                continue
            if isinstance(widget, Table) and position.row is not None:
                rows = [record.id for record in widget.records]
                if position.row in rows:
                    widget.move_cursor(row=rows.index(position.row), scroll=False)
            if isinstance(widget, TreeMenu) and position.tree is not None:
                widget.restore_position(position.tree)
            if position.focused:
                widget.focus(scroll_visible=False)
            widget.scroll_to(position.scroll_x, position.scroll_y, animate=False, force=True)
