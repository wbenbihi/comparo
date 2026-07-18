"""The opt-in version check — the one outside call comparo makes for itself.

Kept in an adapter because it touches the network (httpx) and compares versions;
``core`` never does either. The TUI runs :func:`check_latest` in a worker on
startup *only when the user has enabled it* (Settings → Updates & Privacy), and
toasts if a newer release is out. No telemetry is sent — the request is a plain
GET of PyPI's public JSON, carrying nothing about the user or their projects.
"""

from __future__ import annotations

from packaging.version import InvalidVersion
from packaging.version import Version

#: PyPI's public metadata endpoint for the project.
PYPI_URL = "https://pypi.org/pypi/comparo/json"


async def check_latest(current: str, *, timeout: float = 3.0) -> str | None:
    """Return the latest PyPI version if it is newer than *current*, else ``None``.

    Never raises: any network, parsing, or version error resolves to ``None`` so a
    failed check is silent, never a crash or a nag.

    Args:
        current: The installed version string (``comparo.__version__``).
        timeout: Per-request timeout in seconds — kept short; this is best-effort.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(PYPI_URL, headers={"Accept": "application/json"})
            response.raise_for_status()
            latest = str(response.json()["info"]["version"])
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None
    return latest if is_newer(latest, current) else None


def is_newer(latest: str, current: str) -> bool:
    """Whether *latest* is a strictly newer release than *current* (PEP 440)."""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False
