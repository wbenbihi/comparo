"""Keep gate reduction carriers independent from evaluator payload models."""

from __future__ import annotations

import ast
from pathlib import Path

import comparo.core.domain.gates


def test_gate_carriers_do_not_import_assertion_or_diff_fact_models() -> None:
    package_dir = Path(comparo.core.domain.gates.__file__).parent
    violations: list[str] = []
    for module_name in ("model.py", "summaries.py", "types.py"):
        tree = ast.parse((package_dir / module_name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module.startswith(
                ("comparo.core.domain.assertions", "comparo.core.domain.diffs")
            ):
                violations.append(f"{module_name}:{node.lineno}:{node.module}")
    assert not violations, f"gate carriers depend on evaluator payload models: {violations}"
