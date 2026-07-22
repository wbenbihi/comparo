"""Tests for execute-sink secret resolution."""

from pathlib import Path

import pytest

from comparo.core.resolution import ExecuteSecrets
from comparo.core.resolution import SecretError
from comparo.core.resolution import SecretUnavailableError


def test_env_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPARO_TEST_TOKEN", "real-value")
    secrets = ExecuteSecrets({"API": {"$env": "COMPARO_TEST_TOKEN"}}, tmp_path)
    assert secrets["API"] == "real-value"


def test_literal_secret(tmp_path: Path) -> None:
    secrets = ExecuteSecrets({"API": {"$literal": "lit"}}, tmp_path)
    assert secrets["API"] == "lit"


def test_missing_env_raises_unavailable(tmp_path: Path) -> None:
    # An unset $env is a *benign* gap (the value was never available), so it
    # raises the SecretUnavailableError subclass the redactor is allowed to skip.
    secrets = ExecuteSecrets({"API": {"$env": "COMPARO_DEFINITELY_UNSET"}}, tmp_path)
    with pytest.raises(SecretUnavailableError):
        _ = secrets["API"]


def test_undeclared_secret_is_unavailable(tmp_path: Path) -> None:
    secrets = ExecuteSecrets({"API": {"$literal": "x"}}, tmp_path)
    with pytest.raises(SecretUnavailableError):
        _ = secrets["MISSING"]


def test_missing_file_is_benign_but_unreadable_is_anomalous(tmp_path: Path) -> None:
    # A merely-ABSENT $file is BENIGN (SecretUnavailableError) — never available this
    # session — so a $from chain skips it and the redactor drops it without crashing.
    absent = ExecuteSecrets({"API": {"$file": "does-not-exist.txt"}}, tmp_path)
    with pytest.raises(SecretUnavailableError):
        _ = absent["API"]
    # An EXISTS-but-unreadable $file (here a directory in a file slot) is ANOMALOUS: a
    # plain SecretError, so the redactor fails closed on it.
    (tmp_path / "adir").mkdir()
    unreadable = ExecuteSecrets({"API": {"$file": "adir"}}, tmp_path)
    with pytest.raises(SecretError) as exc:
        _ = unreadable["API"]
    assert not isinstance(exc.value, SecretUnavailableError)


def test_exhausted_from_chain_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("COMPARO_MISS_A", raising=False)
    monkeypatch.delenv("COMPARO_MISS_B", raising=False)
    sources: dict[str, object] = {
        "API": {"$from": [{"$env": "COMPARO_MISS_A"}, {"$env": "COMPARO_MISS_B"}]}
    }
    with pytest.raises(SecretUnavailableError):
        _ = ExecuteSecrets(sources, tmp_path)["API"]


def test_from_chain_propagates_an_anomalous_error(tmp_path: Path) -> None:
    # A $from chain skips only a BENIGN absence (unset $env). An anomalous source
    # — a root-escaping (or unreadable) $file — is a real misconfiguration and must
    # fail closed, NOT be silently swallowed in favour of a later fallback that works.
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("leaked", encoding="utf-8")
    sources: dict[str, object] = {
        "API": {"$from": [{"$file": "../outside.txt"}, {"$literal": "fallback"}]}
    }
    with pytest.raises(SecretError) as exc:
        _ = ExecuteSecrets(sources, root)["API"]
    assert not isinstance(exc.value, SecretUnavailableError)  # anomalous, not benign


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
        "API": {"$from": [{"$env": "COMPARO_MISS"}, {"$env": "COMPARO_TEST_FALLBACK"}]}
    }
    assert ExecuteSecrets(sources, tmp_path)["API"] == "fb"


def test_file_secret(tmp_path: Path) -> None:
    (tmp_path / "s.txt").write_text("filesecret\n")
    assert ExecuteSecrets({"API": {"$file": "s.txt"}}, tmp_path)["API"] == "filesecret"
