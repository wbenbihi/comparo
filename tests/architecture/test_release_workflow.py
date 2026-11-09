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


@pytest.mark.parametrize("mode", ["missing-credential", "success", "rejected-push"])
def test_release_push_is_authenticated_atomic_and_fails_closed(tmp_path: Path, mode: str) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("gh", "git"):
        executable = commands / name
        executable.write_text(
            "#!/bin/sh\nset -eu\n"
            f'printf \'{name} %s\\n\' "$*" >> "$TRANSPORT_LOG"\n'
            + ('test "$REJECT_PUSH" != true\n' if name == "git" else "")
        )
        executable.chmod(0o700)
    log = tmp_path / "transport.log"
    environment = {
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "GH_TOKEN": "" if mode == "missing-credential" else "test-credential-canary",
        "RELEASE_TAG": "v0.3.0",
        "GITHUB_REF_NAME": "main",
        "TRANSPORT_LOG": str(log),
        "REJECT_PUSH": str(mode == "rejected-push").lower(),
    }
    command = ["bash", "-e", "-c", _step_script("Publish verified commit and tag atomically")]
    if mode == "success":
        result = _run(command, tmp_path, environment)
    else:
        with pytest.raises(subprocess.CalledProcessError) as failure:
            _run(command, tmp_path, environment)
        result = subprocess.CompletedProcess(
            command, failure.value.returncode, failure.value.stdout, failure.value.stderr
        )
    assert "test-credential-canary" not in result.stdout + result.stderr
    if mode == "missing-credential":
        assert not log.exists()
        assert "RELEASE_TOKEN is required" in result.stderr
    else:
        assert log.read_text().splitlines() == [
            "gh auth setup-git --hostname github.com",
            "git push --atomic origin HEAD:refs/heads/main refs/tags/v0.3.0:refs/tags/v0.3.0",
        ]


def test_publishing_credential_is_scoped_to_verified_push_and_release_cannot_loop() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/release.yml").read_text())
    job = workflow["jobs"]["release"]
    steps = job["steps"]
    push = next(
        step for step in steps if step.get("name") == "Publish verified commit and tag atomically"
    )
    smoke = next(step for step in steps if step.get("name") == "Smoke test installed release wheel")
    assert steps.index(push) > steps.index(smoke)
    assert (
        push["if"]
        == "steps.plan.outputs.released == 'true' && steps.plan.outputs.recovery != 'true'"
    )
    assert push["env"]["GH_TOKEN"] == "${{ secrets.RELEASE_TOKEN }}"
    assert steps[0]["with"]["persist-credentials"] is False
    assert "RELEASE_TOKEN" not in str(job.get("env", {}))
    assert all("secrets.RELEASE_TOKEN" not in str(step) for step in steps if step is not push)
    assert (
        "!contains(github.event.head_commit.message, "
        "'Automatically generated by python-semantic-release')" in job["if"]
    )
    assert workflow["jobs"]["publish"]["permissions"] == {"contents": "read", "id-token": "write"}


def test_every_supported_python_gates_the_exact_release_candidate_before_publication() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/release.yml").read_text())
    ci = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["release"]
    assert not job.get("continue-on-error", False)
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    staged = names.index("Stage version, changelog, tag, and distributions locally")
    publication = names.index("Publish verified commit and tag atomically")
    for version in ci["jobs"]["compatibility"]["strategy"]["matrix"]["python-version"]:
        gate = names.index(f"Verify release candidate on Python {version}")
        assert staged < gate < publication
        step = steps[gate]
        assert step["if"] == "steps.plan.outputs.released == 'true'"
        assert not step.get("continue-on-error", False)
        assert "python -m pytest" in step["run"]
        assert "--frozen" in step["run"]
        assert (
            step.get("env", {}).get("UV_PYTHON") == version or f"--python {version} " in step["run"]
        )
    assert publication < names.index("Create or repair the verified GitHub release")
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "release"
    assert publish["if"] == "needs.release.outputs.released == 'true'"


@pytest.mark.parametrize("failure", ["3.13", "3.14", "none"])
def test_release_candidate_test_failure_prevents_publication(tmp_path: Path, failure: str) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    uv = commands / "uv"
    uv.write_text(
        '#!/bin/sh\nset -eu\nprintf "%s\\n" "$*" >> "$VALIDATION_LOG"\n'
        'case "$*" in\n'
        '  *"--python 3.14 python -m pytest"*) test "$FAILURE" != 3.14 ;;\n'
        '  *"python -m pytest --cov"*) test "$FAILURE" != 3.13 ;;\n'
        "esac\n"
    )
    uv.chmod(0o700)
    log = tmp_path / "validation.log"
    environment = {
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "VALIDATION_LOG": str(log),
        "FAILURE": failure,
    }
    script = "\n".join(
        [
            _step_script("Verify release candidate on Python 3.13"),
            _step_script("Verify release candidate on Python 3.14"),
            'printf "publication\\n" >> "$VALIDATION_LOG"',
        ]
    )
    if failure == "none":
        _run(["bash", "-e", "-c", script], tmp_path, environment)
    else:
        with pytest.raises(subprocess.CalledProcessError):
            _run(["bash", "-e", "-c", script], tmp_path, environment)
    calls = log.read_text().splitlines()
    assert ("publication" in calls) is (failure == "none")
    assert any("--python 3.14" in call for call in calls) is (failure != "3.13")
