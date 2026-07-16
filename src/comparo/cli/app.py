"""Console entry point for comparo.

Only wiring lives here — the CLI is a thin front-end that will call the
:mod:`comparo.core` engine. No engine logic belongs in this module.
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Annotated
from typing import NoReturn

import typer

from comparo import __version__
from comparo.adapters.httpx_client import HttpxClient
from comparo.adapters.reporters import REPORTERS
from comparo.core.compare import CellDiff
from comparo.core.compare import diff_run
from comparo.core.diagnostics import LoadError
from comparo.core.execute import Execution
from comparo.core.execute import execute_all
from comparo.core.loader import LoadedProject
from comparo.core.loader import load_project
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.report import RunReport
from comparo.core.report import build_report
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import resolve_pair
from comparo.core.resolve import select_environment

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

    Run ``comparo`` with no command to open the terminal UI on ``./comparo.yaml``.
    """
    if ctx.invoked_subcommand is None:
        _launch_tui(DEFAULT_CONFIG)


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


@app.command()
def validate(config: ConfigOption = DEFAULT_CONFIG) -> None:
    """Validate a project's envelope, ids, and references.

    Exits non-zero and prints every problem if the project does not load.

    Args:
        config: The manifest (or project directory) to validate.
    """
    loaded = _open_project(config)
    typer.secho(f"✓ {len(loaded.objects)} object(s) valid", fg=typer.colors.GREEN)


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
    _print_resolved(Resolver(loaded, environment).resolve_request(obj), environment.metadata.name)


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
    _print_results(results, environment.metadata.name)
    if any(not execution.ok for execution in results):
        raise typer.Exit(1)


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
        Path, typer.Option("--output", "-o", help="Directory for report files.")
    ] = Path("reports"),
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
    passed = _print_diffs(results, base_env.metadata.name, candidate_env.metadata.name)
    if report:
        run_report = build_report(base_env.metadata.name, candidate_env.metadata.name, results)
        _write_reports(run_report, report, output)
    if not passed:
        raise typer.Exit(1)


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
    typer.secho(f"✓ created {manifest}", fg=typer.colors.GREEN)
    typer.secho(
        f"✓ created {data_dir}/ with a sample environment and request", fg=typer.colors.GREEN
    )
    default_here = config == "comparo.yaml" and directory == Path()
    flag = "" if default_here else f" --config {manifest}"
    typer.echo("\nNext:")
    typer.echo(f"  comparo validate{flag}    # check it loads")
    typer.echo(f"  comparo{flag}             # open the TUI")


def _abort(message: str) -> NoReturn:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def _manifest_yaml(name: str, project_id: str, description: str | None, data: str) -> str:
    summary = description or "An HTTP regression & diff project."
    return (
        "apiVersion: comparo/v1\n"
        "kind: Project\n"
        "metadata:\n"
        f"  name: {name}\n"
        f"  id: {project_id}\n"
        f"  description: {summary}\n"
        "spec:\n"
        "  # Where comparo's objects live, relative to this file.\n"
        f"  data: {data}\n"
        "\n"
        "  environments:\n"
        "    default: local\n"
        "\n"
        "  run:\n"
        "    concurrency: 4\n"
    )


_STARTER_ENV = """apiVersion: comparo/v1
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

_STARTER_REQUEST = """apiVersion: comparo/v1
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
    try:
        return await diff_run(loaded, baseline, candidate, requests, client)
    finally:
        await client.aclose()


def _print_diffs(results: list[CellDiff], baseline_name: str, candidate_name: str) -> bool:
    typer.secho(f"diff · {baseline_name} ⇄ {candidate_name}", bold=True)
    same = drift = errors = skipped = 0
    for cell in results:
        identifier = cell.request.metadata.id or cell.request.metadata.name
        if cell.cell_key:
            identifier = f"{identifier} [{cell.cell_key}]"
        skipped += cell.skipped
        if cell.error is not None:
            errors += 1
            typer.secho(f"  ! {identifier:<44} {cell.error}", fg=typer.colors.YELLOW)
        elif cell.drifted:
            drift += 1
            typer.secho(f"  ✗ {identifier:<44} drift", fg=typer.colors.RED)
            for field in cell.drifts:
                typer.echo(f"      {field.path}  {field.detail}")
        else:
            note = f"  ({cell.skipped} skipped)" if cell.skipped else ""
            typer.secho(f"  ✓ {identifier:<44} same{note}", fg=typer.colors.GREEN)
            same += 1
    summary = f"{same} same · {drift} drift · {errors} error · {skipped} fields skipped"
    typer.echo()
    typer.secho(f"summary: {summary}", bold=True)
    passed = drift == 0 and errors == 0
    color = typer.colors.GREEN if passed else typer.colors.RED
    typer.secho("gate: PASS" if passed else "gate: FAIL", fg=color)
    return passed


def _select_requests(loaded: LoadedProject, request_id: str | None) -> list[Request]:
    if request_id is not None:
        obj = loaded.objects.get(request_id)
        return [obj] if isinstance(obj, Request) else []
    return sorted(
        (o for o in loaded.objects.values() if isinstance(o, Request)),
        key=lambda request: request.metadata.id or "",
    )


async def _execute(
    loaded: LoadedProject, environment: Environment, requests: list[Request]
) -> list[Execution]:
    client = HttpxClient()
    try:
        return await execute_all(loaded, environment, requests, client)
    finally:
        await client.aclose()


def _print_results(results: list[Execution], environment_name: str) -> None:
    typer.secho(f"run · {environment_name}", bold=True)
    for execution in results:
        identifier = execution.request.metadata.id or execution.request.metadata.name
        if execution.cell_key:
            identifier = f"{identifier} [{execution.cell_key}]"
        response = execution.response
        if response is not None:
            latency = f"{response.elapsed_ms:.0f}ms"
            typer.secho(f"  ✓ {identifier:<44} {response.status}  {latency}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  ✗ {identifier:<44} {execution.error}", fg=typer.colors.RED)


def _print_load_error(error: LoadError) -> None:
    for diagnostic in error.diagnostics:
        typer.echo(diagnostic.render(error.root), err=True)
    typer.secho(f"\n✗ {len(error.diagnostics)} problem(s)", fg=typer.colors.RED, err=True)


def _print_resolved(resolved: ResolvedRequest, environment_name: str) -> None:
    typer.secho(f"{resolved.method} {resolved.url}", bold=True)
    typer.secho(f"  env: {environment_name}", dim=True)
    if resolved.headers:
        typer.echo("\nheaders:")
        for key, value in resolved.headers:
            typer.echo(f"  {key}: {value}")
    if resolved.query:
        typer.echo("\nquery:")
        for key, value in resolved.query.items():
            typer.echo(f"  {key}: {value}")
    if resolved.body is not None:
        typer.echo("\nbody:")
        body = json.dumps(resolved.body, indent=2, ensure_ascii=False)
        typer.echo("\n".join(f"  {line}" for line in body.splitlines()))
    if resolved.trail:
        typer.echo("\nprovenance:")
        for entry in resolved.trail:
            tag = "secret" if entry.tainted else entry.origin.value
            typer.echo(f"  {entry.path:<26} {tag:<9} ← {entry.detail}")


def run() -> None:
    """Entry point referenced by the ``comparo`` console script."""
    app()


if __name__ == "__main__":
    run()
