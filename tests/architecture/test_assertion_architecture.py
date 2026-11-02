"""Enforce the assertion engine's pure and cohesive dependency boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import comparo.core.assertions
import comparo.core.domain.assertions

_MAX_MODULE_LINES = 500
_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "collections",
        "comparo",
        "decimal",
        "fractions",
        "hashlib",
        "msgspec",
        "re",
        "typing",
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


def _assertion_sources() -> tuple[Path, ...]:
    package_paths = (
        Path(comparo.core.assertions.__file__).parent,
        Path(comparo.core.domain.assertions.__file__).parent,
    )
    return tuple(sorted(path for package in package_paths for path in package.glob("*.py")))


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


def test_assertion_modules_remain_cohesive() -> None:
    oversized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in _assertion_sources()
        if len(path.read_text(encoding="utf-8").splitlines()) > _MAX_MODULE_LINES
    }
    assert not oversized, f"split oversized assertion modules by responsibility: {oversized}"


def test_assertion_engine_uses_only_approved_pure_dependencies() -> None:
    violations: list[str] = []
    for path in _assertion_sources():
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
    assert not violations, "assertion purity violations:\n" + "\n".join(violations)
