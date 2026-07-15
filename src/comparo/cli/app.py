"""Console entry point for comparo.

Only wiring lives here — the CLI is a thin front-end that will call the
:mod:`comparo.core` engine. No engine logic belongs in this module.
"""

from pathlib import Path
from typing import Annotated

import typer

from comparo import __version__
from comparo.core.diagnostics import LoadError
from comparo.core.loader import load_project

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
        for diagnostic in error.diagnostics:
            typer.echo(diagnostic.render(error.root), err=True)
        typer.secho(f"\n✗ {len(error.diagnostics)} problem(s)", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error
    typer.secho(f"✓ {len(loaded.objects)} object(s) valid", fg=typer.colors.GREEN)


def run() -> None:
    """Entry point referenced by the ``comparo`` console script."""
    app()


if __name__ == "__main__":
    run()
