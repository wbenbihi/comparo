"""Textual bindings generated from Comparo's semantic command catalog."""

from __future__ import annotations

from textual.binding import Binding
from textual.binding import BindingType

from comparo.tui.commands import COMMAND_CATALOG


def textual_bindings() -> list[BindingType]:
    """Build one physical-key declaration from the central semantic catalog."""
    keys = {key: command.label for command in COMMAND_CATALOG for key in command.keys}
    return [
        Binding(
            key,
            f"dispatch_catalog_key({key!r})",
            label,
            show=False,
            priority=key in {"escape", "ctrl+k"},
        )
        for key, label in keys.items()
    ]


__all__ = ("textual_bindings",)
