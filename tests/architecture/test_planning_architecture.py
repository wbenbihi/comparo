"""Architecture guardrails for deterministic pre-effect planning."""

from pathlib import Path


def test_planning_package_contains_no_ambient_effect_imports() -> None:
    """Keep planning independent from adapters, ambient state, and schedulers."""
    root = Path(__file__).parents[2] / "src" / "comparo" / "core" / "planning"
    forbidden = (
        "import asyncio",
        "import os",
        "import pathlib",
        "import random",
        "import time",
        "from comparo.configuration",
        "from pathlib",
        "from random",
        "from time",
    )
    sources = tuple(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert sources
    assert all(token not in source for source in sources for token in forbidden)
