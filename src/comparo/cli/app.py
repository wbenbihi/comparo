"""Console entry point for comparo.

Only wiring lives here — the CLI is a thin front-end that will call the
:mod:`comparo.core` engine. No engine logic belongs in this module.
"""

from typing import Annotated

import typer

from comparo import __version__

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


def run() -> None:
    """Entry point referenced by the ``comparo`` console script."""
    app()


if __name__ == "__main__":
    run()
