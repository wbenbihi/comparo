"""Keep the ExecutionPolicy trust frontend isolated from effects and adapter frameworks."""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_IMPORTS = (
    "aiohttp",
    "anyio",
    "click",
    "cyclopts",
    "httpcore",
    "httpx",
    "os",
    "pathlib",
    "requests",
    "rich",
    "socket",
    "ssl",
    "subprocess",
    "textual",
    "typer",
    "yaml",
)


def _imported_modules(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(modules)


def test_core_policy_frontend_has_no_effect_or_adapter_imports() -> None:
    root = Path(__file__).parents[2] / "src" / "comparo" / "core" / "policy"
    violations: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.name}: forbidden import {imported}"
            for imported in _imported_modules(tree)
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in _FORBIDDEN_IMPORTS
            )
        )
    assert violations == []
