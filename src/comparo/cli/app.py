"""Console entry point for comparo.

Only wiring lives here — the CLI is a thin front-end that will call the
:mod:`comparo.core` engine. No engine logic belongs in this module.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from comparo import __version__
from comparo.core.diagnostics import LoadError
from comparo.core.loader import load_project
from comparo.core.models import Request
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
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
