"""Tests for the project loader: envelope, ids, and reference resolution."""

from pathlib import Path

import pytest

from comparo.core.diagnostics import LoadError
from comparo.core.loader import load_project

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def test_sample_project_loads() -> None:
    loaded = load_project(SAMPLE)
    assert loaded.project is not None
    assert len(loaded.objects) == 13
    assert "request.echo-anything" in loaded.objects


def test_fractional_tolerance_loads(tmp_path: Path) -> None:
    # Regression: the round-trip parser wraps floats as ScalarFloat, which strict
    # msgspec convert rejected — so a fractional tolerance failed to load.
    _write(
        tmp_path,
        "profile.yaml",
        "apiVersion: comparo/v1\n"
        "kind: DiffProfile\n"
        "metadata:\n  name: Tol\n  id: diff.tol\n"
        "spec:\n"
        "  default: exact\n"
        "  rules:\n"
        "    - path: $.price\n      mode: tolerance\n      tolerance: 0.01\n",
    )
    loaded = load_project(tmp_path)
    rules = loaded.objects["diff.tol"].spec.rules  # type: ignore[union-attr]
    assert rules is not None
    assert rules[0].tolerance == 0.01
    assert type(rules[0].tolerance) is float


def test_dangling_ref_suggests_near_miss(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "matrices/m.yaml",
        "apiVersion: comparo/v1\nkind: Matrix\n"
        "metadata:\n  name: M\n  id: matrix.chat.models\n"
        "spec:\n  target: request.body\n  values:\n    - a: 1\n",
    )
    _write(
        tmp_path,
        "requests/r.yaml",
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: R\n  id: request.r\n"
        "spec:\n  matrix:\n    - $ref: matrix.models.chat\n"
        "  request:\n    method: GET\n    endpoint: /x\n",
    )
    with pytest.raises(LoadError) as caught:
        load_project(tmp_path)
    hints = [d.hint for d in caught.value.diagnostics if d.hint is not None]
    assert any("matrix.chat.models" in hint for hint in hints)


def test_unknown_framework_key_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "environments/e.yaml",
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: E\n  id: environment.e\n"
        "spec:\n  baseUrl: http://localhost\n  bogusKey: nope\n",
    )
    with pytest.raises(LoadError):
        load_project(tmp_path)


def test_missing_id_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "environments/e.yaml",
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata:\n  name: E\nspec:\n  baseUrl: http://localhost\n",
    )
    with pytest.raises(LoadError) as caught:
        load_project(tmp_path)
    assert any("metadata.id" in d.message for d in caught.value.diagnostics)


def test_wrong_api_version_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "environments/e.yaml",
        "apiVersion: comparo/v2\nkind: Environment\n"
        "metadata:\n  name: E\n  id: environment.e\n"
        "spec:\n  baseUrl: http://localhost\n",
    )
    with pytest.raises(LoadError):
        load_project(tmp_path)
