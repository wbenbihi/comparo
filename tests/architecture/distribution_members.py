"""Fail-closed verification for wheel and source-distribution archive members."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from shutil import which
from subprocess import run

_SDIST_METADATA = frozenset(
    {
        PurePosixPath(".gitignore"),
        PurePosixPath("LICENSE"),
        PurePosixPath("PKG-INFO"),
        PurePosixPath("README.md"),
        PurePosixPath("pyproject.toml"),
    }
)
_WHEEL_METADATA = frozenset(
    {
        PurePosixPath("entry_points.txt"),
        PurePosixPath("METADATA"),
        PurePosixPath("RECORD"),
        PurePosixPath("WHEEL"),
        PurePosixPath("licenses/LICENSE"),
    }
)
_EXPECTED_ENTRY_POINTS = b"[console_scripts]\ncomparo = comparo.cli:main\n"
_FORBIDDEN_PARTS = frozenset(
    {
        ".agents",
        ".claude",
        ".codex",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".reports",
        ".ruff_cache",
        ".vscode",
        "__pycache__",
        "deprecated.ignore.folder",
        "htmlcov",
        "internal",
        "internal-project",
        "reports",
        "runs",
        "secrets",
    }
)
_FORBIDDEN_SUFFIXES = frozenset({".credentials", ".key", ".p12", ".pem", ".pfx", ".pyc", ".pyo"})
_REVIEWED_SECURITY_MODULES = frozenset(
    {
        PurePosixPath("comparo/core/credentials.py"),
        PurePosixPath("comparo/core/domain/http/secret_egress.py"),
        PurePosixPath("src/comparo/core/credentials.py"),
        PurePosixPath("src/comparo/core/domain/http/secret_egress.py"),
    }
)
_REVIEWED_PACKAGE_ASSETS = frozenset(
    {
        "src/comparo/tui/components/comparo.tcss",
        "src/comparo/tui4/styles/main.tcss",
    }
)


class DistributionAuditError(ValueError):
    """A built distribution contains unexpected or sensitive members."""


def _archive_members(archive: Path) -> frozenset[PurePosixPath]:
    """Read file member names without extracting archive content."""
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            return frozenset(
                PurePosixPath(member.filename) for member in wheel.infolist() if not member.is_dir()
            )
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, mode="r:gz") as source:
            return frozenset(
                PurePosixPath(member.name) for member in source.getmembers() if member.isfile()
            )
    raise DistributionAuditError(f"unsupported distribution archive: {archive.name}")


def _wheel_entry_points(archive: Path) -> bytes:
    """Read the one console-entry-point record without extracting the wheel."""
    if archive.suffix != ".whl":
        raise DistributionAuditError("console entry-point audit requires a wheel")
    with zipfile.ZipFile(archive) as wheel:
        candidates = tuple(
            member
            for member in wheel.infolist()
            if not member.is_dir()
            and PurePosixPath(member.filename).name == "entry_points.txt"
            and PurePosixPath(member.filename).parent.name.endswith(".dist-info")
        )
        if len(candidates) != 1:
            raise DistributionAuditError("wheel requires one console entry-point record")
        return wheel.read(candidates[0])


def _is_sensitive(member: PurePosixPath) -> bool:
    """Recognize local, generated, credential, internal, and deprecated path forms."""
    if member in _REVIEWED_SECURITY_MODULES:
        return False
    for part in member.parts:
        lowered = part.lower()
        if (
            lowered in _FORBIDDEN_PARTS
            or lowered.startswith((".coverage", ".env"))
            or "credential" in lowered
            or "secret" in lowered
            or "deprecated" in lowered
            or ".ignore." in lowered
            or ".deprecated." in lowered
            or lowered.endswith(tuple(_FORBIDDEN_SUFFIXES))
        ):
            return True
    return False


def _expected_package_files(source_root: Path) -> frozenset[PurePosixPath]:
    """Derive authorized package members from the reviewed Git index, not local files."""
    git = which("git")
    if git is None:
        raise DistributionAuditError("distribution audit requires git")

    completed = run(  # noqa: S603
        (git, "ls-files", "--", source_root.as_posix()),
        check=False,
        capture_output=True,
        cwd=Path.cwd(),
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()[:200]
        raise DistributionAuditError(f"could not read reviewed package members: {detail}")

    source_prefix = f"{source_root.as_posix().rstrip('/')}/"
    members = frozenset(
        PurePosixPath("comparo") / PurePosixPath(line.removeprefix(source_prefix))
        for line in completed.stdout.splitlines()
        if line.startswith(source_prefix)
        and (
            line.endswith((".py", "/py.typed"))
            or (line.startswith("src/comparo/schemas/") and line.endswith(".schema.json"))
            or line in _REVIEWED_PACKAGE_ASSETS
        )
    )
    if not members:
        raise DistributionAuditError("reviewed package member set is empty")
    return members


def _without_sdist_root(
    members: frozenset[PurePosixPath],
) -> frozenset[PurePosixPath]:
    """Remove the one generated project-version root from every sdist member."""
    roots = {member.parts[0] for member in members if member.parts}
    if len(roots) != 1:
        raise DistributionAuditError("sdist members require one project-version root")
    return frozenset(PurePosixPath(*member.parts[1:]) for member in members)


def verify_sdist_members(
    members: frozenset[PurePosixPath],
    expected_package: frozenset[PurePosixPath],
) -> None:
    """Require exactly installable package sources and required build metadata."""
    relative_members = _without_sdist_root(members)
    expected = _SDIST_METADATA | {
        PurePosixPath("src") / package_member for package_member in expected_package
    }
    _require_exact_members(relative_members, frozenset(expected), "sdist")


def verify_wheel_members(
    members: frozenset[PurePosixPath],
    expected_package: frozenset[PurePosixPath],
) -> None:
    """Require exact package sources and the fixed wheel metadata surface."""
    metadata_roots = {
        member.parts[0]
        for member in members
        if member.parts and member.parts[0].endswith(".dist-info")
    }
    if len(metadata_roots) != 1:
        raise DistributionAuditError("wheel members require one dist-info root")
    metadata_root = metadata_roots.pop()
    expected = expected_package | {
        PurePosixPath(metadata_root) / metadata_member for metadata_member in _WHEEL_METADATA
    }
    _require_exact_members(members, frozenset(expected), "wheel")


def verify_wheel_entry_points(payload: bytes) -> None:
    """Require the reviewed wheel to expose only the Comparo console command."""
    if type(payload) is not bytes or payload != _EXPECTED_ENTRY_POINTS:
        raise DistributionAuditError("wheel console entry-point record is not exact")


def _require_exact_members(
    actual: frozenset[PurePosixPath],
    expected: frozenset[PurePosixPath],
    label: str,
) -> None:
    """Reject sensitive, unexpected, and missing archive members with bounded path evidence."""
    sensitive = sorted(str(member) for member in actual if _is_sensitive(member))
    unexpected = sorted(str(member) for member in actual - expected)
    missing = sorted(str(member) for member in expected - actual)
    if sensitive or unexpected or missing:
        details = "; ".join(
            (
                f"sensitive={sensitive[:5]}",
                f"unexpected={unexpected[:5]}",
                f"missing={missing[:5]}",
            )
        )
        raise DistributionAuditError(f"{label} member audit failed: {details}")


def verify_distributions(archives: tuple[Path, ...], source_root: Path) -> None:
    """Verify exactly one source archive and one wheel against current package sources."""
    source_archives = tuple(path for path in archives if path.name.endswith(".tar.gz"))
    wheels = tuple(path for path in archives if path.suffix == ".whl")
    if len(source_archives) != 1 or len(wheels) != 1 or len(archives) != 2:
        raise DistributionAuditError("distribution audit requires exactly one sdist and one wheel")
    expected_package = _expected_package_files(source_root)
    verify_sdist_members(_archive_members(source_archives[0]), expected_package)
    verify_wheel_members(_archive_members(wheels[0]), expected_package)
    verify_wheel_entry_points(_wheel_entry_points(wheels[0]))


def main(arguments: tuple[str, ...]) -> int:
    """Run archive verification from the repository root."""
    verify_distributions(tuple(Path(argument) for argument in arguments), Path("src/comparo"))
    return 0


if __name__ == "__main__":
    sys.exit(main(tuple(sys.argv[1:])))
