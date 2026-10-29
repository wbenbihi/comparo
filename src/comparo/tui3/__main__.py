"""Launch: ``python -m comparo.tui3`` — no wiring outside this package."""

from __future__ import annotations

import sys

from comparo.tui3.app import ComparoTui3


def main() -> int:
    """Run the app and translate its result to an exit code."""
    result = ComparoTui3().run()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
