"""Tests for the execution planner — assert both envs, diff, and gate."""

import asyncio
import json
from pathlib import Path

from comparo.core.execution import ExecutionResult
from comparo.core.execution import run_execution
from comparo.core.http import HttpResponse
from comparo.core.http import TimeoutBudget
from comparo.core.loader import load_project
from comparo.core.models import ExecutionProfile
from comparo.core.resolve import ResolvedRequest

_ENV = """apiVersion: comparo/v1
kind: Environment
metadata:
  name: {name}
  id: environment.{id}
spec:
  baseUrl: https://{id}.test
"""

_EXEC = """apiVersion: comparo/v1
kind: ExecutionProfile
metadata:
  name: Run
  id: exec.run
spec:
  environments:
    baseline: environment.base
    candidate: environment.cand
  check:
    assertions: true
    diff: true
"""


class _EnvEchoClient:
    """Returns a body that differs by which environment (host) is called."""

    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse:
        env = "base" if "base.test" in request.url else "cand"
        body = json.dumps({"env": env, "shared": "x"}).encode()
        return HttpResponse(200, [("content-type", "application/json")], body, 5.0)

    async def aclose(self) -> None:
        return None


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def _project(root: Path, *, status: int = 200, matrix: bool = False) -> None:
    _write(root, "base.yaml", _ENV.format(name="Base", id="base"))
    _write(root, "cand.yaml", _ENV.format(name="Cand", id="cand"))
    _write(root, "exec.yaml", _EXEC)
    probe = (
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Probe\n  id: request.probe\n  tags:\n    - smoke\n"
        "spec:\n"
    )
    if matrix:
        probe += "  matrix:\n    - $ref: matrix.tiers\n"
    probe += f"  request:\n    method: GET\n    endpoint: /get\n  response:\n    status: {status}\n"
    _write(root, "probe.yaml", probe)
    if matrix:
        _write(
            root,
            "tiers.yaml",
            "apiVersion: comparo/v1\nkind: Matrix\n"
            "metadata:\n  name: Tiers\n  id: matrix.tiers\n"
            "spec:\n  target: request.query\n  values:\n"
            "    - tier: free\n    - tier: pro\n",
        )


def _run(root: Path) -> ExecutionResult:
    loaded = load_project(root)
    profile = loaded.objects["exec.run"]
    assert isinstance(profile, ExecutionProfile)
    return asyncio.run(run_execution(loaded, profile, _EnvEchoClient()))


def test_execution_asserts_both_envs_and_diffs(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _run(tmp_path)
    assert result.baseline == "Base"
    assert result.candidate == "Cand"
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    # the status==200 sugar holds on both environments
    assert all(a.ok for a in outcome.baseline_assertions)
    assert all(a.ok for a in outcome.candidate_assertions)
    # $.env differs between the two -> drift -> gate fails
    assert outcome.diff is not None
    assert outcome.diff.drifted
    assert not result.passed
    assert result.drift == 1


def test_execution_assertion_failure_fails_the_gate(tmp_path: Path) -> None:
    _project(tmp_path, status=201)  # server answers 200
    result = _run(tmp_path)
    outcome = result.outcomes[0]
    assert not all(a.ok for a in outcome.baseline_assertions)
    assert not result.passed


def test_execution_inline_diff_profile_composes(tmp_path: Path) -> None:
    _project(tmp_path)
    # An inline diff profile (a list of one) that ignores the field which differs
    # between the two envs — so the cell no longer drifts.
    probe = (
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Probe\n  id: request.probe\n"
        "spec:\n"
        "  request:\n    method: GET\n    endpoint: /get\n"
        "  response:\n    status: 200\n"
        "    diff:\n"
        "      - default: exact\n"
        "        rules:\n"
        "          - path: $.env\n            mode: ignore\n"
    )
    _write(tmp_path, "probe.yaml", probe)
    result = _run(tmp_path)
    outcome = result.outcomes[0]
    assert outcome.diff is not None
    assert not outcome.diff.drifted  # $.env ignored by the inline profile
    assert result.passed


def test_execution_matrix_scope_limits_cells(tmp_path: Path) -> None:
    _project(tmp_path, matrix=True)
    # scope the tiers matrix down to just `free`
    exec_scoped = _EXEC + ("  matrix:\n    matrix.tiers:\n      include:\n        - tier: free\n")
    _write(tmp_path, "exec.yaml", exec_scoped)
    result = _run(tmp_path)
    assert len(result.outcomes) == 1
    assert "tier=free" in result.outcomes[0].cell_key
