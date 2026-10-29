"""Component-based development is mandatory — enforced, not aspirational.

Screens compose the component library; they never import Textual widgets,
Rich primitives, or style anything directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

SCREENS = Path(__file__).parents[2] / "src" / "comparo" / "tuibis" / "screens"

FORBIDDEN_PREFIXES = ("textual.widgets", "textual.widget", "rich")
ALLOWED_TEXTUAL = (
    "textual.containers",
    "textual.binding",
    "textual.message",
    "textual.app",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_screens_only_compose_components() -> None:
    offenders: list[str] = []
    for path in SCREENS.glob("*.py"):
        for module in _imports(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                offenders.append(f"{path.name}: {module}")
            if module.startswith("textual") and not module.startswith(ALLOWED_TEXTUAL):
                offenders.append(f"{path.name}: {module}")
    assert not offenders, (
        "screens must compose comparo components, not Textual/Rich "
        f"primitives — offending imports: {offenders}"
    )


def test_screens_never_set_styles_inline() -> None:
    offenders: list[str] = []
    for path in SCREENS.glob("*.py"):
        source = path.read_text()
        if ".styles." in source or "DEFAULT_CSS" in source:
            offenders.append(path.name)
    assert not offenders, f"styling belongs to styles.tcss and widgets, not screens: {offenders}"
