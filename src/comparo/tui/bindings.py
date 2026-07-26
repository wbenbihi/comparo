"""Textual bindings generated from Comparo's semantic command catalog."""

from __future__ import annotations

from textual.binding import Binding
from textual.binding import BindingType

from comparo.tui.commands import COMMAND_CATALOG
from comparo.tui.commands import CommandDescriptor
from comparo.tui.commands import CommandScope
from comparo.tui.commands import default_keys


def textual_bindings(scope: CommandScope | None = CommandScope.GLOBAL) -> list[BindingType]:
    """Build native bindings for one semantic scope from the default key map."""
    if scope is not None and type(scope) is not CommandScope:
        raise TypeError("Textual binding scope must be typed")
    commands = (
        COMMAND_CATALOG
        if scope is None
        else tuple(command for command in COMMAND_CATALOG if command.scope is scope)
    )
    return [
        Binding(
            ",".join(default_keys(command.command_id)),
            f"app.dispatch_command_id({command.command_id.value!r})",
            command.label,
            show=command.footer,
            priority=True,
            tooltip=command.description,
            id=command.command_id.value,
        )
        for command in commands
    ]


def command_primary_key(command: CommandDescriptor) -> str:
    """Return the default primary key for labels before user remapping is enabled."""
    if type(command) is not CommandDescriptor:
        raise TypeError("command key display requires CommandDescriptor")
    return default_keys(command.command_id)[0]


__all__ = ("command_primary_key", "textual_bindings")
