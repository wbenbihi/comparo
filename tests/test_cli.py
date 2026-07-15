"""Smoke tests for the CLI entry point."""

from typer.testing import CliRunner

from comparo import __version__
from comparo.cli.app import app

runner = CliRunner()


def test_version_flag_reports_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_flag_shows_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "HTTP regression" in result.stdout
