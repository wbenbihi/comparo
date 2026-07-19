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
    request = project / ".comparo" / "requests" / "example.yaml"
    assert (project / ".comparo" / "environments" / "local.yaml").exists()
    assert request.exists()
    # The starter files carry the schema modeline for editor autocomplete.
    assert request.read_text().startswith("# yaml-language-server: $schema=")
    # An agent-authoring guide is dropped so coding agents are competent here.
    agents = project / ".comparo" / "AGENTS.md"
    assert agents.exists()
    guide = agents.read_text()
    assert "comparo validate" in guide
    assert "$secret" in guide
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


# ── Phase 5: manifest-driven selection and the redaction backstop toggle ──


def test_manifest_selection_filters_the_default_request_set(tmp_path: Path) -> None:
    from comparo.cli.app import _select_requests
    from comparo.core.loader import load_project

    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\nmetadata: {name: P, id: project.p}\n"
        "spec:\n  data: .\n  environments:\n    default: local\n"
        "  selection:\n    tags:\n      - smoke\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: local, id: environment.local}\n"
        "spec: {baseUrl: 'http://127.0.0.1:1'}\n",
        encoding="utf-8",
    )
    (tmp_path / "a.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: a, id: request.a, tags: [smoke]}\n"
        "spec: {request: {method: GET, endpoint: /a}}\n",
        encoding="utf-8",
    )
    (tmp_path / "b.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata: {name: b, id: request.b, tags: [nightly]}\n"
        "spec: {request: {method: GET, endpoint: /b}}\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path / "comparo.yaml")
    assert [r.metadata.id for r in _select_requests(loaded, None)] == ["request.a"]


def test_redaction_backstop_is_an_unconditional_floor(tmp_path: Path) -> None:
    # Security floor: stringMatchBackstop:false must NOT disable masking — a
    # persisted sink would otherwise write a server-echoed secret to disk.
    from comparo.core.loader import load_project
    from comparo.core.redaction import Redactor

    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\nmetadata: {name: P, id: project.p}\n"
        "spec:\n  data: .\n  redaction:\n    stringMatchBackstop: false\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: local, id: environment.local}\n"
        "spec:\n  baseUrl: http://127.0.0.1:1\n"
        "  secrets:\n    T:\n      from:\n        - $literal: SUPERSECRETVALUE\n",
        encoding="utf-8",
    )
    loaded = load_project(tmp_path / "comparo.yaml")
    # Even with the toggle off, the redactor still masks (the floor holds).
    assert Redactor.for_project(loaded).text("echo=SUPERSECRETVALUE") == "echo=••••••"


def test_validate_fails_on_a_manifest_with_no_objects(tmp_path: Path) -> None:
    # A manifest whose data dir has no objects must not report a green '0 valid'.
    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\nmetadata: {name: P, id: project.p}\n"
        "spec: {data: nonexistent}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", "--config", str(tmp_path / "comparo.yaml")])
    assert result.exit_code == 1
    assert "no objects found" in result.output


def test_diff_rejects_an_unknown_report_format() -> None:
    result = runner.invoke(
        app, ["diff", "--config", str(SAMPLE), "--pair", "local-vs-prod", "--report", "junitxml"]
    )
    assert result.exit_code == 1
    assert "unknown report format" in result.output


def test_run_fails_closed_when_the_plan_expands_to_zero_cells(tmp_path: Path) -> None:
    # M-1: a run whose selected request expands to zero matrix cells verified
    # nothing — it must exit non-zero, never report a green gate.
    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\n"
        "metadata: {name: P, id: project.p}\nspec: {data: .}\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: Local, id: environment.local}\nspec: {baseUrl: 'http://localhost'}\n",
        encoding="utf-8",
    )
    (tmp_path / "matrix.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Matrix\n"
        "metadata: {name: Empty, id: matrix.empty}\nspec: {target: request.query, values: []}\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: R, id: request.r}\n"
        "spec:\n  matrix:\n    - $ref: matrix.empty\n  request: {method: GET, endpoint: /x}\n",
        encoding="utf-8",
    )
    config = str(tmp_path / "comparo.yaml")
    result = runner.invoke(app, ["run", "--config", config, "--env", "local"])
    assert result.exit_code == 1
    assert "zero cells" in result.output


def test_render_reports_an_unresolved_variable_cleanly(tmp_path: Path) -> None:
    # M-3: `comparo render` used to crash with a traceback when a required ${VAR}
    # was unset; it must surface a clean error and exit non-zero instead.
    (tmp_path / "comparo.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Project\n"
        "metadata: {name: P, id: project.p}\nspec: {data: .}\n",
        encoding="utf-8",
    )
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: Local, id: environment.local}\nspec: {baseUrl: 'http://h'}\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\nmetadata: {name: R, id: request.r}\n"
        "spec: {request: {method: GET, endpoint: '/x/${MISSING_VAR}'}}\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["render", "request.r", "--config", str(tmp_path / "comparo.yaml"), "--env", "local"]
    )
    assert result.exit_code == 1
    assert "could not resolve" in result.output
    assert "MISSING_VAR" in result.output
