"""Starter project journeys and non-destructive initialization failures."""

import json
import os
from io import StringIO
from pathlib import Path

import pytest

from comparo.cli import main
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import InitRequest


def _invoke(*arguments: str) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_init_current_directory_creates_a_valid_plannable_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code, stdout, stderr = _invoke("init")
    assert code == 0
    assert "comparo validate" in stdout
    assert "comparo diff" in stdout
    assert stderr == ""
    assert list(tmp_path.iterdir()) == [tmp_path / "comparo.yaml"]

    for command in ("validate", "plan", "render"):
        code, stdout, stderr = _invoke(command, "--format", "json")
        assert code == 0, (command, stdout, stderr)
        assert isinstance(json.loads(stdout), dict)
        assert stderr == ""

    code, stdout, stderr = _invoke("plan", "--profile", "compare-health")
    assert code == 0, (stdout, stderr)
    code, stdout, stderr = _invoke("plan", "--env", "baseline")
    assert code == 0, (stdout, stderr)


def test_init_creates_a_nested_destination_and_machine_result(tmp_path: Path) -> None:
    destination = tmp_path / "my project" / "api"
    code, stdout, stderr = _invoke("init", str(destination), "--format", "json")
    assert code == 0
    assert json.loads(stdout) == {
        "apiVersion": "comparo.cli/v1alpha1",
        "command": "init",
        "status": "created",
    }
    assert stderr == ""
    assert (destination / "comparo.yaml").is_file()


@pytest.mark.parametrize("existing", ["file", "directory", "symlink", "dangling-symlink"])
def test_init_never_replaces_an_existing_manifest(tmp_path: Path, existing: str) -> None:
    manifest = tmp_path / "comparo.yaml"
    original = b"existing configuration\n"
    target = tmp_path / "target"
    if existing == "file":
        manifest.write_bytes(original)
    elif existing == "directory":
        manifest.mkdir()
    else:
        if existing == "symlink":
            target.write_bytes(original)
        manifest.symlink_to(target)
    before = set(tmp_path.iterdir())

    code, stdout, stderr = _invoke("init", str(tmp_path), "--format", "json")
    assert code == 2
    assert json.loads(stdout)["status"] == "already-exists"
    assert stderr == ""
    assert set(tmp_path.iterdir()) == before
    if existing == "file":
        assert manifest.read_bytes() == original
    elif existing == "directory":
        assert manifest.is_dir()
    else:
        assert manifest.is_symlink()
        if existing == "symlink":
            assert target.read_bytes() == original
        else:
            assert not target.exists()


def test_repeated_init_preserves_user_edits(tmp_path: Path) -> None:
    assert _invoke("init", str(tmp_path))[0] == 0
    manifest = tmp_path / "comparo.yaml"
    edited = manifest.read_text().replace("localhost:8000", "localhost:9000")
    manifest.write_text(edited)
    code, stdout, stderr = _invoke("init", str(tmp_path))
    assert code == 2
    assert "already exists" in stderr
    assert stdout == ""
    assert manifest.read_text() == edited


def test_init_reports_write_failure_without_publishing_partial_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_link(source: Path, destination: Path) -> None:
        raise PermissionError("sensitive filesystem details")

    monkeypatch.setattr(os, "link", fail_link)
    code, stdout, stderr = _invoke("init", str(tmp_path))
    assert code == 2
    assert stdout == ""
    assert "write permissions" in " ".join(stderr.split())
    assert "sensitive" not in stderr
    assert list(tmp_path.iterdir()) == []


def test_init_rejects_a_file_as_project_directory(tmp_path: Path) -> None:
    destination = tmp_path / "file"
    destination.write_text("keep me")
    assert _invoke("init", str(destination))[0] == 2
    assert destination.read_text() == "keep me"


def test_init_request_validates_direct_construction() -> None:
    with pytest.raises(TypeError, match="Path"):
        InitRequest(directory=".", output=CliOutputMode.HUMAN)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="typed"):
        InitRequest(directory=Path(), output="human")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="layout"):
        InitRequest(directory=Path(), output=CliOutputMode.HUMAN, layout="unknown")


def test_split_initialization_is_plannable_and_contains_each_resource_once(tmp_path: Path) -> None:
    code, stdout, stderr = _invoke("init", str(tmp_path), "--layout", "split")
    assert code == 0, (stdout, stderr)
    assert {item.relative_to(tmp_path).as_posix() for item in tmp_path.rglob("*.yaml")} == {
        "comparo.yaml",
        ".comparo/environments.yaml",
        ".comparo/requests/health.yaml",
        ".comparo/profiles/checks.yaml",
    }
    assert "kind: Environment" not in (tmp_path / "comparo.yaml").read_text()
    for command in ("validate", "plan", "render"):
        code, stdout, stderr = _invoke(command, "--config", str(tmp_path), "--format", "json")
        assert code == 0, (command, stdout, stderr)


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_split_init_preserves_existing_data_directory(tmp_path: Path, kind: str) -> None:
    data = tmp_path / ".comparo"
    if kind == "directory":
        data.mkdir()
        (data / "existing.yaml").write_text("keep")
    elif kind == "file":
        data.write_text("keep")
    else:
        data.symlink_to(tmp_path / "missing")
    assert _invoke("init", str(tmp_path), "--layout", "split")[0] == 2
    assert not (tmp_path / "comparo.yaml").exists()
    if kind == "directory":
        assert (data / "existing.yaml").read_text() == "keep"
    elif kind == "file":
        assert data.read_text() == "keep"
    else:
        assert data.is_symlink()


def test_split_init_rolls_back_partial_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.link

    def fail_manifest(source: Path, destination: Path) -> None:
        if destination.name == "comparo.yaml":
            raise PermissionError
        original(source, destination)

    monkeypatch.setattr(os, "link", fail_manifest)
    assert _invoke("init", str(tmp_path), "--layout", "split")[0] == 2
    assert list(tmp_path.iterdir()) == []
