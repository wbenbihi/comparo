"""Architecture guards for the removable component-based terminal frontend."""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_PACKAGE = _ROOT / "src" / "comparo"
_TUI = _PACKAGE / "tui"
_BACKEND_PACKAGES = (
    "adapters",
    "application",
    "configuration",
    "core",
    "policy",
    "reporters",
)
_PURE_TUI_PACKAGES = ("contracts.py", "states.py", "types", "breakpoints.py")
# Inherited TUI4 modules above the normal ceiling. Explicit baselines prevent further growth;
# splitting these existing presenters/backend is independent of promoting the frontend.
_EXISTING_MODULE_LIMITS = {
    "backend.py": 930,
    "components/results.py": 1203,
    "components/launchsummary.py": 659,
    "components/launchconfig.py": 609,
    "models/resource_presenters.py": 1106,
    "models/remaining_resource_presenters.py": 507,
    "workspaces/explorer.py": 693,
    "workspaces/launch.py": 537,
}
_FORBIDDEN_FRONTEND_IMPORTS = (
    "comparo.adapters",
    "comparo.cli",
    "comparo.configuration",
    "comparo.core",
    "comparo.policy",
)
_UNSAFE_IMPORTS = (
    "comparo.core.domain.sensitivity",
    "pdb",
    "rich.traceback",
    "traceback",
)
_UNSAFE_CALLS = frozenset({"breakpoint", "open", "print_exc", "print_exception", "set_trace"})
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")


def _sources(root: Path) -> tuple[tuple[Path, ast.Module], ...]:
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted((root,) if root.is_file() else root.rglob("*.py"))
        if "__pycache__" not in path.parts
    )


def _imports(tree: ast.Module) -> tuple[tuple[int, str], ...]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.extend((node.lineno, node.module) for _alias in node.names)
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


def test_backend_packages_do_not_depend_on_the_tui() -> None:
    """Keep every backend capability available when comparo.tui is removed."""
    violations = [
        f"{path.relative_to(_ROOT)}:{line}:{imported}"
        for package in _BACKEND_PACKAGES
        for path, tree in _sources(_PACKAGE / package)
        for line, imported in _imports(tree)
        if imported == "comparo.tui" or imported.startswith("comparo.tui.")
    ]
    assert violations == []


def test_pure_tui_contracts_do_not_import_textual_or_rich() -> None:
    """Keep state, navigation, commands, and ports testable without a renderer."""
    assert all(_sources(_TUI / package) for package in _PURE_TUI_PACKAGES)
    violations = [
        f"{path.relative_to(_ROOT)}:{line}:{imported}"
        for package in _PURE_TUI_PACKAGES
        for path, tree in _sources(_TUI / package)
        for line, imported in _imports(tree)
        if imported.partition(".")[0] in {"rich", "textual"}
    ]
    assert violations == []


def test_tui_presentation_never_reaches_backend_implementation_packages() -> None:
    """Require widgets and workspaces to consume application projections only."""
    presentation_roots = tuple(
        root for root in (_TUI / "components", _TUI / "workspaces") if root.exists()
    )
    violations = [
        f"{path.relative_to(_ROOT)}:{line}:{imported}"
        for root in presentation_roots
        for path, tree in _sources(root)
        for line, imported in _imports(tree)
        if any(
            imported == forbidden or imported.startswith(f"{forbidden}.")
            for forbidden in _FORBIDDEN_FRONTEND_IMPORTS
        )
    ]
    assert violations == []


def test_tui_never_imports_live_secrets_or_traceback_renderers() -> None:
    """Prevent unsafe exception and secret containers from entering terminal code."""
    violations: list[str] = []
    for path, tree in _sources(_TUI):
        for line, imported in _imports(tree):
            if any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for forbidden in _UNSAFE_IMPORTS
            ):
                violations.append(f"{path.relative_to(_ROOT)}:{line}:{imported}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                # The bounded user-preferences reader is the sole presentation-owned file read.
                preference_read = path == _TUI / "preferences.py" and name == "path.open"
                if (
                    name is not None
                    and name.rpartition(".")[2] in _UNSAFE_CALLS
                    and not preference_read
                ):
                    violations.append(f"{path.relative_to(_ROOT)}:{node.lineno}:{name}")
    assert violations == []


def test_tui_modules_remain_component_sized() -> None:
    """Stop another terminal god module from emerging during feature growth."""
    oversized = {
        str(path.relative_to(_ROOT)): len(path.read_text(encoding="utf-8").splitlines())
        for path, _tree in _sources(_TUI)
        if len(path.read_text(encoding="utf-8").splitlines())
        > _EXISTING_MODULE_LIMITS.get(path.relative_to(_TUI).as_posix(), 500)
    }
    assert oversized == {}


def test_tui_theme_definitions_are_the_only_raw_color_source() -> None:
    """Keep every feature component independent from concrete theme palettes."""
    allowed = _TUI / "theme.py"
    violations = [
        str(path.relative_to(_ROOT))
        for path in sorted((*_TUI.rglob("*.py"), *_TUI.rglob("*.tcss")))
        if path != allowed and _HEX_COLOR.search(path.read_text(encoding="utf-8")) is not None
    ]
    assert violations == []


def test_global_workspace_bindings_have_one_adapter_source() -> None:
    """Keep global workspace routing centralized while allowing local component keys."""
    allowed = _TUI / "bindings.py"
    violations = [
        f"{path.relative_to(_ROOT)}:{node.lineno}"
        for path, tree in _sources(_TUI)
        if path != allowed
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node) == "Binding"
        and any(
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.startswith("workspace(")
            for arg in node.args
        )
    ]
    assert violations == []


def test_only_the_canonical_terminal_package_remains() -> None:
    """Prevent retired implementations from reentering the shipped package."""
    assert (_TUI / "app.py").is_file()
    assert (_TUI / "workspaces" / "launch.py").is_file()
    assert (_TUI / "workspaces" / "reports.py").is_file()
    for retired in ("tui3", "tui4", "tuibis"):
        assert not (_PACKAGE / retired).exists()
        assert not (_ROOT / "tests" / retired).exists()


def test_resource_inspectors_use_shared_actions_instead_of_native_buttons() -> None:
    """Keep resource controls within the agreed compact semantic action grammar."""
    violations = [
        str(path.relative_to(_ROOT))
        for path, tree in _sources(_TUI / "components" / "resources")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "textual.widgets"
        and any(alias.name == "Button" for alias in node.names)
    ]
    assert violations == []
