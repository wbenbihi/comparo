"""Tests for the project loader: envelope, ids, and reference resolution."""

from pathlib import Path

import pytest

from comparo.core.diagnostics import LoadError
from comparo.core.loader import load_project
from comparo.core.models import DiffProfile
from comparo.core.models import Request

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def test_sample_project_loads() -> None:
    loaded = load_project(SAMPLE)
    assert loaded.project is not None
    assert len(loaded.objects) == 14
    assert "request.echo-anything" in loaded.objects


def test_assertion_and_execution_profiles_load(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "contract.yaml",
        "apiVersion: comparo/v1\nkind: AssertionProfile\n"
        "metadata:\n  name: Contract\n  id: assert.contract\n"
        "spec:\n  rules:\n"
        "    - target: status\n      op: equals\n      value: 200\n"
        "    - target: latency\n      op: lte\n      value: 800ms\n      severity: warn\n",
    )
    _write(
        tmp_path,
        "gate.yaml",
        "apiVersion: comparo/v1\nkind: ExecutionProfile\n"
        "metadata:\n  name: Gate\n  id: exec.gate\n"
        "spec:\n"
        "  environments:\n    baseline: environment.stable\n    candidate: environment.canary\n"
        "  check:\n    assertions: true\n    diff: true\n",
    )
    loaded = load_project(tmp_path)
    contract = loaded.objects["assert.contract"]
    gate = loaded.objects["exec.gate"]
    assert contract.spec.rules[0].op == "equals"  # type: ignore[union-attr, index]
    assert contract.spec.rules[1].severity == "warn"  # type: ignore[union-attr, index]
    assert gate.spec.environments.candidate == "environment.canary"  # type: ignore[union-attr]
    assert gate.spec.check.assertions is True  # type: ignore[union-attr]


def test_mistyped_execution_profiles_key_is_rejected(tmp_path: Path) -> None:
    # A typo in the profiles block (asert) must be a hard load error, not a
    # silently-disabled profile — the profiles struct forbids unknown fields.
    _write(
        tmp_path,
        "gate.yaml",
        "apiVersion: comparo/v1\nkind: ExecutionProfile\n"
        "metadata:\n  name: Gate\n  id: exec.gate\n"
        "spec:\n"
        "  environments:\n    baseline: environment.stable\n"
        "  profiles:\n    asert:\n      $use: assert.contract\n",  # typo: asert
    )
    with pytest.raises(LoadError):
        load_project(tmp_path)


def test_wrong_kind_assertion_include_is_rejected(tmp_path: Path) -> None:
    # An include that points at a non-AssertionProfile would be silently dropped
    # at runtime (composing zero rules → false green); it must fail loud instead.
    _write(
        tmp_path,
        "req.yaml",
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Probe\n  id: request.probe\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /get\n",
    )
    _write(
        tmp_path,
        "profile.yaml",
        "apiVersion: comparo/v1\nkind: AssertionProfile\n"
        "metadata:\n  name: Composed\n  id: assert.composed\n"
        "spec:\n  include:\n    - $use: request.probe\n",  # a Request, not an AssertionProfile
    )
    with pytest.raises(LoadError) as caught:
        load_project(tmp_path)
    assert any("not an AssertionProfile" in d.message for d in caught.value.diagnostics)


def test_inline_assertion_include_wrong_kind_is_rejected(tmp_path: Path) -> None:
    # An INLINE response.assert include (no standalone profile object) must be
    # validated too — else a wrong-kind include loads clean and drops the rules.
    _write(
        tmp_path,
        "req.yaml",
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Probe\n  id: request.probe\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /get\n"
        "  response:\n    assert:\n      include:\n        - request.probe\n",  # bare string
    )
    with pytest.raises(LoadError) as caught:
        load_project(tmp_path)
    assert any("include is not" in d.message for d in caught.value.diagnostics)


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
    profile = loaded.objects["diff.tol"]
    assert isinstance(profile, DiffProfile)
    rules = profile.spec.rules
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
        "spec:\n  matrix:\n    - $use: matrix.models.chat\n"
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


def test_a_yaml_native_date_in_a_body_loads_as_an_iso_string(tmp_path: Path) -> None:
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: R, id: request.r}\n"
        "spec:\n  request:\n    method: POST\n    endpoint: /x\n    body: {when: 2026-07-18}\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path)
    request = loaded.objects["request.r"]
    assert isinstance(request, Request)
    assert request.spec.request.body == {"when": "2026-07-18"}


def test_a_typoed_matrix_target_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / "m.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Matrix\nmetadata: {name: M, id: matrix.m}\n"
        "spec:\n  target: request.qeury\n  values: [{code: '200'}]\n",  # 'qeury' typo
        encoding="utf-8",
    )
    with pytest.raises(LoadError):
        load_project(tmp_path)


def test_a_bare_or_wrong_kind_matrix_ref_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / "m.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Matrix\nmetadata: {name: M, id: matrix.m}\n"
        "spec:\n  target: request.query\n  values: [{code: '200'}]\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: R, id: request.r}\n"
        "spec:\n  matrix: [matrix.m]\n  request: {method: GET, endpoint: /x}\n",  # bare string
        encoding="utf-8",
    )
    with pytest.raises(LoadError) as caught:
        load_project(tmp_path)
    assert any("matrix entry is not" in d.message for d in caught.value.diagnostics)


def test_a_val_pointing_at_a_non_instance_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\nmetadata: {name: E, id: environment.e}\n"
        "spec: {baseUrl: 'http://h'}\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: R, id: request.r}\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /x\n"
        "    query: {v: {$val: environment.e}}\n",  # $val -> an Environment, not an Instance
        encoding="utf-8",
    )
    with pytest.raises(LoadError) as caught:
        load_project(tmp_path)
    assert any("not an Instance" in d.message for d in caught.value.diagnostics)


def test_spec_data_escaping_the_project_root_is_refused(tmp_path: Path) -> None:
    # S-2: a spec.data that climbs out of the project (../ or absolute) would make
    # comparo scan and parse YAML from anywhere on disk. It must be refused up front.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: E, id: environment.e}\nspec: {baseUrl: 'http://h'}\n",
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    project.mkdir()
    (project / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\n"
        "metadata: {name: P, id: project.p}\nspec: {data: ../outside}\n",
        encoding="utf-8",
    )
    with pytest.raises(LoadError) as caught:
        load_project(project / "comparo.yaml")
    assert any("escapes the project root" in d.message for d in caught.value.diagnostics)


def test_yml_extension_objects_are_loaded_too(tmp_path: Path) -> None:
    # Objects may use the .yml extension, not only .yaml.
    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\n"
        "metadata: {name: P, id: project.p}\nspec: {data: .}\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: E, id: environment.e}\nspec: {baseUrl: 'http://h'}\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path / "comparo.yaml")
    assert "environment.e" in loaded.objects


def test_a_val_instance_cycle_is_rejected_at_load(tmp_path: Path) -> None:
    # A $val cycle (A -> B -> A) would only blow up at run time; validate must
    # catch it statically instead of reporting a false green.
    _write(
        tmp_path,
        "a.yaml",
        "apiVersion: comparo/v1\nkind: Instance\n"
        "metadata: {name: A, id: instance.a}\nspec: {value: {$val: instance.b}}\n",
    )
    _write(
        tmp_path,
        "b.yaml",
        "apiVersion: comparo/v1\nkind: Instance\n"
        "metadata: {name: B, id: instance.b}\nspec: {value: {$val: instance.a}}\n",
    )
    with pytest.raises(LoadError) as caught:
        load_project(tmp_path)
    assert any("$val cycle" in d.message for d in caught.value.diagnostics)


def test_a_non_cyclic_val_chain_loads(tmp_path: Path) -> None:
    # A -> B (no back edge) is fine; the cycle check must not flag a plain chain.
    _write(
        tmp_path,
        "a.yaml",
        "apiVersion: comparo/v1\nkind: Instance\n"
        "metadata: {name: A, id: instance.a}\nspec: {value: {$val: instance.b}}\n",
    )
    _write(
        tmp_path,
        "b.yaml",
        "apiVersion: comparo/v1\nkind: Instance\n"
        "metadata: {name: B, id: instance.b}\nspec: {value: 42}\n",
    )
    loaded = load_project(tmp_path)
    assert "instance.a" in loaded.objects
