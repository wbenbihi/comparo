"""Tests for inline-or-$use attachment resolution."""

from pathlib import Path

import pytest

from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import DiffProfileSpec
from comparo.core.refs import SpecResolutionError
from comparo.core.refs import resolve_specs


def _loaded(tmp_path: Path) -> LoadedProject:
    (tmp_path / "strict.yaml").write_text(
        "apiVersion: comparo/v1\nkind: DiffProfile\n"
        "metadata:\n  name: Strict\n  id: diff.strict\n"
        "spec:\n  default: exact\n",
        encoding="utf-8",
    )
    return load_project(tmp_path)


def test_resolve_ref(tmp_path: Path) -> None:
    loaded = _loaded(tmp_path)
    specs = resolve_specs(loaded, {"$use": "diff.strict"}, DiffProfileSpec)
    assert len(specs) == 1
    assert specs[0].default == "exact"


def test_resolve_inline(tmp_path: Path) -> None:
    loaded = _loaded(tmp_path)
    inline = {"default": "shape", "rules": [{"path": "$.x", "mode": "ignore"}]}
    specs = resolve_specs(loaded, inline, DiffProfileSpec)
    assert len(specs) == 1
    assert specs[0].default == "shape"
    assert specs[0].rules is not None
    assert specs[0].rules[0].path == "$.x"


def test_resolve_list_mixes_ref_and_inline(tmp_path: Path) -> None:
    loaded = _loaded(tmp_path)
    specs = resolve_specs(
        loaded,
        [{"$use": "diff.strict"}, {"default": "shape"}],
        DiffProfileSpec,
    )
    assert [spec.default for spec in specs] == ["exact", "shape"]


def test_resolve_none_is_empty(tmp_path: Path) -> None:
    # An absent slot is legitimately empty — not an error.
    assert resolve_specs(_loaded(tmp_path), None, DiffProfileSpec) == []


def test_resolve_fails_loud_on_unresolvable(tmp_path: Path) -> None:
    # Every bad shape is a hard error, never a silent empty rule set — an empty
    # rule set passes every gate, so swallowing here would be a false green.
    loaded = _loaded(tmp_path)
    with pytest.raises(SpecResolutionError):
        resolve_specs(loaded, "nonsense", DiffProfileSpec)  # bare string
    with pytest.raises(SpecResolutionError):
        resolve_specs(loaded, {"$use": "diff.missing"}, DiffProfileSpec)  # dangling ref
    with pytest.raises(SpecResolutionError):
        resolve_specs(loaded, {"rule": []}, DiffProfileSpec)  # inline typo of "rules"


def test_bad_inline_profile_fails_the_load(tmp_path: Path) -> None:
    # A request whose inline assert profile has a typo must fail to LOAD, not
    # resolve to zero rules and pass the gate.
    _loaded(tmp_path)  # seed diff.strict
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata:\n  name: Q\n  id: request.q\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /get\n"
        "  response:\n    assert:\n      rulez:\n"  # typo of 'rules'
        "        - target: status\n          op: equals\n          value: 200\n",
        encoding="utf-8",
    )
    from comparo.core.diagnostics import LoadError

    with pytest.raises(LoadError):
        load_project(tmp_path)
