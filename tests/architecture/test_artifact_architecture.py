"""Architecture guards for the safe artifact boundary and pure reporters."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_SAFE_ARTIFACT_MODULES = (
    "common.py",
    "facts.py",
    "http.py",
    "lifecycle.py",
    "model.py",
    "values.py",
)
_FORBIDDEN_TYPE_NAMES = {
    "BodyHandle",
    "BodyStorageToken",
    "PreparedRun",
    "RepeatableByteSource",
    "RunResult",
    "SensitiveValue",
}


def test_persistable_struct_annotations_cannot_name_live_carriers() -> None:
    artifact_root = _ROOT / "src" / "comparo" / "core" / "artifacts"
    violations: list[str] = []
    for name in _SAFE_ARTIFACT_MODULES:
        path = artifact_root / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign):
                continue
            annotation = ast.unparse(node.annotation)
            forbidden = sorted(item for item in _FORBIDDEN_TYPE_NAMES if item in annotation)
            if forbidden:
                violations.append(f"{name}:{node.lineno}:{','.join(forbidden)}")
    assert violations == []


def test_reporter_comparo_imports_are_confined_to_artifacts_and_reporters() -> None:
    reporter_root = _ROOT / "src" / "comparo" / "reporters"
    violations: list[str] = []
    for path in sorted(reporter_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module.startswith("comparo.") and not node.module.startswith(
                ("comparo.core.artifacts", "comparo.reporters")
            ):
                violations.append(f"{path.name}:{node.lineno}:{node.module}")
    assert violations == []


def test_reporter_public_renderers_accept_run_artifact_not_live_results() -> None:
    reporter_root = _ROOT / "src" / "comparo" / "reporters"
    violations: list[str] = []
    for path in sorted(reporter_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("render_"):
                continue
            positional = node.args.args
            first_annotation = None if not positional else positional[0].annotation
            if first_annotation is None or ast.unparse(first_annotation) != "RunArtifact":
                violations.append(f"{path.name}:{node.lineno}:{node.name}")
            annotations = " ".join(
                ast.unparse(item.annotation)
                for item in (*node.args.args, *node.args.kwonlyargs)
                if item.annotation is not None
            )
            if any(name in annotations for name in _FORBIDDEN_TYPE_NAMES):
                violations.append(f"{path.name}:{node.lineno}:{annotations}")
    assert violations == []


def test_importing_reporters_does_not_load_live_runtime_or_secret_modules() -> None:
    program = """
import sys
import comparo.reporters

forbidden = (
    "comparo.core.domain.http",
    "comparo.core.domain.sensitivity",
    "comparo.core.planning",
    "comparo.core.resolution",
    "comparo.core.runtime",
)
loaded = sorted(name for name in sys.modules if name.startswith(forbidden))
raise SystemExit("\\n".join(loaded) if loaded else 0)
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local literal program
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_body_free_results_depend_on_pure_coordinates_not_artifact_io() -> None:
    path = _ROOT / "src" / "comparo" / "application" / "results.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "comparo.application.artifact_references" in imports
    assert "comparo.application.artifacts" not in imports
