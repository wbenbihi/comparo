"""Module entry point sharing the console command's launch options and exit codes."""

import sys
from collections.abc import Sequence

from comparo.cli import main as cli_main


def main(argv: Sequence[str] | None = None) -> int:
    """Launch through the same parser as `comparo tui`."""
    return cli_main(["tui", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
