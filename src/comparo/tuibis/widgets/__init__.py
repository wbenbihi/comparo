"""The comparo component library — every visual element lives here.

Screens compose these components and never touch Textual primitives or raw
styling directly; that rule is enforced by tests/tuibis/test_discipline.py.
"""

from comparo.tuibis.widgets.bars import FooterBar
from comparo.tuibis.widgets.bars import TopBar
from comparo.tuibis.widgets.bars import WorkspaceTabs
from comparo.tuibis.widgets.inspectors import EnvironmentInspector
from comparo.tuibis.widgets.inspectors import RequestInspector
from comparo.tuibis.widgets.panel import Panel
from comparo.tuibis.widgets.project_tree import ProjectTree
from comparo.tuibis.widgets.sections import Rule
from comparo.tuibis.widgets.sections import TextRow

__all__ = [
    "EnvironmentInspector",
    "FooterBar",
    "Panel",
    "ProjectTree",
    "RequestInspector",
    "Rule",
    "TextRow",
    "TopBar",
    "WorkspaceTabs",
]
