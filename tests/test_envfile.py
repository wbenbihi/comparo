"""Tests for the env-file overlay that backs the ``$env`` directive.

An ``Environment``'s ``envFile`` (and the CLI ``--env-file``) provide a
``KEY=VALUE`` overlay consulted only by ``$env``, ahead of ``os.environ``.
Nothing is auto-injected, and every value the file supplies is masked so it
never reaches a display, a report, or an export.
"""

from pathlib import Path

import pytest

from comparo.core.envfile import env_file_values
from comparo.core.envfile import load_env_overlay
from comparo.core.envfile import parse_env_file
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import EnvironmentSpec
from comparo.core.models import Meta
from comparo.core.models import Request
from comparo.core.provenance import Origin
from comparo.core.redaction import MASK
from comparo.core.redaction import Redactor
from comparo.core.resolution import Context
from comparo.core.resolution import SecretError
from comparo.core.resolution import resolve_source
from comparo.core.resolution import resolve_value
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink


def _env(
    *, env_file: str | None = None, secrets: dict[str, object] | None = None, name: str = "Local"
) -> Environment:
    return Environment(
        api_version="comparo/v1",
        metadata=Meta(name=name, id="environment.local"),
        spec=EnvironmentSpec(base_url="http://x", env_file=env_file, secrets=secrets),
    )


def _project(env: Environment, root: Path) -> LoadedProject:
    return LoadedProject(root=root, project=None, objects={"environment.local": env})


# ── parser ──────────────────────────────────────────────────────────────────


def test_parse_basic_pairs_comments_and_blanks() -> None:
    text = "# a comment\nAPI_TOKEN=sk-live-abc\n\n  LOG_LEVEL = debug \n"
    assert parse_env_file(text) == {"API_TOKEN": "sk-live-abc", "LOG_LEVEL": "debug"}


def test_parse_strips_surrounding_quotes_without_unescaping() -> None:
    text = "A=\"has spaces\"\nB='single'\nC=no#quote\nD=raw\\nbytes"
    assert parse_env_file(text) == {
        "A": "has spaces",
        "B": "single",
        "C": "no#quote",  # an inline # is NOT a comment — it is kept
        "D": "raw\\nbytes",  # no escape expansion
    }


def test_parse_tolerates_export_prefix_and_empty_value() -> None:
    assert parse_env_file("export TOKEN=abc\nEMPTY=") == {"TOKEN": "abc", "EMPTY": ""}


def test_parse_skips_malformed_lines_and_last_duplicate_wins() -> None:
    assert parse_env_file("no_equals_here\n=novalue\nK=1\nK=2") == {"K": "2"}


def test_parse_does_no_variable_expansion() -> None:
    # ``${A}`` is comparo's own interpolation grammar — a literal here, never expanded.
    assert parse_env_file("A=hello\nB=${A}-x") == {"A": "hello", "B": "${A}-x"}


# ── load_env_overlay ─────────────────────────────────────────────────────────


def test_overlay_reads_the_declared_file_relative_to_root(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_TOKEN=from-file\n", encoding="utf-8")
    assert load_env_overlay(_env(env_file=".env"), tmp_path) == {"API_TOKEN": "from-file"}


def test_overlay_missing_declared_file_is_benign(tmp_path: Path) -> None:
    assert load_env_overlay(_env(env_file="absent.env"), tmp_path) == {}


def test_overlay_unreadable_file_raises_unless_best_effort(tmp_path: Path) -> None:
    (tmp_path / "secret.env").mkdir()  # a directory in the file's place → anomalous read
    env = _env(env_file="secret.env")
    with pytest.raises(SecretError):
        load_env_overlay(env, tmp_path)
    assert load_env_overlay(env, tmp_path, best_effort=True) == {}


def test_overlay_escaping_path_raises(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "outside.env").write_text("X=leak\n", encoding="utf-8")
    with pytest.raises(SecretError):
        load_env_overlay(_env(env_file="../outside.env"), root)


def test_overlay_cli_env_wins_per_key(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\nB=2\n", encoding="utf-8")
    overlay = load_env_overlay(_env(env_file=".env"), tmp_path, cli_env={"B": "9", "C": "3"})
    assert overlay == {"A": "1", "B": "9", "C": "3"}


def test_overlay_cli_env_applies_without_a_declared_file(tmp_path: Path) -> None:
    assert load_env_overlay(_env(env_file=None), tmp_path, cli_env={"K": "v"}) == {"K": "v"}


# ── env-file-wins precedence over os.environ ─────────────────────────────────


def test_env_source_prefers_the_overlay_over_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEY", "from-shell")
    value, origin = resolve_source({"$env": "KEY"}, None, env={"KEY": "from-file"})
    assert value == "from-file"
    assert origin is Origin.ENV


def test_env_source_falls_back_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONLY_SHELL", "from-shell")
    value, _ = resolve_source({"$env": "ONLY_SHELL"}, None, env={"OTHER": "x"})
    assert value == "from-shell"


def test_inline_env_resolves_from_the_context_overlay() -> None:
    context = Context(
        variables={}, secret_names=frozenset(), mask_secrets=False, env={"PORT": "9000"}
    )
    value, trail = resolve_value({"$env": "PORT"}, context)
    assert value == "9000"
    assert trail[0].origin is Origin.ENV


# ── env_file_values (the redactor floor) ─────────────────────────────────────


def test_env_file_values_collects_nonempty_across_the_project(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=secret-a\nB=\n", encoding="utf-8")
    project = _project(_env(env_file=".env"), tmp_path)
    assert env_file_values(project) == {"secret-a"}  # the empty B is skipped


def test_env_file_values_includes_cli_env(tmp_path: Path) -> None:
    project = _project(_env(env_file=None), tmp_path)
    assert env_file_values(project, cli_env={"K": "cli-secret"}) == {"cli-secret"}


# ── masking: no env-file value ever appears ──────────────────────────────────


def test_redactor_masks_every_env_file_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_TOKEN=sk-live-zzz\n", encoding="utf-8")
    project = _project(_env(env_file=".env"), tmp_path)
    redact = Redactor.for_project(project).text
    assert redact("leaked sk-live-zzz here") == f"leaked {MASK} here"


def test_redactor_masks_a_secret_sourced_from_the_env_file(tmp_path: Path) -> None:
    # A declared secret whose $env key lives only in the env file still resolves
    # (env-file-wins) and joins the mask like any other declared secret.
    (tmp_path / ".env").write_text("DB_PASSWORD=p@ss-file\n", encoding="utf-8")
    env = _env(env_file=".env", secrets={"DB": {"$env": "DB_PASSWORD"}})
    redact = Redactor.for_project(_project(env, tmp_path)).text
    assert redact("echo p@ss-file back") == f"echo {MASK} back"


# ── end-to-end through the Resolver ──────────────────────────────────────────


def _project_with_env_request(tmp_path: Path) -> LoadedProject:
    (tmp_path / ".env").write_text("TOKEN=sk-file-secret\n", encoding="utf-8")
    (tmp_path / "env.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Environment\n"
        "metadata: {name: Local, id: environment.local}\n"
        "spec:\n  baseUrl: http://x\n  envFile: .env\n",
        encoding="utf-8",
    )
    (tmp_path / "req.yaml").write_text(
        "apiVersion: comparo/v1\nkind: Request\n"
        "metadata: {name: ping, id: request.ping}\n"
        "spec:\n  request:\n    method: GET\n    endpoint: /p\n"
        "    headers: {Authorization: {$env: TOKEN}}\n",
        encoding="utf-8",
    )
    return load_project(tmp_path)


def test_execute_sink_injects_the_file_value_and_cli_env_overrides(tmp_path: Path) -> None:
    loaded = _project_with_env_request(tmp_path)
    env = loaded.objects["environment.local"]
    request = loaded.objects["request.ping"]
    assert isinstance(env, Environment)
    assert isinstance(request, Request)

    execute = Resolver(loaded, env, Sink.EXECUTE).resolve_request(request)
    assert any(value == "sk-file-secret" for _, value in execute.headers)

    override = Resolver(loaded, env, Sink.EXECUTE, cli_env={"TOKEN": "sk-cli"}).resolve_request(
        request
    )
    assert any(value == "sk-cli" for _, value in override.headers)


def test_display_value_from_the_env_file_is_masked_by_the_redactor(tmp_path: Path) -> None:
    loaded = _project_with_env_request(tmp_path)
    env = loaded.objects["environment.local"]
    request = loaded.objects["request.ping"]
    assert isinstance(env, Environment)
    assert isinstance(request, Request)
    redact = Redactor.for_project(loaded).text
    display = Resolver(loaded, env).resolve_request(request)
    shown = [redact(str(value)) for _, value in display.headers]
    assert MASK in shown
    assert all("sk-file-secret" not in value for value in shown)
