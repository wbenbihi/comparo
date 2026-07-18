"""Console entry point for comparo.

Only wiring lives here — the CLI is a thin front-end that will call the
:mod:`comparo.core` engine. No engine logic belongs in this module.
"""

import asyncio
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from typing import NoReturn

import typer

from comparo import __version__
from comparo.adapters import openapi
from comparo.adapters.httpx_client import HttpxClient
from comparo.adapters.reporters import REPORTERS
from comparo.core.assertions import evaluate_rules
from comparo.core.assertions import passed as assertions_pass
from comparo.core.assertions import request_response_rules
from comparo.core.compare import CellDiff
from comparo.core.compare import diff_run
from comparo.core.diagnostics import LoadError
from comparo.core.execute import Execution
from comparo.core.execute import execute_all
from comparo.core.execution import ExecutionResult
from comparo.core.execution import build_execution_report
from comparo.core.execution import run_execution
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.models import Request
from comparo.core.redaction import Redactor
from comparo.core.report import RunReport
from comparo.core.report import build_report
from comparo.core.report import diff_passed
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import resolve_pair
from comparo.core.resolve import select_environment
from comparo.core.schema import SCHEMA_ID

app = typer.Typer(
    name="comparo",
    help="HTTP regression & diff testing across environments.",
    add_completion=False,
)

DEFAULT_CONFIG = Path("comparo.yaml")

ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        "-C",
        help="The comparo.yaml manifest to load (or a project directory).",
    ),
]

import_app = typer.Typer(
    name="import",
    help="Scaffold a comparo project from an existing API description.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(import_app)


def _version_callback(*, value: bool) -> None:
    """Print the version and exit when the flag is set.

    Args:
        value: Whether the ``--version`` / ``-V`` flag was passed.
    """
    if value:
        typer.echo(f"comparo {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    *,
    config: ConfigOption = DEFAULT_CONFIG,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Replay requests across environments and diff the responses.

    Run ``comparo`` with no command to open the terminal UI on ``./comparo.yaml``,
    or ``comparo --config <manifest>`` to open it on a project elsewhere.
    """
    if ctx.invoked_subcommand is None:
        _launch_tui(config)


@app.command()
def init(
    directory: Annotated[
        Path,
        typer.Argument(help="Where to create the project (default: the current directory)."),
    ] = Path(),
    *,
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Project name; prompted for if omitted."),
    ] = None,
    data: Annotated[
        Path,
        typer.Option("--data", help="Directory the project's objects live in."),
    ] = Path(".comparo"),
    config: Annotated[
        str,
        typer.Option("--config", "-C", help="Filename for the manifest."),
    ] = "comparo.yaml",
    description: Annotated[
        str | None,
        typer.Option("--description", help="A one-line project description."),
    ] = None,
) -> None:
    """Scaffold a new comparo project: a manifest plus a starter data directory.

    Writes ``<config>`` (the manifest) and ``<data>/`` with a sample environment
    and request, so the project validates and runs immediately. Refuses to touch
    an existing manifest or data directory, so it never clobbers your files.

    Args:
        directory: The directory to create the project in.
        name: The project name; if omitted, comparo prompts for it.
        data: The directory objects live in, relative to *directory*.
        config: The manifest filename.
        description: An optional one-line description.
    """
    _scaffold(directory, name, data, config, description)


@import_app.command("openapi")
def import_openapi(
    spec: Annotated[Path, typer.Argument(help="The OpenAPI 3.x document (JSON or YAML).")],
    *,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Directory to create the project in (default: a slug of the project name).",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Project name; taken from info.title when omitted."),
    ] = None,
) -> None:
    """Scaffold a comparo project from an OpenAPI 3.x specification.

    Turns the mechanical parts of a spec into ``comparo/v1`` YAML: ``servers``
    become Environments, operations become Requests, ``components.schemas`` become
    Schema objects, and ``securitySchemes`` become ``$secret``-backed auth stubs.
    It is a *scaffold* — no DiffProfile is generated (which fields are volatile is
    your call) and no real credential is ever written. Refuses to overwrite an
    existing manifest or data directory, like ``comparo init``.

    Args:
        spec: The OpenAPI 3.0/3.1 document to import (JSON or YAML).
        output: Where to create the project; defaults to a slug of the project name.
        name: The project name; taken from ``info.title`` when omitted.
    """
    if not spec.exists():
        _abort(f"no spec at '{spec}' — pass the path to an OpenAPI 3.x document")
    try:
        document = openapi.load_spec(spec.read_text(encoding="utf-8"))
        result = openapi.import_openapi(document, name=name)
    except openapi.OpenApiImportError as error:
        _abort(str(error))
    except OSError as error:
        _abort(f"could not read '{spec}': {error}")
    directory = output if output is not None else Path(_slug(result.project_name))
    _write_openapi_project(directory, result)


@app.command()
def validate(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """Validate a project's envelope, ids, and references.

    Exits non-zero and prints every problem if the project does not load.

    Args:
        config: The manifest (or project directory) to validate.
    """
    loaded = _open_project(config)
    if not loaded.objects:
        typer.secho(
            "no objects found — check that spec.data points at your object files",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    typer.secho(f"✓ {len(loaded.objects)} object(s) valid", fg=typer.colors.GREEN)


@app.command()
def doctor() -> None:
    """Run the never-leak self-check: a canary secret through every sink.

    Sends a known canary secret through every output path — the TUI display,
    saved runs and reports, the JUnit/SARIF/JSON/Markdown reporters, the copied
    curl, and the crash report — and verifies each one masked it. Exits non-zero
    if any sink leaked. The TUI runs the same check in Settings → Security (``t``).
    """
    from comparo.adapters import doctor as doctor_adapter

    checks = doctor_adapter.run_selfcheck()
    for check in checks:
        mark, colour = ("✓", typer.colors.GREEN) if check.ok else ("✗", typer.colors.RED)
        typer.secho(f"{mark} {check.name:<18} {check.detail}", fg=colour)
    passed = sum(1 for check in checks if check.ok)
    total = len(checks)
    typer.secho(
        f"\n{passed}/{total} sinks masked the canary",
        fg=typer.colors.GREEN if passed == total else typer.colors.RED,
        bold=True,
    )
    if passed != total:
        raise typer.Exit(1)


@app.command()
def schema(
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to a file instead of stdout.")
    ] = None,
) -> None:
    """Emit the comparo/v1 JSON Schema.

    The schema is generated from the object models, so it never drifts from the
    real config. Point an editor's YAML language server at it for autocomplete
    and inline validation, or hand it to an agent authoring config.

    Args:
        output: A file to write the schema to; prints to stdout when omitted.
    """
    from comparo.core.schema import json_schema

    document = json.dumps(json_schema(), indent=2) + "\n"
    if output is not None:
        output.write_text(document, encoding="utf-8")
        typer.secho(f"✓ wrote {output}", fg=typer.colors.GREEN)
    else:
        typer.echo(document, nl=False)


@app.command()
def render(
    request_id: Annotated[str, typer.Argument(help="metadata.id of the request to render.")],
    *,
    config: ConfigOption = DEFAULT_CONFIG,
    env: Annotated[str | None, typer.Option("--env", "-e", help="Environment name or id.")] = None,
) -> None:
    """Show a request fully resolved for an environment, with secrets masked.

    Args:
        request_id: The ``metadata.id`` of the request to resolve.
        config: The manifest (or project directory) to load.
        env: The environment to resolve for; defaults to the project default.
    """
    loaded = _open_project(config)
    obj = loaded.objects.get(request_id)
    if not isinstance(obj, Request):
        typer.secho(f"no Request with id '{request_id}'", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        environment = select_environment(loaded, env)
    except EnvironmentSelectionError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error
    _print_resolved(
        Resolver(loaded, environment).resolve_request(obj),
        environment.metadata.name,
        Redactor.for_project(loaded).text,
    )


@app.command(name="run")
def run_requests(
    request_id: Annotated[
        str | None, typer.Argument(help="A single request id; omit to run all.")
    ] = None,
    *,
    config: ConfigOption = DEFAULT_CONFIG,
    env: Annotated[str | None, typer.Option("--env", "-e", help="Environment name or id.")] = None,
) -> None:
    """Execute requests against an environment and report status and latency.

    Args:
        request_id: A single request id to run, or ``None`` to run every request.
        config: The manifest (or project directory) to load.
        env: The environment to run against; defaults to the project default.
    """
    loaded = _open_project(config)
    try:
        environment = select_environment(loaded, env)
    except EnvironmentSelectionError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error
    requests = _select_requests(loaded, request_id)
    if not requests:
        typer.secho("no requests to run", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    results = asyncio.run(_execute(loaded, environment, requests))
    ok = _print_results(loaded, results, environment.metadata.name)
    if not ok:
        raise typer.Exit(1)


@app.command(name="exec")
def exec_profile(
    execution_id: Annotated[str, typer.Argument(help="The ExecutionProfile id to run.")],
    *,
    config: ConfigOption = DEFAULT_CONFIG,
    report: Annotated[
        list[str] | None,
        typer.Option("--report", help="Report format(s): junit, sarif, json, markdown."),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Directory for report files.")
    ] = None,
) -> None:
    """Run an ExecutionProfile headless — assert both envs, diff, and gate.

    Exits 0 only when the gate passes (assertions hold on both environments and
    nothing untriaged drifted), so CI can gate on it. This is the exact gate the
    TUI Execution screen shows. With ``--report`` it also writes CI artifacts,
    where a cell that failed only its assertions (no drift) is still a failure.

    Args:
        execution_id: The ``metadata.id`` of the ExecutionProfile to run.
        config: The manifest (or project directory) to load.
        report: Report format(s) to write (defaults to the manifest's).
        output: The directory report files are written to.
    """
    loaded = _open_project(config)
    profile = loaded.objects.get(execution_id)
    if not isinstance(profile, ExecutionProfile):
        typer.secho(f"no ExecutionProfile with id '{execution_id}'", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        result = asyncio.run(_run_execution(loaded, profile))
    except EnvironmentSelectionError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error
    redact = Redactor.for_project(loaded).text
    _print_execution(result, redact)
    _emit_reports(loaded, report, output, lambda: build_execution_report(result, redact))
    raise typer.Exit(0 if result.passed else 1)


async def _run_execution(loaded: LoadedProject, profile: ExecutionProfile) -> ExecutionResult:
    client, candidate_client = HttpxClient(), HttpxClient()
    try:
        return await run_execution(loaded, profile, client, candidate_client)
    finally:
        await client.aclose()
        await candidate_client.aclose()


def _print_execution(result: ExecutionResult, redact: Callable[[str], str] = str) -> None:
    pair = f"{result.baseline} ⇄ {result.candidate}" if result.candidate else result.baseline
    typer.secho(f"exec · {result.profile_id}  {pair}", bold=True)
    for outcome in result.outcomes:
        failed = [r for r in outcome.baseline_assertions + outcome.candidate_assertions if not r.ok]
        cell = f"{outcome.request_id}" + (
            f" [{redact(outcome.cell_key)}]" if outcome.cell_key else ""
        )
        if outcome.ok:
            typer.secho(f"  ✓ {cell}", fg=typer.colors.GREEN)
        else:
            if outcome.error is not None:
                reason = redact(outcome.error)
            elif failed:
                reason = redact(failed[0].label)
            else:
                reason = "drift"
            typer.secho(f"  ✗ {cell:<40} {reason}", fg=typer.colors.RED)
    counts = f"{len(result.outcomes)} cells · {result.drift} drift · {result.errors} error"
    if result.passed:
        typer.secho(f"\n✓ gate PASS  {counts}", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho(f"\n✗ gate FAIL  {counts}", fg=typer.colors.RED, bold=True)


@app.command()
def diff(
    request_id: Annotated[
        str | None, typer.Argument(help="A single request id; omit to diff all.")
    ] = None,
    *,
    config: ConfigOption = DEFAULT_CONFIG,
    pair: Annotated[str | None, typer.Option("--pair", "-p", help="Named diff pair.")] = None,
    baseline: Annotated[
        str | None, typer.Option("--baseline", "-b", help="Baseline environment.")
    ] = None,
    candidate: Annotated[
        str | None, typer.Option("--candidate", "-c", help="Candidate environment.")
    ] = None,
    report: Annotated[
        list[str] | None,
        typer.Option("--report", help="Report format(s): junit, sarif, json, markdown."),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Directory for report files.")
    ] = None,
) -> None:
    """Diff every request-cell across two environments and report drift.

    Args:
        request_id: A single request id to diff, or ``None`` for all.
        config: The manifest (or project directory) to load.
        pair: A named diff pair from the manifest.
        baseline: An explicit baseline environment (overrides the pair).
        candidate: An explicit candidate environment (overrides the pair).
        report: Report format(s) to write.
        output: The directory report files are written to.
    """
    loaded = _open_project(config)
    try:
        base_env, candidate_env = resolve_pair(loaded, pair, baseline, candidate)
    except EnvironmentSelectionError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error
    requests = _select_requests(loaded, request_id)
    if not requests:
        typer.secho("no requests to diff", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    results = asyncio.run(_diff(loaded, base_env, candidate_env, requests))
    redact = Redactor.for_project(loaded).text
    if not results:
        typer.secho(
            "nothing to diff — every selected request expanded to zero cells",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    passed = _print_diffs(results, base_env.metadata.name, candidate_env.metadata.name, redact)
    _emit_reports(
        loaded,
        report,
        output,
        lambda: build_report(base_env.metadata.name, candidate_env.metadata.name, results, redact),
    )
    if not passed:
        raise typer.Exit(1)


def _emit_reports(
    loaded: LoadedProject,
    report: list[str] | None,
    output: Path | None,
    build: Callable[[], RunReport],
) -> None:
    """Write CI report artifacts, falling back to the manifest's report defaults.

    *build* is deferred so the (small) report is materialized only when a format
    is actually requested. An unknown format aborts even when nothing is written,
    so a typo in ``--report`` never silently produces no artifact.
    """
    report_config = loaded.project.spec.report if loaded.project is not None else None
    formats = report or (report_config.formats if report_config is not None else None)
    unknown = [name for name in formats or [] if name not in REPORTERS]
    if unknown:
        known = ", ".join(sorted(REPORTERS))
        _abort(f"unknown report format(s): {', '.join(unknown)} (known: {known})")
    if not formats:
        return
    out_dir = output or Path(
        report_config.output if report_config is not None and report_config.output else "reports"
    )
    _write_reports(build(), formats, out_dir)


@app.command()
def tui(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """Launch the terminal UI to explore a project.

    Args:
        config: The manifest (or project directory) to open.
    """
    _launch_tui(config)


@app.command(name="help")
def show_help(ctx: typer.Context) -> None:
    """Show the full command reference."""
    typer.echo(ctx.find_root().get_help())


def _missing_config(config: Path) -> str:
    return (
        f"no project at '{config}' — run `comparo init` to create one, "
        "or point --config at a manifest"
    )


def _open_project(config: Path) -> LoadedProject:
    """Load a project from *config*, exiting with a friendly message on failure.

    Args:
        config: The manifest file or project directory to load.

    Returns:
        The validated project.
    """
    if not config.exists():
        typer.secho(_missing_config(config), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        return load_project(config)
    except LoadError as error:
        _print_load_error(error)
        raise typer.Exit(1) from error


def _launch_tui(config: Path) -> None:
    """Open the TUI on *config*, or the error screen if the project will not load.

    Args:
        config: The manifest file or project directory to open.
    """
    from comparo.tui.app import ComparoApp

    if not config.exists():
        typer.secho(_missing_config(config), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        loaded = load_project(config)
    except LoadError as error:
        ComparoApp.from_error(error).run()
        raise typer.Exit(1) from error
    ComparoApp(loaded).run()


def _scaffold(
    directory: Path, name: str | None, data: Path, config: str, description: str | None
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / config
    data_dir = directory / data
    if manifest.exists():
        _abort(f"{manifest} already exists — refusing to overwrite")
    if data_dir.exists():
        _abort(f"{data_dir} already exists — refusing to touch your data")
    if name is None:
        name = typer.prompt("Project name").strip()
    if not name:
        _abort("a project name is required")
    data_rel = os.path.relpath(data_dir, directory).replace(os.sep, "/")
    manifest.write_text(
        _manifest_yaml(name, f"project.{_slug(name)}", description, data_rel), encoding="utf-8"
    )
    (data_dir / "environments").mkdir(parents=True)
    (data_dir / "requests").mkdir(parents=True)
    (data_dir / "environments" / "local.yaml").write_text(_STARTER_ENV, encoding="utf-8")
    (data_dir / "requests" / "example.yaml").write_text(_STARTER_REQUEST, encoding="utf-8")
    (data_dir / "AGENTS.md").write_text(
        _AGENTS_MD.replace("__SCHEMA_URL__", SCHEMA_ID), encoding="utf-8"
    )
    typer.secho(f"✓ created {manifest}", fg=typer.colors.GREEN)
    typer.secho(
        f"✓ created {data_dir}/ with a sample environment and request, and AGENTS.md",
        fg=typer.colors.GREEN,
    )
    default_here = config == "comparo.yaml" and directory == Path()
    flag = "" if default_here else f" --config {manifest}"
    typer.echo("\nNext:")
    typer.echo(f"  comparo validate{flag}    # check it loads")
    typer.echo(f"  comparo{flag}             # open the TUI")


def _write_openapi_project(directory: Path, result: openapi.ImportResult) -> None:
    """Write a scaffolded project from an OpenAPI import, refusing to clobber files.

    Mirrors :func:`_scaffold`: a ``comparo.yaml`` manifest plus a ``.comparo/``
    data directory holding the environments, requests, and schemas — each file
    carrying the editor schema modeline — and the agent-authoring ``AGENTS.md``.

    Args:
        directory: The directory to create the project in.
        result: The objects the OpenAPI import produced.
    """
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "comparo.yaml"
    data_dir = directory / ".comparo"
    if manifest.exists():
        _abort(f"{manifest} already exists — refusing to overwrite")
    if data_dir.exists():
        _abort(f"{data_dir} already exists — refusing to touch your data")

    manifest.write_text(
        _manifest_yaml(
            result.project_name,
            f"project.{_slug(result.project_name)}",
            "Imported from an OpenAPI document; refine it before relying on it.",
            ".comparo",
            default_env=result.default_environment,
            diff_pairs=_openapi_diff_pairs(result),
        ),
        encoding="utf-8",
    )
    _write_objects(data_dir / "environments", result.environments)
    _write_objects(data_dir / "requests", result.requests)
    if result.schemas:
        _write_objects(data_dir / "schemas", result.schemas)
    (data_dir / "AGENTS.md").write_text(
        _AGENTS_MD.replace("__SCHEMA_URL__", SCHEMA_ID), encoding="utf-8"
    )

    counts = (
        f"{len(result.environments)} environment(s), {len(result.requests)} request(s), "
        f"{len(result.schemas)} schema(s)"
    )
    typer.secho(f"✓ created {manifest}", fg=typer.colors.GREEN)
    typer.secho(f"✓ created {data_dir}/ — {counts}, and AGENTS.md", fg=typer.colors.GREEN)
    for warning in result.warnings:
        typer.secho(f"⚠ {warning}", fg=typer.colors.YELLOW, err=True)
    if result.secret_env_vars:
        typer.echo("\nSecrets are declared as $env refs — provide real values before running:")
        for var in result.secret_env_vars:
            typer.echo(f"  export {var}=…")
    typer.echo(
        "\nThis is a scaffold: no diff profiles were generated — deciding which fields are\n"
        "volatile is your call. Add DiffProfiles (and real secret values), then validate."
    )
    typer.echo("\nNext:")
    typer.echo(f"  comparo validate --config {manifest}    # check it loads")
    typer.echo(f"  comparo --config {manifest}             # open the TUI")


def _write_objects(directory: Path, objects: list[openapi.ImportedObject]) -> None:
    """Write each imported object to ``<directory>/<id-suffix>.yaml`` with the modeline."""
    directory.mkdir(parents=True, exist_ok=True)
    for obj in objects:
        filename = obj.id.split(".", 1)[-1] + ".yaml"
        (directory / filename).write_text(
            _SCHEMA_MODELINE + openapi.to_yaml(obj.document), encoding="utf-8"
        )


def _openapi_diff_pairs(result: openapi.ImportResult) -> list[tuple[str, str, str]] | None:
    """Pair the first two environments into a diff pair when a spec has 2+ servers."""
    if len(result.environments) < 2:
        return None
    baseline = result.environments[0].id.split(".", 1)[-1]
    candidate = result.environments[1].id.split(".", 1)[-1]
    return [(f"{baseline}-vs-{candidate}", baseline, candidate)]


def _abort(message: str) -> NoReturn:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def _yaml_scalar(value: str) -> str:
    """Return *value* quoted only when a plain YAML scalar would be misread."""
    unsafe = re.search(r"""[:#\[\]{}&*!|>'"%@`,]""", value) or value != value.strip()
    if value and not unsafe and "\n" not in value:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _manifest_yaml(
    name: str,
    project_id: str,
    description: str | None,
    data: str,
    *,
    default_env: str = "local",
    diff_pairs: list[tuple[str, str, str]] | None = None,
) -> str:
    summary = description or "An HTTP regression & diff project."
    environments = f"  environments:\n    default: {default_env}\n"
    if diff_pairs:
        environments += "    diffPairs:\n"
        for pair_name, baseline, candidate in diff_pairs:
            environments += (
                f"      - name: {pair_name}\n"
                f"        baseline: {baseline}\n"
                f"        candidate: {candidate}\n"
            )
    return (
        "apiVersion: comparo/v1\n"
        "kind: Project\n"
        "metadata:\n"
        f"  name: {_yaml_scalar(name)}\n"
        f"  id: {project_id}\n"
        f"  description: {_yaml_scalar(summary)}\n"
        "spec:\n"
        "  # Where comparo's objects live, relative to this file.\n"
        f"  data: {data}\n"
        "\n" + environments + "\n"
        "  run:\n"
        "    concurrency: 4\n"
    )


#: Editors with the YAML language server autocomplete and validate against this.
_SCHEMA_MODELINE = f"# yaml-language-server: $schema={SCHEMA_ID}\n"

_STARTER_ENV = (
    _SCHEMA_MODELINE
    + """apiVersion: comparo/v1
kind: Environment
metadata:
  name: Local
  id: environment.local
  description: A starter environment — point baseUrl at your API.
spec:
  baseUrl: https://postman-echo.com
  timeout:
    connect: 5s
    read: 30s
  health:
    - method: GET
      endpoint: /get
"""
)

_STARTER_REQUEST = (
    _SCHEMA_MODELINE
    + """apiVersion: comparo/v1
kind: Request
metadata:
  name: Example
  id: request.example
  description: A starter request — edit or replace it.
  tags:
    - smoke
spec:
  request:
    method: GET
    endpoint: /get
    query:
      hello: world
  response:
    status: 200
"""
)

# Dropped into every scaffolded project so any coding agent working here is
# instantly competent at authoring comparo config. `__SCHEMA_URL__` is replaced
# with the real schema URL at write time.
_AGENTS_MD = """# comparo — authoring guide for coding agents

This directory is a **comparo** project: version-controlled YAML that replays HTTP
requests across environments and diffs the responses to catch regressions. You may
author and edit these files. **After any change, run `comparo validate` and fix
what it reports** — its diagnostics are precise and name the fix.

## The object model

Each file is one object with a Kubernetes-style envelope:

    apiVersion: comparo/v1
    kind: <Kind>
    metadata: { id, name, description?, tags? }
    spec: { ... }

Field names are **camelCase** (`baseUrl`, not `base_url`). The kinds:

- **Environment** — a target: `baseUrl`, `timeout`, `variables`, `secrets`, `auth`, `health`.
- **Request** — an HTTP request (`method`, `endpoint`, `headers`, `query`, `body`, `cookies`,
  `auth`) with an optional response `schema` and `diff`/`assert` profiles. Matrix-expanded.
- **Schema** — a JSON Schema used for structural validation.
- **Instance** — reusable values injected by reference, to avoid duplication.
- **Matrix** — the parameter cases a request runs against (values can inject into the path).
- **DiffProfile** — how two responses are compared, per JSON path.
- **AssertionProfile** — assertions on a single response (status, body, latency, schema).
- **ExecutionProfile** — one run that asserts BOTH environments and diffs the pair, gated.
- **Project** — the manifest (`comparo.yaml`): data dir, environments, concurrency.

## References and secrets

- `$ref: <id>` — link to another object by its `metadata.id`.
- `$val: <instance-id>` — inject the value of an Instance by reference.
- `$secret: NAME` — a secret declared in the environment. **Never write a real secret
  value in these files.** Declare it once under an Environment's `secrets` (sourced from
  `$env` or `$file`) and reference it by name. comparo masks declared secrets everywhere.
- `$file: path` — read a value from a file (confined to the project root).

## `${...}` interpolation (inside strings)

- `${VAR}` — required (fails if unset)
- `${VAR?}` — optional (empty if unset)
- `${VAR | default}` — a default used only when unset
- `${VAR:int}` — a typed cast (`int` | `number` | `bool`)

## Diff modes (in a DiffProfile, per JSON path)

- `ignore` — skip it (volatile fields: timestamps, generated ids)
- `exact` — must be equal (recurses)
- `type` — same JSON type
- `shape` — same structure, values may differ (recurses)
- `tolerance` — numbers within a delta

## The loop

1. Edit YAML.
2. `comparo validate` — fix every diagnostic before moving on.
3. `comparo run --env <env>` to execute, or
   `comparo diff --baseline <A> --candidate <B>` to compare two environments.
4. `comparo schema` prints the full JSON Schema — the complete, authoritative field
   surface. Editors autocomplete against it via the modeline already on each file:
   `# yaml-language-server: $schema=__SCHEMA_URL__`

## Example — a request with a schema and a diff profile

    # yaml-language-server: $schema=__SCHEMA_URL__
    apiVersion: comparo/v1
    kind: Request
    metadata: { id: request.user, name: Get user }
    spec:
      request:
        method: GET
        endpoint: /users/${USER_ID}
        headers:
          Authorization: "Bearer ${API_TOKEN}"   # API_TOKEN is a $secret in the env
      response:
        schema: { $ref: schema.user }
        diff: { $ref: diffprofile.user }

Do not invent fields. When unsure, consult `comparo schema` or let `comparo validate`
correct you.
"""


def _write_reports(report: RunReport, formats: list[str], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name in formats:
        reporter = REPORTERS.get(name)
        if reporter is None:
            known = ", ".join(sorted(REPORTERS))
            typer.secho(
                f"unknown report format '{name}' (known: {known})", fg=typer.colors.YELLOW, err=True
            )
            continue
        rendered = reporter.render(report)
        destination = output / reporter.filename
        destination.write_text(rendered, encoding="utf-8")
        typer.secho(f"  wrote {destination}", dim=True)
        if name == "markdown":
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with Path(summary).open("a", encoding="utf-8") as handle:
                    handle.write(rendered + "\n")


async def _diff(
    loaded: LoadedProject, baseline: Environment, candidate: Environment, requests: list[Request]
) -> list[CellDiff]:
    client = HttpxClient()
    candidate_client = HttpxClient()
    try:
        return await diff_run(loaded, baseline, candidate, requests, client, candidate_client)
    finally:
        await client.aclose()
        await candidate_client.aclose()


def _print_diffs(
    results: list[CellDiff],
    baseline_name: str,
    candidate_name: str,
    redact: Callable[[str], str] = str,
) -> bool:
    typer.secho(f"diff · {baseline_name} ⇄ {candidate_name}", bold=True)
    same = drift = errors = skipped = 0
    for cell in results:
        identifier = cell.request.metadata.id or cell.request.metadata.name
        if cell.cell_key:
            identifier = f"{identifier} [{redact(cell.cell_key)}]"
        skipped += cell.skipped
        if cell.error is not None:
            errors += 1
            typer.secho(f"  ! {identifier:<44} {redact(cell.error)}", fg=typer.colors.YELLOW)
        elif cell.drifted:
            drift += 1
            typer.secho(f"  ✗ {identifier:<44} drift", fg=typer.colors.RED)
            for field in cell.drifts:
                typer.echo(f"      {redact(field.path)}  {redact(field.detail)}")
        else:
            note = f"  ({cell.skipped} skipped)" if cell.skipped else ""
            typer.secho(f"  ✓ {identifier:<44} same{note}", fg=typer.colors.GREEN)
            same += 1
    summary = f"{same} same · {drift} drift · {errors} error · {skipped} fields skipped"
    typer.echo()
    typer.secho(f"summary: {summary}", bold=True)
    passed = diff_passed(len(results), drift, errors)
    color = typer.colors.GREEN if passed else typer.colors.RED
    typer.secho("gate: PASS" if passed else "gate: FAIL", fg=color)
    return passed


def _select_requests(loaded: LoadedProject, request_id: str | None) -> list[Request]:
    if request_id is not None:
        obj = loaded.objects.get(request_id)
        return [obj] if isinstance(obj, Request) else []
    requests = sorted(
        (o for o in loaded.objects.values() if isinstance(o, Request)),
        key=lambda request: request.metadata.id or "",
    )
    # With no explicit request, honour the manifest's default selection (if any).
    selection = loaded.project.spec.selection if loaded.project is not None else None
    if selection is not None and (selection.tags or selection.requests):
        ids = set(selection.requests or [])
        tags = set(selection.tags or [])
        requests = [
            request
            for request in requests
            if request.metadata.id in ids or (tags & set(request.metadata.tags or []))
        ]
    return requests


async def _execute(
    loaded: LoadedProject, environment: Environment, requests: list[Request]
) -> list[Execution]:
    client = HttpxClient()
    try:
        return await execute_all(loaded, environment, requests, client)
    finally:
        await client.aclose()


def _print_results(loaded: LoadedProject, results: list[Execution], environment_name: str) -> bool:
    """Print each result against its declared response, returning the gate verdict.

    A request that returns a response is *not* automatically a pass — its
    ``response.status`` / ``response.schema`` sugar is evaluated, so a 500 against
    a declared 200 is red and fails the gate.
    """
    typer.secho(f"run · {environment_name}", bold=True)
    redact = Redactor.for_project(loaded).text
    ok_all = True
    for execution in results:
        identifier = execution.request.metadata.id or execution.request.metadata.name
        if execution.cell_key:
            identifier = f"{identifier} [{redact(execution.cell_key)}]"
        response = execution.response
        if response is None:
            ok_all = False
            error = redact(execution.error) if execution.error else "error"
            typer.secho(f"  ✗ {identifier:<44} {error}", fg=typer.colors.RED)
            continue
        rules = request_response_rules(loaded, execution.request)
        checks = evaluate_rules(loaded, rules, execution)
        latency = f"{response.elapsed_ms:.0f}ms"
        if assertions_pass(checks):
            typer.secho(f"  ✓ {identifier:<44} {response.status}  {latency}", fg=typer.colors.GREEN)
        else:
            ok_all = False
            failed = next((c for c in checks if not c.ok and c.severity == "error"), None)
            reason = redact(failed.label) if failed is not None else "check failed"
            typer.secho(
                f"  ✗ {identifier:<44} {response.status}  {latency}  {reason}", fg=typer.colors.RED
            )
    return ok_all


def _print_load_error(error: LoadError) -> None:
    for diagnostic in error.diagnostics:
        typer.echo(diagnostic.render(error.root), err=True)
    typer.secho(f"\n✗ {len(error.diagnostics)} problem(s)", fg=typer.colors.RED, err=True)


def _print_resolved(
    resolved: ResolvedRequest, environment_name: str, redact: Callable[[str], str] = str
) -> None:
    typer.secho(f"{redact(resolved.method)} {redact(resolved.url)}", bold=True)
    typer.secho(f"  env: {environment_name}", dim=True)
    if resolved.headers:
        typer.echo("\nheaders:")
        for key, value in resolved.headers:
            typer.echo(f"  {redact(key)}: {redact(str(value))}")
    if resolved.query:
        typer.echo("\nquery:")
        for key, value in resolved.query.items():
            typer.echo(f"  {redact(key)}: {redact(str(value))}")
    if resolved.body is not None:
        typer.echo("\nbody:")
        body = redact(json.dumps(resolved.body, indent=2, ensure_ascii=False))
        typer.echo("\n".join(f"  {line}" for line in body.splitlines()))
    if resolved.trail:
        typer.echo("\nprovenance:")
        for entry in resolved.trail:
            tag = "secret" if entry.tainted else entry.origin.value
            typer.echo(f"  {redact(entry.path):<26} {tag:<9} ← {redact(entry.detail)}")


def run() -> None:
    """Entry point referenced by the ``comparo`` console script."""
    app()


if __name__ == "__main__":
    run()
