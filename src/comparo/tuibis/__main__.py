"""Side-door launch: ``python -m comparo.tuibis`` — no CLI wiring yet."""

from __future__ import annotations

import sys

from comparo.tuibis.app import ComparoApp


def main() -> int:
    """Run the app and translate its result to an exit code."""
    result = ComparoApp().run()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
