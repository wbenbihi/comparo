"""Comparo's component-based Textual frontend."""

from comparo.tui.design.breakpoints import LayoutClass as LayoutClass
from comparo.tui.design.breakpoints import layout_class as layout_class
from comparo.tui.design.semantics import SemanticStatus as SemanticStatus
from comparo.tui.design.semantics import semantic_style as semantic_style
from comparo.tui.navigation.routes import WorkspaceId as WorkspaceId

__all__ = (
    "LayoutClass",
    "SemanticStatus",
    "WorkspaceId",
    "layout_class",
    "semantic_style",
)
