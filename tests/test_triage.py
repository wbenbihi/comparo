"""Tests for silencing a drift into a diff profile file."""

from pathlib import Path

from comparo.core.loader import load_project
from comparo.core.triage import TriageError
from comparo.core.triage import silence


def _project(tmp_path: Path) -> Path:
    (tmp_path / "profile.yaml").write_text(
        "apiVersion: comparo/v1\n"
        "kind: DiffProfile\n"
        "metadata:\n  name: Lenient\n  id: diff.lenient\n"
        "spec:\n"
        "  default: shape\n"
        "  rules:\n"
        "    - path: $.origin\n      mode: ignore  # volatile\n",
        encoding="utf-8",
    )
    return tmp_path


def test_silence_appends_a_rule_and_preserves_comments(tmp_path: Path) -> None:
    root = _project(tmp_path)
    project = load_project(root)

    written = silence(project, "diff.lenient", "$.headers")

    text = written.read_text(encoding="utf-8")
    assert "$.headers" in text
    assert "# volatile" in text  # ruamel round-trip preserved the comment
    # The rule is loadable and now carries two rules.
    reloaded = load_project(root)
    profile = reloaded.objects["diff.lenient"]
    rules = profile.spec.rules  # type: ignore[union-attr]
    assert rules is not None
    assert len(rules) == 2


def test_silence_is_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path)
    project = load_project(root)
    silence(project, "diff.lenient", "$.headers")
    silence(load_project(root), "diff.lenient", "$.headers")
    profile = load_project(root).objects["diff.lenient"]
    paths = [rule.path for rule in profile.spec.rules]  # type: ignore[union-attr]
    assert paths.count("$.headers") == 1


def test_silence_unknown_profile_raises(tmp_path: Path) -> None:
    project = load_project(_project(tmp_path))
    try:
        silence(project, "diff.missing", "$.x")
    except TriageError:
        return
    raise AssertionError("expected TriageError")
