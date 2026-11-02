"""Build provenance must identify Comparo, survive packaging, and tolerate missing Git."""

import json
import subprocess
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from comparo import build_info


def test_installed_build_uses_stamp_not_an_unrelated_current_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "site-packages" / "comparo"
    package.mkdir(parents=True)
    sha = "a" * 40
    (package / "_build_info.json").write_text(json.dumps({"sha": sha, "modified": True}))
    monkeypatch.setattr(build_info, "__file__", str(package / "build_info.py"))
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    info = build_info.read_build_info()
    assert info.sha == sha
    assert info.modified
    assert info.source.endswith("/tree/" + sha)
    assert info.author == "Walid Benbihi"
    assert info.license == "MIT"


@pytest.mark.parametrize("payload", ["{", "null", "[]", '{"sha":"bad","modified":false}'])
def test_missing_or_invalid_build_stamp_is_explicit(tmp_path: Path, payload: str) -> None:
    assert build_info.recorded_revision(tmp_path) == (None, False)
    (tmp_path / "_build_info.json").write_text(payload)
    assert build_info.recorded_revision(tmp_path) == (None, False)


def test_source_revision_uses_exact_checkout_and_reports_modified_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert command[1:3] == ["-C", str(tmp_path)]
        assert kwargs["timeout"] == 2
        return subprocess.CompletedProcess(
            command, 0, "b" * 40 if "rev-parse" in command else " M src/comparo/build_info.py"
        )

    monkeypatch.setattr(build_info, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(subprocess, "run", run)
    assert build_info.source_revision(tmp_path) == ("b" * 40, True)
    assert len(calls) == 2
    monkeypatch.setattr(build_info, "which", lambda _: None)
    assert build_info.source_revision(tmp_path) == (None, False)


@pytest.mark.parametrize("failure", [OSError("no git"), subprocess.TimeoutExpired("git", 2)])
def test_source_revision_failure_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(build_info, "which", lambda _: "/usr/bin/git")

    def run(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(subprocess, "run", run)
    assert build_info.source_revision(tmp_path) == (None, False)


def test_missing_distribution_metadata_keeps_available_build_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "comparo"
    package.mkdir()
    (package / "_build_info.json").write_text(json.dumps({"sha": "c" * 40, "modified": False}))
    monkeypatch.setattr(build_info, "__file__", str(package / "build_info.py"))

    def missing(name: str) -> None:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(build_info, "metadata", missing)
    info = build_info.read_build_info()
    assert info.version == "Development"
    assert info.source == "Installed package"
    assert info.sha == "c" * 40
    assert info.author == info.license == info.repository == "Unavailable"


def test_metadata_without_github_url_is_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from email.message import Message

    package = tmp_path / "comparo"
    package.mkdir()
    (package / "_build_info.json").write_text(json.dumps({"sha": "c" * 40, "modified": False}))
    monkeypatch.setattr(build_info, "__file__", str(package / "build_info.py"))
    info = Message()
    info["Project-URL"] = "malformed"
    info["Project-URL"] = "Source, https://example.test/source"
    monkeypatch.setattr(build_info, "metadata", lambda _: info)
    result = build_info.read_build_info()
    assert result.source == "https://example.test/source"
    assert result.repository == result.author == result.license == result.version == "Unavailable"
