"""Enforce resolution's injected-effect boundary and module cohesion."""

from __future__ import annotations

import ast
from pathlib import Path

import comparo.core.resolution

_MAX_MODULE_LINES = 650
_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "asyncio",
        "collections",
        "comparo",
        "decimal",
        "hashlib",
        "hmac",
        "json",
        "msgspec",
        "re",
        "typing",
        "uuid",
    }
)
_FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "compile",
        "eval",
        "exec",
        "getattr",
        "input",
        "open",
        "print",
        "setattr",
    }
)


def _resolution_sources() -> tuple[Path, ...]:
    package = Path(comparo.core.resolution.__file__).parent
    return tuple(sorted(package.glob("*.py")))


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_forbidden_call(node: ast.Call) -> bool:
    name = _call_name(node)
    if name not in _FORBIDDEN_CALLS:
        return False
    return not (
        name == "compile"
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
    )


def test_resolution_modules_remain_cohesive() -> None:
    oversized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in _resolution_sources()
        if len(path.read_text(encoding="utf-8").splitlines()) > _MAX_MODULE_LINES
    }
    assert not oversized, f"split oversized resolution modules by responsibility: {oversized}"


def test_resolution_uses_only_approved_dependencies_and_calls() -> None:
    violations: list[str] = []
    for path in _resolution_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = tuple(alias.name.partition(".")[0] for alias in node.names)
                unknown = tuple(root for root in roots if root not in _ALLOWED_IMPORT_ROOTS)
                if unknown:
                    violations.append(f"{path.name}:{node.lineno}: unapproved imports {unknown!r}")
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                root = node.module.partition(".")[0]
                if root not in _ALLOWED_IMPORT_ROOTS:
                    violations.append(f"{path.name}:{node.lineno}: unapproved import {root!r}")
            elif isinstance(node, ast.Call) and _is_forbidden_call(node):
                violations.append(f"{path.name}:{node.lineno}: forbidden call {_call_name(node)}")
    assert not violations, "resolution boundary violations:\n" + "\n".join(violations)


def test_expression_resolver_does_not_depend_on_planning_or_adapters() -> None:
    resolver = next(path for path in _resolution_sources() if path.name == "resolver.py")
    source = resolver.read_text(encoding="utf-8")
    assert "comparo.core.planning" not in source
    assert "comparo.configuration" not in source
