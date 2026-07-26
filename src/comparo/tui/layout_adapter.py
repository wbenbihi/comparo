"""Apply shared layout and focus policy through native Textual widgets."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import ContentSwitcher
from textual.widgets import DataTable
from textual.widgets import ListView
from textual.widgets import OptionList
from textual.widgets import Tree

from comparo.tui.design import LayoutClass
from comparo.tui.design import layout_class


def apply_responsive_class(switcher: ContentSwitcher, width: int) -> None:
    """Apply exactly one canonical terminal-width class to the workspace switcher."""
    if not isinstance(switcher, ContentSwitcher):
        raise TypeError("responsive layout requires a ContentSwitcher")
    selected = layout_class(width)
    for candidate in LayoutClass:
        switcher.remove_class(f"layout-{candidate.value}")
    switcher.add_class(f"layout-{selected.value}")


def focus_workspace(workspace: Widget, focused: Widget | None) -> None:
    """Preserve valid local focus or focus the first native child/region anchor."""
    if not isinstance(workspace, Widget):
        raise TypeError("workspace focus requires a Widget")
    if focused is not None and workspace in focused.ancestors:
        return
    children = tuple(
        item
        for item in workspace.walk_children(Widget)
        if item.can_focus and item.display and item.visible
    )
    candidate = next(
        (item for item in children if isinstance(item, (Tree, DataTable, ListView, OptionList))),
        children[0] if children else workspace,
    )
    candidate.focus()


__all__ = ("apply_responsive_class", "focus_workspace")
