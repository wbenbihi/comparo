"""Tests for inline-or-$ref attachment resolution."""

from pathlib import Path

from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import DiffProfileSpec
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
    specs = resolve_specs(loaded, {"$ref": "diff.strict"}, DiffProfileSpec)
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
        [{"$ref": "diff.strict"}, {"default": "shape"}],
        DiffProfileSpec,
    )
    assert [spec.default for spec in specs] == ["exact", "shape"]


def test_resolve_skips_unresolvable(tmp_path: Path) -> None:
    loaded = _loaded(tmp_path)
    assert resolve_specs(loaded, None, DiffProfileSpec) == []
    assert resolve_specs(loaded, "nonsense", DiffProfileSpec) == []
    assert resolve_specs(loaded, {"$ref": "diff.missing"}, DiffProfileSpec) == []
