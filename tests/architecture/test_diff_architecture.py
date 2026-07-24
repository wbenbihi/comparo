"""Enforce the diff engine's pure and cohesive dependency boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import comparo.core.diffs
import comparo.core.domain.diffs

_MAX_MODULE_LINES = 500
_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "collections",
        "comparo",
        "decimal",
        "enum",
        "fractions",
        "hashlib",
        "msgspec",
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


def _package_sources(package_file: str) -> tuple[Path, ...]:
    return tuple(sorted(Path(package_file).parent.glob("*.py")))


def _diff_sources() -> tuple[Path, ...]:
    return _package_sources(comparo.core.diffs.__file__)


def _domain_diff_sources() -> tuple[Path, ...]:
    return _package_sources(comparo.core.domain.diffs.__file__)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def test_diff_modules_remain_cohesive() -> None:
    oversized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in (*_diff_sources(), *_domain_diff_sources())
        if len(path.read_text(encoding="utf-8").splitlines()) > _MAX_MODULE_LINES
    }
    assert not oversized, f"split oversized diff modules by responsibility: {oversized}"


def test_diff_engine_uses_only_approved_pure_dependencies() -> None:
    violations: list[str] = []
    for path in _diff_sources():
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
            elif isinstance(node, ast.Call) and _call_name(node) in _FORBIDDEN_CALLS:
                violations.append(f"{path.name}:{node.lineno}: forbidden call {_call_name(node)}")
    assert not violations, "diff purity violations:\n" + "\n".join(violations)
