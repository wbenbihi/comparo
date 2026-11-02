"""Release planning uses real semantic-release without corrupting Actions outputs."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]


def _step_script(name: str) -> str:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/release.yml").read_text())
    for step in workflow["jobs"]["release"]["steps"]:
        if step.get("name") == name:
            script = step["run"]
            assert isinstance(script, str)
            return script
    raise AssertionError(f"Missing workflow step: {name}")


def _run(
    command: list[str], root: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed test commands in a temporary repository
        command, cwd=root, env=environment, text=True, capture_output=True, check=True, timeout=30
    )


@pytest.fixture
def release_repository(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "repository"
    root.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    # Run the exact locked semantic-release package; uv itself is not under test.
    uv = commands / "uv"
    uv.write_text(
        '#!/bin/sh\nset -eu\ntest "$1" = run\ntest "$2" = --frozen\n'
        'test "$3" = semantic-release\nshift 3\n'
        'exec "$RELEASE_TEST_PYTHON" -m semantic_release "$@"\n'
    )
    uv.chmod(0o700)
    environment = {
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "RECOVERY_TAG": "",
        "RELEASE_TEST_PYTHON": sys.executable,
    }
    _run(["git", "init", "-q", "-b", "main"], root, environment)
    _run(["git", "config", "user.name", "Release Test"], root, environment)
    _run(["git", "config", "user.email", "release@example.test"], root, environment)
    _run(
        ["git", "remote", "add", "origin", "https://github.com/example/release-test.git"],
        root,
        environment,
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "release-test"\nversion = "0.2.0"\n'
        "[tool.semantic_release]\nallow_zero_version = true\nmajor_on_zero = false\n"
        'version_toml = ["pyproject.toml:project.version"]\n'
    )
    _run(["git", "add", "pyproject.toml"], root, environment)
    _run(["git", "commit", "-qm", "chore: initial version"], root, environment)
    _run(["git", "tag", "v0.2.0"], root, environment)
    return root, environment


@pytest.mark.parametrize("scenario", ["new", "unchanged", "recovery"])
def test_release_plan_owns_outputs_and_never_modifies_source(
    release_repository: tuple[Path, dict[str, str]], scenario: str
) -> None:
    root, environment = release_repository
    if scenario == "new":
        _run(["git", "commit", "--allow-empty", "-qm", "feat: new capability"], root, environment)
    if scenario == "recovery":
        environment["RECOVERY_TAG"] = "v0.2.0"
    head = _run(["git", "rev-parse", "HEAD"], root, environment).stdout
    result = _run(["bash", "-e", "-c", _step_script("Plan semantic release")], root, environment)
    outputs = Path(environment["GITHUB_OUTPUT"]).read_text().splitlines()
    expected = (
        ["released=false", "recovery=false"]
        if scenario == "unchanged"
        else ["released=true", "recovery=true", "tag=v0.2.0"]
        if scenario == "recovery"
        else ["released=true", "recovery=false", "tag=v0.3.0"]
    )
    assert outputs == expected
    if scenario == "new":
        assert "0.2.0 -> 0.3.0 (v0.3.0)" in result.stdout
    assert _run(["git", "rev-parse", "HEAD"], root, environment).stdout == head
    assert _run(["git", "tag", "--list"], root, environment).stdout.strip() == "v0.2.0"
    assert _run(["git", "diff", "HEAD", "--exit-code"], root, environment).stdout == ""
