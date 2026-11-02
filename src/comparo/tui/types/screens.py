"""Workspace identifiers exposed by the terminal application."""

from enum import StrEnum


class ComparoScreen(StrEnum):
    """Stable identifiers for top-level workspaces."""

    EXPLORE = "EXPLORE"
    LAUNCH = "LAUNCH"
    REPORTS = "REPORTS"
    SETTINGS = "SETTINGS"
    DEBUG = "DEBUG"
