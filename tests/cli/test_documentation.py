"""Versioned documentation rendering, executable examples, and skill installation."""

import json
import re
from io import StringIO
from pathlib import Path
from typing import override

import pytest
import yaml

from comparo.cli import main
from comparo.cli.documentation import TOPICS
from comparo.cli.documentation import skill_markdown
from comparo.cli.documentation import topic_markdown
from comparo.configuration.loader import compile_project_path


class _Terminal(StringIO):
    @override
    def isatty(self) -> bool:
        return True


def _invoke(*arguments: str, terminal: bool = False) -> tuple[int, str, str]:
    stdout = _Terminal() if terminal else StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_docs_index_lists_versioned_retrievable_topics() -> None:
    code, stdout, stderr = _invoke("docs", "--format", "json")
    assert code == 0
    assert stderr == ""
    index = json.loads(stdout)
    assert index["version"]
    assert {item["topic"] for item in index["topics"]} == {item[0] for item in TOPICS}
    for entry in index["topics"]:
        code, stdout, stderr = _invoke("docs", entry["topic"], "--format", "json")
        assert code == 0
        assert stderr == ""
        topic = json.loads(stdout)
        assert topic["version"] == index["version"]
        assert topic["markdown"] == topic_markdown(entry["topic"])


@pytest.mark.parametrize("color", ["auto", "always", "never"])
def test_redirected_documentation_preserves_exact_markdown(color: str) -> None:
    code, stdout, stderr = _invoke("docs", "authentication", "--color", color)
    assert code == 0
    assert stderr == ""
    assert stdout == topic_markdown("authentication")
    assert "\x1b" not in stdout


@pytest.mark.parametrize("output_format", ["markdown", "json"])
def test_explicit_raw_document_formats_never_render_even_in_terminal(output_format: str) -> None:
    code, stdout, stderr = _invoke(
        "docs", "requests", "--format", output_format, "--color", "always", terminal=True
    )
    assert code == 0
    assert stderr == ""
    assert "\x1b" not in stdout
    if output_format == "json":
        assert json.loads(stdout)["markdown"] == topic_markdown("requests")
    else:
        assert stdout == topic_markdown("requests")


@pytest.mark.parametrize("color", ["always", "never"])
def test_terminal_docs_render_markdown_without_losing_example_content(color: str) -> None:
    code, stdout, stderr = _invoke("docs", "authentication", "--color", color, terminal=True)
    assert code == 0
    assert stderr == ""
    assert ("\x1b" in stdout) is (color == "always")
    visible = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
    assert "```yaml" not in visible
    assert "API_TOKEN" in visible
    assert "kind: Request" in visible
    assert "comparo run --subject account --env baseline" in visible


def test_unknown_topic_has_a_machine_readable_error_without_path_access() -> None:
    code, stdout, stderr = _invoke("docs", "../../pyproject.toml", "--format", "json")
    assert code == 2
    assert stderr == ""
    assert json.loads(stdout)["status"] == "unknown-topic"


@pytest.mark.parametrize("topic", [item[0] for item in TOPICS])
def test_all_published_yaml_examples_compile_in_a_starter_project(
    tmp_path: Path, topic: str
) -> None:
    code, stdout, stderr = _invoke("init", str(tmp_path), "--layout", "split")
    assert code == 0, (stdout, stderr)
    snippets = re.findall(r"```yaml\n(.*?)\n```", topic_markdown(topic), flags=re.DOTALL)
    for index, snippet in enumerate(snippets):
        docs = list(yaml.safe_load_all(snippet))
        if docs[0]["kind"] == "Project":
            destination = tmp_path / "comparo.yaml"
        else:
            destination = tmp_path / ".comparo" / f"example-{index}.yaml"
        destination.write_text(snippet + "\n")
    compiled = compile_project_path(tmp_path).compilation
    assert compiled.project is not None, compiled.diagnostics


def test_skill_preview_install_and_idempotence(tmp_path: Path) -> None:
    assert _invoke("skill", "show")[1] == skill_markdown()
    code, stdout, stderr = _invoke("skill", "install", str(tmp_path), "--format", "json")
    assert code == 0
    assert stderr == ""
    destination = Path(json.loads(stdout)["path"])
    assert destination == tmp_path / ".agents" / "skills" / "comparo" / "SKILL.md"
    assert destination.read_text() == skill_markdown()
    assert _invoke("skill", "install", str(tmp_path))[0] == 0
    destination.write_text("user-authored skill")
    assert _invoke("skill", "install", str(tmp_path))[0] == 2
    assert destination.read_text() == "user-authored skill"
    assert not (tmp_path / "AGENTS.md").exists()


def test_personal_skill_installs_under_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _invoke("skill", "install", "--user")[0] == 0
    assert (tmp_path / ".agents/skills/comparo/SKILL.md").is_file()


def test_skill_refuses_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("keep")
    skill = tmp_path / ".agents/skills/comparo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.symlink_to(target)
    assert _invoke("skill", "install", str(tmp_path))[0] == 2
    assert target.read_text() == "keep"
