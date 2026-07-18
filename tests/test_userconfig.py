"""Tests for the persisted app-level user config (adapters/userconfig.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from comparo.adapters import userconfig


def test_config_home_prefers_the_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", "/tmp/cmp-cfg")
    assert userconfig.config_home() == Path("/tmp/cmp-cfg")
    assert userconfig.config_path() == Path("/tmp/cmp-cfg/config.toml")


def test_config_home_falls_back_to_xdg_then_dot_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPARO_CONFIG_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert userconfig.config_home() == Path("/xdg/comparo")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert userconfig.config_home() == Path.home() / ".config" / "comparo"


def test_missing_file_yields_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))
    assert userconfig.load() == userconfig.UserConfig()
    assert userconfig.load().update_check is False  # version check is opt-in


def test_save_then_load_round_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))
    config = userconfig.UserConfig(
        update_check=True,
        update_last_checked="2026-07-18",
        update_latest_seen="9.9.9",
        theme="midnight",
        diff_view="side-by-side",
        confirm_quit=True,
        default_tab="diff",
    )
    path = userconfig.save(config)
    assert path.exists()
    assert userconfig.load() == config


def test_malformed_file_yields_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COMPARO_CONFIG_HOME", str(tmp_path))
    userconfig.config_path().parent.mkdir(parents=True, exist_ok=True)
    userconfig.config_path().write_text("not valid toml [[[ = ", encoding="utf-8")
    assert userconfig.load() == userconfig.UserConfig()


def test_with_returns_a_changed_copy() -> None:
    base = userconfig.UserConfig()
    changed = base.with_(update_check=True)
    assert changed.update_check is True
    assert base.update_check is False  # original untouched
