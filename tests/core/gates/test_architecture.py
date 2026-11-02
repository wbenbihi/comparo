"""Keep the gate reducer isolated from evaluator payloads and external effects."""

from __future__ import annotations

import ast
from pathlib import Path

import comparo.core.gates


def test_gate_reducer_imports_only_pure_gate_contracts_and_primitives() -> None:
    package_dir = Path(comparo.core.gates.__file__).parent
    violations: list[str] = []
    allowed = {
        "__future__",
        "comparo.core.domain.gates.model",
        "comparo.core.domain.gates.summaries",
        "comparo.core.domain.gates.types",
        "comparo.core.domain.primitives",
        "comparo.core.gates.engine",
    }
    for source in package_dir.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend(
                    f"{source.name}:{node.lineno}:{alias.name}"
                    for alias in node.names
                    if alias.name not in allowed
                )
            elif isinstance(node, ast.ImportFrom) and node.module not in allowed:
                violations.append(f"{source.name}:{node.lineno}:{node.module}")
    assert not violations, f"gate reducer crossed its pure dependency boundary: {violations}"
