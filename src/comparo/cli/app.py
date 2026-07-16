"""Console entry point for comparo.

Only wiring lives here — the CLI is a thin front-end that will call the
:mod:`comparo.core` engine. No engine logic belongs in this module.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

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
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(*, value: bool) -> None:
    """Print the version and exit when the flag is set.

    Args:
        value: Whether the ``--version`` / ``-V`` flag was passed.
    """
    if value:
        typer.echo(f"comparo {__version__}")
        raise typer.Exit


@app.callback()
def main(
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
    """Replay requests across environments and diff the responses."""


@app.command()
def validate(
    project: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="Path to the project directory to validate.",
        ),
    ],
) -> None:
    """Validate a project's envelope, ids, and references.

    Exits non-zero and prints every problem if the project does not load.

    Args:
        project: The project directory to load and validate.
    """
    try:
        loaded = load_project(project)
    except LoadError as error:
        _print_load_error(error)
        raise typer.Exit(1) from error
    typer.secho(f"✓ {len(loaded.objects)} object(s) valid", fg=typer.colors.GREEN)


@app.command()
def render(
    project: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, help="Project directory."),
    ],
    request_id: Annotated[str, typer.Argument(help="metadata.id of the request to render.")],
    env: Annotated[str | None, typer.Option("--env", "-e", help="Environment name or id.")] = None,
) -> None:
    """Show a request fully resolved for an environment, with secrets masked.

    Args:
        project: The project directory to load.
        request_id: The ``metadata.id`` of the request to resolve.
        env: The environment to resolve for; defaults to the project default.
    """
    try:
        loaded = load_project(project)
    except LoadError as error:
        _print_load_error(error)
        raise typer.Exit(1) from error
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
    project: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, help="Project directory."),
    ],
    request_id: Annotated[
        str | None, typer.Argument(help="A single request id; omit to run all.")
    ] = None,
    env: Annotated[str | None, typer.Option("--env", "-e", help="Environment name or id.")] = None,
) -> None:
    """Execute requests against an environment and report status and latency.

    Args:
        project: The project directory to load.
        request_id: A single request id to run, or ``None`` to run every request.
        env: The environment to run against; defaults to the project default.
    """
    try:
        loaded = load_project(project)
    except LoadError as error:
        _print_load_error(error)
        raise typer.Exit(1) from error
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
    project: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, help="Project directory."),
    ],
    request_id: Annotated[
        str | None, typer.Argument(help="A single request id; omit to diff all.")
    ] = None,
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
        project: The project directory to load.
        request_id: A single request id to diff, or ``None`` for all.
        pair: A named diff pair from the manifest.
        baseline: An explicit baseline environment (overrides the pair).
        candidate: An explicit candidate environment (overrides the pair).
        report: Report format(s) to write.
        output: The directory report files are written to.
    """
    try:
        loaded = load_project(project)
    except LoadError as error:
        _print_load_error(error)
        raise typer.Exit(1) from error
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
def tui(
    project: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, help="Project directory."),
    ],
) -> None:
    """Launch the terminal UI to explore a project.

    Args:
        project: The project directory to open.
    """
    from comparo.tui.app import ComparoApp

    try:
        loaded = load_project(project)
    except LoadError as error:
        ComparoApp.from_error(error).run()
        raise typer.Exit(1) from error
    ComparoApp(loaded).run()


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
