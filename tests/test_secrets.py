"""Tests for execute-sink secret resolution."""

from pathlib import Path

import pytest

from comparo.core.secrets import ExecuteSecrets
from comparo.core.secrets import SecretError


def test_env_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPARO_TEST_TOKEN", "real-value")
    secrets = ExecuteSecrets({"API": {"$env": "COMPARO_TEST_TOKEN"}}, tmp_path)
    assert secrets["API"] == "real-value"


def test_literal_secret(tmp_path: Path) -> None:
    secrets = ExecuteSecrets({"API": {"$literal": "lit"}}, tmp_path)
    assert secrets["API"] == "lit"


def test_missing_env_raises(tmp_path: Path) -> None:
    secrets = ExecuteSecrets({"API": {"$env": "COMPARO_DEFINITELY_UNSET"}}, tmp_path)
    with pytest.raises(SecretError):
        _ = secrets["API"]


def test_file_secret_reads_within_the_project(tmp_path: Path) -> None:
    (tmp_path / "token.txt").write_text("in-tree-secret\n", encoding="utf-8")
    secrets = ExecuteSecrets({"API": {"$file": "token.txt"}}, tmp_path)
    assert secrets["API"] == "in-tree-secret"


def test_file_secret_cannot_escape_the_project_root(tmp_path: Path) -> None:
    # A $file path that climbs out of the project must be refused, not read.
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("etc-passwd-like", encoding="utf-8")
    secrets = ExecuteSecrets({"API": {"$file": "../outside.txt"}}, root)
    with pytest.raises(SecretError, match="escapes"):
        _ = secrets["API"]


def test_file_secret_absolute_path_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    secret = tmp_path / "abs.txt"
    secret.write_text("nope", encoding="utf-8")
    secrets = ExecuteSecrets({"API": {"$file": str(secret)}}, root)
    with pytest.raises(SecretError, match="escapes"):
        _ = secrets["API"]


def test_from_falls_back_to_first_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("COMPARO_MISS", raising=False)
    monkeypatch.setenv("COMPARO_TEST_FALLBACK", "fb")
    sources: dict[str, object] = {
        "API": {"from": [{"$env": "COMPARO_MISS"}, {"$env": "COMPARO_TEST_FALLBACK"}]}
    }
    assert ExecuteSecrets(sources, tmp_path)["API"] == "fb"


def test_file_secret(tmp_path: Path) -> None:
    (tmp_path / "s.txt").write_text("filesecret\n")
    assert ExecuteSecrets({"API": {"$file": "s.txt"}}, tmp_path)["API"] == "filesecret"
