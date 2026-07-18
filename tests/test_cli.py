"""Smoke tests for the CLI entry point."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from comparo import __version__
from comparo.cli.app import app

runner = CliRunner()

SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"


def test_version_flag_reports_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_flag_shows_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "HTTP regression" in result.stdout


def test_help_command_lists_the_commands() -> None:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    for command in ("init", "validate", "run", "diff", "tui"):
        assert command in result.stdout


def test_validate_accepts_a_config_directory() -> None:
    result = runner.invoke(app, ["validate", "--config", str(SAMPLE)])
    assert result.exit_code == 0
    assert "valid" in result.stdout


def test_validate_missing_config_points_at_init(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1
    assert "comparo init" in result.output


CANARY = Path(__file__).parent.parent / "examples" / "canary-project"


def test_exec_exit_code_matches_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # The CI contract: `comparo exec` exits 0 iff the gate passes, 1 otherwise.
    import comparo.cli.app as cli
    from comparo.core.execution import CellOutcome
    from comparo.core.execution import ExecutionResult

    async def fake(passed: bool) -> ExecutionResult:
        outcome = CellOutcome("request.x", "", [], [], None, None if passed else "boom")
        return ExecutionResult("exec.release-gate", "Stable", "Canary", True, True, [outcome])

    for passes, expected in ((True, 0), (False, 1)):
        monkeypatch.setattr(cli, "_run_execution", lambda _l, _p, ok=passes: fake(ok))
        invoked = runner.invoke(app, ["exec", "execution.release-gate", "--config", str(CANARY)])
        assert invoked.exit_code == expected


def test_exec_unknown_profile_exits_one() -> None:
    invoked = runner.invoke(app, ["exec", "execution.nope", "--config", str(CANARY)])
    assert invoked.exit_code == 1
    assert "no ExecutionProfile" in invoked.output


def test_diff_exit_code_matches_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    import comparo.cli.app as cli
    from comparo.core.compare import CellDiff
    from comparo.core.diff import FieldDiff
    from comparo.core.diff import State
    from comparo.core.loader import load_project
    from comparo.core.models import Request

    loaded = load_project(CANARY)
    request = next(o for o in loaded.objects.values() if isinstance(o, Request))

    async def fake(drift: bool) -> list[CellDiff]:
        fields = [FieldDiff("$.x", State.DRIFT if drift else State.SAME, "exact")]
        return [CellDiff(request, "", fields)]

    for drifts, expected in ((False, 0), (True, 1)):
        monkeypatch.setattr(cli, "_diff", lambda *_a, d=drifts, **_k: fake(d))
        invoked = runner.invoke(app, ["diff", "--config", str(CANARY)])
        assert invoked.exit_code == expected


def test_init_scaffolds_a_loadable_project(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(app, ["init", str(project), "--name", "Demo API"])
    assert result.exit_code == 0
    manifest = project / "comparo.yaml"
    assert manifest.exists()
    assert (project / ".comparo" / "environments" / "local.yaml").exists()
    assert (project / ".comparo" / "requests" / "example.yaml").exists()
    # The scaffold loads and validates via the manifest (file mode + spec.data).
    validated = runner.invoke(app, ["validate", "--config", str(manifest)])
    assert validated.exit_code == 0
    assert "valid" in validated.stdout


def test_init_refuses_to_overwrite_existing(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    runner.invoke(app, ["init", str(project), "--name", "Demo"])
    again = runner.invoke(app, ["init", str(project), "--name", "Demo"])
    assert again.exit_code == 1
    assert "already exists" in again.output


def test_init_prompts_for_a_name_when_omitted(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    result = runner.invoke(app, ["init", str(project)], input="Prompted Name\n")
    assert result.exit_code == 0
    assert "Prompted Name" in (project / "comparo.yaml").read_text(encoding="utf-8")


def test_bare_invocation_without_a_config_is_friendly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # empty dir — no comparo.yaml to find
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "comparo init" in result.output
