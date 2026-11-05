"""Managed completion and real-shell checks of offline project suggestions."""

import json
import os
import shlex
import shutil
import subprocess
from io import StringIO
from pathlib import Path

import pytest

from comparo.cli import completion
from comparo.cli import main
from comparo.cli.completion import completion_paths
from comparo.cli.completion_candidates import candidates
from comparo.cli.completion_scripts import MARKER
from comparo.cli.onboarding import publish_text

_ROOT = Path(__file__).resolve().parents[2]


def _invoke(*arguments: str) -> tuple[int, str, str]:
    stdout, stderr = StringIO(), StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def shell_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", f"{_ROOT / '.venv/bin'}:{os.environ['PATH']}")
    for name in ("ZDOTDIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "ZSH", "ZSH_CUSTOM"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
def test_completion_install_status_reinstall_uninstall(shell_home: Path, shell: str) -> None:
    paths = completion_paths(shell)
    initial = "# user settings\nexport EXAMPLE=value\n"
    if paths.startup is not None:
        paths.startup.write_text(initial)
    assert _invoke("completion", "status", shell)[0] == 2
    code, stdout, stderr = _invoke("completion", "install", shell, "--format", "json")
    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)["status"] == "installed"
    assert paths.script.read_text().startswith(MARKER)
    original_script = paths.script.read_bytes()
    assert _invoke("completion", "install", shell)[0] == 0
    assert paths.script.read_bytes() == original_script
    code, stdout, stderr = _invoke("completion", "status", shell, "--format", "json")
    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)["status"] == "ready"
    if paths.startup is not None:
        assert paths.startup.read_text().startswith(initial)
        assert paths.startup.read_text().count("# >>> comparo completion >>>") == 1
    assert _invoke("completion", "uninstall", shell)[0] == 0
    assert not paths.script.exists()
    if paths.startup is not None:
        assert paths.startup.read_text() == initial
    assert _invoke("completion", "uninstall", shell)[0] == 0


def test_completion_respects_custom_paths_and_quotes_them(
    shell_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = shell_home / "data with 'quotes' and $(commands)"
    zdot = shell_home / "zsh settings"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("ZDOTDIR", str(zdot))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(shell_home / "config"))
    assert _invoke("completion", "install")[0] == 0
    paths = completion_paths("zsh")
    assert paths.script.is_relative_to(data)
    assert paths.startup == zdot / ".zshrc"
    startup = paths.startup.read_text()
    assert shlex.quote(str(paths.script)) in startup
    assert completion_paths("fish").script == shell_home / "config/fish/completions/comparo.fish"
    assert not (shell_home / ".zshrc").exists()
    _run_shell(
        "zsh", f"source {shlex.quote(str(paths.startup))}; print -r -- $_comps[comparo]", shell_home
    )


def test_completion_preserves_startup_symlink(shell_home: Path) -> None:
    actual = shell_home / "dotfiles" / "zshrc"
    actual.parent.mkdir()
    actual.write_text("# custom\n")
    (shell_home / ".zshrc").symlink_to(actual)
    assert _invoke("completion", "install", "zsh")[0] == 0
    assert (shell_home / ".zshrc").is_symlink()
    assert _invoke("completion", "uninstall", "zsh")[0] == 0
    assert actual.read_text() == "# custom\n"


@pytest.mark.parametrize("conflict", ["script", "block", "symlink"])
def test_completion_refuses_unowned_or_ambiguous_configuration(
    shell_home: Path, conflict: str
) -> None:
    paths = completion_paths("zsh")
    assert paths.startup is not None
    paths.script.parent.mkdir(parents=True)
    if conflict == "script":
        paths.script.write_text("user-managed completion")
    elif conflict == "symlink":
        paths.script.symlink_to(shell_home / "missing")
    else:
        paths.startup.write_text("# >>> comparo completion >>>\n# incomplete\n")
    before = {path: path.read_bytes() for path in shell_home.rglob("*") if path.is_file()}
    for action in ("install", "uninstall"):
        assert _invoke("completion", action, "zsh")[0] == 2
    assert {path: path.read_bytes() for path in before} == before


def test_failed_startup_update_restores_previous_script(
    shell_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = completion_paths("zsh")
    paths.script.parent.mkdir(parents=True)
    paths.script.write_text(MARKER + "\n# previous version\n")
    previous = paths.script.read_bytes()

    def fail_startup(destination: Path, content: str, *, replace: bool = False) -> None:
        if destination.name == ".zshrc":
            raise PermissionError
        publish_text(destination, content, replace=replace)

    monkeypatch.setattr(completion, "publish_text", fail_startup)
    assert _invoke("completion", "install", "zsh")[0] == 2
    assert paths.script.read_bytes() == previous


def test_unsupported_login_shell_is_actionable(
    shell_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL", "/bin/unknown")
    code, stdout, stderr = _invoke("completion", "install", "--format", "json")
    assert code == 2
    assert stderr == ""
    assert json.loads(stdout)["status"] == "configuration-conflict"


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (("diff", "--baseline", "b"), ("baseline",)),
        (("run", "--env", ""), ("baseline", "candidate")),
        (("run", "--environment", "c"), ("candidate",)),
        (("diff", "--candidate=c"), ("candidate",)),
        (("diff", "--candidate", "=", "c"), ("candidate",)),
        (("diff", "--candidate", "="), ("baseline", "candidate")),
        (("plan", "--subject", ""), ("health",)),
        (("render", "--profile", "c"), ("compare-health",)),
        (("exec", ""), ("compare-health",)),
        (("exec", "--trials", "2", "c"), ("compare-health",)),
        (("exec", "compare-health", ""), ()),
        (("run", "--", ""), ()),
        (("diff", "--help", ""), ()),
    ],
)
def test_candidates_use_project_identities_without_resolving_inputs(
    shell_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert _invoke("init", str(shell_home))[0] == 0
    manifest = shell_home / "comparo.yaml"
    manifest.write_text(
        manifest.read_text().replace(
            "Accept: application/json", "Authorization: {$env: NEVER_RESOLVE_ME}"
        )
    )
    monkeypatch.chdir(shell_home)
    assert candidates(("comparo", *suffix)) == expected


@pytest.mark.parametrize("config_option", [("--config",), ("-C",), ("--config=",)])
def test_candidates_respect_config_and_additional_sources(
    shell_home: Path, config_option: tuple[str, ...]
) -> None:
    project = shell_home / "api"
    assert _invoke("init", str(project))[0] == 0
    extra = project / "additional.yaml"
    extra.write_text(
        "apiVersion: comparo/v1alpha1\nkind: Environment\n"
        "metadata: {id: staging, name: Staging}\nspec: {baseUrl: https://example.test}\n"
    )
    option = config_option[0]
    config = (option + str(project),) if option.endswith("=") else (option, str(project))
    assert candidates(("comparo", "diff", *config, "--source", str(extra), "--baseline", "s")) == (
        "staging",
    )
    assert candidates(("comparo", "exec", *config, "c")) == ("compare-health",)


def test_missing_and_invalid_projects_yield_no_candidates(
    shell_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(shell_home)
    assert candidates(("comparo", "diff", "--baseline", "")) == ()
    (shell_home / "comparo.yaml").write_text("invalid: [")
    code, stdout, stderr = _invoke("_complete", "--", "comparo", "diff", "--baseline", "")
    assert code == 0
    assert stdout.strip() == ""
    assert stderr == ""
    assert candidates(("comparo", "docs", "auth")) == ("authentication",)


def _run_shell(shell: str, script: str, directory: Path) -> str:
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    result = subprocess.run(  # noqa: S603 - fixed local shell with test-owned commands and home
        [executable, "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout


@pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (("diff", "--baseline", "b"), ("baseline",)),
        (("diff", "--baseline", ""), ("baseline", "candidate")),
        (("run", "--subject", "h"), ("health",)),
        (("exec", "c"), ("compare-health",)),
        (("docs", "auth"), ("authentication",)),
    ],
)
def test_generated_scripts_complete_project_values_in_real_shells(
    shell_home: Path, shell: str, words: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    assert _invoke("init", str(shell_home))[0] == 0
    generated = _invoke("completion", shell)[1]
    script = shell_home / "generated-completion"
    script.write_text(generated)
    load = f"source {shlex.quote(str(script))}\n"
    tokens = ("comparo", *words)
    arguments = shlex.join(tokens)
    if shell == "bash":
        body = f"COMP_WORDS=({arguments}); COMP_CWORD={len(tokens) - 1}; _comparo; "
        body += "printf '%s\\n' \"${COMPREPLY[@]}\""
    elif shell == "zsh":
        body = 'compadd() { shift; print -rl -- "$@"; }; '
        body += f"words=({arguments}); CURRENT={len(tokens)}; _comparo"
    else:
        commandline = shlex.join(tokens[:-1]) + " " + tokens[-1]
        body = f"complete -C {shlex.quote(commandline)}"
    output = _run_shell(shell, load + body, shell_home)
    assert set(expected) <= set(output.splitlines())
    if words[-1]:
        assert all(item.startswith(words[-1]) for item in output.splitlines())


def test_zsh_installed_startup_registers_comparo_without_private_manual_steps(
    shell_home: Path,
) -> None:
    assert _invoke("completion", "install", "zsh")[0] == 0
    startup = shell_home / ".zshrc"
    output = _run_shell(
        "zsh", f"source {shlex.quote(str(startup))}; print -r -- $_comps[comparo]", shell_home
    )
    assert output.strip() == "_comparo"
