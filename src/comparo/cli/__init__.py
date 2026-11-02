"""Public entry points for Comparo's command-line adapter."""

from __future__ import annotations

from .app import generate_cli_reference as generate_cli_reference
from .app import main as main

__all__ = ["generate_cli_reference", "main"]
