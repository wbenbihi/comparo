"""Source-distribution boundaries that prevent ignored local state from shipping."""

import tomllib
from pathlib import Path
from pathlib import PurePosixPath
from subprocess import CompletedProcess

import pytest

from tests.architecture import distribution_members
from tests.architecture.distribution_members import DistributionAuditError
from tests.architecture.distribution_members import verify_sdist_members
from tests.architecture.distribution_members import verify_wheel_entry_points
from tests.architecture.distribution_members import verify_wheel_members

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_SDIST_INPUTS = {
    "/src/comparo",
    "/README.md",
    "/LICENSE",
    "/pyproject.toml",
}
_REQUIRED_BUILD_EXCLUSIONS = {
    "/**/__pycache__",
    "/**/.env*",
    "/**/*.pem",
    "/**/*.key",
    "/**/*.credentials",
    "/**/.claude/**",
    "/**/.codex/**",
    "/**/.agents/**",
    "/**/.hypothesis/**",
    "/**/.pytest_cache/**",
    "/**/.coverage*",
    "/**/reports/**",
    "/**/internal*/**",
    "/**/*deprecated*/**",
    "/**/*.ignore.*",
}


def test_source_distribution_uses_an_exact_public_allowlist() -> None:
    """Keep editor, agent, test-cache, secret, and deprecated trees out of release archives."""
    configuration = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())
    build = configuration["tool"]["hatch"]["build"]
    sdist = build["targets"]["sdist"]
    assert build["ignore-vcs"] is False
    assert set(sdist["include"]) == _EXPECTED_SDIST_INPUTS
    assert set(build["exclude"]) >= _REQUIRED_BUILD_EXCLUSIONS


def test_package_exposes_one_exact_console_entry_point() -> None:
    """Keep the installed command name and import target a reviewed public contract."""
    configuration = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())
    assert configuration["project"]["scripts"] == {"comparo": "comparo.cli:main"}

    verify_wheel_entry_points(b"[console_scripts]\ncomparo = comparo.cli:main\n")
    for malformed in (
        b"",
        b"[console_scripts]\ncomparo = comparo.cli.app:main\n",
        b"[console_scripts]\ncomparo = comparo.cli:main\nunsafe = comparo.cli:main\n",
        "[console_scripts]\ncomparo = comparo.cli:main\n",
    ):
        with pytest.raises(DistributionAuditError, match="entry-point record is not exact"):
            verify_wheel_entry_points(malformed)  # type: ignore[arg-type]


def test_sdist_member_audit_rejects_untracked_working_and_sensitive_files() -> None:
    """Prove an allowed parent directory cannot admit arbitrary local descendants."""
    package = frozenset({PurePosixPath("comparo/__init__.py")})
    safe = frozenset(
        {
            PurePosixPath("comparo-0.2.0/.gitignore"),
            PurePosixPath("comparo-0.2.0/LICENSE"),
            PurePosixPath("comparo-0.2.0/PKG-INFO"),
            PurePosixPath("comparo-0.2.0/README.md"),
            PurePosixPath("comparo-0.2.0/pyproject.toml"),
            PurePosixPath("comparo-0.2.0/src/comparo/__init__.py"),
        }
    )
    verify_sdist_members(safe, package)

    for leaked in (
        PurePosixPath("comparo-0.2.0/docs/untracked-working-note.md"),
        PurePosixPath("comparo-0.2.0/src/comparo/.env.production"),
        PurePosixPath("comparo-0.2.0/src/comparo/private.credentials"),
        PurePosixPath("comparo-0.2.0/src/comparo/__pycache__/module.pyc"),
        PurePosixPath("comparo-0.2.0/src/comparo/internal/report.json"),
    ):
        with pytest.raises(DistributionAuditError, match="sdist member audit"):
            verify_sdist_members(safe | {leaked}, package)


def test_sdist_audit_rejects_untracked_python_inside_the_package() -> None:
    """An untracked Python source cannot silently become an accepted release member."""
    reviewed_package = frozenset({PurePosixPath("comparo/__init__.py")})
    archive_with_untracked_source = frozenset(
        {
            PurePosixPath("comparo-0.2.0/.gitignore"),
            PurePosixPath("comparo-0.2.0/LICENSE"),
            PurePosixPath("comparo-0.2.0/PKG-INFO"),
            PurePosixPath("comparo-0.2.0/README.md"),
            PurePosixPath("comparo-0.2.0/pyproject.toml"),
            PurePosixPath("comparo-0.2.0/src/comparo/__init__.py"),
            PurePosixPath("comparo-0.2.0/src/comparo/untracked.py"),
        }
    )

    with pytest.raises(DistributionAuditError, match=r"unexpected=.*untracked\.py"):
        verify_sdist_members(archive_with_untracked_source, reviewed_package)


def test_wheel_member_audit_rejects_extra_package_content() -> None:
    """Keep wheels limited to explicit Python package files and fixed metadata."""
    package = frozenset({PurePosixPath("comparo/__init__.py")})
    safe = package | {
        PurePosixPath("comparo-0.2.0.dist-info/entry_points.txt"),
        PurePosixPath("comparo-0.2.0.dist-info/METADATA"),
        PurePosixPath("comparo-0.2.0.dist-info/RECORD"),
        PurePosixPath("comparo-0.2.0.dist-info/WHEEL"),
        PurePosixPath("comparo-0.2.0.dist-info/licenses/LICENSE"),
    }
    verify_wheel_members(frozenset(safe), package)
    with pytest.raises(DistributionAuditError, match="wheel member audit"):
        verify_wheel_members(
            frozenset(safe | {PurePosixPath("comparo/private-secret.json")}),
            package,
        )


def test_distribution_audit_allows_only_exact_reviewed_security_module_paths() -> None:
    """Public security code is not mistaken for credential or secret payload data."""
    package = frozenset(
        {
            PurePosixPath("comparo/core/credentials.py"),
            PurePosixPath("comparo/core/domain/http/secret_egress.py"),
        }
    )
    sdist = frozenset(
        {
            PurePosixPath("comparo-0.2.0/.gitignore"),
            PurePosixPath("comparo-0.2.0/LICENSE"),
            PurePosixPath("comparo-0.2.0/PKG-INFO"),
            PurePosixPath("comparo-0.2.0/README.md"),
            PurePosixPath("comparo-0.2.0/pyproject.toml"),
            *(PurePosixPath("comparo-0.2.0/src") / member for member in package),
        }
    )
    verify_sdist_members(sdist, package)

    with pytest.raises(DistributionAuditError, match="sensitive"):
        verify_sdist_members(
            sdist | {PurePosixPath("comparo-0.2.0/src/comparo/core/domain/http/another-secret.py")},
            package,
        )


def test_reviewed_package_members_come_only_from_git_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not bless arbitrary working-tree descendants as release inputs."""

    def git_path(_command: str) -> str:
        return "/usr/bin/git"

    def tracked_files(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args=(),
            returncode=0,
            stdout=(
                "src/comparo/__init__.py\n"
                "src/comparo/py.typed\n"
                "src/comparo/schemas/comparo-report-v1alpha1.schema.json\n"
                "src/comparo/tui/components/comparo.tcss\n"
                "src/comparo/tui/components/unreviewed.tcss\n"
                "src/comparo/untracked.txt\n"
                "README.md\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(distribution_members, "which", git_path)
    monkeypatch.setattr(distribution_members, "run", tracked_files)
    assert distribution_members._expected_package_files(Path("src/comparo")) == frozenset(
        {
            PurePosixPath("comparo/__init__.py"),
            PurePosixPath("comparo/py.typed"),
            PurePosixPath("comparo/schemas/comparo-report-v1alpha1.schema.json"),
            PurePosixPath("comparo/tui/components/comparo.tcss"),
        }
    )


def test_reviewed_package_member_discovery_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing Git executable, failed index query, or empty index stops publishing."""
    monkeypatch.setattr(distribution_members, "which", lambda _command: None)
    with pytest.raises(DistributionAuditError, match="requires git"):
        distribution_members._expected_package_files(Path("src/comparo"))

    monkeypatch.setattr(distribution_members, "which", lambda _command: "/usr/bin/git")
    monkeypatch.setattr(
        distribution_members,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            args=(), returncode=1, stdout="", stderr="index unavailable"
        ),
    )
    with pytest.raises(DistributionAuditError, match="index unavailable"):
        distribution_members._expected_package_files(Path("src/comparo"))

    monkeypatch.setattr(
        distribution_members,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(args=(), returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(DistributionAuditError, match="member set is empty"):
        distribution_members._expected_package_files(Path("src/comparo"))
