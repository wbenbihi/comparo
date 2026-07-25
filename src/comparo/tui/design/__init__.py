"""Shared visual semantics and responsive layout policy."""

from comparo.tui.design.breakpoints import BreakpointPolicy as BreakpointPolicy
from comparo.tui.design.breakpoints import LayoutClass as LayoutClass
from comparo.tui.design.breakpoints import layout_class as layout_class
from comparo.tui.design.semantics import SemanticStatus as SemanticStatus
from comparo.tui.design.semantics import SemanticStyle as SemanticStyle
from comparo.tui.design.semantics import semantic_style as semantic_style

__all__ = (
    "BreakpointPolicy",
    "LayoutClass",
    "SemanticStatus",
    "SemanticStyle",
    "layout_class",
    "semantic_style",
)
