"""Module entry point for the experimental terminal interface."""

import sys
from pathlib import Path

from .app import ComparoApp


def main() -> None:
    """Launch with an optional project path argument."""
    config = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    app = ComparoApp(config)
    app.run()


if __name__ == "__main__":
    main()
