"""Installed package identity and source revision, independent of the loaded project."""

import json
import re
import subprocess
from dataclasses import dataclass
from email.utils import parseaddr
from importlib.metadata import PackageNotFoundError
from importlib.metadata import metadata
from pathlib import Path
from shutil import which


def source_revision(root: Path) -> tuple[str | None, bool]:
    """Read only the Comparo source checkout, never search the current working directory."""
    git = which("git")
    if git is None or not (root / ".git").exists():
        return None, False
    try:
        revision = subprocess.run(  # noqa: S603
            [git, "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603
            [
                git,
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=normal",
                "--",
                "src/comparo",
                "pyproject.toml",
                "hatch_build.py",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, False
    return (revision, bool(status)) if re.fullmatch(r"[0-9a-f]{40,64}", revision) else (None, False)


def recorded_revision(package: Path) -> tuple[str | None, bool]:
    """Read the small build stamp carried from source distribution into wheel."""
    try:
        with (package / "_build_info.json").open("rb") as stream:
            value = json.loads(stream.read(4096))
        if (
            type(value) is dict
            and isinstance(value.get("sha"), str)
            and re.fullmatch(r"[0-9a-f]{40,64}", value["sha"])
            and type(value.get("modified")) is bool
        ):
            return value["sha"], value["modified"]
    except (OSError, ValueError):
        pass
    return None, False


@dataclass(frozen=True)
class BuildInfo:
    """Read-only identity shown by Settings; missing provenance is explicit."""

    version: str
    author: str
    repository: str
    source: str
    license: str
    sha: str | None
    modified: bool


def read_build_info() -> BuildInfo:
    """Load package metadata and bounded provenance off the UI event loop."""
    package = Path(__file__).resolve().parent
    root = package.parent.parent
    checkout = root / "src" / "comparo" == package and (root / ".git").exists()
    sha, modified = source_revision(root) if checkout else recorded_revision(package)
    try:
        info = metadata("comparo")
    except PackageNotFoundError:
        return BuildInfo(
            "Development",
            "Unavailable",
            "Unavailable",
            "Source checkout" if checkout else "Installed package",
            "Unavailable",
            sha,
            modified,
        )
    urls: dict[str, str] = {}
    for value in info.get_all("Project-URL", []):
        if ", " in value:
            key, url = value.split(", ", 1)
            urls[key] = url
    repository = urls.get("Repository", urls.get("Homepage", "Unavailable"))
    source = urls.get("Source", urls.get("Source Code", repository))
    if sha and source.startswith("https://github.com/"):
        source = f"{source.rstrip('/')}/tree/{sha}"
    return BuildInfo(
        info.get("Version", "Unavailable"),
        info.get("Author") or parseaddr(info.get("Author-email", ""))[0] or "Unavailable",
        repository,
        source,
        info.get("License-Expression") or info.get("License") or "Unavailable",
        sha,
        modified,
    )
