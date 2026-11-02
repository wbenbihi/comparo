"""Architecture guards for the command-line adapter and its output boundary."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_PACKAGE_ROOT = _ROOT / "src" / "comparo"
_BACKEND_ROOTS = ("adapters", "configuration", "core", "reporters")
_CLI_FRAMEWORKS = frozenset({"click", "cyclopts", "rich", "rich_rst", "textual", "typer"})
_UNSAFE_CLI_IMPORTS = frozenset(
    {
        "comparo.core.domain.sensitivity",
        "pdb",
        "rich.traceback",
        "traceback",
    }
)
_UNSAFE_CLI_CALLS = frozenset(
    {
        "post_mortem",
        "print_exc",
        "print_exception",
        "set_trace",
    }
)


def _sources(root: Path) -> tuple[tuple[Path, ast.Module], ...]:
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    )


def _imports(tree: ast.Module) -> tuple[tuple[int, str], ...]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.extend(
                (
                    node.lineno,
                    node.module if alias.name == "*" else f"{node.module}.{alias.name}",
                )
                for alias in node.names
            )
    return tuple(imports)


def _call_name(node: ast.Call) -> str | None:
    parts: list[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def test_cli_frameworks_are_confined_to_the_cli_adapter() -> None:
    """Prevent presentation and parser dependencies from entering backend packages."""
    violations: list[str] = []
    for root_name in _BACKEND_ROOTS:
        for path, tree in _sources(_PACKAGE_ROOT / root_name):
            for line, imported in _imports(tree):
                if imported.partition(".")[0] in _CLI_FRAMEWORKS:
                    violations.append(f"{path.relative_to(_ROOT)}:{line}:{imported}")
    assert violations == []


def test_cli_frameworks_are_confined_to_the_cli_app_module() -> None:
    """Keep command contracts and services independent from Cyclopts and Rich."""
    cli_root = _PACKAGE_ROOT / "cli"
    assert cli_root.is_dir(), "the installed console entry point requires comparo.cli"
    violations: list[str] = []
    for path, tree in _sources(cli_root):
        if path.name == "app.py":
            continue
        for line, imported in _imports(tree):
            if imported.partition(".")[0] in _CLI_FRAMEWORKS:
                violations.append(f"{path.relative_to(_ROOT)}:{line}:{imported}")
    assert violations == []


def test_backend_packages_do_not_depend_on_the_cli_frontend() -> None:
    """Keep the CLI removable without changing backend behavior."""
    violations: list[str] = []
    for root_name in _BACKEND_ROOTS:
        for path, tree in _sources(_PACKAGE_ROOT / root_name):
            for line, imported in _imports(tree):
                if imported == "comparo.cli" or imported.startswith("comparo.cli."):
                    violations.append(f"{path.relative_to(_ROOT)}:{line}:{imported}")
    assert violations == []


def test_cli_does_not_import_live_secrets_or_traceback_renderers() -> None:
    """Block the most direct ways live values or unsafe exception text reach a terminal."""
    cli_root = _PACKAGE_ROOT / "cli"
    assert cli_root.is_dir(), "the installed console entry point requires comparo.cli"
    violations: list[str] = []
    for path, tree in _sources(cli_root):
        for line, imported in _imports(tree):
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in _UNSAFE_CLI_IMPORTS
            ):
                violations.append(f"{path.relative_to(_ROOT)}:{line}:{imported}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _call_name(node)
            if call is not None and call.rpartition(".")[2] in _UNSAFE_CLI_CALLS:
                violations.append(f"{path.relative_to(_ROOT)}:{node.lineno}:{call}")
    assert violations == []


def test_console_entry_point_target_is_importable_and_callable() -> None:
    """Catch packaging metadata that names a missing or non-callable adapter."""
    cli = importlib.import_module("comparo.cli")
    assert callable(cli.main)
